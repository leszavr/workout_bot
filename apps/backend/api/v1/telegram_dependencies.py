"""Сборка контура Telegram Gateway на стороне Backend.

Отдельный модуль, а не расширение `dependencies.py`: там собирается генерация, и
смешивать её с диалогом анкеты значит увеличивать поверхность файла, который
архитектурный тест проверяет как единственную точку генерации.

Генерация здесь только запускается — через тот же `build_generation_orchestrator`.
Своего pipeline у Telegram-контура нет и после переноса.
"""
from __future__ import annotations

import asyncio
import logging

from src.application.profiles.finalization import ProfileFinalizationService
from src.application.programs.orchestrator import GenerationRequest
from src.application.questionnaire.service import QuestionnaireService
from src.application.telegram.delivery_queue import DeliveryQueueService
from src.application.telegram.dialog import TelegramDialogService
from src.domain.generation import GenerationTrigger
from src.infrastructure.config import (
    ADMIN_CHAT_ID,
    AUTO_GENERATE_PROGRAM_AFTER_FINALIZE,
    MAX_PHOTO_SIZE_MB,
    MAX_PHOTOS,
)
from src.infrastructure.files.object_photo_storage import ObjectStoragePhotoStorage
from src.infrastructure.media.object_storage import create_object_storage
from src.infrastructure.persistence.postgres.db import get_session_factory
from src.infrastructure.persistence.postgres.telegram_session_repository import (
    TelegramSessionRepository,
)

logger = logging.getLogger(__name__)

# Сильные ссылки на фоновые задачи генерации: event loop держит только слабые,
# и без этого множества задача может быть собрана GC посреди выполнения.
_BACKGROUND_TASKS: set[asyncio.Task] = set()


def build_photo_storage() -> ObjectStoragePhotoStorage:
    """Фото анкеты — в MinIO (RU), а не на диск контейнера.

    Диск Backend'а тоже не используется: том контейнера не переживает
    пересоздание, а фотографии нужны генерации при повторной сборке программы.
    """
    return ObjectStoragePhotoStorage(
        create_object_storage(),
        max_files=MAX_PHOTOS,
        max_size_mb=MAX_PHOTO_SIZE_MB,
    )


def build_telegram_dialog_service() -> TelegramDialogService:
    from apps.backend.api.v1.dependencies import build_profile_repository

    session_factory = get_session_factory()
    repository = build_profile_repository()
    return TelegramDialogService(
        sessions=TelegramSessionRepository(session_factory),
        questionnaire=QuestionnaireService(build_photo_storage()),
        finalization=ProfileFinalizationService(repository),
        profiles=repository,
        admin_chat_id=ADMIN_CHAT_ID or None,
    )


def build_delivery_queue_service() -> DeliveryQueueService:
    from apps.backend.api.v1.dependencies import (
        build_delivery_repository,
        build_program_html_service,
        build_program_service,
        build_retry_policy,
    )

    return DeliveryQueueService(
        deliveries=build_delivery_repository(),
        # Read-only фасад: очередь доставки не имеет права создать версию
        # программы, и отсутствие пишущего репозитория делает это невозможным
        # по конструкции.
        programs=build_program_service(),
        html_service=build_program_html_service(),
        retry_policy=build_retry_policy(),
    )


async def start_program_generation(*, profile_id: str, chat_id: str) -> None:
    """Запускает автогенерацию после финализации анкеты.

    Фоновая задача, а не синхронный вызов: генерация занимает минуты, а Gateway
    ждёт ответа, чтобы показать пользователю подтверждение анкеты. HTTP-запрос
    длиной в AI-вызов упёрся бы в любой таймаут на пути RU↔EU.

    Задача принадлежит процессу Backend, а не Gateway, — в этом и смысл переноса.
    Если Backend перезапустится посреди генерации, job останется в `running` с
    арендой и будет восстановлен worker'ом (Phase 1.2-D).
    """
    if not AUTO_GENERATE_PROGRAM_AFTER_FINALIZE:
        logger.info(
            "event=auto_generation_disabled", extra={"profile_id": profile_id}
        )
        return

    task = asyncio.create_task(_generate_and_enqueue(profile_id, chat_id))
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)


async def _generate_and_enqueue(profile_id: str, chat_id: str) -> None:
    """Генерация и постановка файла в очередь отправки.

    Ошибки не пробрасываются: у фоновой задачи нет вызывающего, который мог бы их
    обработать, а состояние генерации уже записано job-контуром. Пользователю о
    неудаче сообщает не эта задача — он получит либо файл, либо ничего, и
    администратор увидит отказ в журнале операций.
    """
    from apps.backend.api.v1.dependencies import build_generation_orchestrator

    try:
        result = await build_generation_orchestrator().generate(
            GenerationRequest(
                profile_id=profile_id,
                trigger=GenerationTrigger.AUTO_FINALIZATION,
                # Стратегия из конфигурации, fallback разрешён: неработоспособный
                # ИИ не должен ломать пользовательский сценарий.
                allow_fallback=True,
                reuse_existing=True,
            )
        )
    except Exception:  # noqa: BLE001 — job уже закрыт, здесь только журнал
        logger.warning(
            "event=telegram_auto_generation_failed", extra={"profile_id": profile_id}
        )
        return

    program = result.program
    if not program.program_id:
        logger.error(
            "event=telegram_generation_without_program",
            extra={"profile_id": profile_id},
        )
        return

    try:
        await build_delivery_queue_service().enqueue(
            program_id=program.program_id,
            profile_id=program.profile_id,
            version=program.version,
            chat_id=chat_id,
        )
    except Exception:  # noqa: BLE001 — программа сохранена, доставку повторят
        logger.exception(
            "event=telegram_delivery_enqueue_failed",
            extra={"profile_id": profile_id, "program_id": program.program_id},
        )
