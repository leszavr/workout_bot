"""ProgramGenerationOrchestrator: единственная точка генерации программы.

Phase 1.2-C: и Telegram, и Admin API приходят сюда. Альтернативных
generation pipeline'ов в приложении больше нет — `ProgramService` отвечает
только за чтение уже созданных программ.

    GenerationRequest
        ↓
    Profile → GenerationJob (Phase 1.2-B) → Pools → readiness gate
        → primary generator → validation
        → (failure, если fallback разрешён) fallback generator → validation
    → ProgramRepository → GenerationOutcome.

Различие между вызывающими слоями выражается только запросом, а не отдельным
конвейером:

- Telegram (автогенерация после finalize) — стратегия из конфигурации и
  `allow_fallback=True`: пользовательский сценарий не должен падать из-за
  неработоспособного AI;
- Admin API — генератор выбран администратором явно и `allow_fallback=False`:
  подменять выбор молча нельзя, администратор должен увидеть причину отказа.

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
from dataclasses import dataclass
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
from src.domain.enums import GenerationJobStatus, GenerationSource, ProgramStatus
from src.domain.generation import (
    GenerationErrorCode,
    GenerationJob,
    GenerationTrigger,
    classify_error,
    fallback_reason_for_code,
    safe_error_message,
)
from src.domain.pools import ExerciseCandidatePool, SafeExercisePool
from src.domain.profile import FitnessProfile
from src.domain.program import WorkoutProgram
from src.errors import GenerationFailedError
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


@dataclass(frozen=True)
class GenerationRequest:
    """Запрос генерации: единственный вход в pipeline (Phase 1.2-C).

    Telegram и Admin API различаются только этим запросом.

    - `requested_generator=None` — взять стратегию из конфигурации приложения
      (автогенерация);
    - `allow_fallback=False` — генератор выбран вызывающей стороной явно,
      подменять его нельзя (запрос администратора);
    - `reuse_existing=True` — если у профиля уже есть валидная программа,
      вернуть её без новой генерации (повторный finalize).
    """

    profile_id: str
    trigger: GenerationTrigger
    requested_generator: str | None = None
    allow_fallback: bool = True
    reuse_existing: bool = False
    client_idempotency_key: str | None = None


@dataclass(frozen=True)
class GenerationStrategy:
    """Порядок генераторов для одного запроса.

    `fallback=None` означает «подмена генератора запрещена»: так работает явный
    запрос администратора, который выбрал генератор сам.
    """

    primary: str
    fallback: str | None = None

    @property
    def ordered(self) -> tuple[str, ...]:
        if self.fallback is None or self.fallback == self.primary:
            return (self.primary,)
        return (self.primary, self.fallback)


@dataclass
class OrchestratorResult:
    """Единый application-level результат генерации (Phase 1.2-C).

    Вызывающий слой узнаёт из него всё, что ему нужно: программу, состояние
    operational-записи, фактически применённую стратегию и причину fallback.
    Внутренние исключения AI-контура наружу не выходят.
    """

    program: WorkoutProgram
    candidate_pool: ExerciseCandidatePool
    safe_pool: SafeExercisePool
    fallback_used: bool = False
    reused_existing: bool = False
    # Заполняется, когда генерация шла под persistent job (Phase 1.2-B).
    job: GenerationJob | None = None
    requested_generator: str = ""
    actual_generator: str = ""
    fallback_reason_code: str | None = None

    @property
    def status(self) -> GenerationJobStatus:
        """Состояние генерации. Без job-контура успешный возврат = SUCCEEDED."""
        return self.job.status if self.job else GenerationJobStatus.SUCCEEDED


@dataclass
class _GeneratorAttempt:
    name: str
    reason: str | None = None
    reason_code: str | None = None
    error_type: str | None = None
    error_message: str | None = None
    # Стабильный код отказа этой попытки: по нему оркестратор формирует
    # наружный контракт ошибки, а job — свою классификацию.
    error_code: GenerationErrorCode = GenerationErrorCode.GENERATION_FAILED
    # skipped=True — генератор не вызывался (gate запретил или он не настроен).
    skipped: bool = False


def _ai_attempt_from_error(exc: BaseException) -> tuple[GenerationErrorCode, str]:
    """Код отказа и причина fallback для одной неудачной AI-попытки.

    Классификация исключения выполняется один раз (`classify_error`), причина
    для администратора выводится из полученного кода. Второй разбор иерархии
    исключений здесь сознательно отсутствует: раньше он давал общий
    `ai_runtime_failure` там, где код был конкретным.
    """
    code = classify_error(exc)
    return code, fallback_reason_for_code(code).value


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

    async def generate(self, request: GenerationRequest) -> OrchestratorResult:
        """Единственный вход в генерацию программы (Phase 1.2-C).

        `reuse_existing=True` (автозапуск после finalize): если у профиля уже
        есть валидная программа — возвращает её без новой генерации,
        повторный finalize не создаёт дубликаты.

        Phase 1.2-B: при настроенном `generation_jobs` генерация выполняется под
        persistent job. Проверка reuse_existing остаётся быстрым путём, но
        защиту от параллельных дубликатов обеспечивает уже не она, а
        PostgreSQL: два одновременных запроса, оба не увидевшие готовой
        программы, создают ровно один job.

        Транзакционные границы наследуются от job-сервиса: короткая транзакция
        на создание/переход job, затем генерация (включая внешний AI-вызов) вне
        транзакции, затем короткая транзакция на закрытие job.
        """
        profile_id = request.profile_id
        strategy = self._resolve_strategy(request)

        profile = await self._profiles.get(profile_id)
        if profile is None:
            raise GenerationFailedError(
                f"Профиль {profile_id} не найден",
                generation_error_code=GenerationErrorCode.PROFILE_NOT_FOUND.value,
            )

        if request.reuse_existing:
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
            return await self._generate(profile, profile_id, strategy)

        run = await self._generation_jobs.run(
            profile_id=profile_id,
            trigger=request.trigger,
            requested_generator=strategy.primary,
            client_idempotency_key=request.client_idempotency_key,
            operation=lambda: self._generate(profile, profile_id, strategy),
        )
        if run.duplicate and run.existing_program is not None:
            result = self._reused_result(profile_id, run.existing_program)
            result.job = run.job
            return result
        if run.result is None:
            # Контракт `run`: либо duplicate с готовой программой, либо result.
            raise GenerationFailedError(
                "Генерация не вернула результат",
                generation_error_code=GenerationErrorCode.GENERATION_FAILED.value,
            )
        run.result.job = run.job
        return run.result

    # --- internals --------------------------------------------------------------

    def _resolve_strategy(self, request: GenerationRequest) -> GenerationStrategy:
        """Стратегия одного запроса.

        Явно выбранный генератор не подменяется: `allow_fallback=False` даёт
        стратегию из одного генератора, поэтому администратор видит настоящую
        причину отказа, а не молча получает другую программу.

        Недопустимый генератор — отказ доменного контракта, а не `ValueError`:
        оркестратор является application-level boundary и обязан отвечать
        одинаково любому вызывающему слою, а не только тому, перед которым
        стоит pydantic-валидация HTTP-запроса.
        """
        primary = request.requested_generator or self._primary
        if primary not in VALID_GENERATORS:
            raise GenerationFailedError(
                f"Недопустимый генератор: {primary}",
                generation_error_code=GenerationErrorCode.VALIDATION_FAILED.value,
            )
        if not request.allow_fallback:
            return GenerationStrategy(primary=primary)
        return GenerationStrategy(primary=primary, fallback=self._fallback)

    def _reused_result(
        self, profile_id: str, program: WorkoutProgram
    ) -> OrchestratorResult:
        """Результат без новой генерации: пулы не пересчитывались."""
        info = program.generation
        return OrchestratorResult(
            program=program,
            candidate_pool=ExerciseCandidatePool(
                profile_id=profile_id,
                total_exercises=info.candidate_pool_total or 0,
            ),
            safe_pool=SafeExercisePool(profile_id=profile_id),
            reused_existing=True,
            requested_generator=(
                info.requested_generator.value if info.requested_generator else ""
            ),
            actual_generator=(
                info.actual_generator.value
                if info.actual_generator
                else info.source.value
            ),
            fallback_used=info.fallback_used,
            fallback_reason_code=info.fallback_reason_code,
        )

    async def _generate(
        self,
        profile: FitnessProfile,
        profile_id: str,
        strategy: GenerationStrategy,
    ) -> OrchestratorResult:
        catalog = await self._exercises.list(limit=CATALOG_FETCH_LIMIT)
        catalog_ids = {e.external_id for e in catalog}
        candidate_pool = await self._filter.select_candidates(profile, catalog)
        safe_pool = self._safety.apply(profile, candidate_pool.included)

        program, fallback_used = await self._run_generators(
            profile, safe_pool, catalog_ids, strategy
        )

        program.generation.candidate_pool_total = candidate_pool.total_exercises
        if program.generation.safe_pool_size is None:
            program.generation.safe_pool_size = len(safe_pool.allowed)

        program.status = ProgramStatus.VALIDATED
        await self._persist(program, profile_id)

        info = program.generation
        return OrchestratorResult(
            program=program,
            candidate_pool=candidate_pool,
            safe_pool=safe_pool,
            fallback_used=fallback_used,
            requested_generator=strategy.primary,
            actual_generator=(
                info.actual_generator.value if info.actual_generator else ""
            ),
            fallback_reason_code=info.fallback_reason_code,
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
        strategy: GenerationStrategy,
    ) -> tuple[WorkoutProgram, bool]:
        ordered = strategy.ordered

        attempts: list[_GeneratorAttempt] = []
        logger.info(
            "event=generation_started",
            extra={
                "profile_id": profile.profile_id,
                "primary_generator": strategy.primary,
                "fallback_generator": strategy.fallback or "none",
            },
        )

        for index, name in enumerate(ordered):
            is_fallback = index > 0
            if is_fallback:
                reason = "; ".join(
                    f"{a.name}: {a.error_type or 'unavailable'} ({a.reason or 'n/a'})"
                    for a in attempts
                ) or f"{strategy.primary} недоступен"
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
                        error_code=GenerationErrorCode.AI_NOT_CONFIGURED
                        if name == GENERATOR_AI
                        else GenerationErrorCode.GENERATION_FAILED,
                        skipped=True,
                    )
                )
                continue

            try:
                program = await generator.generate(profile, safe_pool)
            except Exception as exc:  # noqa: BLE001 — любая ошибка генератора ведёт к fallback
                message = safe_error_message(exc)[:400]
                error_code, fallback_reason = _ai_attempt_from_error(exc)
                attempts.append(
                    _GeneratorAttempt(
                        name=name,
                        reason=f"ошибка генерации: {message}",
                        reason_code=fallback_reason if name == GENERATOR_AI else None,
                        error_type=exc.__class__.__name__,
                        error_message=message,
                        error_code=error_code,
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

            result = self._validator.validate(
                program, safe_pool, profile, catalog_ids, safe_pool.allowed_sources()
            )
            if not result.valid:
                message = safe_error_message(
                    "; ".join(f"{i.code}: {i.message}" for i in result.issues)
                )[:400]
                attempts.append(
                    _GeneratorAttempt(
                        name=name,
                        reason=f"validation failed: {message}",
                        reason_code=fallback_reason_for_code(
                            GenerationErrorCode.VALIDATION_FAILED
                        ).value
                        if name == GENERATOR_AI
                        else None,
                        error_type="ValidationError",
                        error_message=message,
                        error_code=GenerationErrorCode.VALIDATION_FAILED,
                    )
                )
                logger.warning(
                    "event=generation_validation_failed",
                    extra={"profile_id": profile.profile_id, "generator": name},
                )
                continue

            self._fill_generation_metadata(
                program, name, is_fallback, reason, attempts, strategy
            )

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
                await self._record_fallback(program, attempts, strategy)
            return program, is_fallback

        last = attempts[-1]
        logger.error(
            "event=generation_failed",
            extra={
                "profile_id": profile.profile_id,
                "primary_generator": strategy.primary,
                "fallback_generator": strategy.fallback or "none",
                "last_generator": last.name,
                "error_type": last.error_type or "unavailable",
                "error_code": last.error_code.value,
            },
        )
        # Наружу уходит стабильный код отказа, а не тип внутреннего исключения:
        # HTTP-слой и Telegram решают по коду, не разбирая ошибки AI Gateway.
        raise GenerationFailedError(
            f"Не удалось сгенерировать программу "
            f"(primary={strategy.primary}, fallback={strategy.fallback or 'нет'}): "
            f"{last.reason or 'нет доступного генератора'}",
            generation_error_code=last.error_code.value,
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
            # Detail приходит из чек-листа readiness: он содержит имена сервисов
            # и подключений, поэтому проходит ту же санитизацию, что и остальные
            # тексты, попадающие наружу и в operational-запись.
            reason=safe_error_message(decision.detail or "AI-конфигурация не готова"),
            reason_code=reason_code,
            # Gate отклонил попытку по состоянию конфигурации, а не по сбою
            # вызова: код отказа должен отражать именно это.
            error_code=GenerationErrorCode.AI_NOT_CONFIGURED,
            skipped=True,
        )

    async def _record_fallback(
        self,
        program: WorkoutProgram,
        attempts: list[_GeneratorAttempt],
        strategy: GenerationStrategy,
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
                    requested_generator=strategy.primary,
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
        strategy: GenerationStrategy,
    ) -> None:
        info = program.generation
        info.requested_generator = GenerationSource(strategy.primary)
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
