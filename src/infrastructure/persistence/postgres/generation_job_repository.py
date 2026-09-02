"""Репозиторий persistent состояния генерации (Phase 1.2-B).

Идемпотентность и переходы состояния обеспечивает PostgreSQL, а не Python:

- создание job: ``INSERT ... ON CONFLICT (idempotency_key) DO NOTHING`` — два
  параллельных запроса одной логической генерации дают ровно один job;
- переход состояния: ``UPDATE ... WHERE id = :id AND status = :expected`` —
  два процесса не могут запустить или завершить один job дважды.

Проверка «если нет — создай» на стороне приложения здесь не годится: она не
защищает несколько backend-процессов.

Длительный AI-вызов в транзакцию не попадает: каждый метод открывает свою
короткую транзакцию, между ними приложение работает без удерживаемых блокировок.

Phase 1.2-D добавляет захват job воркером. Взаимное исключение обеспечивает
``SELECT ... FOR UPDATE SKIP LOCKED`` в той же транзакции, что и перевод в
``RUNNING``: два экземпляра worker'а не могут выбрать одну строку, а
проигравший не ждёт блокировки и берёт следующую.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.domain.enums import GenerationJobStatus
from src.domain.generation import (
    GenerationErrorCode,
    GenerationErrorKind,
    GenerationJob,
    GenerationJobTransitionError,
    GenerationTrigger,
    error_kind,
)
from src.errors import ProgramPersistenceError
from src.infrastructure.persistence.postgres.models import GenerationJobRow


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _attempt_consumed_condition(trigger: GenerationTrigger):
    """Когда предыдущая логическая генерация считается исчерпанной.

    Автогенерация после подтверждения анкеты: успех исчерпывающим не является —
    программа уже есть, повторять нечего, поэтому повторный finalize попадает в
    тот же ключ и возвращает существующий job. Исключение — успешный job,
    потерявший ссылку на программу (версию удалили, FK обнулил ссылку): иначе
    автогенерация оказалась бы заблокированной навсегда.

    Явный запрос администратора: он просит новую программу, поэтому любой
    завершённый запрос освобождает счётчик и следующий клик — уже другая
    логическая генерация.
    """
    if trigger is GenerationTrigger.ADMIN_REQUEST:
        return GenerationJobRow.status.in_(
            [GenerationJobStatus.FAILED.value, GenerationJobStatus.SUCCEEDED.value]
        )
    return or_(
        GenerationJobRow.status == GenerationJobStatus.FAILED.value,
        and_(
            GenerationJobRow.status == GenerationJobStatus.SUCCEEDED.value,
            GenerationJobRow.program_id.is_(None),
        ),
    )


def _to_domain(row: GenerationJobRow) -> GenerationJob:
    return GenerationJob(
        id=row.id,
        job_id=row.job_id,
        profile_id=row.profile_id,
        idempotency_key=row.idempotency_key,
        trigger=GenerationTrigger(row.trigger),
        requested_generator=row.requested_generator,
        status=GenerationJobStatus(row.status),
        attempts=row.attempts,
        program_id=row.program_id,
        program_version=row.program_version,
        last_error_code=row.last_error_code,
        last_error_message=row.last_error_message,
        next_attempt_at=row.next_attempt_at,
        lease_owner=row.lease_owner,
        lease_expires_at=row.lease_expires_at,
        created_at=row.created_at,
        started_at=row.started_at,
        completed_at=row.completed_at,
    )


class GenerationJobRepository:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._sessions = session_factory

    async def next_attempt(
        self, profile_id: str, trigger: GenerationTrigger
    ) -> int:
        """Номер следующей попытки для (профиль, триггер).

        Гонка здесь безопасна: два параллельных запроса получают одинаковый
        номер, строят одинаковый idempotency key, и дубликат отсекает UNIQUE
        constraint при вставке.
        """
        try:
            async with self._sessions() as session:
                consumed = (
                    await session.execute(
                        select(func.count())
                        .select_from(GenerationJobRow)
                        .where(
                            GenerationJobRow.profile_id == profile_id,
                            GenerationJobRow.trigger == trigger.value,
                            _attempt_consumed_condition(trigger),
                        )
                    )
                ).scalar_one()
        except SQLAlchemyError as exc:
            raise ProgramPersistenceError(
                f"Не удалось определить номер попытки генерации: {exc.__class__.__name__}"
            ) from exc
        return consumed + 1

    async def create_or_get(self, job: GenerationJob) -> tuple[GenerationJob, bool]:
        """Создаёт job либо возвращает существующий с тем же idempotency key.

        Возвращает (job, created). Гонку разрешает БД: проигравшая вставка
        ничего не создаёт, а читает победителя.
        """
        try:
            async with self._sessions() as session:
                async with session.begin():
                    stmt = (
                        pg_insert(GenerationJobRow)
                        .values(
                            job_id=job.job_id,
                            profile_id=job.profile_id,
                            idempotency_key=job.idempotency_key,
                            trigger=job.trigger.value,
                            requested_generator=job.requested_generator,
                            status=job.status.value,
                            attempts=job.attempts,
                        )
                        .on_conflict_do_nothing(
                            index_elements=[GenerationJobRow.idempotency_key]
                        )
                        .returning(GenerationJobRow)
                    )
                    created_row = (
                        await session.execute(stmt)
                    ).scalar_one_or_none()
                    if created_row is not None:
                        return _to_domain(created_row), True

                    existing = (
                        await session.execute(
                            select(GenerationJobRow).where(
                                GenerationJobRow.idempotency_key == job.idempotency_key
                            )
                        )
                    ).scalar_one()
                    return _to_domain(existing), False
        except SQLAlchemyError as exc:
            raise ProgramPersistenceError(
                f"Не удалось создать запись генерации: {exc.__class__.__name__}"
            ) from exc

    async def get(self, job_id: str) -> GenerationJob | None:
        try:
            async with self._sessions() as session:
                row = (
                    await session.execute(
                        select(GenerationJobRow).where(GenerationJobRow.job_id == job_id)
                    )
                ).scalar_one_or_none()
        except SQLAlchemyError as exc:
            raise ProgramPersistenceError(
                f"Не удалось прочитать запись генерации: {exc.__class__.__name__}"
            ) from exc
        return _to_domain(row) if row else None

    async def get_by_idempotency_key(self, key: str) -> GenerationJob | None:
        try:
            async with self._sessions() as session:
                row = (
                    await session.execute(
                        select(GenerationJobRow).where(
                            GenerationJobRow.idempotency_key == key
                        )
                    )
                ).scalar_one_or_none()
        except SQLAlchemyError as exc:
            raise ProgramPersistenceError(
                f"Не удалось прочитать запись генерации: {exc.__class__.__name__}"
            ) from exc
        return _to_domain(row) if row else None

    async def list_for_profile(
        self, profile_id: str, limit: int = 50
    ) -> list[GenerationJob]:
        try:
            async with self._sessions() as session:
                rows = (
                    await session.execute(
                        select(GenerationJobRow)
                        .where(GenerationJobRow.profile_id == profile_id)
                        .order_by(GenerationJobRow.id.desc())
                        .limit(limit)
                    )
                ).scalars().all()
        except SQLAlchemyError as exc:
            raise ProgramPersistenceError(
                f"Не удалось прочитать список генераций: {exc.__class__.__name__}"
            ) from exc
        return [_to_domain(r) for r in rows]

    # --- переходы состояния ---------------------------------------------------

    async def mark_running(self, job: GenerationJob) -> GenerationJob:
        """PENDING → RUNNING. Условие перехода проверяет сам UPDATE.

        Повтор (FAILED → RUNNING) сюда не попадает намеренно: он обязан
        сопровождаться захватом аренды, поэтому выполняется через `claim_due`.
        Вызов этого метода на провалившемся job отклонит `UPDATE`.
        """
        updated = job.model_copy(deep=True)
        updated.start()
        return await self._transition(
            updated,
            expected=GenerationJobStatus.PENDING,
            values={
                "status": updated.status.value,
                "attempts": updated.attempts,
                "started_at": updated.started_at,
            },
        )

    async def mark_succeeded(
        self, job: GenerationJob, *, program_id: str, program_version: int
    ) -> GenerationJob:
        """RUNNING → SUCCEEDED вместе со ссылкой на созданную версию программы."""
        updated = job.model_copy(deep=True)
        updated.succeed(program_id=program_id, program_version=program_version)
        return await self._transition(
            updated,
            expected=GenerationJobStatus.RUNNING,
            values={
                "status": updated.status.value,
                "program_id": updated.program_id,
                "program_version": updated.program_version,
                "last_error_code": None,
                "last_error_message": None,
                "completed_at": updated.completed_at,
                "next_attempt_at": None,
                "lease_owner": None,
                "lease_expires_at": None,
            },
        )

    async def mark_failed(
        self,
        job: GenerationJob,
        *,
        error_code: GenerationErrorCode | str,
        message: str,
        next_attempt_at: datetime | None = None,
    ) -> GenerationJob:
        """RUNNING → FAILED. Программа при этом не создаётся."""
        updated = job.model_copy(deep=True)
        updated.fail(
            error_code=error_code, message=message, next_attempt_at=next_attempt_at
        )
        return await self._transition(
            updated,
            expected=GenerationJobStatus.RUNNING,
            values={
                "status": updated.status.value,
                "last_error_code": updated.last_error_code,
                "last_error_message": updated.last_error_message,
                "completed_at": updated.completed_at,
                "next_attempt_at": updated.next_attempt_at,
                "lease_owner": None,
                "lease_expires_at": None,
            },
        )

    # --- worker: захват, аренда, recovery (Phase 1.2-D) ------------------------

    async def claim_due(
        self,
        *,
        owner: str,
        lease_seconds: float,
        limit: int = 1,
        now: datetime | None = None,
    ) -> list[GenerationJob]:
        """Забирает job'ы, которым назначен повтор, и переводит их в RUNNING.

        Выборка и перевод выполняются в одной транзакции с ``FOR UPDATE SKIP
        LOCKED``: конкурирующий worker не увидит уже заблокированную строку и не
        станет её ждать. Условие по статусу дублируется в `UPDATE` не «на всякий
        случай» — оно оставляет переход состояния там же, где он был до 1.2-D:
        в самом запросе, а не в Python.

        Возвращаются только фактически захваченные job'ы. Пустой список —
        нормальный результат: очередь пуста.
        """
        moment = now or _utcnow()
        expires = moment + timedelta(seconds=lease_seconds)
        try:
            async with self._sessions() as session:
                async with session.begin():
                    rows = (
                        await session.execute(
                            select(GenerationJobRow)
                            .where(
                                GenerationJobRow.status
                                == GenerationJobStatus.FAILED.value,
                                GenerationJobRow.next_attempt_at.is_not(None),
                                GenerationJobRow.next_attempt_at <= moment,
                            )
                            .order_by(GenerationJobRow.next_attempt_at)
                            .limit(limit)
                            .with_for_update(skip_locked=True)
                        )
                    ).scalars().all()

                    claimed: list[GenerationJob] = []
                    for row in rows:
                        job = _to_domain(row)
                        job.start()
                        row.status = job.status.value
                        row.attempts = job.attempts
                        row.started_at = job.started_at
                        row.completed_at = None
                        row.next_attempt_at = None
                        row.lease_owner = owner
                        row.lease_expires_at = expires
                        job.lease_owner = owner
                        job.lease_expires_at = expires
                        claimed.append(job)
                    return claimed
        except SQLAlchemyError as exc:
            raise ProgramPersistenceError(
                f"Не удалось захватить операции генерации: {exc.__class__.__name__}"
            ) from exc

    async def release_stale(
        self,
        *,
        error_code: GenerationErrorCode | str,
        message: str,
        next_attempt_at: datetime | None = None,
        limit: int = 50,
        now: datetime | None = None,
    ) -> list[GenerationJob]:
        """Закрывает RUNNING job'ы с просроченной арендой (crash recovery).

        Признак «застрял» — истёкшая аренда, а не время в статусе: аренда
        продлевается работающим исполнителем, поэтому её просрочка означает
        именно исчезновение исполнителя, а не долгую генерацию.

        RUNNING без аренды не трогаем: такой job создан не воркером, а
        синхронным запросом (Telegram-пайплайн, Admin API), который живёт в
        другом процессе и не обязан отчитываться воркеру. Это осознанное
        ограничение, оно снимается вместе с переносом генерации в worker.
        """
        moment = now or _utcnow()
        # Решение «планировать повтор» принимается по коду, который записывает
        # сама recovery, а не по коду прошлого отказа: прежний код относится к
        # предыдущей попытке и здесь уже неактуален.
        retryable = error_kind(error_code) is GenerationErrorKind.TRANSIENT
        try:
            async with self._sessions() as session:
                async with session.begin():
                    rows = (
                        await session.execute(
                            select(GenerationJobRow)
                            .where(
                                GenerationJobRow.status
                                == GenerationJobStatus.RUNNING.value,
                                GenerationJobRow.lease_expires_at.is_not(None),
                                GenerationJobRow.lease_expires_at <= moment,
                            )
                            .order_by(GenerationJobRow.lease_expires_at)
                            .limit(limit)
                            .with_for_update(skip_locked=True)
                        )
                    ).scalars().all()

                    released: list[GenerationJob] = []
                    for row in rows:
                        job = _to_domain(row)
                        job.fail(
                            error_code=error_code,
                            message=message,
                            next_attempt_at=next_attempt_at if retryable else None,
                        )
                        row.status = job.status.value
                        row.last_error_code = job.last_error_code
                        row.last_error_message = job.last_error_message
                        row.completed_at = job.completed_at
                        row.next_attempt_at = job.next_attempt_at
                        row.lease_owner = None
                        row.lease_expires_at = None
                        released.append(job)
                    return released
        except SQLAlchemyError as exc:
            raise ProgramPersistenceError(
                f"Не удалось восстановить зависшие операции: {exc.__class__.__name__}"
            ) from exc

    async def renew_lease(
        self, job: GenerationJob, *, lease_seconds: float, now: datetime | None = None
    ) -> datetime | None:
        """Продлевает аренду. None — job уже отобран у этого владельца.

        Условие по владельцу обязательно: без него продление вернуло бы job
        исполнителю, у которого его уже забрал recovery, и генерация пошла бы
        дважды.
        """
        if job.lease_owner is None:
            return None
        expires = (now or _utcnow()) + timedelta(seconds=lease_seconds)
        try:
            async with self._sessions() as session:
                async with session.begin():
                    updated = (
                        await session.execute(
                            update(GenerationJobRow)
                            .where(
                                GenerationJobRow.id == job.id,
                                GenerationJobRow.status
                                == GenerationJobStatus.RUNNING.value,
                                GenerationJobRow.lease_owner == job.lease_owner,
                            )
                            .values(lease_expires_at=expires)
                            .returning(GenerationJobRow.lease_expires_at)
                        )
                    ).scalar_one_or_none()
        except SQLAlchemyError as exc:
            raise ProgramPersistenceError(
                f"Не удалось продлить аренду операции: {exc.__class__.__name__}"
            ) from exc
        return updated

    async def schedule_retry(
        self, job: GenerationJob, *, next_attempt_at: datetime
    ) -> bool:
        """Назначает повтор уже провалившемуся job.

        Нужен там, где отказ записал не worker: синхронный путь (Telegram,
        Admin API) закрывает job сам и о политике повторов ничего не знает.
        Возвращает False, если job больше не в FAILED или повтор уже назначен —
        второй раз планировать нельзя, иначе одна неудача дала бы две очереди.
        """
        try:
            async with self._sessions() as session:
                async with session.begin():
                    updated = (
                        await session.execute(
                            update(GenerationJobRow)
                            .where(
                                GenerationJobRow.id == job.id,
                                GenerationJobRow.status
                                == GenerationJobStatus.FAILED.value,
                                GenerationJobRow.next_attempt_at.is_(None),
                            )
                            .values(next_attempt_at=next_attempt_at)
                            .returning(GenerationJobRow.id)
                        )
                    ).scalar_one_or_none()
        except SQLAlchemyError as exc:
            raise ProgramPersistenceError(
                f"Не удалось назначить повтор генерации: {exc.__class__.__name__}"
            ) from exc
        return updated is not None

    async def clear_retry(self, job: GenerationJob) -> None:
        """Снимает назначенный повтор: попытки исчерпаны, job окончателен."""
        try:
            async with self._sessions() as session:
                async with session.begin():
                    await session.execute(
                        update(GenerationJobRow)
                        .where(GenerationJobRow.id == job.id)
                        .values(next_attempt_at=None)
                    )
        except SQLAlchemyError as exc:
            raise ProgramPersistenceError(
                f"Не удалось снять повтор генерации: {exc.__class__.__name__}"
            ) from exc

    async def _transition(
        self,
        updated: GenerationJob,
        *,
        expected: GenerationJobStatus,
        values: dict,
    ) -> GenerationJob:
        if updated.id is None:
            raise ProgramPersistenceError("generation job id is empty")
        try:
            async with self._sessions() as session:
                async with session.begin():
                    row = (
                        await session.execute(
                            update(GenerationJobRow)
                            .where(
                                GenerationJobRow.id == updated.id,
                                GenerationJobRow.status == expected.value,
                            )
                            .values(**values)
                            .returning(GenerationJobRow)
                        )
                    ).scalar_one_or_none()
                    if row is not None:
                        return _to_domain(row)

                    # Переход не выполнен: либо job уже в другом состоянии
                    # (другой процесс успел раньше), либо записи нет.
                    actual = (
                        await session.execute(
                            select(GenerationJobRow.status).where(
                                GenerationJobRow.id == updated.id
                            )
                        )
                    ).scalar_one_or_none()
        except SQLAlchemyError as exc:
            raise ProgramPersistenceError(
                f"Не удалось обновить состояние генерации: {exc.__class__.__name__}"
            ) from exc

        if actual is None:
            raise ProgramPersistenceError(
                f"Запись генерации id={updated.id} не найдена"
            )
        raise GenerationJobTransitionError(
            GenerationJobStatus(actual), updated.status
        )
