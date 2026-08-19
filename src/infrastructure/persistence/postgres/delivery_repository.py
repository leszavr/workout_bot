"""Репозиторий доставки программ пользователю (Stage 5).

Хранит статусы доставки (pending/sending/sent/failed) и число попыток.
Delivery retry независим от generation retry: программа уже сохранена,
повторяется только отправка файла.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.domain.enums import ProgramDeliveryStatus
from src.errors import ProgramDeliveryError
from src.infrastructure.persistence.postgres.models import ProgramDeliveryRow


class ProgramDeliveryRecord:
    def __init__(
        self,
        *,
        id: int | None = None,
        program_id: str,
        profile_id: str,
        chat_id: str | None = None,
        filename: str | None = None,
        status: ProgramDeliveryStatus = ProgramDeliveryStatus.PENDING,
        attempts: int = 0,
        last_error: str | None = None,
        sent_message_id: int | None = None,
        source_media_mode: str | None = None,
    ) -> None:
        self.id = id
        self.program_id = program_id
        self.profile_id = profile_id
        self.chat_id = chat_id
        self.filename = filename
        self.status = status
        self.attempts = attempts
        self.last_error = last_error
        self.sent_message_id = sent_message_id
        self.source_media_mode = source_media_mode


class ProgramDeliveryRepository:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._sessions = session_factory

    async def create(self, record: ProgramDeliveryRecord) -> ProgramDeliveryRecord:
        try:
            async with self._sessions() as session:
                async with session.begin():
                    row = ProgramDeliveryRow(
                        program_id=record.program_id,
                        profile_id=record.profile_id,
                        chat_id=record.chat_id,
                        filename=record.filename,
                        status=record.status.value,
                        attempts=record.attempts,
                        sent_message_id=record.sent_message_id,
                        source_media_mode=record.source_media_mode,
                    )
                    session.add(row)
                    await session.flush()
                    record.id = row.id
        except SQLAlchemyError as exc:
            raise ProgramDeliveryError(f"Не удалось создать запись доставки: {exc}") from exc
        return record

    async def update(self, record: ProgramDeliveryRecord) -> None:
        if record.id is None:
            raise ProgramDeliveryError("delivery record id is empty")
        try:
            async with self._sessions() as session:
                async with session.begin():
                    row = await session.get(ProgramDeliveryRow, record.id)
                    if row is None:
                        raise ProgramDeliveryError(f"Запись доставки id={record.id} не найдена")
                    row.status = record.status.value
                    row.attempts = record.attempts
                    row.last_error = record.last_error
                    row.sent_message_id = record.sent_message_id
                    row.chat_id = record.chat_id or row.chat_id
                    row.filename = record.filename or row.filename
                    if record.status is ProgramDeliveryStatus.SENT:
                        row.delivered_at = datetime.now(timezone.utc)
        except SQLAlchemyError as exc:
            raise ProgramDeliveryError(f"Не удалось обновить запись доставки: {exc}") from exc

    async def get_for_profile(self, profile_id: str) -> ProgramDeliveryRecord | None:
        try:
            async with self._sessions() as session:
                row = (
                    await session.execute(
                        select(ProgramDeliveryRow)
                        .where(ProgramDeliveryRow.profile_id == profile_id)
                        .order_by(ProgramDeliveryRow.id.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
        except SQLAlchemyError as exc:
            raise ProgramDeliveryError(f"Ошибка чтения записи доставки: {exc}") from exc
        return _to_record(row) if row else None

    async def list_failed(self, limit: int = 50) -> list[ProgramDeliveryRecord]:
        try:
            async with self._sessions() as session:
                rows = (
                    await session.execute(
                        select(ProgramDeliveryRow)
                        .where(ProgramDeliveryRow.status == ProgramDeliveryStatus.FAILED.value)
                        .order_by(ProgramDeliveryRow.id.desc())
                        .limit(limit)
                    )
                ).scalars().all()
        except SQLAlchemyError as exc:
            raise ProgramDeliveryError(f"Ошибка списка failed доставок: {exc}") from exc
        return [_to_record(r) for r in rows]


def _to_record(row: ProgramDeliveryRow) -> ProgramDeliveryRecord:
    return ProgramDeliveryRecord(
        id=row.id,
        program_id=row.program_id,
        profile_id=row.profile_id,
        chat_id=row.chat_id,
        filename=row.filename,
        status=ProgramDeliveryStatus(row.status),
        attempts=row.attempts,
        last_error=row.last_error,
        sent_message_id=row.sent_message_id,
        source_media_mode=row.source_media_mode,
    )
