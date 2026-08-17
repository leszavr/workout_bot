"""ProgramService: оркестрация pipeline генерации программ.

API вызывает только этот сервис; сервис собирает зависимости (фильтр,
safety, генератор, валидатор, репозитории) через конструктор — никакой
бизнес-логики в FastAPI routes.

Pipeline:
    Profile → ExerciseFilter → CandidatePool → SafetyEngine → SafeExercisePool
    → ProgramGenerator → ProgramValidator → ProgramRepository (versioned).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from src.application.programs.filtering import ExerciseFilter
from src.application.programs.generator import ProgramGenerator
from src.application.programs.safety import SafetyEngine
from src.application.programs.validator import ProgramValidator
from src.domain.enums import ProgramStatus
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
    ) -> None:
        self._profiles = profile_repository
        self._exercises = exercise_repository
        self._programs = program_repository
        self._generator = generator
        self._filter = exercise_filter or ExerciseFilter()
        self._safety = safety_engine or SafetyEngine()
        self._validator = validator or ProgramValidator()

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

    async def generate(self, profile_id: str) -> GenerationResult:
        """Полный pipeline: профиль → пулы → генерация → валидация → сохранение версии."""
        profile = await self._profiles.get(profile_id)
        if profile is None:
            raise ProgramGenerationError(f"Профиль {profile_id} не найден")

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
