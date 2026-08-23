"""ProgramService: оркестрация pipeline генерации программ.

API вызывает только этот сервис; сервис собирает зависимости (фильтр,
safety, генератор, валидатор, репозитории) через конструктор — никакой
бизнес-логики в FastAPI routes.

Pipeline:
    Profile → ExerciseFilter → CandidatePool → SafetyEngine → SafeExercisePool
    → ProgramGenerator → ProgramValidator → ProgramRepository (versioned).

Phase 1.2-B: генерация выполняется под persistent `GenerationJob`, если он
передан. Тогда повторный запрос той же логической генерации не создаёт вторую
программу, а состояние операции сохраняется в PostgreSQL.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from src.application.programs.filtering import ExerciseFilter
from src.application.programs.generation_jobs import GenerationJobService
from src.application.programs.generator import ProgramGenerator
from src.application.programs.safety import SafetyEngine
from src.application.programs.validator import ProgramValidator
from src.domain.enums import GenerationSource, ProgramStatus
from src.domain.generation import GenerationJob, GenerationTrigger
from src.domain.pools import ExerciseCandidatePool, SafeExercisePool
from src.domain.profile import FitnessProfile
from src.domain.program import WorkoutProgram
from src.errors import ProgramGenerationError, ProgramValidationError
from src.infrastructure.persistence.postgres.exercise_repository import (
    ExerciseRepository,
)
from src.infrastructure.persistence.program_repository import ProgramRepository
from src.infrastructure.persistence.profile_repository import ProfileRepository

# Максимум упражнений, загружаемых из каталога для pipeline.
CATALOG_FETCH_LIMIT = 5000


@dataclass
class GenerationResult:
    program: WorkoutProgram
    candidate_pool: ExerciseCandidatePool
    safe_pool: SafeExercisePool
    # Заполняется, когда генерация шла под persistent job (Phase 1.2-B).
    job: GenerationJob | None = None
    # True — программа получена от предыдущей успешной логической генерации,
    # новая генерация не выполнялась. Пулы тогда пусты: их не пересчитывали.
    reused_existing: bool = False


class ProgramService:
    def __init__(
        self,
        *,
        profile_repository: ProfileRepository,
        exercise_repository: ExerciseRepository,
        program_repository: ProgramRepository,
        generator: ProgramGenerator,
        exercise_filter: ExerciseFilter | None = None,
        safety_engine: SafetyEngine | None = None,
        validator: ProgramValidator | None = None,
        generation_jobs: GenerationJobService | None = None,
        requested_generator: str = GenerationSource.DETERMINISTIC.value,
    ) -> None:
        self._profiles = profile_repository
        self._exercises = exercise_repository
        self._programs = program_repository
        self._generator = generator
        self._filter = exercise_filter or ExerciseFilter()
        self._safety = safety_engine or SafetyEngine()
        self._validator = validator or ProgramValidator()
        self._generation_jobs = generation_jobs
        self._requested_generator = requested_generator

    async def build_pools(
        self, profile: FitnessProfile
    ) -> tuple[ExerciseCandidatePool, SafeExercisePool, set[str]]:
        """Фильтрация + safety без генерации (для отладки/админки).

        Возвращает также полный набор ID каталога (для валидатора).
        """
        catalog = await self._exercises.list(limit=CATALOG_FETCH_LIMIT)
        catalog_ids = {e.external_id for e in catalog}
        candidate_pool = await self._filter.select_candidates(profile, catalog)
        safe_pool = self._safety.apply(profile, candidate_pool.included)
        return candidate_pool, safe_pool, catalog_ids

    async def generate(
        self,
        profile_id: str,
        *,
        trigger: GenerationTrigger = GenerationTrigger.ADMIN_REQUEST,
        client_idempotency_key: str | None = None,
    ) -> GenerationResult:
        """Полный pipeline: профиль → пулы → генерация → валидация → сохранение версии.

        Профиль читается до создания job: у несуществующего профиля не должно
        оставаться operational-записи о генерации.
        """
        profile = await self._profiles.get(profile_id)
        if profile is None:
            raise ProgramGenerationError(f"Профиль {profile_id} не найден")

        if self._generation_jobs is None:
            return await self._generate(profile)

        run = await self._generation_jobs.run(
            profile_id=profile_id,
            trigger=trigger,
            requested_generator=self._requested_generator,
            client_idempotency_key=client_idempotency_key,
            operation=lambda: self._generate(profile),
        )
        if run.duplicate and run.existing_program is not None:
            program = run.existing_program
            return GenerationResult(
                program=program,
                candidate_pool=ExerciseCandidatePool(
                    profile_id=profile_id,
                    total_exercises=program.generation.candidate_pool_total or 0,
                ),
                safe_pool=SafeExercisePool(profile_id=profile_id),
                job=run.job,
                reused_existing=True,
            )
        if run.result is None:
            # Контракт `run`: либо duplicate с готовой программой, либо result.
            raise ProgramGenerationError("Генерация не вернула результат")
        run.result.job = run.job
        return run.result

    async def _generate(self, profile: FitnessProfile) -> GenerationResult:
        profile_id = profile.profile_id or ""
        candidate_pool, safe_pool, catalog_ids = await self.build_pools(profile)

        program = await self._generator.generate(profile, safe_pool)
        program.generation.candidate_pool_total = candidate_pool.total_exercises

        result = self._validator.validate(program, safe_pool, profile, catalog_ids)
        if not result.valid:
            program.status = ProgramStatus.FAILED
            raise ProgramValidationError(
                "Программа не прошла валидацию: "
                + "; ".join(f"{i.code}: {i.message}" for i in result.issues)
            )

        program.status = ProgramStatus.VALIDATED
        program.program_id = uuid.uuid4().hex
        program.version = await self._programs.next_version(profile_id)
        program.touch()
        await self._programs.save(program)
        return GenerationResult(
            program=program, candidate_pool=candidate_pool, safe_pool=safe_pool
        )

    async def get(self, program_id: str, version: int | None = None) -> WorkoutProgram | None:
        return await self._programs.get(program_id, version)

    async def list_versions(self, program_id: str) -> list[WorkoutProgram]:
        return await self._programs.list_versions(program_id)

    async def list_for_profile(self, profile_id: str) -> list[WorkoutProgram]:
        return await self._programs.list_for_profile(profile_id)

    async def list_all(self, limit: int = 50, offset: int = 0) -> tuple[int, list[WorkoutProgram]]:
        return await self._programs.list_all(limit=limit, offset=offset)
