"""Репозиторий медиа-ассетов упражнений (Stage 5).

Идемпотентный upsert по ключу (exercise_external_id, exercise_source, sequence):
повторный импорт не создаёт дубликатов.
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.domain.media import ExerciseMediaAsset
from src.errors import MediaStorageError
from src.infrastructure.persistence.postgres.models import ExerciseMediaRow


class ExerciseMediaRepository:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._sessions = session_factory

    async def upsert(self, asset: ExerciseMediaAsset) -> None:
        values = {
            "exercise_external_id": asset.exercise_external_id,
            "exercise_source": asset.exercise_source,
            "media_type": asset.media_type,
            "sequence": asset.sequence,
            "storage_key": asset.storage_key,
            "mime_type": asset.mime_type,
            "width": asset.width,
            "height": asset.height,
            "size_bytes": asset.size_bytes,
            "checksum": asset.checksum,
            "source": asset.source,
            "source_url": asset.source_url,
            "license": asset.license,
        }
        try:
            async with self._sessions() as session:
                async with session.begin():
                    stmt = pg_insert(ExerciseMediaRow).values(**values)
                    stmt = stmt.on_conflict_do_update(
                        constraint="uq_exercise_media_external_source_sequence",
                        set_=values,
                    )
                    await session.execute(stmt)
        except SQLAlchemyError as exc:
            raise MediaStorageError(
                f"Не удалось сохранить медиа {asset.exercise_external_id}/{asset.sequence}: {exc}"
            ) from exc

    async def list_for_exercise(
        self, external_id: str, source: str = "leszavr/workout", limit: int | None = None
    ) -> list[ExerciseMediaAsset]:
        stmt = (
            select(ExerciseMediaRow)
            .where(
                ExerciseMediaRow.exercise_external_id == external_id,
                ExerciseMediaRow.exercise_source == source,
            )
            .order_by(ExerciseMediaRow.sequence)
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        try:
            async with self._sessions() as session:
                rows = (await session.execute(stmt)).scalars().all()
        except SQLAlchemyError as exc:
            raise MediaStorageError(f"Ошибка чтения медиа упражнения: {exc}") from exc
        return [_to_domain(r) for r in rows]

    async def bulk_list(self, pairs: list[tuple[str, str]], limit_per_exercise: int | None = None) -> dict[str, list[ExerciseMediaAsset]]:
        """Медиа для набора упражнений за минимальное число запросов.

        Возвращает dict: external_id -> [ассеты по sequence].
        """
        if not pairs:
            return {}
        external_ids = sorted({external_id for external_id, _ in pairs})
        stmt = (
            select(ExerciseMediaRow)
            .where(ExerciseMediaRow.exercise_external_id.in_(external_ids))
            .order_by(ExerciseMediaRow.exercise_external_id, ExerciseMediaRow.sequence)
        )
        try:
            async with self._sessions() as session:
                rows = (await session.execute(stmt)).scalars().all()
        except SQLAlchemyError as exc:
            raise MediaStorageError(f"Ошибка массовой загрузки медиа: {exc}") from exc

        result: dict[str, list[ExerciseMediaAsset]] = {}
        for row in rows:
            bucket = result.setdefault(row.exercise_external_id, [])
            if limit_per_exercise is not None and len(bucket) >= limit_per_exercise:
                continue
            bucket.append(_to_domain(row))
        return result

    async def list_all(self, limit: int = 5000) -> list[ExerciseMediaAsset]:
        try:
            async with self._sessions() as session:
                rows = (
                    await session.execute(
                        select(ExerciseMediaRow)
                        .order_by(ExerciseMediaRow.id)
                        .limit(limit)
                    )
                ).scalars().all()
        except SQLAlchemyError as exc:
            raise MediaStorageError(f"Ошибка чтения всех медиа: {exc}") from exc
        return [_to_domain(r) for r in rows]

    async def count_exercises_with_media(self) -> int:
        try:
            async with self._sessions() as session:
                return (
                    await session.execute(
                        select(func.count(func.distinct(ExerciseMediaRow.exercise_external_id)))
                    )
                ).scalar_one()
        except SQLAlchemyError as exc:
            raise MediaStorageError(f"Ошибка подсчёта упражнений с медиа: {exc}") from exc

    async def count(self) -> int:
        try:
            async with self._sessions() as session:
                return (
                    await session.execute(select(func.count()).select_from(ExerciseMediaRow))
                ).scalar_one()
        except SQLAlchemyError as exc:
            raise MediaStorageError(f"Ошибка подсчёта медиа: {exc}") from exc


def _to_domain(row: ExerciseMediaRow) -> ExerciseMediaAsset:
    return ExerciseMediaAsset(
        id=row.id,
        exercise_external_id=row.exercise_external_id,
        exercise_source=row.exercise_source,
        media_type=row.media_type,
        sequence=row.sequence,
        storage_key=row.storage_key,
        mime_type=row.mime_type,
        width=row.width,
        height=row.height,
        size_bytes=row.size_bytes,
        checksum=row.checksum,
        source=row.source,
        source_url=row.source_url,
        license=row.license,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
