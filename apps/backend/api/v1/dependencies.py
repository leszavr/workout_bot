"""Фабрика зависимостей backend.

Routes не создают бизнес-логику напрямую — только запрашивают сервис.
Генерация собирается в единственном месте: `build_generation_orchestrator`.
"""
from __future__ import annotations

import httpx

from src.application.ai.program_generator import (
    AIProgramGenerator,
    ModelAttempt,
    PromptLoader,
)
from src.application.media.service import ExerciseMediaService
from src.application.programs.filtering import ExerciseFilter
from src.application.programs.generation_context import current_generation_job_id
from src.application.programs.generation_jobs import GenerationJobService
from src.application.programs.generator import DeterministicProgramGenerator
from src.application.programs.html_service import ProgramHtmlService
from src.application.programs.orchestrator import (
    FallbackEvent,
    GateDecision,
    ProgramGenerationOrchestrator,
)
from src.application.programs.safety import SafetyEngine
from src.application.programs.service import ProgramService
from src.application.programs.validator import ProgramValidator
from src.application.profiles.admin_service import ProfileAdminService
from src.domain.ai.enums import AITaskType
from src.domain.retry import RetryPolicy
from src.infrastructure.config import (
    EXERCISE_MEDIA_MAX_PER_EXERCISE,
    MEDIA_PUBLIC_BASE_URL,
    PROGRAM_FALLBACK_GENERATOR,
    PROGRAM_HTML_MEDIA_MODE,
    PROGRAM_PRIMARY_GENERATOR,
    WORKER_MAX_ATTEMPTS,
    WORKER_RETRY_INITIAL_DELAY_SECONDS,
    WORKER_RETRY_MAX_DELAY_SECONDS,
    WORKER_RETRY_MULTIPLIER,
)
from src.infrastructure.media.object_storage import create_object_storage
from src.infrastructure.persistence.postgres.db import get_session_factory
from src.infrastructure.persistence.postgres.delivery_repository import (
    ProgramDeliveryRepository,
)
from src.infrastructure.persistence.postgres.exercise_media_repository import (
    ExerciseMediaRepository,
)
from src.infrastructure.persistence.postgres.exercise_repository import (
    ExerciseRepository,
)
from src.infrastructure.persistence.postgres.generation_job_repository import (
    GenerationJobRepository,
)
from src.infrastructure.persistence.postgres.profile_repository import (
    PostgresProfileRepository,
)
from src.infrastructure.persistence.postgres.program_repository import (
    PostgresProgramRepository,
)


def build_retry_policy() -> RetryPolicy:
    """Единая политика повторов для генерации и доставки (Phase 1.2-D).

    Собирается из конфигурации в одном месте: две независимые политики
    разошлись бы при первом изменении, а объяснить администратору «почему
    доставка повторяется иначе, чем генерация» было бы нечем.
    """
    return RetryPolicy(
        max_attempts=WORKER_MAX_ATTEMPTS,
        initial_delay_seconds=WORKER_RETRY_INITIAL_DELAY_SECONDS,
        multiplier=WORKER_RETRY_MULTIPLIER,
        max_delay_seconds=WORKER_RETRY_MAX_DELAY_SECONDS,
    )


def build_generation_job_service() -> GenerationJobService:
    """Persistent состояние генерации (Phase 1.2-B).

    Идемпотентность и переходы состояния обеспечивает PostgreSQL, поэтому
    сервис получает те же session factory и репозиторий программ, что и
    остальной контур генерации.

    Политика повторов передаётся здесь, а не в worker'е: `next_attempt_at`
    выставляется в момент отказа, а отказать генерация может в любом процессе —
    в Telegram-пайплайне, в Admin API и в самом worker'е.
    """
    session_factory = get_session_factory()
    return GenerationJobService(
        repository=GenerationJobRepository(session_factory),
        program_repository=PostgresProgramRepository(session_factory),
        retry_policy=build_retry_policy(),
    )


def build_program_service() -> ProgramService:
    """Чтение сохранённых программ. Генерации здесь нет (Phase 1.2-C)."""
    return ProgramService(
        program_repository=PostgresProgramRepository(get_session_factory())
    )


def build_delivery_repository() -> ProgramDeliveryRepository:
    """Записи доставки программ пользователю.

    Нужны админке в двух местах: маркер «программа отправлена» в списке анкет и
    очистка записей при удалении анкеты или программы.
    """
    return ProgramDeliveryRepository(get_session_factory())


def build_profile_admin_service() -> ProfileAdminService:
    """Удаление анкет и программ.

    Целостность обеспечивает сервис, а не база: внешних ключей на
    `workout_programs.profile_id` и `program_deliveries` в схеме нет.
    """
    session_factory = get_session_factory()
    return ProfileAdminService(
        profiles=PostgresProfileRepository(session_factory),
        programs=PostgresProgramRepository(session_factory),
        deliveries=ProgramDeliveryRepository(session_factory),
    )


def build_generation_orchestrator() -> ProgramGenerationOrchestrator:
    """Единственная точка сборки generation pipeline (Phase 1.2-C).

    Стратегия по умолчанию берётся из конфигурации приложения; вызывающий слой
    может переопределить генератор в самом `GenerationRequest`, поэтому
    отдельных фабрик под Telegram и Admin API нет.

    Оркестратор получает readiness gate и журнал fallback: решение «вызывать
    ли AI» принимается по фактическому состоянию конфигурации, а причина
    отказа попадает в журнал администратора.
    """
    session_factory = get_session_factory()
    return ProgramGenerationOrchestrator(
        profile_repository=PostgresProfileRepository(session_factory),
        exercise_repository=ExerciseRepository(session_factory),
        program_repository=PostgresProgramRepository(session_factory),
        primary_generator=PROGRAM_PRIMARY_GENERATOR,
        fallback_generator=PROGRAM_FALLBACK_GENERATOR,
        ai_generator_factory=build_ai_program_generator,
        deterministic_generator=DeterministicProgramGenerator(),
        exercise_filter=ExerciseFilter(),
        safety_engine=SafetyEngine(),
        validator=ProgramValidator(),
        ai_readiness_gate=_workout_generation_gate,
        fallback_recorder=_record_generation_fallback,
        generation_jobs=build_generation_job_service(),
    )


async def _workout_generation_gate() -> GateDecision:
    """Актуальное решение readiness для задачи генерации программы.

    Компоненты собираются на каждый вызов намеренно: решение должно
    отражать конфигурацию на момент генерации, а не на момент старта
    приложения.
    """
    from apps.backend.api.v1.ai_dependencies import build_ai_components

    decision = await build_ai_components().readiness.runtime_gate(
        AITaskType.WORKOUT_GENERATION
    )
    return GateDecision(
        allowed=decision.allowed,
        reason=decision.reason,
        detail=decision.detail,
    )


async def _record_generation_fallback(event: FallbackEvent) -> None:
    """Кладёт fallback в существующий журнал событий AI-контура."""
    from apps.backend.api.v1.ai_dependencies import build_ai_components

    await build_ai_components().admin.record_generation_fallback(
        requested_generator=event.requested_generator,
        actual_generator=event.actual_generator,
        reason_code=event.reason_code,
        detail=event.detail,
        ai_attempted=event.ai_attempted,
        job_id=current_generation_job_id(),
    )


def build_exercise_repository() -> ExerciseRepository:
    """Каталог упражнений для чтения из API.

    Отдельная фабрика нужна, чтобы роут не собирал репозиторий сам и не
    дублировал session factory: поиск и счётчики каталога живут в репозитории.
    """
    return ExerciseRepository(get_session_factory())


def build_exercise_media_service() -> ExerciseMediaService:
    session_factory = get_session_factory()
    return ExerciseMediaService(
        repository=ExerciseMediaRepository(session_factory),
        storage=create_object_storage(),
    )


def build_program_html_service() -> ProgramHtmlService:
    session_factory = get_session_factory()
    return ProgramHtmlService(
        exercise_repository=ExerciseRepository(session_factory),
        media_service=build_exercise_media_service(),
        media_mode=PROGRAM_HTML_MEDIA_MODE,
        public_base_url=MEDIA_PUBLIC_BASE_URL,
        max_media_per_exercise=EXERCISE_MEDIA_MAX_PER_EXERCISE,
    )


def build_ai_program_generator(http_client: httpx.AsyncClient | None = None) -> AIProgramGenerator:
    """Собирает AIProgramGenerator с AIGateway."""
    from apps.backend.api.v1.ai_dependencies import build_ai_components
    from src.infrastructure.persistence.postgres.ai_repository import (
        AITaskConfigRepository,
        PromptTemplateRepository,
    )

    components = build_ai_components(http_client)
    session_factory = get_session_factory()

    return AIProgramGenerator(
        gateway=components.gateway,
        # Версию инструкции загрузчик берёт из настроек задачи: другого
        # источника промптов нет.
        prompt_loader=PromptLoader(
            PromptTemplateRepository(session_factory),
            AITaskConfigRepository(session_factory),
        ),
        validator=ProgramValidator(),
        attempt_recorder=_record_model_attempts,
        # Проба отсеивает недоступную модель до полного запроса: наблюдалось, что
        # две сломанные модели исчерпывали бюджет генерации за 400 секунд, и до
        # рабочих в конце цепочки дело не доходило.
        probe_service=components.probe,
    )


async def _record_model_attempts(
    attempts: list[ModelAttempt], prompt_version: int | None
) -> None:
    """Кладёт историю попыток моделей в журнал AI-контура.

    Без неё администратор видит только «программа собрана без ИИ» и не может
    отличить «резервные модели тоже не справились» от «до них дело не дошло».

    Версия инструкции и ссылка на операцию генерации нужны аналитике: по ним
    попытки сводятся к конкретной генерации и к конкретной формулировке
    инструкции.
    """
    from apps.backend.api.v1.ai_dependencies import build_ai_components

    await build_ai_components().admin.record_model_attempts(
        AIProgramGenerator.attempts_metadata(attempts),
        task_type=AITaskType.WORKOUT_GENERATION,
        job_id=current_generation_job_id(),
        prompt_version=prompt_version,
    )

