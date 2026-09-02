"""Сборка retry-контура worker'а.

Worker не собирает generation pipeline сам: оркестратор он берёт из
существующей единственной фабрики (`build_generation_orchestrator`). Это то же
требование, что закреплено архитектурным тестом для Telegram и Admin API, — у
генерации одна точка сборки.

Доставка собирается здесь, потому что worker'у нужен собственный Telegram Bot:
экземпляр `Bot` живёт в процессе, и разделить его с Gateway невозможно. Токен
тот же, отправка идёт тем же Bot API — второго транспорта не появляется.
"""
from __future__ import annotations

import logging

from src.application.notifications.program_alerts import ProgramAlertService
from src.application.programs.retry_service import (
    DeliveryRetryService,
    GenerationRetryService,
    RetryCoordinator,
)
from src.application.programs.telegram_delivery import ProgramDeliveryService
from src.infrastructure.config import (
    ADMIN_CHAT_ID,
    BOT_TOKEN,
    WORKER_BATCH_SIZE,
    WORKER_COMPONENT_ID,
    WORKER_DELIVERY_ENABLED,
    WORKER_LEASE_SECONDS,
)
from src.infrastructure.persistence.postgres.db import get_session_factory
from src.infrastructure.persistence.postgres.delivery_repository import (
    ProgramDeliveryRepository,
)
from src.infrastructure.persistence.postgres.generation_job_repository import (
    GenerationJobRepository,
)

logger = logging.getLogger(__name__)


def build_retry_coordinator() -> RetryCoordinator:
    from apps.backend.api.v1.dependencies import (
        build_generation_orchestrator,
        build_retry_policy,
    )

    session_factory = get_session_factory()
    policy = build_retry_policy()

    generation = GenerationRetryService(
        jobs=GenerationJobRepository(session_factory),
        orchestrator=build_generation_orchestrator(),
        policy=policy,
        owner=WORKER_COMPONENT_ID,
        lease_seconds=WORKER_LEASE_SECONDS,
        batch_size=WORKER_BATCH_SIZE,
    )

    delivery = None
    if WORKER_DELIVERY_ENABLED and BOT_TOKEN:
        delivery = _build_delivery_retry(session_factory, policy)
    else:
        # Явное сообщение вместо тихого пропуска: иначе «доставки не
        # повторяются» выглядело бы как дефект, а не как конфигурация.
        logger.warning(
            "event=worker_delivery_retry_disabled reason=%s",
            "config" if not WORKER_DELIVERY_ENABLED else "no_bot_token",
        )

    return RetryCoordinator(generation=generation, delivery=delivery)


def _build_delivery_retry(session_factory, policy) -> DeliveryRetryService:
    from aiogram import Bot
    from aiogram.client.default import DefaultBotProperties
    from aiogram.enums import ParseMode

    from apps.backend.api.v1.dependencies import (
        build_program_html_service,
        build_program_service,
    )
    from src.infrastructure.telegram.alert_sender import TelegramAlertSender
    from src.infrastructure.telegram.program_sender import TelegramProgramSender

    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    alert_service = (
        ProgramAlertService(TelegramAlertSender(bot, ADMIN_CHAT_ID))
        if ADMIN_CHAT_ID
        else None
    )
    deliveries = ProgramDeliveryRepository(session_factory)
    return DeliveryRetryService(
        deliveries=deliveries,
        # Read-only фасад: повтор доставки не имеет доступа к записи программ.
        programs=build_program_service(),
        delivery_service=ProgramDeliveryService(
            html_service=build_program_html_service(),
            delivery_repository=deliveries,
            sender=TelegramProgramSender(bot),
            alert_service=alert_service,
            retry_policy=policy,
        ),
        policy=policy,
        owner=WORKER_COMPONENT_ID,
        lease_seconds=WORKER_LEASE_SECONDS,
        batch_size=WORKER_BATCH_SIZE,
    )
