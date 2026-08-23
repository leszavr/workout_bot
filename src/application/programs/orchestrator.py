"""ProgramGenerationOrchestrator: end-to-end генерация программы (Stage 5).

Оркестратор соединяет существующие компоненты (фильтр, safety, генераторы,
валидатор, репозиторий) в конвейер с primary/fallback конфигурацией:

    Profile → Pools → readiness gate → primary generator → validation
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

Readiness gate (Phase 1.1.1). Перед AI-попыткой оркестратор спрашивает
`ai_readiness_gate`, имеет ли смысл вызывать AI. Это разделяет два разных
класса fallback:

- *configuration fallback* — конфигурация заведомо нерабочая, AI-запрос не
  выполняется вообще (не платим за гарантированно бесполезный вызов);
- *runtime fallback* — AI был готов, попытка сделана, но не удалась.

Причина в обоих случаях сохраняется машиночитаемо (`AIFallbackReason`), чтобы
администратор мог ответить на вопрос «почему программа детерминированная,
хотя AI включён?».
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from src.application.programs.filtering import ExerciseFilter
from src.application.programs.generation_jobs import GenerationJobService
from src.application.programs.generator import (
    DeterministicProgramGenerator,
    ProgramGenerator,
)
from src.application.programs.safety import SafetyEngine
from src.application.programs.validator import ProgramValidator
from src.domain.ai.enums import AIFallbackReason
from src.domain.ai.errors import (
    AIConfigurationError,
    AIInvalidResponseError,
    AITimeoutError,
)
from src.domain.enums import GenerationSource, ProgramStatus
from src.domain.generation import GenerationJob, GenerationTrigger
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


@dataclass(frozen=True)
class GateDecision:
    """Ответ readiness gate. Структурно совпадает с RuntimeGateDecision.

    Оркестратор принимает простой протокол, а не конкретный AI-сервис:
    так его можно тестировать без AI-инфраструктуры.
    """

    allowed: bool
    reason: AIFallbackReason | None = None
    detail: str | None = None


@dataclass(frozen=True)
class FallbackEvent:
    """Факт fallback для журнала администратора.

    Персональных данных не содержит: profile_id и содержимое программы сюда
    не попадают.
    """

    requested_generator: str
    actual_generator: str
    reason_code: str
    detail: str
    ai_attempted: bool


@dataclass
class OrchestratorResult:
    program: WorkoutProgram
    candidate_pool: ExerciseCandidatePool
    safe_pool: SafeExercisePool
    fallback_used: bool = False
    reused_existing: bool = False
    # Заполняется, когда генерация шла под persistent job (Phase 1.2-B).
    job: GenerationJob | None = None


@dataclass
class _GeneratorAttempt:
    name: str
    reason: str | None = None
    reason_code: str | None = None
    error_type: str | None = None
    error_message: str | None = None
    # skipped=True — генератор не вызывался (gate запретил или он не настроен).
    skipped: bool = False


def _classify_ai_error(exc: BaseException) -> AIFallbackReason:
    """Относит ошибку AI-вызова к машиночитаемой причине fallback."""
    if isinstance(exc, AITimeoutError):
        return AIFallbackReason.AI_TIMEOUT
    if isinstance(exc, AIInvalidResponseError):
        return AIFallbackReason.AI_INVALID_RESPONSE
    if isinstance(exc, AIConfigurationError):
        return AIFallbackReason.AI_NOT_CONFIGURED
    return AIFallbackReason.AI_RUNTIME_FAILURE


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
        ai_readiness_gate: Callable[[], Awaitable[GateDecision]] | None = None,
        fallback_recorder: Callable[[FallbackEvent], Awaitable[None]] | None = None,
        generation_jobs: GenerationJobService | None = None,
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
        self._ai_gate = ai_readiness_gate
        self._fallback_recorder = fallback_recorder
        self._generation_jobs = generation_jobs

    # --- public API -------------------------------------------------------------

    async def generate(
        self,
        profile_id: str,
        *,
        reuse_existing: bool = False,
        trigger: GenerationTrigger = GenerationTrigger.AUTO_FINALIZATION,
        client_idempotency_key: str | None = None,
    ) -> OrchestratorResult:
        """Основной сценарий pipeline.

        reuse_existing=True (автозапуск после finalize): если у профиля уже
        есть валидная программа — возвращает её без новой генерации,
        повторный finalize не создаёт дубликаты.

        Phase 1.2-B: при настроенном `generation_jobs` генерация выполняется под
        persistent job. Проверка reuse_existing остаётся быстрым путём, но
        защиту от параллельных дубликатов обеспечивает уже не она, а
        PostgreSQL: два одновременных запроса, оба не увидевшие готовой
        программы, создают ровно один job.
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
                return self._reused_result(profile_id, existing)

        if self._generation_jobs is None:
            return await self._generate(profile, profile_id)

        run = await self._generation_jobs.run(
            profile_id=profile_id,
            trigger=trigger,
            requested_generator=self._primary,
            client_idempotency_key=client_idempotency_key,
            operation=lambda: self._generate(profile, profile_id),
        )
        if run.duplicate and run.existing_program is not None:
            result = self._reused_result(profile_id, run.existing_program)
            result.job = run.job
            return result
        if run.result is None:
            # Контракт `run`: либо duplicate с готовой программой, либо result.
            raise ProgramGenerationError("Генерация не вернула результат")
        run.result.job = run.job
        return run.result

    # --- internals --------------------------------------------------------------

    def _reused_result(
        self, profile_id: str, program: WorkoutProgram
    ) -> OrchestratorResult:
        """Результат без новой генерации: пулы не пересчитывались."""
        return OrchestratorResult(
            program=program,
            candidate_pool=ExerciseCandidatePool(
                profile_id=profile_id,
                total_exercises=program.generation.candidate_pool_total or 0,
            ),
            safe_pool=SafeExercisePool(profile_id=profile_id),
            reused_existing=True,
        )

    async def _generate(
        self, profile: FitnessProfile, profile_id: str
    ) -> OrchestratorResult:
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

            # Configuration gate: заведомо нерабочую AI-конфигурацию не
            # вызываем вообще — только фиксируем структурированную причину.
            if name == GENERATOR_AI:
                skip = await self._ai_gate_decision(profile.profile_id)
                if skip is not None:
                    attempts.append(skip)
                    continue

            generator = self._resolve_generator(name)
            if generator is None:
                attempts.append(
                    _GeneratorAttempt(
                        name=name,
                        reason="генератор не настроен",
                        reason_code=AIFallbackReason.GENERATOR_NOT_CONFIGURED.value
                        if name == GENERATOR_AI
                        else None,
                        skipped=True,
                    )
                )
                continue

            try:
                program = await generator.generate(profile, safe_pool)
            except Exception as exc:  # noqa: BLE001 — любая ошибка генератора ведёт к fallback
                message = str(exc)[:400]
                attempts.append(
                    _GeneratorAttempt(
                        name=name,
                        reason=f"ошибка генерации: {message}",
                        reason_code=_classify_ai_error(exc).value
                        if name == GENERATOR_AI
                        else None,
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
                        reason_code=AIFallbackReason.AI_VALIDATION_FAILED.value
                        if name == GENERATOR_AI
                        else None,
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
            if is_fallback:
                await self._record_fallback(program, attempts)
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

    async def _ai_gate_decision(self, profile_id: str) -> _GeneratorAttempt | None:
        """None — AI можно вызывать; иначе готовая запись о пропуске попытки.

        Сбой самого gate не должен ломать генерацию: если состояние
        readiness неизвестно, попытку выполняем, а решение принимает AI.
        """
        if self._ai_gate is None:
            return None
        try:
            decision = await self._ai_gate()
        except Exception as exc:  # noqa: BLE001 — gate не критичен для генерации
            logger.warning(
                "event=generation_readiness_gate_failed",
                extra={"profile_id": profile_id, "error_type": exc.__class__.__name__},
            )
            return None
        if decision.allowed:
            return None

        reason_code = (decision.reason or AIFallbackReason.TASK_NOT_READY).value
        logger.warning(
            "event=generation_ai_skipped_not_ready",
            extra={
                "profile_id": profile_id,
                "fallback_reason_code": reason_code,
            },
        )
        return _GeneratorAttempt(
            name=GENERATOR_AI,
            reason=decision.detail or "AI-конфигурация не готова",
            reason_code=reason_code,
            skipped=True,
        )

    async def _record_fallback(
        self, program: WorkoutProgram, attempts: list[_GeneratorAttempt]
    ) -> None:
        """Пишет fallback в журнал администратора. Сбой журнала не критичен."""
        if self._fallback_recorder is None:
            return
        info = program.generation
        if info.fallback_reason_code is None:
            return
        ai_attempted = any(
            a.name == GENERATOR_AI and not a.skipped for a in attempts
        )
        try:
            await self._fallback_recorder(
                FallbackEvent(
                    requested_generator=self._primary,
                    actual_generator=(
                        info.actual_generator.value if info.actual_generator else ""
                    ),
                    reason_code=info.fallback_reason_code,
                    detail=(info.fallback_reason or "")[:500],
                    ai_attempted=ai_attempted,
                )
            )
        except Exception:  # noqa: BLE001 — журнал не должен ломать генерацию
            logger.exception("Не удалось записать fallback-событие")

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
        # Машиночитаемый код берём из первой неудавшейся попытки: админа
        # интересует именно причина отказа запрошенного генератора.
        info.fallback_reason_code = (
            next((a.reason_code for a in attempts if a.reason_code), None)
            if is_fallback
            else None
        )

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
