"""Репозиторий каталога упражнений (PostgreSQL).

Идемпотентный upsert по ключу (external_id, source).
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.domain.exercise import Exercise
from src.errors import ProfilePersistenceError
from src.infrastructure.persistence.postgres.models import ExerciseRow


class ExerciseRepository:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._sessions = session_factory

    async def upsert(self, exercise: Exercise) -> None:
        """Создаёт или обновляет упражнение по (external_id, source)."""
        values = {
            "external_id": exercise.external_id,
            "source": exercise.source,
            "source_version": exercise.source_version,
            "name": exercise.name,
            "name_ru": exercise.name_ru,
            "aliases": exercise.aliases,
            "description": exercise.description,
            "technique": exercise.technique,
            "technique_ru": exercise.technique_ru,
            "common_mistakes": exercise.common_mistakes,
            "primary_muscles": exercise.primary_muscles,
            "secondary_muscles": exercise.secondary_muscles,
            "equipment": exercise.equipment,
            "exercise_type": exercise.exercise_type,
            "difficulty": exercise.difficulty,
            "force": exercise.force,
            "mechanic": exercise.mechanic,
            "contraindications": exercise.contraindications,
            "limitations": exercise.limitations,
            "images": exercise.images,
            "is_active": exercise.is_active,
        }
        try:
            async with self._sessions() as session:
                async with session.begin():
                    stmt = pg_insert(ExerciseRow).values(**values)
                    update = dict(values)
                    update.pop("external_id", None)
                    update.pop("source", None)
                    stmt = stmt.on_conflict_do_update(
                        constraint="uq_exercise_external_source", set_=update
                    )
                    await session.execute(stmt)
        except SQLAlchemyError as exc:
            raise ProfilePersistenceError(
                f"Не удалось сохранить упражнение {exercise.external_id}: {exc}"
            ) from exc

    async def get_by_external_id(self, external_id: str, source: str = "leszavr/workout") -> Exercise | None:
        try:
            async with self._sessions() as session:
                row = (
                    await session.execute(
                        select(ExerciseRow).where(
                            ExerciseRow.external_id == external_id,
                            ExerciseRow.source == source,
                        )
                    )
                ).scalar_one_or_none()
        except SQLAlchemyError as exc:
            raise ProfilePersistenceError(f"Ошибка чтения упражнения: {exc}") from exc
        return _to_domain(row) if row else None

    async def list(
        self,
        *,
        search: str | None = None,
        exercise_type: str | None = None,
        difficulty: str | None = None,
        equipment: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Exercise]:
        stmt = select(ExerciseRow).where(ExerciseRow.is_active.is_(True))
        if search:
            like = f"%{search}%"
            stmt = stmt.where(ExerciseRow.name.ilike(like) | ExerciseRow.name_ru.ilike(like))
        if exercise_type:
            stmt = stmt.where(ExerciseRow.exercise_type == exercise_type)
        if difficulty:
            stmt = stmt.where(ExerciseRow.difficulty == difficulty)
        if equipment:
            stmt = stmt.where(ExerciseRow.equipment.contains([equipment]))
        stmt = stmt.order_by(ExerciseRow.name).limit(limit).offset(offset)
        try:
            async with self._sessions() as session:
                rows = (await session.execute(stmt)).scalars().all()
        except SQLAlchemyError as exc:
            raise ProfilePersistenceError(f"Ошибка списка упражнений: {exc}") from exc
        return [_to_domain(r) for r in rows]

    async def count(self) -> int:
        try:
            async with self._sessions() as session:
                return (
                    await session.execute(
                        select(func.count()).select_from(ExerciseRow).where(
                            ExerciseRow.is_active.is_(True)
                        )
                    )
                ).scalar_one()
        except SQLAlchemyError as exc:
            raise ProfilePersistenceError(f"Ошибка подсчёта упражнений: {exc}") from exc


def _to_domain(row: ExerciseRow) -> Exercise:
    return Exercise(
        external_id=row.external_id,
        source=row.source,
        source_version=row.source_version,
        name=row.name,
        name_ru=row.name_ru,
        aliases=row.aliases or [],
        description=row.description,
        technique=row.technique,
        technique_ru=row.technique_ru,
        common_mistakes=row.common_mistakes,
        primary_muscles=row.primary_muscles or [],
        secondary_muscles=row.secondary_muscles or [],
        equipment=row.equipment or [],
        exercise_type=row.exercise_type,
        difficulty=row.difficulty,
        force=row.force,
        mechanic=row.mechanic,
        contraindications=row.contraindications or [],
        limitations=row.limitations or [],
        images=row.images or [],
        is_active=row.is_active,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
