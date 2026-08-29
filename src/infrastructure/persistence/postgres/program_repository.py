"""PostgresProgramRepository — реализация ProgramRepository поверх PostgreSQL.

Pydantic WorkoutProgram → строгая валидация → PostgreSQL JSONB.
Каждая версия программы — отдельная строка (program_id, version);
исторические версии не перезаписываются.
"""
from __future__ import annotations

from sqlalchemy import delete, desc, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import aliased

from src.domain.program import WorkoutProgram
from src.errors import ProgramPersistenceError
from src.infrastructure.persistence.postgres.models import WorkoutProgramRow
from src.infrastructure.persistence.program_repository import ProgramRepository


class PostgresProgramRepository(ProgramRepository):
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._sessions = session_factory

    async def save(self, program: WorkoutProgram) -> WorkoutProgram:
        if not program.program_id:
            raise ProgramPersistenceError("program_id is empty")
        program.touch()
        payload = program.model_dump(mode="json")
        try:
            async with self._sessions() as session:
                async with session.begin():
                    session.add(
                        WorkoutProgramRow(
                            program_id=program.program_id,
                            profile_id=program.profile_id,
                            version=program.version,
                            status=program.status.value,
                            generation_source=program.generation.source.value,
                            generator_version=program.generation.generator_version,
                            title=program.title,
                            training_days_per_week=program.training_days_per_week,
                            duration_weeks=program.duration_weeks,
                            data=payload,
                        )
                    )
        except SQLAlchemyError as exc:
            raise ProgramPersistenceError(
                f"Не удалось сохранить программу {program.program_id} v{program.version}: {exc}"
            ) from exc
        return program

    async def get(self, program_id: str, version: int | None = None) -> WorkoutProgram | None:
        try:
            async with self._sessions() as session:
                stmt = select(WorkoutProgramRow).where(WorkoutProgramRow.program_id == program_id)
                if version is not None:
                    stmt = stmt.where(WorkoutProgramRow.version == version)
                stmt = stmt.order_by(desc(WorkoutProgramRow.version)).limit(1)
                row = (await session.execute(stmt)).scalar_one_or_none()
        except SQLAlchemyError as exc:
            raise ProgramPersistenceError(f"Ошибка чтения программы: {exc}") from exc
        return _to_domain(row) if row else None

    async def list_versions(self, program_id: str) -> list[WorkoutProgram]:
        try:
            async with self._sessions() as session:
                rows = (
                    await session.execute(
                        select(WorkoutProgramRow)
                        .where(WorkoutProgramRow.program_id == program_id)
                        .order_by(WorkoutProgramRow.version)
                    )
                ).scalars().all()
        except SQLAlchemyError as exc:
            raise ProgramPersistenceError(f"Ошибка чтения версий программы: {exc}") from exc
        return [_to_domain(r) for r in rows]

    async def list_for_profile(self, profile_id: str) -> list[WorkoutProgram]:
        try:
            async with self._sessions() as session:
                rows = (
                    await session.execute(
                        select(WorkoutProgramRow)
                        .where(WorkoutProgramRow.profile_id == profile_id)
                        .order_by(desc(WorkoutProgramRow.version))
                    )
                ).scalars().all()
        except SQLAlchemyError as exc:
            raise ProgramPersistenceError(f"Ошибка чтения программ профиля: {exc}") from exc
        # Оставляем только последнюю версию каждой программы.
        latest: dict[str, WorkoutProgramRow] = {}
        for row in rows:
            if row.program_id not in latest:
                latest[row.program_id] = row
        return [_to_domain(r) for r in latest.values()]

    async def list_all(
        self, limit: int = 50, offset: int = 0
    ) -> tuple[int, list[WorkoutProgram]]:
        try:
            async with self._sessions() as session:
                # Последние версии: строки, для которых не существует строки
                # той же программы с большим номером версии.
                newer = aliased(WorkoutProgramRow)
                newer_exists = (
                    select(newer.id)
                    .where(
                        newer.program_id == WorkoutProgramRow.program_id,
                        newer.version > WorkoutProgramRow.version,
                    )
                    .exists()
                )
                latest_rows = select(WorkoutProgramRow).where(~newer_exists)
                total = (
                    await session.execute(
                        select(func.count()).select_from(latest_rows.subquery())
                    )
                ).scalar_one()
                rows = (
                    await session.execute(
                        latest_rows.order_by(desc(WorkoutProgramRow.created_at))
                        .limit(limit)
                        .offset(offset)
                    )
                ).scalars().all()
        except SQLAlchemyError as exc:
            raise ProgramPersistenceError(f"Ошибка списка программ: {exc}") from exc
        return total, [_to_domain(r) for r in rows]

    async def next_version(self, profile_id: str) -> int:
        try:
            async with self._sessions() as session:
                result = await session.execute(
                    select(func.max(WorkoutProgramRow.version)).where(
                        WorkoutProgramRow.profile_id == profile_id
                    )
                )
                max_version = result.scalar_one()
        except SQLAlchemyError as exc:
            raise ProgramPersistenceError(f"Ошибка получения версии: {exc}") from exc
        return (max_version or 0) + 1

    async def count(self) -> int:
        total, _ = await self.list_all(limit=1, offset=0)
        return total

    async def delete(self, program_id: str) -> int:
        """Удаляет все версии программы.

        Hard delete: программа целиком выводится из системы вместе с историей
        версий. Частичное удаление версий не поддерживается — оно оставило бы
        программу с дырами в истории и рассинхронизировало `next_version`.
        """
        try:
            async with self._sessions() as session:
                async with session.begin():
                    result = await session.execute(
                        delete(WorkoutProgramRow).where(
                            WorkoutProgramRow.program_id == program_id
                        )
                    )
                    return int(result.rowcount or 0)
        except SQLAlchemyError as exc:
            raise ProgramPersistenceError(
                f"Не удалось удалить программу {program_id}: {exc}"
            ) from exc

    async def delete_for_profile(self, profile_id: str) -> int:
        """Удаляет все программы профиля (все их версии)."""
        try:
            async with self._sessions() as session:
                async with session.begin():
                    result = await session.execute(
                        delete(WorkoutProgramRow).where(
                            WorkoutProgramRow.profile_id == profile_id
                        )
                    )
                    return int(result.rowcount or 0)
        except SQLAlchemyError as exc:
            raise ProgramPersistenceError(
                f"Не удалось удалить программы профиля {profile_id}: {exc}"
            ) from exc


def _to_domain(row: WorkoutProgramRow) -> WorkoutProgram:
    return WorkoutProgram.model_validate(row.data)
