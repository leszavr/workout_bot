"""Unit-тесты ProgramGenerationOrchestrator (Stage 5).

Никакой внешней БД: все зависимости заменены in-memory фейками.
"""
from __future__ import annotations

import pytest

from src.application.programs.orchestrator import (
    ProgramGenerationOrchestrator,
)
from src.domain.enums import GenerationSource, ProgramStatus
from src.domain.exercise import Exercise
from src.domain.pools import ExerciseCandidatePool, SafeExercisePool
from src.domain.profile import FitnessProfile
from src.domain.program import (
    GenerationInfo,
    ProgramExercise,
    TrainingDay,
    WorkoutProgram,
)
from src.errors import ProgramGenerationError

EX_ID = "Barbell_Full_Squat"


def _exercise() -> Exercise:
    return Exercise(external_id=EX_ID, name="Barbell Full Squat")


def _profile(profile_id: str = "p1") -> FitnessProfile:
    return FitnessProfile(profile_id=profile_id)


def _valid_program(profile_id: str, generator: GenerationSource) -> WorkoutProgram:
    return WorkoutProgram(
        profile_id=profile_id,
        title="Тестовая программа",
        duration_weeks=4,
        training_days_per_week=1,
        training_days=[
            TrainingDay(
                day_number=1,
                title="День 1",
                focus="Full body",
                exercises=[
                    ProgramExercise(
                        exercise_external_id=EX_ID,
                        order=1,
                        sets=3,
                        repetitions_min=10,
                        repetitions_max=12,
                        rest_seconds=60,
                    )
                ],
            )
        ],
        generation=GenerationInfo(source=generator),
    )


class FakeProfileRepository:
    def __init__(self, profile: FitnessProfile | None) -> None:
        self._profile = profile

    async def save(self, profile: FitnessProfile) -> FitnessProfile:
        self._profile = profile
        return profile

    async def get(self, profile_id: str) -> FitnessProfile | None:
        return self._profile

    async def exists(self, profile_id: str) -> bool:
        return self._profile is not None

    async def next_display_number(self) -> str:
        return "REQ-20260818-00001"

    async def delete(self, profile_id: str) -> None:
        self._profile = None


class FakeExerciseRepository:
    async def list(self, *, limit: int = 50, **kwargs) -> list[Exercise]:
        return [_exercise()]

    async def get_by_external_id(self, external_id: str, source: str = "leszavr/workout"):
        return _exercise() if external_id == EX_ID else None

    async def upsert(self, exercise: Exercise) -> None: ...

    async def count(self) -> int:
        return 1


class FakeProgramRepository:
    def __init__(self) -> None:
        self.programs: list[WorkoutProgram] = []

    async def save(self, program: WorkoutProgram) -> WorkoutProgram:
        self.programs.append(program)
        return program

    async def get(self, program_id: str, version: int | None = None):
        for p in reversed(self.programs):
            if p.program_id == program_id and (version is None or p.version == version):
                return p
        return None

    async def list_versions(self, program_id: str) -> list[WorkoutProgram]:
        return [p for p in self.programs if p.program_id == program_id]

    async def list_for_profile(self, profile_id: str) -> list[WorkoutProgram]:
        return [p for p in self.programs if p.profile_id == profile_id]

    async def list_all(self, limit: int = 50, offset: int = 0):
        return len(self.programs), self.programs[offset : offset + limit]

    async def next_version(self, profile_id: str) -> int:
        return len([p for p in self.programs if p.profile_id == profile_id]) + 1

    async def count(self) -> int:
        return len(self.programs)


class FakeFilter:
    async def select_candidates(self, profile, catalog):
        return ExerciseCandidatePool(
            profile_id=profile.profile_id or "", total_exercises=len(catalog), included=catalog
        )


class FakeSafety:
    def apply(self, profile, included):
        return SafeExercisePool(profile_id=profile.profile_id or "", allowed=included)


class FakeGenerator:
    """Генератор с настраиваемым поведением."""

    def __init__(
        self,
        name: str,
        *,
        fail: bool = False,
        fail_exception: Exception | None = None,
        invalid: bool = False,
    ) -> None:
        self.name = name
        self.fail = fail
        self.fail_exception = fail_exception or RuntimeError(f"{name} generation failed")
        self.invalid = invalid
        self.calls = 0

    async def generate(self, profile, pool):
        self.calls += 1
        if self.fail:
            raise self.fail_exception
        source = GenerationSource.AI if self.name == "ai" else GenerationSource.DETERMINISTIC
        program = _valid_program(profile.profile_id, source)
        if self.invalid:
            program.training_days[0].exercises[0].exercise_external_id = "not_in_catalog"
        return program


def _orchestrator(
    *,
    primary: str = "ai",
    fallback: str = "deterministic",
    ai_generator: FakeGenerator | None = None,
    deterministic_generator: FakeGenerator | None = None,
    program_repo: FakeProgramRepository | None = None,
    ai_factory_error: Exception | None = None,
) -> tuple[ProgramGenerationOrchestrator, FakeProgramRepository]:
    repo = program_repo or FakeProgramRepository()

    def ai_factory():
        if ai_factory_error is not None:
            raise ai_factory_error
        return ai_generator

    orchestrator = ProgramGenerationOrchestrator(
        profile_repository=FakeProfileRepository(_profile()),
        exercise_repository=FakeExerciseRepository(),
        program_repository=repo,
        primary_generator=primary,
        fallback_generator=fallback,
        ai_generator_factory=ai_factory if primary == "ai" or fallback == "ai" else None,
        deterministic_generator=deterministic_generator
        or (ai_generator if ai_generator and ai_generator.name == "deterministic" else None),
        exercise_filter=FakeFilter(),
        safety_engine=FakeSafety(),
    )
    return orchestrator, repo


class TestOrchestratorPrimarySuccess:
    async def test_ai_success_no_fallback(self):
        ai = FakeGenerator("ai")
        orchestrator, repo = _orchestrator(ai_generator=ai)

        result = await orchestrator.generate("p1")

        assert result.fallback_used is False
        assert ai.calls == 1
        info = result.program.generation
        assert info.requested_generator is GenerationSource.AI
        assert info.actual_generator is GenerationSource.AI
        assert info.fallback_used is False
        assert info.fallback_reason is None
        assert result.program.status is ProgramStatus.VALIDATED
        assert len(repo.programs) == 1
        assert result.program.version == 1

    async def test_deterministic_primary_success(self):
        det = FakeGenerator("deterministic")
        orchestrator, _ = _orchestrator(
            primary="deterministic", fallback="ai", deterministic_generator=det
        )

        result = await orchestrator.generate("p1")

        assert result.fallback_used is False
        assert det.calls == 1
        assert result.program.generation.actual_generator is GenerationSource.DETERMINISTIC

    async def test_invalid_generator_config_rejected(self):
        with pytest.raises(ValueError):
            ProgramGenerationOrchestrator(
                profile_repository=FakeProfileRepository(_profile()),
                exercise_repository=FakeExerciseRepository(),
                program_repository=FakeProgramRepository(),
                primary_generator="foo",
                fallback_generator="deterministic",
            )


class TestOrchestratorFallback:
    async def test_ai_error_falls_back_to_deterministic(self):
        ai = FakeGenerator("ai", fail=True)
        det = FakeGenerator("deterministic")
        orchestrator, repo = _orchestrator(ai_generator=ai, deterministic_generator=det)

        result = await orchestrator.generate("p1")

        assert result.fallback_used is True
        assert ai.calls == 1
        assert det.calls == 1
        info = result.program.generation
        assert info.requested_generator is GenerationSource.AI
        assert info.actual_generator is GenerationSource.DETERMINISTIC
        assert info.fallback_used is True
        assert info.fallback_reason is not None
        assert "ошибка генерации" in info.fallback_reason

    async def test_ai_validation_failure_falls_back(self):
        ai = FakeGenerator("ai", invalid=True)
        det = FakeGenerator("deterministic")
        orchestrator, _ = _orchestrator(ai_generator=ai, deterministic_generator=det)

        result = await orchestrator.generate("p1")

        assert result.fallback_used is True
        assert det.calls == 1
        info = result.program.generation
        assert info.actual_generator is GenerationSource.DETERMINISTIC
        assert "validation failed" in (info.fallback_reason or "")

    async def test_reverse_configuration_deterministic_to_ai(self):
        det = FakeGenerator("deterministic", fail=True)
        ai = FakeGenerator("ai")
        orchestrator, _ = _orchestrator(
            primary="deterministic",
            fallback="ai",
            ai_generator=ai,
            deterministic_generator=det,
        )

        result = await orchestrator.generate("p1")

        assert result.fallback_used is True
        info = result.program.generation
        assert info.requested_generator is GenerationSource.DETERMINISTIC
        assert info.actual_generator is GenerationSource.AI

    async def test_both_generators_fail_raises(self):
        ai = FakeGenerator("ai", fail=True)
        det = FakeGenerator("deterministic", fail=True)
        orchestrator, repo = _orchestrator(ai_generator=ai, deterministic_generator=det)

        with pytest.raises(ProgramGenerationError):
            await orchestrator.generate("p1")

        assert ai.calls == 1
        assert det.calls == 1
        assert len(repo.programs) == 0

    async def test_no_infinite_fallback_loop_same_generator(self):
        det = FakeGenerator("deterministic", fail=True)
        orchestrator, _ = _orchestrator(
            primary="deterministic", fallback="deterministic", deterministic_generator=det
        )

        with pytest.raises(ProgramGenerationError):
            await orchestrator.generate("p1")

        assert det.calls == 1

    async def test_ai_factory_unavailable_falls_back(self):
        det = FakeGenerator("deterministic")
        orchestrator, _ = _orchestrator(
            deterministic_generator=det,
            ai_factory_error=RuntimeError("ai config missing"),
        )

        result = await orchestrator.generate("p1")

        assert result.fallback_used is True
        assert det.calls == 1

    async def test_ai_not_configured_is_fallback_unavailable(self):
        det = FakeGenerator("deterministic", fail=True)
        orchestrator, _ = _orchestrator(
            primary="deterministic", fallback="ai", deterministic_generator=det
        )

        with pytest.raises(ProgramGenerationError):
            await orchestrator.generate("p1")

        assert det.calls == 1


class TestOrchestratorIdempotency:
    async def test_reuse_existing_returns_without_generation(self):
        det = FakeGenerator("deterministic")
        orchestrator, repo = _orchestrator(deterministic_generator=det)

        first = await orchestrator.generate("p1", reuse_existing=True)
        second = await orchestrator.generate("p1", reuse_existing=True)

        assert det.calls == 1
        assert second.reused_existing is True
        assert second.program.program_id == first.program.program_id
        assert second.program.version == 1
        assert len(repo.programs) == 1

    async def test_regenerate_without_reuse_creates_new_version(self):
        det = FakeGenerator("deterministic")
        orchestrator, repo = _orchestrator(deterministic_generator=det)

        first = await orchestrator.generate("p1")
        second = await orchestrator.generate("p1")

        assert det.calls == 2
        assert second.program.version == 2
        assert len(repo.programs) == 2

    async def test_missing_profile_raises(self):
        orchestrator, _ = _orchestrator(deterministic_generator=FakeGenerator("deterministic"))
        orchestrator._profiles = FakeProfileRepository(None)

        with pytest.raises(ProgramGenerationError):
            await orchestrator.generate("no-such-profile")
