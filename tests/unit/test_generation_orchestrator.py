"""Unit-тесты ProgramGenerationOrchestrator.

Никакой внешней БД: все зависимости заменены in-memory фейками.

Phase 1.2-C: оркестратор — единственная точка генерации. Здесь же проверяется
контракт запроса (стратегия на запрос, запрет подмены явно выбранного
генератора) и наружный контракт результата/ошибки.
"""
from __future__ import annotations

import pytest

from src.application.programs.orchestrator import (
    FallbackEvent,
    GateDecision,
    GenerationRequest,
    ProgramGenerationOrchestrator,
)
from src.domain.ai.enums import AIFallbackReason
from src.domain.ai.errors import (
    AIConnectionError,
    AIInvalidResponseError,
    AIRateLimitError,
    AITimeoutError,
)
from src.domain.enums import GenerationJobStatus, GenerationSource, ProgramStatus
from src.domain.exercise import Exercise
from src.domain.generation import (
    GenerationErrorCode,
    GenerationTrigger,
    classify_error,
    fallback_reason_for_code,
)
from src.domain.pools import ExerciseCandidatePool, SafeExercisePool
from src.domain.profile import FitnessProfile
from src.domain.program import (
    GenerationInfo,
    ProgramExercise,
    TrainingDay,
    WorkoutProgram,
)
from src.errors import GenerationFailedError, ProgramGenerationError

EX_ID = "Barbell_Full_Squat"


def _request(
    *,
    profile_id: str = "p1",
    trigger: GenerationTrigger = GenerationTrigger.AUTO_FINALIZATION,
    requested_generator: str | None = None,
    allow_fallback: bool = True,
    reuse_existing: bool = False,
    client_idempotency_key: str | None = None,
) -> GenerationRequest:
    return GenerationRequest(
        profile_id=profile_id,
        trigger=trigger,
        requested_generator=requested_generator,
        allow_fallback=allow_fallback,
        reuse_existing=reuse_existing,
        client_idempotency_key=client_idempotency_key,
    )


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


class _RecordingJobService:
    """Фиксирует, дошёл ли запрос до operational-записи.

    Нужен, чтобы отличить отказ контракта запроса (job создаваться не должен) от
    отказа самой генерации.
    """

    def __init__(self) -> None:
        self.calls = 0

    async def run(self, **kwargs):
        self.calls += 1
        raise AssertionError("job не должен создаваться при отказе контракта запроса")


def _orchestrator(
    *,
    primary: str = "ai",
    fallback: str = "deterministic",
    ai_generator: FakeGenerator | None = None,
    deterministic_generator: FakeGenerator | None = None,
    program_repo: FakeProgramRepository | None = None,
    ai_factory_error: Exception | None = None,
    gate: GateDecision | Exception | None = None,
    recorder: list[FallbackEvent] | None = None,
) -> tuple[ProgramGenerationOrchestrator, FakeProgramRepository]:
    repo = program_repo or FakeProgramRepository()

    def ai_factory():
        if ai_factory_error is not None:
            raise ai_factory_error
        return ai_generator

    async def ai_gate() -> GateDecision:
        if isinstance(gate, Exception):
            raise gate
        assert gate is not None
        return gate

    async def fallback_recorder(event: FallbackEvent) -> None:
        assert recorder is not None
        recorder.append(event)

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
        ai_readiness_gate=ai_gate if gate is not None else None,
        fallback_recorder=fallback_recorder if recorder is not None else None,
    )
    return orchestrator, repo


class TestOrchestratorPrimarySuccess:
    async def test_ai_success_no_fallback(self):
        ai = FakeGenerator("ai")
        orchestrator, repo = _orchestrator(ai_generator=ai)

        result = await orchestrator.generate(_request())

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

        result = await orchestrator.generate(_request())

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


class TestRequestContract:
    """Phase 1.2-C: Telegram и Admin API различаются только запросом."""

    async def test_request_generator_overrides_configuration(self):
        """Явно выбранный генератор перекрывает конфигурацию приложения."""
        ai = FakeGenerator("ai")
        det = FakeGenerator("deterministic")
        orchestrator, _ = _orchestrator(
            primary="ai", ai_generator=ai, deterministic_generator=det
        )

        result = await orchestrator.generate(
            _request(
                trigger=GenerationTrigger.ADMIN_REQUEST,
                requested_generator="deterministic",
                allow_fallback=False,
            )
        )

        assert ai.calls == 0
        assert det.calls == 1
        assert result.requested_generator == "deterministic"
        assert result.actual_generator == "deterministic"

    async def test_forbidden_fallback_does_not_substitute_generator(self):
        """Отказ явно выбранного AI не подменяется детерминированной программой."""
        ai = FakeGenerator("ai", fail=True, fail_exception=AITimeoutError("timeout"))
        det = FakeGenerator("deterministic")
        orchestrator, repo = _orchestrator(
            ai_generator=ai, deterministic_generator=det
        )

        with pytest.raises(GenerationFailedError) as exc:
            await orchestrator.generate(
                _request(
                    trigger=GenerationTrigger.ADMIN_REQUEST,
                    requested_generator="ai",
                    allow_fallback=False,
                )
            )

        assert ai.calls == 1
        assert det.calls == 0
        assert repo.programs == []
        assert exc.value.generation_error_code == GenerationErrorCode.AI_TIMEOUT.value

    async def test_unknown_requested_generator_is_domain_error(self):
        """Недопустимый генератор — доменный отказ, а не raw ValueError.

        Оркестратор — application-level boundary: он обязан отвечать одинаково
        любому вызывающему слою, а не только HTTP-слою с pydantic-валидацией.
        """
        orchestrator, repo = _orchestrator(
            deterministic_generator=FakeGenerator("deterministic")
        )

        with pytest.raises(GenerationFailedError) as exc:
            await orchestrator.generate(_request(requested_generator="magic"))

        assert (
            exc.value.generation_error_code
            == GenerationErrorCode.VALIDATION_FAILED.value
        )
        # Отказ контракта не должен создавать программу.
        assert repo.programs == []

    async def test_unknown_generator_error_is_not_value_error(self):
        """Regression: наружу уходит доменный контракт, а не raw ValueError.

        `GenerationFailedError` не наследует `ValueError`, поэтому вызывающий
        слой не может случайно поймать его общим `except ValueError` и не
        получит 500 вместо 4xx.
        """
        orchestrator, _ = _orchestrator(
            deterministic_generator=FakeGenerator("deterministic")
        )

        with pytest.raises(GenerationFailedError):
            await orchestrator.generate(_request(requested_generator="magic"))

        assert not issubclass(GenerationFailedError, ValueError)

    async def test_invalid_generator_creates_no_job(self):
        """Отказ контракта происходит до создания operational-записи."""
        jobs = _RecordingJobService()
        orchestrator, repo = _orchestrator(
            deterministic_generator=FakeGenerator("deterministic")
        )
        orchestrator._generation_jobs = jobs  # noqa: SLF001 — подмена в тесте

        with pytest.raises(GenerationFailedError):
            await orchestrator.generate(_request(requested_generator="magic"))

        assert jobs.calls == 0
        assert repo.programs == []

    async def test_result_reports_strategy_and_status(self):
        """Результат самодостаточен: стратегия и статус доступны вызывающему."""
        ai = FakeGenerator("ai", fail=True)
        det = FakeGenerator("deterministic")
        orchestrator, _ = _orchestrator(
            ai_generator=ai, deterministic_generator=det, gate=GateDecision(allowed=True)
        )

        result = await orchestrator.generate(_request())

        assert result.requested_generator == "ai"
        assert result.actual_generator == "deterministic"
        assert result.fallback_used is True
        assert result.fallback_reason_code == "ai_runtime_failure"
        # Без job-контура успешный возврат означает завершённую генерацию.
        assert result.status is GenerationJobStatus.SUCCEEDED

    async def test_failure_code_does_not_leak_secrets(self):
        """Наружу уходит безопасное сообщение: ключей в тексте нет."""
        ai = FakeGenerator(
            "ai",
            fail=True,
            fail_exception=AITimeoutError(
                "HTTP 401 Authorization: Bearer sk-secret-value-123456"
            ),
        )
        orchestrator, _ = _orchestrator(
            ai_generator=ai,
            deterministic_generator=FakeGenerator("deterministic"),
            gate=GateDecision(allowed=True),
        )

        with pytest.raises(GenerationFailedError) as exc:
            await orchestrator.generate(
                _request(requested_generator="ai", allow_fallback=False)
            )

        assert "sk-secret-value-123456" not in str(exc.value)
        assert "[redacted]" in str(exc.value)

    async def test_reused_result_reports_previous_strategy(self):
        det = FakeGenerator("deterministic")
        orchestrator, _ = _orchestrator(
            primary="deterministic", fallback="deterministic", deterministic_generator=det
        )

        await orchestrator.generate(_request(reuse_existing=True))
        second = await orchestrator.generate(_request(reuse_existing=True))

        assert second.reused_existing is True
        assert second.actual_generator == "deterministic"
        assert second.status is GenerationJobStatus.SUCCEEDED


class TestOrchestratorFallback:
    async def test_ai_error_falls_back_to_deterministic(self):
        ai = FakeGenerator("ai", fail=True)
        det = FakeGenerator("deterministic")
        orchestrator, repo = _orchestrator(ai_generator=ai, deterministic_generator=det)

        result = await orchestrator.generate(_request())

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

        result = await orchestrator.generate(_request())

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

        result = await orchestrator.generate(_request())

        assert result.fallback_used is True
        info = result.program.generation
        assert info.requested_generator is GenerationSource.DETERMINISTIC
        assert info.actual_generator is GenerationSource.AI

    async def test_both_generators_fail_raises(self):
        ai = FakeGenerator("ai", fail=True)
        det = FakeGenerator("deterministic", fail=True)
        orchestrator, repo = _orchestrator(ai_generator=ai, deterministic_generator=det)

        with pytest.raises(ProgramGenerationError):
            await orchestrator.generate(_request())

        assert ai.calls == 1
        assert det.calls == 1
        assert len(repo.programs) == 0

    async def test_no_infinite_fallback_loop_same_generator(self):
        det = FakeGenerator("deterministic", fail=True)
        orchestrator, _ = _orchestrator(
            primary="deterministic", fallback="deterministic", deterministic_generator=det
        )

        with pytest.raises(ProgramGenerationError):
            await orchestrator.generate(_request())

        assert det.calls == 1

    async def test_ai_factory_unavailable_falls_back(self):
        det = FakeGenerator("deterministic")
        orchestrator, _ = _orchestrator(
            deterministic_generator=det,
            ai_factory_error=RuntimeError("ai config missing"),
        )

        result = await orchestrator.generate(_request())

        assert result.fallback_used is True
        assert det.calls == 1

    async def test_ai_not_configured_is_fallback_unavailable(self):
        det = FakeGenerator("deterministic", fail=True)
        orchestrator, _ = _orchestrator(
            primary="deterministic", fallback="ai", deterministic_generator=det
        )

        with pytest.raises(ProgramGenerationError):
            await orchestrator.generate(_request())

        assert det.calls == 1


class TestOrchestratorIdempotency:
    async def test_reuse_existing_returns_without_generation(self):
        det = FakeGenerator("deterministic")
        orchestrator, repo = _orchestrator(deterministic_generator=det)

        first = await orchestrator.generate(_request(reuse_existing=True))
        second = await orchestrator.generate(_request(reuse_existing=True))

        assert det.calls == 1
        assert second.reused_existing is True
        assert second.program.program_id == first.program.program_id
        assert second.program.version == 1
        assert len(repo.programs) == 1

    async def test_regenerate_without_reuse_creates_new_version(self):
        det = FakeGenerator("deterministic")
        orchestrator, repo = _orchestrator(deterministic_generator=det)

        first = await orchestrator.generate(_request())
        second = await orchestrator.generate(_request())

        assert det.calls == 2
        assert second.program.version == 2
        assert len(repo.programs) == 2

    async def test_missing_profile_raises(self):
        orchestrator, _ = _orchestrator(deterministic_generator=FakeGenerator("deterministic"))
        orchestrator._profiles = FakeProfileRepository(None)

        with pytest.raises(GenerationFailedError) as exc:
            await orchestrator.generate(_request(profile_id="no-such-profile"))

        assert (
            exc.value.generation_error_code
            == GenerationErrorCode.PROFILE_NOT_FOUND.value
        )


class TestReadinessGate:
    """readiness должен реально влиять на генерацию, а не только на UI."""

    async def test_not_ready_skips_ai_call_entirely(self):
        ai = FakeGenerator("ai")
        det = FakeGenerator("deterministic")
        orchestrator, _ = _orchestrator(
            ai_generator=ai,
            deterministic_generator=det,
            gate=GateDecision(
                allowed=False,
                reason=AIFallbackReason.PROVIDER_UNAVAILABLE,
                detail="Провайдер: все подходящие провайдеры отключены",
            ),
        )

        result = await orchestrator.generate(_request())

        # Ключевое: заведомо бесполезный AI-запрос не выполнялся.
        assert ai.calls == 0
        assert det.calls == 1
        assert result.fallback_used is True

    async def test_not_ready_stores_structured_reason(self):
        orchestrator, _ = _orchestrator(
            ai_generator=FakeGenerator("ai"),
            deterministic_generator=FakeGenerator("deterministic"),
            gate=GateDecision(
                allowed=False,
                reason=AIFallbackReason.CONNECTION_NOT_TESTED,
                detail="Проверка подключения: ни разу не проверялось",
            ),
        )

        info = (await orchestrator.generate(_request())).program.generation

        assert info.fallback_reason_code == "connection_not_tested"
        assert info.requested_generator is GenerationSource.AI
        assert info.actual_generator is GenerationSource.DETERMINISTIC
        assert "Проверка подключения" in (info.fallback_reason or "")

    async def test_ready_gate_allows_ai_call(self):
        ai = FakeGenerator("ai")
        orchestrator, _ = _orchestrator(
            ai_generator=ai,
            deterministic_generator=FakeGenerator("deterministic"),
            gate=GateDecision(allowed=True),
        )

        result = await orchestrator.generate(_request())

        assert ai.calls == 1
        assert result.fallback_used is False
        assert result.program.generation.fallback_reason_code is None

    async def test_gate_failure_does_not_block_generation(self):
        """Неизвестное состояние readiness не должно ломать генерацию."""
        ai = FakeGenerator("ai")
        orchestrator, _ = _orchestrator(
            ai_generator=ai,
            deterministic_generator=FakeGenerator("deterministic"),
            gate=RuntimeError("readiness backend down"),
        )

        result = await orchestrator.generate(_request())

        assert ai.calls == 1
        assert result.fallback_used is False

    async def test_gate_block_does_not_loop(self):
        """Запрет AI + падение deterministic → одна ошибка, без циклов."""
        det = FakeGenerator("deterministic", fail=True)
        ai = FakeGenerator("ai")
        orchestrator, _ = _orchestrator(
            ai_generator=ai,
            deterministic_generator=det,
            gate=GateDecision(
                allowed=False, reason=AIFallbackReason.TASK_DISABLED, detail="выключена"
            ),
        )

        with pytest.raises(ProgramGenerationError):
            await orchestrator.generate(_request())

        assert ai.calls == 0
        assert det.calls == 1


class TestRuntimeFallbackClassification:
    """AI был готов, попытка сделана — причина должна быть runtime, не конфиг."""

    async def test_timeout_is_classified_as_ai_timeout(self):
        ai = FakeGenerator("ai", fail=True, fail_exception=AITimeoutError("timeout"))
        orchestrator, _ = _orchestrator(
            ai_generator=ai,
            deterministic_generator=FakeGenerator("deterministic"),
            gate=GateDecision(allowed=True),
        )

        info = (await orchestrator.generate(_request())).program.generation

        assert ai.calls == 1
        assert info.fallback_reason_code == "ai_timeout"

    async def test_invalid_response_is_classified(self):
        ai = FakeGenerator(
            "ai", fail=True, fail_exception=AIInvalidResponseError("broken json")
        )
        orchestrator, _ = _orchestrator(
            ai_generator=ai,
            deterministic_generator=FakeGenerator("deterministic"),
            gate=GateDecision(allowed=True),
        )

        info = (await orchestrator.generate(_request())).program.generation

        assert info.fallback_reason_code == "ai_invalid_response"

    async def test_unknown_error_is_runtime_failure(self):
        ai = FakeGenerator("ai", fail=True, fail_exception=RuntimeError("boom"))
        orchestrator, _ = _orchestrator(
            ai_generator=ai,
            deterministic_generator=FakeGenerator("deterministic"),
            gate=GateDecision(allowed=True),
        )

        info = (await orchestrator.generate(_request())).program.generation

        assert info.fallback_reason_code == "ai_runtime_failure"

    async def test_rate_limit_is_not_generic_runtime_failure(self):
        """Конкретная причина не деградирует в общий сбой (Phase 1.2-C).

        До объединения классификаций rate limit получал конкретный
        `GenerationErrorCode`, но общий `ai_runtime_failure` в журнале
        администратора.
        """
        ai = FakeGenerator(
            "ai", fail=True, fail_exception=AIRateLimitError("too many requests")
        )
        orchestrator, _ = _orchestrator(
            ai_generator=ai,
            deterministic_generator=FakeGenerator("deterministic"),
            gate=GateDecision(allowed=True),
        )

        info = (await orchestrator.generate(_request())).program.generation

        assert info.fallback_reason_code == "ai_rate_limited"

    async def test_connection_failure_is_not_generic_runtime_failure(self):
        ai = FakeGenerator(
            "ai", fail=True, fail_exception=AIConnectionError("connection reset")
        )
        orchestrator, _ = _orchestrator(
            ai_generator=ai,
            deterministic_generator=FakeGenerator("deterministic"),
            gate=GateDecision(allowed=True),
        )

        info = (await orchestrator.generate(_request())).program.generation

        assert info.fallback_reason_code == "ai_connection_failed"

    async def test_fallback_reason_matches_job_error_code(self):
        """Журнал администратора и код отказа описывают одну причину.

        Это и есть свойство, которое раньше нарушалось: две независимые таблицы
        классификации давали разные ответы на одно исключение.
        """
        ai = FakeGenerator(
            "ai", fail=True, fail_exception=AIRateLimitError("too many requests")
        )
        orchestrator, _ = _orchestrator(
            ai_generator=ai,
            deterministic_generator=FakeGenerator("deterministic"),
            gate=GateDecision(allowed=True),
        )

        result = await orchestrator.generate(_request())

        code = classify_error(AIRateLimitError("x"))
        assert result.fallback_reason_code == fallback_reason_for_code(code).value

    async def test_validation_failure_is_classified(self):
        ai = FakeGenerator("ai", invalid=True)
        orchestrator, _ = _orchestrator(
            ai_generator=ai,
            deterministic_generator=FakeGenerator("deterministic"),
            gate=GateDecision(allowed=True),
        )

        info = (await orchestrator.generate(_request())).program.generation

        assert info.fallback_reason_code == "ai_validation_failed"

    async def test_factory_unavailable_is_generator_not_configured(self):
        orchestrator, _ = _orchestrator(
            deterministic_generator=FakeGenerator("deterministic"),
            ai_factory_error=RuntimeError("ai config missing"),
            gate=GateDecision(allowed=True),
        )

        info = (await orchestrator.generate(_request())).program.generation

        assert info.fallback_reason_code == "generator_not_configured"


class TestFallbackObservability:
    async def test_fallback_is_recorded_for_admin(self):
        events: list[FallbackEvent] = []
        orchestrator, _ = _orchestrator(
            ai_generator=FakeGenerator("ai"),
            deterministic_generator=FakeGenerator("deterministic"),
            gate=GateDecision(
                allowed=False,
                reason=AIFallbackReason.MODEL_UNAVAILABLE,
                detail="Модели задачи: ни одна не доступна",
            ),
            recorder=events,
        )

        await orchestrator.generate(_request())

        assert len(events) == 1
        event = events[0]
        assert event.requested_generator == "ai"
        assert event.actual_generator == "deterministic"
        assert event.reason_code == "model_unavailable"
        # AI не вызывался: это configuration fallback, а не runtime.
        assert event.ai_attempted is False

    async def test_runtime_fallback_marks_ai_as_attempted(self):
        events: list[FallbackEvent] = []
        orchestrator, _ = _orchestrator(
            ai_generator=FakeGenerator("ai", fail=True),
            deterministic_generator=FakeGenerator("deterministic"),
            gate=GateDecision(allowed=True),
            recorder=events,
        )

        await orchestrator.generate(_request())

        assert events[0].ai_attempted is True
        assert events[0].reason_code == "ai_runtime_failure"

    async def test_successful_primary_records_nothing(self):
        events: list[FallbackEvent] = []
        orchestrator, _ = _orchestrator(
            ai_generator=FakeGenerator("ai"),
            deterministic_generator=FakeGenerator("deterministic"),
            gate=GateDecision(allowed=True),
            recorder=events,
        )

        await orchestrator.generate(_request())

        assert events == []

    async def test_recorder_failure_does_not_break_generation(self):
        async def broken_recorder(event: FallbackEvent) -> None:
            raise RuntimeError("audit down")

        orchestrator, _ = _orchestrator(
            ai_generator=FakeGenerator("ai", fail=True),
            deterministic_generator=FakeGenerator("deterministic"),
            gate=GateDecision(allowed=True),
        )
        orchestrator._fallback_recorder = broken_recorder

        result = await orchestrator.generate(_request())

        assert result.fallback_used is True
        assert result.program.generation.fallback_reason_code == "ai_runtime_failure"
