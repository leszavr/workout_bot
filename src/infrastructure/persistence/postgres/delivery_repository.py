"""Репозиторий доставки программ пользователю (Stage 5).

Хранит статусы доставки (pending/sending/sent/failed) и число попыток.
Delivery retry независим от generation retry: программа уже сохранена,
повторяется только отправка файла.

Phase 1.2-D добавляет межпроцессный повтор: `next_attempt_at` (когда повтор
допустим) и аренду (`lease_owner`/`lease_expires_at`). До этого повторы жили
внутри одного вызова `_send_with_retry`, и после его завершения `failed`-запись
никто не подхватывал.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.domain.enums import ProgramDeliveryStatus
from src.errors import ProgramDeliveryError
from src.infrastructure.persistence.postgres.models import ProgramDeliveryRow


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


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
        next_attempt_at: datetime | None = None,
        lease_owner: str | None = None,
        lease_expires_at: datetime | None = None,
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
        self.next_attempt_at = next_attempt_at
        self.lease_owner = lease_owner
        self.lease_expires_at = lease_expires_at


class ProgramDeliverySummary:
    """Итог доставки программ одной анкеты для административного списка.

    Отвечает на вопрос «эта анкета уже исполнена?»: была ли программа
    отправлена пользователю и когда. Берётся последняя успешная отправка, а не
    последняя запись: неудачная попытка после успешной не отменяет того, что
    пользователь программу получил.
    """

    def __init__(
        self,
        *,
        delivered: bool,
        delivered_at: datetime | None,
        last_status: ProgramDeliveryStatus | None,
    ) -> None:
        self.delivered = delivered
        self.delivered_at = delivered_at
        self.last_status = last_status


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
                    row.next_attempt_at = record.next_attempt_at
                    row.lease_owner = record.lease_owner
                    row.lease_expires_at = record.lease_expires_at
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

    async def summaries_for_profiles(
        self, profile_ids: list[str]
    ) -> dict[str, ProgramDeliverySummary]:
        """Сводка доставок по списку анкет — одним запросом.

        Список анкет читается страницами, и запрос на каждую строку отдельно
        превратил бы открытие раздела в N+1. Возвращаются только анкеты, у
        которых доставки есть; отсутствие ключа означает «программу не
        отправляли».
        """
        if not profile_ids:
            return {}
        try:
            async with self._sessions() as session:
                rows = (
                    await session.execute(
                        select(ProgramDeliveryRow)
                        .where(ProgramDeliveryRow.profile_id.in_(profile_ids))
                        .order_by(ProgramDeliveryRow.id)
                    )
                ).scalars().all()
        except SQLAlchemyError as exc:
            raise ProgramDeliveryError(f"Ошибка сводки доставок: {exc}") from exc

        result: dict[str, ProgramDeliverySummary] = {}
        for row in rows:
            status = ProgramDeliveryStatus(row.status)
            current = result.get(row.profile_id)
            delivered = status is ProgramDeliveryStatus.SENT
            result[row.profile_id] = ProgramDeliverySummary(
                # Успешная отправка не отменяется более поздней неудачной
                # попыткой: пользователь программу уже получил.
                delivered=delivered or bool(current and current.delivered),
                delivered_at=row.delivered_at
                if delivered
                else (current.delivered_at if current else None),
                last_status=status,
            )
        return result

    # --- worker: захват, аренда, recovery (Phase 1.2-D) ------------------------

    async def claim_due(
        self,
        *,
        owner: str,
        lease_seconds: float,
        limit: int = 1,
        now: datetime | None = None,
    ) -> list[ProgramDeliveryRecord]:
        """Забирает failed-доставки, которым назначен повтор.

        Взаимное исключение — ``FOR UPDATE SKIP LOCKED`` в той же транзакции,
        что и перевод в `SENDING`: второй worker не увидит заблокированную
        строку и не будет её ждать. Статус меняется сразу, потому что
        отправка уже началась с точки зрения любого другого читателя.

        Записи без `chat_id` не берутся: повторить отправку некуда, и попытка
        только сожгла бы лимит попыток.
        """
        moment = now or _utcnow()
        expires = moment + timedelta(seconds=lease_seconds)
        try:
            async with self._sessions() as session:
                async with session.begin():
                    rows = (
                        await session.execute(
                            select(ProgramDeliveryRow)
                            .where(
                                ProgramDeliveryRow.status
                                == ProgramDeliveryStatus.FAILED.value,
                                ProgramDeliveryRow.next_attempt_at.is_not(None),
                                ProgramDeliveryRow.next_attempt_at <= moment,
                                ProgramDeliveryRow.chat_id.is_not(None),
                            )
                            .order_by(ProgramDeliveryRow.next_attempt_at)
                            .limit(limit)
                            .with_for_update(skip_locked=True)
                        )
                    ).scalars().all()

                    claimed: list[ProgramDeliveryRecord] = []
                    for row in rows:
                        row.status = ProgramDeliveryStatus.SENDING.value
                        row.next_attempt_at = None
                        row.lease_owner = owner
                        row.lease_expires_at = expires
                        claimed.append(_to_record(row))
                    return claimed
        except SQLAlchemyError as exc:
            raise ProgramDeliveryError(
                f"Не удалось захватить доставки: {exc.__class__.__name__}"
            ) from exc

    async def release_stale(
        self,
        *,
        message: str,
        next_attempt_at: datetime | None = None,
        limit: int = 50,
        now: datetime | None = None,
    ) -> list[ProgramDeliveryRecord]:
        """Возвращает в failed доставки, чей исполнитель исчез.

        `SENDING` с просроченной арендой означает, что процесс умер между
        началом отправки и записью результата. Такая запись без вмешательства
        осталась бы в `SENDING` навсегда, и `list_failed`/`claim_due` её больше
        не увидели бы.

        `SENDING` без аренды не трогаем: она принадлежит синхронной отправке из
        другого процесса (Telegram-пайплайн), которая воркеру не подотчётна.
        """
        moment = now or _utcnow()
        try:
            async with self._sessions() as session:
                async with session.begin():
                    rows = (
                        await session.execute(
                            select(ProgramDeliveryRow)
                            .where(
                                ProgramDeliveryRow.status
                                == ProgramDeliveryStatus.SENDING.value,
                                ProgramDeliveryRow.lease_expires_at.is_not(None),
                                ProgramDeliveryRow.lease_expires_at <= moment,
                            )
                            .order_by(ProgramDeliveryRow.lease_expires_at)
                            .limit(limit)
                            .with_for_update(skip_locked=True)
                        )
                    ).scalars().all()

                    released: list[ProgramDeliveryRecord] = []
                    for row in rows:
                        row.status = ProgramDeliveryStatus.FAILED.value
                        row.last_error = message[:500]
                        row.next_attempt_at = next_attempt_at
                        row.lease_owner = None
                        row.lease_expires_at = None
                        released.append(_to_record(row))
                    return released
        except SQLAlchemyError as exc:
            raise ProgramDeliveryError(
                f"Не удалось восстановить зависшие доставки: {exc.__class__.__name__}"
            ) from exc

    async def schedule_retry(
        self, record: ProgramDeliveryRecord, *, next_attempt_at: datetime
    ) -> bool:
        """Назначает повтор доставке, которую закрыл не worker.

        Возвращает False, если запись больше не в failed или повтор уже
        назначен: одна неудача не должна давать две очереди.
        """
        if record.id is None:
            raise ProgramDeliveryError("delivery record id is empty")
        try:
            async with self._sessions() as session:
                async with session.begin():
                    updated = (
                        await session.execute(
                            update(ProgramDeliveryRow)
                            .where(
                                ProgramDeliveryRow.id == record.id,
                                ProgramDeliveryRow.status
                                == ProgramDeliveryStatus.FAILED.value,
                                ProgramDeliveryRow.next_attempt_at.is_(None),
                            )
                            .values(next_attempt_at=next_attempt_at)
                            .returning(ProgramDeliveryRow.id)
                        )
                    ).scalar_one_or_none()
        except SQLAlchemyError as exc:
            raise ProgramDeliveryError(
                f"Не удалось назначить повтор доставки: {exc.__class__.__name__}"
            ) from exc
        return updated is not None

    async def delete_for_program(self, program_id: str) -> int:
        """Удаляет записи доставок программы. Возвращает число удалённых строк.

        Внешнего ключа на `workout_programs` нет, поэтому осиротевшие записи
        база не уберёт: доставка удалённой программы — мусор, который в журнале
        уже ничего не объясняет.
        """
        try:
            async with self._sessions() as session:
                async with session.begin():
                    result = await session.execute(
                        delete(ProgramDeliveryRow).where(
                            ProgramDeliveryRow.program_id == program_id
                        )
                    )
                    return int(result.rowcount or 0)
        except SQLAlchemyError as exc:
            raise ProgramDeliveryError(
                f"Не удалось удалить доставки программы {program_id}: {exc}"
            ) from exc

    async def delete_for_profile(self, profile_id: str) -> int:
        """Удаляет записи доставок анкеты. Возвращает число удалённых строк."""
        try:
            async with self._sessions() as session:
                async with session.begin():
                    result = await session.execute(
                        delete(ProgramDeliveryRow).where(
                            ProgramDeliveryRow.profile_id == profile_id
                        )
                    )
                    return int(result.rowcount or 0)
        except SQLAlchemyError as exc:
            raise ProgramDeliveryError(
                f"Не удалось удалить доставки анкеты {profile_id}: {exc}"
            ) from exc


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
        next_attempt_at=row.next_attempt_at,
        lease_owner=row.lease_owner,
        lease_expires_at=row.lease_expires_at,
    )
