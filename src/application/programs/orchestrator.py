"""ProgramGenerationOrchestrator: end-to-end генерация программы (Stage 5).

Оркестратор соединяет существующие компоненты (фильтр, safety, генераторы,
валидатор, репозиторий) в конвейер с primary/fallback конфигурацией:

    Profile → Pools → primary generator → validation
        → (failure) fallback generator → validation
        → (failure) ProgramGenerationError
    → ProgramRepository → результат.

Правила:
- строго один fallback: primary → fallback → final failure, никаких циклов;
- конфигурация симметрична: primary/fallback могут быть в любом порядке;
- метаданные GenerationInfo фиксируют запрошенный и фактический генератор;
- повторная генерация после успешной (idempotent pipeline) возвращает
  существующую валидную программу — новая версия создаётся только явным
  запросом (админ-UI) или после failure.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

from src.application.programs.filtering import ExerciseFilter
from src.application.programs.generator import (
    DeterministicProgramGenerator,
    ProgramGenerator,
)
from src.application.programs.safety import SafetyEngine
from src.application.programs.validator import ProgramValidator
from src.domain.enums import GenerationSource, ProgramStatus
from src.domain.pools import ExerciseCandidatePool, SafeExercisePool
from src.domain.profile import FitnessProfile
from src.domain.program import WorkoutProgram
from src.errors import ProgramGenerationError
from src.infrastructure.persistence.postgres.exercise_repository import (
    ExerciseRepository,
)
from src.infrastructure.persistence.program_repository import ProgramRepository
from src.infrastructure.persistence.profile_repository import ProfileRepository

logger = logging.getLogger(__name__)

CATALOG_FETCH_LIMIT = 5000

GENERATOR_AI = GenerationSource.AI.value
GENERATOR_DETERMINISTIC = GenerationSource.DETERMINISTIC.value
VALID_GENERATORS = {GENERATOR_AI, GENERATOR_DETERMINISTIC}


@dataclass
class OrchestratorResult:
    program: WorkoutProgram
    candidate_pool: ExerciseCandidatePool
    safe_pool: SafeExercisePool
    fallback_used: bool = False
    reused_existing: bool = False


@dataclass
class _GeneratorAttempt:
    name: str
    reason: str | None = None
    error_type: str | None = None
    error_message: str | None = None


class ProgramGenerationOrchestrator:
    def __init__(
        self,
        *,
        profile_repository: ProfileRepository,
        exercise_repository: ExerciseRepository,
        program_repository: ProgramRepository,
        primary_generator: str = GENERATOR_AI,
        fallback_generator: str = GENERATOR_DETERMINISTIC,
        ai_generator_factory: Callable[[], ProgramGenerator] | None = None,
        deterministic_generator: DeterministicProgramGenerator | None = None,
        exercise_filter: ExerciseFilter | None = None,
        safety_engine: SafetyEngine | None = None,
        validator: ProgramValidator | None = None,
    ) -> None:
        if primary_generator not in VALID_GENERATORS:
            raise ValueError(f"Недопустимый primary_generator: {primary_generator}")
        if fallback_generator not in VALID_GENERATORS:
            raise ValueError(f"Недопустимый fallback_generator: {fallback_generator}")

        self._profiles = profile_repository
        self._exercises = exercise_repository
        self._programs = program_repository
        self._primary = primary_generator
        self._fallback = fallback_generator
        self._ai_factory = ai_generator_factory
        self._deterministic = deterministic_generator or DeterministicProgramGenerator()
        self._filter = exercise_filter or ExerciseFilter()
        self._safety = safety_engine or SafetyEngine()
        self._validator = validator or ProgramValidator()

    # --- public API -------------------------------------------------------------

    async def generate(
        self, profile_id: str, *, reuse_existing: bool = False
    ) -> OrchestratorResult:
        """Основной сценарий pipeline.

        reuse_existing=True (автозапуск после finalize): если у профиля уже
        есть валидная программа — возвращает её без новой генерации,
        повторный finalize не создаёт дубликаты.
        """
        profile = await self._profiles.get(profile_id)
        if profile is None:
            raise ProgramGenerationError(f"Профиль {profile_id} не найден")

        if reuse_existing:
            existing = await self._latest_valid_program(profile_id)
            if existing is not None:
                logger.info(
                    "event=generation_reused",
                    extra={
                        "profile_id": profile_id,
                        "program_id": existing.program_id,
                        "version": existing.version,
                    },
                )
                empty_pool = ExerciseCandidatePool(
                    profile_id=profile_id,
                    total_exercises=existing.generation.candidate_pool_total or 0,
                )
                empty_safe = SafeExercisePool(profile_id=profile_id)
                return OrchestratorResult(
                    program=existing,
                    candidate_pool=empty_pool,
                    safe_pool=empty_safe,
                    reused_existing=True,
                )

        catalog = await self._exercises.list(limit=CATALOG_FETCH_LIMIT)
        catalog_ids = {e.external_id for e in catalog}
        candidate_pool = await self._filter.select_candidates(profile, catalog)
        safe_pool = self._safety.apply(profile, candidate_pool.included)

        program, fallback_used = await self._run_generators(
            profile, safe_pool, catalog_ids
        )

        program.generation.candidate_pool_total = candidate_pool.total_exercises
        if program.generation.safe_pool_size is None:
            program.generation.safe_pool_size = len(safe_pool.allowed)

        program.status = ProgramStatus.VALIDATED
        await self._persist(program, profile_id)

        return OrchestratorResult(
            program=program,
            candidate_pool=candidate_pool,
            safe_pool=safe_pool,
            fallback_used=fallback_used,
        )

    # --- internals --------------------------------------------------------------

    def _resolve_generator(self, name: str) -> ProgramGenerator | None:
        if name == GENERATOR_DETERMINISTIC:
            return self._deterministic
        if name == GENERATOR_AI and self._ai_factory is not None:
            try:
                return self._ai_factory()
            except Exception as exc:  # noqa: BLE001 — фабрика недоступна → fallback
                logger.warning(
                    "event=generation_factory_unavailable",
                    extra={"generator": name, "error_type": exc.__class__.__name__},
                )
                return None
        return None

    async def _run_generators(
        self,
        profile: FitnessProfile,
        safe_pool: SafeExercisePool,
        catalog_ids: set[str],
    ) -> tuple[WorkoutProgram, bool]:
        ordered = [self._primary]
        if self._fallback != self._primary:
            ordered.append(self._fallback)

        attempts: list[_GeneratorAttempt] = []
        logger.info(
            "event=generation_started",
            extra={
                "profile_id": profile.profile_id,
                "primary_generator": self._primary,
                "fallback_generator": self._fallback,
            },
        )

        for index, name in enumerate(ordered):
            is_fallback = index > 0
            if is_fallback:
                reason = "; ".join(
                    f"{a.name}: {a.error_type or 'unavailable'} ({a.reason or 'n/a'})"
                    for a in attempts
                ) or f"{self._primary} недоступен"
                logger.warning(
                    "event=generation_fallback_started",
                    extra={
                        "profile_id": profile.profile_id,
                        "fallback_generator": name,
                        "fallback_reason": reason,
                    },
                )
            else:
                reason = None

            generator = self._resolve_generator(name)
            if generator is None:
                attempts.append(_GeneratorAttempt(name=name, reason="генератор не настроен"))
                continue

            try:
                program = await generator.generate(profile, safe_pool)
            except Exception as exc:  # noqa: BLE001 — любая ошибка генератора ведёт к fallback
                message = str(exc)[:400]
                attempts.append(
                    _GeneratorAttempt(
                        name=name,
                        reason=f"ошибка генерации: {message}",
                        error_type=exc.__class__.__name__,
                        error_message=message,
                    )
                )
                logger.warning(
                    "event=generation_attempt_failed",
                    extra={
                        "profile_id": profile.profile_id,
                        "generator": name,
                        "error_type": exc.__class__.__name__,
                    },
                )
                continue

            result = self._validator.validate(program, safe_pool, profile, catalog_ids)
            if not result.valid:
                message = "; ".join(f"{i.code}: {i.message}" for i in result.issues)[:400]
                attempts.append(
                    _GeneratorAttempt(
                        name=name,
                        reason=f"validation failed: {message}",
                        error_type="ValidationError",
                        error_message=message,
                    )
                )
                logger.warning(
                    "event=generation_validation_failed",
                    extra={"profile_id": profile.profile_id, "generator": name},
                )
                continue

            self._fill_generation_metadata(program, name, is_fallback, reason, attempts)

            logger.info(
                "event=generation_primary_success"
                if not is_fallback
                else "event=generation_fallback_success",
                extra={
                    "profile_id": profile.profile_id,
                    "generator": name,
                    "fallback_used": is_fallback,
                },
            )
            return program, is_fallback

        last = attempts[-1]
        logger.error(
            "event=generation_failed",
            extra={
                "profile_id": profile.profile_id,
                "primary_generator": self._primary,
                "fallback_generator": self._fallback,
                "last_generator": last.name,
                "error_type": last.error_type or "unavailable",
            },
        )
        raise ProgramGenerationError(
            f"Не удалось сгенерировать программу "
            f"(primary={self._primary}, fallback={self._fallback}): {last.reason or 'нет доступного генератора'}"
        )

    def _fill_generation_metadata(
        self,
        program: WorkoutProgram,
        generator_name: str,
        is_fallback: bool,
        reason: str | None,
        attempts: list[_GeneratorAttempt],
    ) -> None:
        info = program.generation
        info.requested_generator = GenerationSource(self._primary)
        info.actual_generator = GenerationSource(generator_name)
        info.fallback_used = is_fallback
        fallback_reason = reason
        if not fallback_reason and attempts:
            fallback_reason = "; ".join(
                f"{a.name}: {a.reason}" for a in attempts
            )[:500]
        info.fallback_reason = fallback_reason if is_fallback else None

    async def _persist(self, program: WorkoutProgram, profile_id: str) -> None:
        import uuid

        if not program.program_id:
            program.program_id = uuid.uuid4().hex
        program.profile_id = profile_id
        program.version = await self._programs.next_version(profile_id)
        program.touch()
        await self._programs.save(program)
        logger.info(
            "event=program_persisted",
            extra={
                "profile_id": profile_id,
                "program_id": program.program_id,
                "version": program.version,
            },
        )

    async def _latest_valid_program(self, profile_id: str) -> WorkoutProgram | None:
        programs = await self._programs.list_for_profile(profile_id)
        if not programs:
            return None
        latest = max(programs, key=lambda p: p.version)
        if latest.status in (ProgramStatus.VALIDATED, ProgramStatus.ACTIVE, ProgramStatus.GENERATED):
            return latest
        return None
