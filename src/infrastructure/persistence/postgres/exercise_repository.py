"""Репозиторий каталога упражнений (PostgreSQL).

Идемпотентный upsert по ключу (external_id, source).

Поиск, фильтрация, сортировка и пагинация выполняются в SQL. Причина не в
производительности: каталог из 873 упражнений можно вычитать целиком, но тогда
«первые 100 по алфавиту» — это ответ на другой вопрос, чем «первые 100 самых
сложных», и фильтр применялся бы к произвольной части каталога.

Списочные поля (`equipment`, `primary_muscles`, ...) объявлены типом `JSON`, а
не `JSONB`. Для `JSON` в PostgreSQL не определены ни оператор вхождения `@>`,
ни сравнение — из-за этого фильтр по оборудованию падал с
`operator does not exist: json ~~ text`. Поэтому такие поля приводятся к
`JSONB` в выражении запроса. Менять тип колонки миграцией здесь не требуется:
это не ускорит выборку по каталогу такого размера, но потребует переписать
таблицу целиком.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import String, cast, func, literal, nulls_last, or_, select
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.domain.exercise import Exercise
from src.errors import ProfilePersistenceError
from src.infrastructure.persistence.postgres.models import (
    ExerciseMediaRow,
    ExerciseRow,
)

# Поля сортировки каталога: белый список вместо имени колонки из запроса.
# Подстановка произвольной строки в ORDER BY — это и инъекция, и утечка схемы
# в публичный контракт.
SORT_FIELDS = {
    "name": ExerciseRow.name,
    "name_ru": ExerciseRow.name_ru,
    "exercise_type": ExerciseRow.exercise_type,
    "difficulty": ExerciseRow.difficulty,
    "force": ExerciseRow.force,
    "mechanic": ExerciseRow.mechanic,
    "created_at": ExerciseRow.created_at,
}
DEFAULT_SORT = "name"

# Порядок сложности: алфавитный порядок (beginner, expert, intermediate) для
# сложности бессмысленен — «продвинутый» оказывался между начальным и средним.
DIFFICULTY_ORDER = ("beginner", "intermediate", "expert")


@dataclass(frozen=True)
class ExerciseQuery:
    """Условия выборки каталога. Пустые поля не ограничивают выборку.

    Списки внутри одного поля соединяются через OR (любое из значений), разные
    поля — через AND: «штанга или гантели» и при этом «базовое».
    """

    search: str | None = None
    exercise_types: tuple[str, ...] = ()
    difficulties: tuple[str, ...] = ()
    equipment: tuple[str, ...] = ()
    primary_muscles: tuple[str, ...] = ()
    forces: tuple[str, ...] = ()
    mechanics: tuple[str, ...] = ()
    # None — упражнения в любом состоянии. Каталог по умолчанию показывает
    # только активные, но администратору нужно видеть и отключённые.
    is_active: bool | None = True
    # None — без ограничения; True/False — только с фотографиями или без.
    has_media: bool | None = None
    # Ограничение выборки набором канонических идентификаторов. Нужно фильтрам,
    # которые вычисляются вне каталога: совместимость с оборудованием и наличие
    # знания о требованиях живут в базе знаний, а не в колонках `exercises`.
    # Пустой набор (не None) означает «ничего не подошло» и должен давать пустой
    # результат, а не игнорироваться: иначе фильтр без совпадений показывал бы
    # весь каталог.
    external_ids: frozenset[str] | None = None


@dataclass
class ExerciseFacets:
    """Число упражнений по каждому значению признака в текущей выборке.

    Считается по той же выборке, что и список: счётчик, не зависящий от
    фильтров, обещал бы результаты, которых после уточнения фильтра нет.
    """

    exercise_types: list[dict] = field(default_factory=list)
    difficulties: list[dict] = field(default_factory=list)
    equipment: list[dict] = field(default_factory=list)
    primary_muscles: list[dict] = field(default_factory=list)
    forces: list[dict] = field(default_factory=list)
    mechanics: list[dict] = field(default_factory=list)


def _json_array(column):
    """Списочное JSON-поле как JSONB: для JSON операторы вхождения не определены."""
    return cast(column, JSONB)


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
        """Совместимый одиночный фильтр: используется генерацией и старым API."""
        _, items = await self.search(
            ExerciseQuery(
                search=search,
                exercise_types=(exercise_type,) if exercise_type else (),
                difficulties=(difficulty,) if difficulty else (),
                equipment=(equipment,) if equipment else (),
            ),
            limit=limit,
            offset=offset,
        )
        return items

    # --- Выборка каталога ------------------------------------------------------------

    def _conditions(self, query: ExerciseQuery) -> list:
        conditions: list = []
        if query.is_active is not None:
            conditions.append(ExerciseRow.is_active.is_(query.is_active))
        if query.search:
            # Экранируем `%` и `_`: без этого поиск по «100%» вернул бы всё.
            like = "%" + query.search.replace("\\", "\\\\").replace(
                "%", "\\%"
            ).replace("_", "\\_") + "%"
            conditions.append(
                or_(
                    ExerciseRow.name.ilike(like, escape="\\"),
                    ExerciseRow.name_ru.ilike(like, escape="\\"),
                    ExerciseRow.external_id.ilike(like, escape="\\"),
                )
            )
        if query.exercise_types:
            conditions.append(ExerciseRow.exercise_type.in_(query.exercise_types))
        if query.difficulties:
            conditions.append(ExerciseRow.difficulty.in_(query.difficulties))
        if query.forces:
            conditions.append(ExerciseRow.force.in_(query.forces))
        if query.mechanics:
            conditions.append(ExerciseRow.mechanic.in_(query.mechanics))
        if query.equipment:
            conditions.append(
                or_(
                    *[
                        _json_array(ExerciseRow.equipment).contains([value])
                        for value in query.equipment
                    ]
                )
            )
        if query.primary_muscles:
            conditions.append(
                or_(
                    *[
                        _json_array(ExerciseRow.primary_muscles).contains([value])
                        for value in query.primary_muscles
                    ]
                )
            )
        if query.has_media is not None:
            media_exists = (
                select(ExerciseMediaRow.id)
                .where(
                    ExerciseMediaRow.exercise_external_id == ExerciseRow.external_id,
                    ExerciseMediaRow.exercise_source == ExerciseRow.source,
                )
                .exists()
            )
            conditions.append(media_exists if query.has_media else ~media_exists)
        if query.external_ids is not None:
            if not query.external_ids:
                # Пустой набор — это результат фильтра, а не его отсутствие.
                conditions.append(literal(False))
            else:
                conditions.append(
                    ExerciseRow.external_id.in_(sorted(query.external_ids))
                )
        return conditions

    @staticmethod
    def _order_by(sort_by: str, descending: bool):
        """ORDER BY по разрешённому полю плюс стабильный вторичный ключ.

        Без вторичного ключа строки с равным значением (например, все
        `strength`) идут в произвольном порядке базы, и страницы пагинации
        пересекаются между запросами.
        """
        if sort_by == "difficulty":
            # Сложность сортируется по смыслу, а не по алфавиту.
            primary = func.array_position(
                cast(DIFFICULTY_ORDER, ARRAY(String)), ExerciseRow.difficulty
            )
        else:
            primary = SORT_FIELDS.get(sort_by, SORT_FIELDS[DEFAULT_SORT])
        ordered = primary.desc() if descending else primary.asc()
        return nulls_last(ordered), ExerciseRow.id.asc()

    async def search_rows(
        self,
        query: ExerciseQuery,
        *,
        limit: int = 50,
        offset: int = 0,
        sort_by: str = DEFAULT_SORT,
        descending: bool = False,
    ) -> tuple[int, list[ExerciseRow]]:
        """Страница каталога строками таблицы и полное число под фильтром.

        Нужна там, где вызывающей стороне требуется surrogate `id`: доменная
        модель `Exercise` его не содержит, потому что каноническим
        идентификатором упражнения является пара (external_id, source).
        Добавлять surrogate в домен ради ссылки в интерфейсе не требуется.
        """
        conditions = self._conditions(query)
        stmt = (
            select(ExerciseRow)
            .where(*conditions)
            .order_by(*self._order_by(sort_by, descending))
            .limit(limit)
            .offset(offset)
        )
        count_stmt = select(func.count()).select_from(ExerciseRow).where(*conditions)
        try:
            async with self._sessions() as session:
                total = (await session.execute(count_stmt)).scalar_one()
                rows = (await session.execute(stmt)).scalars().all()
        except SQLAlchemyError as exc:
            raise ProfilePersistenceError(f"Ошибка списка упражнений: {exc}") from exc
        return total, list(rows)

    async def search(
        self,
        query: ExerciseQuery,
        *,
        limit: int = 50,
        offset: int = 0,
        sort_by: str = DEFAULT_SORT,
        descending: bool = False,
    ) -> tuple[int, list[Exercise]]:
        """Страница каталога доменными моделями и полное число под фильтром."""
        total, rows = await self.search_rows(
            query,
            limit=limit,
            offset=offset,
            sort_by=sort_by,
            descending=descending,
        )
        return total, [_to_domain(row) for row in rows]

    # --- Счётчики значений признаков ---------------------------------------------------

    async def facets(self, query: ExerciseQuery) -> ExerciseFacets:
        """Число упражнений по каждому значению признака в текущей выборке.

        Скалярные признаки считаются группировкой, списочные — разворотом
        массива в строки: у упражнения несколько мышц и несколько единиц
        оборудования, и «сколько упражнений со штангой» не равно числу строк.

        Сумма счётчиков по списочному признаку намеренно больше числа
        упражнений: одно упражнение попадает в несколько групп.
        """
        conditions = self._conditions(query)
        try:
            async with self._sessions() as session:
                return ExerciseFacets(
                    exercise_types=await self._scalar_facet(
                        session, ExerciseRow.exercise_type, conditions
                    ),
                    difficulties=await self._scalar_facet(
                        session, ExerciseRow.difficulty, conditions
                    ),
                    forces=await self._scalar_facet(
                        session, ExerciseRow.force, conditions
                    ),
                    mechanics=await self._scalar_facet(
                        session, ExerciseRow.mechanic, conditions
                    ),
                    equipment=await self._array_facet(
                        session, ExerciseRow.equipment, conditions
                    ),
                    primary_muscles=await self._array_facet(
                        session, ExerciseRow.primary_muscles, conditions
                    ),
                )
        except SQLAlchemyError as exc:
            raise ProfilePersistenceError(
                f"Ошибка счётчиков каталога: {exc}"
            ) from exc

    @staticmethod
    async def _scalar_facet(session, column, conditions: list) -> list[dict]:
        stmt = (
            select(column.label("value"), func.count().label("count"))
            .where(*conditions, column.is_not(None))
            .group_by(column)
            .order_by(func.count().desc(), column)
        )
        rows = (await session.execute(stmt)).all()
        return [{"value": row.value, "count": row.count} for row in rows]

    @staticmethod
    async def _array_facet(session, column, conditions: list) -> list[dict]:
        value = func.jsonb_array_elements_text(_json_array(column)).table_valued(
            "value"
        ).lateral("facet_value")
        stmt = (
            select(value.c.value.label("value"), func.count().label("count"))
            .select_from(ExerciseRow.__table__.join(value, literal(True)))
            .where(*conditions)
            .group_by(value.c.value)
            .order_by(func.count().desc(), value.c.value)
        )
        rows = (await session.execute(stmt)).all()
        return [{"value": row.value, "count": row.count} for row in rows]

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
