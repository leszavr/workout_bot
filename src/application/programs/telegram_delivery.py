"""Доставка HTML-программы пользователю (Stage 5).

Ключевое правило: delivery retry ≠ generation retry.
Программа уже сохранена в ProgramRepository; при ошибке отправки
повторяется только рендеринг HTML + отправка файла, генерация НЕ повторяется.

Сервис не зависит от Telegram: sender — callable, возвращающий message_id.
Ограниченное число попыток (не бесконечно) с backoff.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, Protocol

from src.application.notifications.program_alerts import ProgramAlert, ProgramAlertService
from src.application.programs.html_service import ProgramHtmlService
from src.domain.enums import ProgramDeliveryStatus
from src.domain.program import WorkoutProgram
from src.errors import HtmlRenderError, ProgramDeliveryError
from src.infrastructure.persistence.postgres.delivery_repository import (
    ProgramDeliveryRecord,
    ProgramDeliveryRepository,
)
from src.infrastructure.persistence.program_repository import ProgramRepository

logger = logging.getLogger(__name__)

MAX_DELIVERY_ATTEMPTS = 3
RETRY_BASE_DELAY = 1.0  # секунды


@dataclass
class ProgramDocument:
    bytes_content: bytes
    filename: str
    caption: str


class DeliverySender(Protocol):
    async def __call__(self, chat_id: str, document: ProgramDocument) -> int: ...


def build_filename(profile_id: str, version: int) -> str:
    """Без sensitive-данных: только profile_id (hex UUID)."""
    return f"workout_program_{profile_id}_v{version}.html"


class ProgramDeliveryService:
    def __init__(
        self,
        *,
        html_service: ProgramHtmlService,
        delivery_repository: ProgramDeliveryRepository,
        sender: DeliverySender,
        alert_service: ProgramAlertService | None = None,
        max_attempts: int = MAX_DELIVERY_ATTEMPTS,
    ) -> None:
        self._html = html_service
        self._deliveries = delivery_repository
        self._sender = sender
        self._alerts = alert_service
        self._max_attempts = max_attempts

    async def deliver(self, *, program: WorkoutProgram, chat_id: str) -> ProgramDeliveryRecord:
        """Рендерит HTML и отправляет документ пользователю."""
        record = await self._deliveries.create(
            ProgramDeliveryRecord(
                program_id=program.program_id or "",
                profile_id=program.profile_id,
                chat_id=chat_id,
                filename=build_filename(program.profile_id, program.version),
                source_media_mode=self._html.media_mode,
            )
        )
        return await self._send_with_retry(record, program, chat_id)

    async def redeliver(self, record: ProgramDeliveryRecord, program: WorkoutProgram) -> ProgramDeliveryRecord:
        """Повторная доставка существующей программы (без новой генерации)."""
        if not record.chat_id:
            raise ProgramDeliveryError("delivery record не содержит chat_id")
        return await self._send_with_retry(record, program, record.chat_id)

    async def _send_with_retry(
        self,
        record: ProgramDeliveryRecord,
        program: WorkoutProgram,
        chat_id: str,
    ) -> ProgramDeliveryRecord:
        logger.info(
            "event=delivery_started",
            extra={"profile_id": program.profile_id, "program_id": program.program_id},
        )

        try:
            html_bytes = await self._html.render(program)
        except HtmlRenderError as exc:
            record.status = ProgramDeliveryStatus.FAILED
            record.last_error = f"html_render: {exc}"
            await self._deliveries.update(record)
            await self._notify_alert(
                stage="html_render",
                program=program,
                exception_type=exc.__class__.__name__,
                message=str(exc),
            )
            raise

        document = ProgramDocument(
            bytes_content=html_bytes,
            filename=record.filename or build_filename(program.profile_id, program.version),
            caption="Ваша персональная программа тренировок готова.",
        )

        record.status = ProgramDeliveryStatus.SENDING
        await self._deliveries.update(record)

        attempts_remaining = self._max_attempts
        last_error: str | None = None
        while attempts_remaining > 0:
            record.attempts += 1
            try:
                message_id = await self._sender(chat_id, document)
            except Exception as exc:  # noqa: BLE001 — нормализуем в ошибку доставки
                last_error = f"{exc.__class__.__name__}: {str(exc)[:300]}"
                attempts_remaining -= 1
                logger.warning(
                    "event=delivery_attempt_failed",
                    extra={
                        "profile_id": program.profile_id,
                        "attempt": record.attempts,
                        "error_type": exc.__class__.__name__,
                    },
                )
                if attempts_remaining > 0:
                    await asyncio.sleep(RETRY_BASE_DELAY * (self._max_attempts - attempts_remaining))
                continue

            record.sent_message_id = message_id
            record.status = ProgramDeliveryStatus.SENT
            record.last_error = None
            await self._deliveries.update(record)
            logger.info(
                "event=delivery_success",
                extra={
                    "profile_id": program.profile_id,
                    "program_id": program.program_id,
                    "attempts": record.attempts,
                },
            )
            return record

        record.status = ProgramDeliveryStatus.FAILED
        record.last_error = last_error
        await self._deliveries.update(record)
        logger.error(
            "event=delivery_failed",
            extra={"profile_id": program.profile_id, "attempts": record.attempts},
        )
        await self._notify_alert(
            stage="delivery",
            program=program,
            exception_type="DeliveryError",
            message=last_error or "",
        )
        raise ProgramDeliveryError(f"Доставка не удалась после {self._max_attempts} попыток")

    async def _notify_alert(
        self,
        *,
        stage: str,
        program: WorkoutProgram,
        exception_type: str,
        message: str,
    ) -> None:
        if self._alerts is None:
            return
        await self._alerts.notify(
            ProgramAlert(
                stage=stage,
                profile_id=program.profile_id,
                program_id=program.program_id,
                generator=program.generation.actual_generator.value
                if program.generation.actual_generator
                else None,
                fallback_status="used" if program.generation.fallback_used else "not_used",
                exception_type=exception_type,
                message=message,
            )
        )
