"""Сборка program pipeline для Telegram gateway (Stage 5).

Handlers получают готовый ProgramPipelineService и не содержат бизнес-логику.
Pipeline собирается на основе конфигурации:
primary/fallback generator'ы, media mode, MinIO и т.д.
"""
from __future__ import annotations

import logging

from aiogram import Bot

from src.application.notifications.program_alerts import ProgramAlertService
from src.application.programs.pipeline import ProgramPipelineService
from src.application.programs.telegram_delivery import ProgramDeliveryService
from src.infrastructure.config import (
    ADMIN_CHAT_ID,
    AUTO_GENERATE_PROGRAM_AFTER_FINALIZE,
    DATABASE_URL,
)
from src.infrastructure.telegram.alert_sender import TelegramAlertSender
from src.infrastructure.telegram.program_sender import TelegramProgramSender

logger = logging.getLogger(__name__)


def is_auto_generation_enabled() -> bool:
    """Автогенерация требует DATABASE_URL (Postgres) для pipeline."""
    return AUTO_GENERATE_PROGRAM_AFTER_FINALIZE and bool(DATABASE_URL)


def build_program_pipeline(bot: Bot) -> ProgramPipelineService:
    """Собирает полный pipeline: orchestrator → html → delivery → alerts."""
    from apps.backend.api.v1.dependencies import (
        build_generation_orchestrator,
        build_program_html_service,
    )
    from src.infrastructure.persistence.postgres.db import get_session_factory
    from src.infrastructure.persistence.postgres.delivery_repository import (
        ProgramDeliveryRepository,
    )

    alert_service = None
    if ADMIN_CHAT_ID:
        alert_service = ProgramAlertService(TelegramAlertSender(bot, ADMIN_CHAT_ID))

    html_service = build_program_html_service()
    delivery_service = ProgramDeliveryService(
        html_service=html_service,
        delivery_repository=ProgramDeliveryRepository(get_session_factory()),
        sender=TelegramProgramSender(bot),
        alert_service=alert_service,
    )

    return ProgramPipelineService(
        orchestrator=build_generation_orchestrator(),
        delivery_service=delivery_service,
        alert_service=alert_service,
    )
