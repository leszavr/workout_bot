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
"""
from __future__ import annotations

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.domain.enums import GenerationJobStatus
from src.domain.generation import (
    GenerationErrorCode,
    GenerationJob,
    GenerationJobTransitionError,
    GenerationTrigger,
)
from src.errors import ProgramPersistenceError
from src.infrastructure.persistence.postgres.models import GenerationJobRow


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
        """PENDING → RUNNING. Условие перехода проверяет сам UPDATE."""
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
            },
        )

    async def mark_failed(
        self,
        job: GenerationJob,
        *,
        error_code: GenerationErrorCode | str,
        message: str,
    ) -> GenerationJob:
        """RUNNING → FAILED. Программа при этом не создаётся."""
        updated = job.model_copy(deep=True)
        updated.fail(error_code=error_code, message=message)
        return await self._transition(
            updated,
            expected=GenerationJobStatus.RUNNING,
            values={
                "status": updated.status.value,
                "last_error_code": updated.last_error_code,
                "last_error_message": updated.last_error_message,
                "completed_at": updated.completed_at,
            },
        )

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
