"""Очередь доставки программ в Telegram (сетевая граница Gateway).

После выноса Gateway за сетевую границу Backend не может отправить файл сам:
`api.telegram.org` доступен только из EU-сегмента, а входящих подключений к
EU-контейнеру нет — он за NAT. Значит инициатором может быть только Gateway.

Отсюда форма: Backend ставит задание в очередь (`pending`-запись доставки),
Gateway опрашивает очередь, забирает задание, запрашивает файл, отправляет и
отчитывается о результате. Никакого нового хранилища не появляется — очередь это
уже существующая таблица `program_deliveries`, у которой с Phase 1.2-D есть и
аренда, и `next_attempt_at`.

Файл в задании не передаётся: он запрашивается отдельным вызовом и живёт в
памяти процесса Gateway до отправки. В EU он не сохраняется.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from src.application.programs.html_service import ProgramHtmlService
from src.application.programs.service import ProgramService
from src.application.programs.telegram_delivery import build_filename
from src.domain.enums import ProgramDeliveryStatus
from src.domain.retry import RetryPolicy
from src.domain.telegram_contract import (
    TelegramDeliveryResult,
    TelegramDeliveryTask,
)
from src.errors import ProgramDeliveryError
from src.infrastructure.persistence.postgres.delivery_repository import (
    ProgramDeliveryRecord,
    ProgramDeliveryRepository,
)

logger = logging.getLogger(__name__)

DELIVERY_CAPTION = "Ваша персональная программа тренировок готова."


class DeliveryQueueService:
    """Постановка в очередь, выдача заданий и приём результата."""

    def __init__(
        self,
        *,
        deliveries: ProgramDeliveryRepository,
        programs: ProgramService,
        html_service: ProgramHtmlService,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        self._deliveries = deliveries
        self._programs = programs
        self._html = html_service
        self._retry = retry_policy

    async def enqueue(
        self, *, program_id: str, profile_id: str, version: int, chat_id: str
    ) -> ProgramDeliveryRecord:
        """Ставит программу в очередь отправки. Идемпотентна.

        Повторный вызов для той же программы и того же чата возвращает
        существующую запись: иначе повторная финализация или повторный запрос
        генерации отправили бы пользователю один файл дважды.
        """
        existing = await self._deliveries.get_active_for_program(program_id, chat_id)
        if existing is not None:
            logger.info(
                "event=delivery_already_queued",
                extra={"program_id": program_id, "status": existing.status.value},
            )
            return existing

        record = await self._deliveries.create(
            ProgramDeliveryRecord(
                program_id=program_id,
                profile_id=profile_id,
                chat_id=chat_id,
                filename=build_filename(profile_id, version),
                status=ProgramDeliveryStatus.PENDING,
                source_media_mode=self._html.media_mode,
            )
        )
        logger.info(
            "event=delivery_queued",
            extra={"program_id": program_id, "delivery_id": record.id},
        )
        return record

    async def claim(
        self, *, owner: str, lease_seconds: float, limit: int = 5
    ) -> list[TelegramDeliveryTask]:
        """Выдаёт задания одному экземпляру Gateway.

        Захват и перевод в `sending` неделимы (`FOR UPDATE SKIP LOCKED`), поэтому
        два экземпляра Gateway не получат одно задание и файл не уйдёт дважды.
        """
        records = await self._deliveries.claim_for_send(
            owner=owner, lease_seconds=lease_seconds, limit=limit
        )
        tasks: list[TelegramDeliveryTask] = []
        for record in records:
            if record.id is None or not record.chat_id:
                continue
            tasks.append(
                TelegramDeliveryTask(
                    delivery_id=record.id,
                    chat_id=record.chat_id,
                    filename=record.filename
                    or build_filename(record.profile_id, 1),
                    caption=DELIVERY_CAPTION,
                )
            )
        return tasks

    async def render_document(self, delivery_id: int) -> tuple[str, bytes]:
        """Готовит файл для отправки. Возвращает (имя файла, содержимое).

        Рендер выполняется по запросу, а не при постановке в очередь: результат
        не хранится, поэтому изображения упражнений не дублируются в БД, а
        повторная отправка получает актуальный HTML.

        Отдаётся только доставке в состоянии `sending`: запрос файла для чужой
        или уже завершённой доставки означает ошибку на стороне вызывающего, и
        отдавать программу «на всякий случай» нельзя.
        """
        record = await self._deliveries.get(delivery_id)
        if record is None:
            raise ProgramDeliveryError(f"Доставка id={delivery_id} не найдена")
        if record.status is not ProgramDeliveryStatus.SENDING:
            raise ProgramDeliveryError(
                f"Доставка id={delivery_id} не находится в состоянии отправки"
            )

        program = await self._programs.get(record.program_id)
        if program is None:
            raise ProgramDeliveryError(
                f"Программа {record.program_id} не найдена: доставка невозможна"
            )
        content = await self._html.render(program)
        filename = record.filename or build_filename(
            program.profile_id, program.version
        )
        return filename, content

    async def report(
        self, delivery_id: int, result: TelegramDeliveryResult
    ) -> ProgramDeliveryRecord:
        """Фиксирует итог отправки, сообщённый Gateway.

        Счётчик попыток увеличивается здесь: попытка отправки происходит в EU, и
        Backend узнаёт о ней только из этого отчёта. Без инкремента бюджет
        попыток не расходовался бы никогда, и неотправляемая доставка
        повторялась бы бесконечно.
        """
        record = await self._deliveries.get(delivery_id)
        if record is None:
            raise ProgramDeliveryError(f"Доставка id={delivery_id} не найдена")

        record.attempts += 1
        record.lease_owner = None
        record.lease_expires_at = None

        if result.delivered:
            record.status = ProgramDeliveryStatus.SENT
            record.sent_message_id = result.message_id
            record.last_error = None
            record.next_attempt_at = None
            await self._deliveries.update(record)
            logger.info(
                "event=delivery_reported_sent",
                extra={"delivery_id": delivery_id, "attempts": record.attempts},
            )
            return record

        record.status = ProgramDeliveryStatus.FAILED
        record.last_error = result.error
        record.next_attempt_at = self._plan_retry(record)
        await self._deliveries.update(record)
        logger.warning(
            "event=delivery_reported_failed",
            extra={
                "delivery_id": delivery_id,
                "attempts": record.attempts,
                "retry_scheduled": record.next_attempt_at is not None,
            },
        )
        return record

    def _plan_retry(self, record: ProgramDeliveryRecord) -> datetime | None:
        if self._retry is None:
            return None
        return self._retry.next_attempt_at(
            now=datetime.now(timezone.utc), attempts_made=record.attempts
        )
