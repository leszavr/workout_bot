"""Репозиторий словаря оборудования и профилей доступного оборудования.

Словарь и профили лежат в одном модуле, потому что читаются вместе: проверка
совместимости требует и списка оборудования с возможностями, и того, что
фактически есть у пользователя. Разделять их значило бы делать два обхода одних
и тех же связей.

Знание об оборудовании читается целиком (`load_index`), а не построчно. Словарь
измеряется десятками записей, а compatibility engine нужен полный граф
«оборудование → возможности» для любого упражнения: выборка по одному ID
превратила бы одну проверку в N запросов.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.domain.equipment import (
    AliasMatchMode,
    EquipmentAlias,
    EquipmentAvailability,
    EquipmentCapability,
    EquipmentItem,
    EquipmentOwnerType,
    EquipmentProfile,
    EquipmentProfileItem,
    EquipmentUsage,
    KnowledgeConfidence,
    KnowledgeSource,
)
from src.errors import ProfilePersistenceError
from src.infrastructure.persistence.postgres.models import (
    EquipmentAliasRow,
    EquipmentCapabilityLinkRow,
    EquipmentCapabilityRow,
    EquipmentItemRow,
    EquipmentProfileItemRow,
    EquipmentProfileRow,
    ExerciseEquipmentRequirementRow,
)


class EquipmentKnowledgeError(Exception):
    """Нарушение правил словаря оборудования (не ошибка инфраструктуры)."""


class EquipmentInUseError(EquipmentKnowledgeError):
    """Удаление невозможно: на запись ссылаются требования или профили."""


def _persistence_error(exc: SQLAlchemyError, what: str) -> ProfilePersistenceError:
    return ProfilePersistenceError(f"{what}: {exc.__class__.__name__}")


@dataclass(frozen=True)
class EquipmentQuery:
    """Условия выборки словаря. Пустые поля не ограничивают выборку."""

    search: str | None = None
    categories: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    # None — оборудование в любом состоянии; по умолчанию видно только активное.
    is_active: bool | None = True
    # None — без ограничения; True/False — только используемое или только
    # неиспользуемое ни одним упражнением.
    in_use: bool | None = None


@dataclass
class EquipmentIndex:
    """Словарь оборудования в форме, пригодной для сопоставления и проверок.

    Загружается одним обходом: compatibility engine и нормализация свободного
    текста обращаются к словарю на каждое упражнение и на каждую фразу, и
    повторные запросы к БД здесь были бы обходом отсутствующего кэша.
    """

    items: dict[str, EquipmentItem] = field(default_factory=dict)
    capabilities: dict[str, EquipmentCapability] = field(default_factory=dict)
    # capability_id -> оборудование, которое эту возможность даёт.
    providers: dict[str, set[str]] = field(default_factory=dict)
    # Родовое оборудование -> его частные случаи, включая транзитивные.
    # Требование `resistance_machine` закрывает любой из них.
    specializations: dict[str, set[str]] = field(default_factory=dict)
    # Нормализованный синоним -> оборудование (одно значение может указывать на
    # несколько единиц: «мяч» — это и медбол, и фитбол).
    exact_aliases: dict[str, set[str]] = field(default_factory=dict)
    stem_aliases: dict[str, set[str]] = field(default_factory=dict)

    def capabilities_of(self, equipment_id: str) -> set[str]:
        item = self.items.get(equipment_id)
        return set(item.capabilities) if item else set()

    def specializations_of(self, equipment_id: str) -> set[str]:
        return self.specializations.get(equipment_id, set())

    def exists(self, equipment_id: str) -> bool:
        return equipment_id in self.items

    def active_ids(self) -> set[str]:
        return {k for k, v in self.items.items() if v.is_active}


class EquipmentRepository:
    """Чтение и изменение контролируемого словаря оборудования."""

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._sessions = session_factory

    # --- Индекс словаря ------------------------------------------------------

    async def load_index(self, *, include_inactive: bool = True) -> EquipmentIndex:
        """Читает словарь целиком: оборудование, возможности и синонимы."""
        try:
            async with self._sessions() as session:
                capability_rows = (
                    await session.execute(select(EquipmentCapabilityRow))
                ).scalars().all()
                item_stmt = select(EquipmentItemRow)
                if not include_inactive:
                    item_stmt = item_stmt.where(EquipmentItemRow.is_active.is_(True))
                item_rows = (await session.execute(item_stmt)).scalars().all()
                link_rows = (
                    await session.execute(select(EquipmentCapabilityLinkRow))
                ).scalars().all()
                alias_rows = (
                    await session.execute(select(EquipmentAliasRow))
                ).scalars().all()
        except SQLAlchemyError as exc:
            raise _persistence_error(exc, "Ошибка чтения словаря оборудования") from exc

        index = EquipmentIndex()
        for row in capability_rows:
            index.capabilities[row.capability_id] = _capability_to_domain(row)

        item_capabilities: dict[str, list[str]] = {}
        for link in link_rows:
            item_capabilities.setdefault(link.equipment_id, []).append(
                link.capability_id
            )
            index.providers.setdefault(link.capability_id, set()).add(link.equipment_id)

        item_aliases: dict[str, list[EquipmentAlias]] = {}
        for alias_row in alias_rows:
            normalized = normalize_alias(alias_row.alias)
            if not normalized:
                continue
            mode = _alias_mode(alias_row.match_mode)
            bucket = (
                index.exact_aliases
                if mode is AliasMatchMode.EXACT
                else index.stem_aliases
            )
            bucket.setdefault(normalized, set()).add(alias_row.equipment_id)
            item_aliases.setdefault(alias_row.equipment_id, []).append(
                EquipmentAlias(
                    alias=alias_row.alias,
                    match_mode=mode,
                    source=_source(alias_row.source),
                )
            )

        for row in item_rows:
            index.items[row.equipment_id] = _item_to_domain(
                row,
                capabilities=sorted(item_capabilities.get(row.equipment_id, [])),
                aliases=sorted(
                    item_aliases.get(row.equipment_id, []), key=lambda a: a.alias
                ),
            )
        _build_specializations(index)
        return index

    # --- Возможности ---------------------------------------------------------

    async def list_capabilities(
        self, *, include_inactive: bool = True
    ) -> list[EquipmentCapability]:
        stmt = select(EquipmentCapabilityRow).order_by(
            EquipmentCapabilityRow.capability_id
        )
        if not include_inactive:
            stmt = stmt.where(EquipmentCapabilityRow.is_active.is_(True))
        try:
            async with self._sessions() as session:
                rows = (await session.execute(stmt)).scalars().all()
        except SQLAlchemyError as exc:
            raise _persistence_error(exc, "Ошибка чтения возможностей") from exc
        return [_capability_to_domain(r) for r in rows]

    # --- Оборудование --------------------------------------------------------

    async def get(self, equipment_id: str) -> EquipmentItem | None:
        try:
            async with self._sessions() as session:
                row = (
                    await session.execute(
                        select(EquipmentItemRow).where(
                            EquipmentItemRow.equipment_id == equipment_id
                        )
                    )
                ).scalar_one_or_none()
                if row is None:
                    return None
                return await self._hydrate(session, row)
        except SQLAlchemyError as exc:
            raise _persistence_error(exc, "Ошибка чтения оборудования") from exc

    async def search(
        self,
        query: EquipmentQuery,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[int, list[EquipmentUsage]]:
        """Страница словаря вместе с числом связанных упражнений.

        Счётчик считается в SQL: без него администратор не видит, какая запись
        словаря фактически используется, а какая заведена и забыта.
        """
        usage = (
            select(func.count(func.distinct(ExerciseEquipmentRequirementRow.id)))
            .where(
                ExerciseEquipmentRequirementRow.equipment_id
                == EquipmentItemRow.equipment_id
            )
            .correlate(EquipmentItemRow)
            .scalar_subquery()
        )
        conditions = self._conditions(query, usage)
        stmt = (
            select(EquipmentItemRow, usage.label("exercise_count"))
            .where(*conditions)
            .order_by(EquipmentItemRow.category, EquipmentItemRow.equipment_id)
            .limit(limit)
            .offset(offset)
        )
        count_stmt = (
            select(func.count()).select_from(EquipmentItemRow).where(*conditions)
        )
        try:
            async with self._sessions() as session:
                total = (await session.execute(count_stmt)).scalar_one()
                rows = (await session.execute(stmt)).all()
                result = [
                    EquipmentUsage(
                        item=await self._hydrate(session, row[0]),
                        exercise_count=row[1] or 0,
                    )
                    for row in rows
                ]
        except SQLAlchemyError as exc:
            raise _persistence_error(exc, "Ошибка списка оборудования") from exc
        return total, result

    @staticmethod
    def _conditions(query: EquipmentQuery, usage) -> list:
        conditions: list = []
        if query.is_active is not None:
            conditions.append(EquipmentItemRow.is_active.is_(query.is_active))
        if query.search:
            like = "%" + _escape_like(query.search) + "%"
            conditions.append(
                EquipmentItemRow.name.ilike(like, escape="\\")
                | EquipmentItemRow.name_ru.ilike(like, escape="\\")
                | EquipmentItemRow.equipment_id.ilike(like, escape="\\")
                | select(EquipmentAliasRow.id)
                .where(
                    EquipmentAliasRow.equipment_id == EquipmentItemRow.equipment_id,
                    EquipmentAliasRow.alias.ilike(like, escape="\\"),
                )
                .exists()
            )
        if query.categories:
            conditions.append(EquipmentItemRow.category.in_(query.categories))
        for capability_id in query.capabilities:
            # AND между возможностями: «наклон и регулировка» — это одно
            # оборудование с обеими возможностями, а не два разных.
            conditions.append(
                select(EquipmentCapabilityLinkRow.id)
                .where(
                    EquipmentCapabilityLinkRow.equipment_id
                    == EquipmentItemRow.equipment_id,
                    EquipmentCapabilityLinkRow.capability_id == capability_id,
                )
                .exists()
            )
        if query.in_use is not None:
            conditions.append(usage > 0 if query.in_use else usage == 0)
        return conditions

    async def _hydrate(self, session, row: EquipmentItemRow) -> EquipmentItem:
        capabilities = (
            await session.execute(
                select(EquipmentCapabilityLinkRow.capability_id)
                .where(EquipmentCapabilityLinkRow.equipment_id == row.equipment_id)
                .order_by(EquipmentCapabilityLinkRow.capability_id)
            )
        ).scalars().all()
        alias_rows = (
            await session.execute(
                select(EquipmentAliasRow)
                .where(EquipmentAliasRow.equipment_id == row.equipment_id)
                .order_by(EquipmentAliasRow.alias)
            )
        ).scalars().all()
        return _item_to_domain(
            row,
            capabilities=list(capabilities),
            aliases=[
                EquipmentAlias(
                    alias=a.alias,
                    match_mode=_alias_mode(a.match_mode),
                    source=_source(a.source),
                )
                for a in alias_rows
            ],
        )

    async def upsert(self, item: EquipmentItem) -> EquipmentItem:
        """Создаёт или полностью заменяет запись словаря.

        Возможности и синонимы заменяются целиком: частичное обновление
        потребовало бы отдельного контракта «добавить/убрать», а админка
        передаёт итоговое состояние записи.
        """
        try:
            async with self._sessions() as session:
                async with session.begin():
                    await self._validate_capabilities(session, item.capabilities)
                    row = (
                        await session.execute(
                            select(EquipmentItemRow)
                            .where(EquipmentItemRow.equipment_id == item.equipment_id)
                            .with_for_update()
                        )
                    ).scalar_one_or_none()
                    if row is None:
                        row = EquipmentItemRow(equipment_id=item.equipment_id)
                        session.add(row)
                    row.name = item.name
                    row.name_ru = item.name_ru
                    row.category = item.category
                    row.description = item.description
                    row.specializes = item.specializes
                    row.manufacturer = item.manufacturer
                    row.model_name = item.model_name
                    row.attributes = item.attributes
                    row.source = item.source.value
                    row.is_active = item.is_active
                    await session.flush()
                    # `updated_at` пересчитывается сервером (onupdate), поэтому
                    # после flush атрибут помечен устаревшим. Без явного refresh
                    # его чтение вызвало бы отложенную загрузку вне async-контекста
                    # и падало бы с MissingGreenlet.
                    await session.refresh(row)

                    await session.execute(
                        delete(EquipmentCapabilityLinkRow).where(
                            EquipmentCapabilityLinkRow.equipment_id == item.equipment_id
                        )
                    )
                    for capability_id in dict.fromkeys(item.capabilities):
                        session.add(
                            EquipmentCapabilityLinkRow(
                                equipment_id=item.equipment_id,
                                capability_id=capability_id,
                            )
                        )
                    await session.execute(
                        delete(EquipmentAliasRow).where(
                            EquipmentAliasRow.equipment_id == item.equipment_id
                        )
                    )
                    seen: set[str] = set()
                    for alias in item.aliases:
                        normalized = normalize_alias(alias.alias)
                        if not normalized or normalized in seen:
                            continue
                        seen.add(normalized)
                        session.add(
                            EquipmentAliasRow(
                                equipment_id=item.equipment_id,
                                alias=normalized,
                                match_mode=alias.match_mode.value,
                                source=alias.source.value,
                            )
                        )
                    await session.flush()
                    return await self._hydrate(session, row)
        except IntegrityError as exc:
            raise EquipmentKnowledgeError(
                "Ссылка на несуществующую возможность или дубликат синонима"
            ) from exc
        except SQLAlchemyError as exc:
            raise _persistence_error(exc, "Не удалось сохранить оборудование") from exc

    @staticmethod
    async def _validate_capabilities(session, capabilities: list[str]) -> None:
        if not capabilities:
            return
        known = set(
            (
                await session.execute(
                    select(EquipmentCapabilityRow.capability_id).where(
                        EquipmentCapabilityRow.capability_id.in_(capabilities)
                    )
                )
            ).scalars().all()
        )
        unknown = sorted(set(capabilities) - known)
        if unknown:
            raise EquipmentKnowledgeError(
                f"Неизвестные возможности: {', '.join(unknown)}"
            )

    async def deactivate(self, equipment_id: str) -> bool:
        """Скрывает оборудование, не разрывая существующие ссылки.

        Деактивация, а не удаление — основной способ вывести запись из
        обращения: требования упражнений, ссылающиеся на неё, остаются
        историческим фактом.
        """
        try:
            async with self._sessions() as session:
                async with session.begin():
                    row = (
                        await session.execute(
                            select(EquipmentItemRow)
                            .where(EquipmentItemRow.equipment_id == equipment_id)
                            .with_for_update()
                        )
                    ).scalar_one_or_none()
                    if row is None:
                        return False
                    row.is_active = False
                    return True
        except SQLAlchemyError as exc:
            raise _persistence_error(exc, "Не удалось деактивировать оборудование") from exc

    async def delete(self, equipment_id: str) -> bool:
        """Удаляет запись словаря, если на неё никто не ссылается."""
        try:
            async with self._sessions() as session:
                async with session.begin():
                    used = (
                        await session.execute(
                            select(func.count())
                            .select_from(ExerciseEquipmentRequirementRow)
                            .where(
                                ExerciseEquipmentRequirementRow.equipment_id
                                == equipment_id
                            )
                        )
                    ).scalar_one()
                    in_profiles = (
                        await session.execute(
                            select(func.count())
                            .select_from(EquipmentProfileItemRow)
                            .where(EquipmentProfileItemRow.equipment_id == equipment_id)
                        )
                    ).scalar_one()
                    if used or in_profiles:
                        raise EquipmentInUseError(
                            f"Оборудование используется: упражнений — {used}, "
                            f"в профилях — {in_profiles}. Используйте деактивацию."
                        )
                    result = await session.execute(
                        delete(EquipmentItemRow).where(
                            EquipmentItemRow.equipment_id == equipment_id
                        )
                    )
                    return bool(result.rowcount)
        except IntegrityError as exc:
            raise EquipmentInUseError(
                "Оборудование используется и не может быть удалено"
            ) from exc
        except SQLAlchemyError as exc:
            raise _persistence_error(exc, "Не удалось удалить оборудование") from exc

    async def categories(self) -> list[dict]:
        """Категории словаря со счётчиками: наполнение фильтров админки."""
        stmt = (
            select(EquipmentItemRow.category, func.count().label("count"))
            .group_by(EquipmentItemRow.category)
            .order_by(EquipmentItemRow.category)
        )
        try:
            async with self._sessions() as session:
                rows = (await session.execute(stmt)).all()
        except SQLAlchemyError as exc:
            raise _persistence_error(exc, "Ошибка категорий оборудования") from exc
        return [{"value": r[0], "count": r[1]} for r in rows]


class EquipmentProfileRepository:
    """Профили фактически доступного оборудования."""

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._sessions = session_factory

    async def get(self, profile_key: str) -> EquipmentProfile | None:
        try:
            async with self._sessions() as session:
                row = (
                    await session.execute(
                        select(EquipmentProfileRow).where(
                            EquipmentProfileRow.profile_key == profile_key
                        )
                    )
                ).scalar_one_or_none()
                if row is None:
                    return None
                items = (
                    await session.execute(
                        select(EquipmentProfileItemRow)
                        .where(EquipmentProfileItemRow.profile_id == row.id)
                        .order_by(EquipmentProfileItemRow.equipment_id)
                    )
                ).scalars().all()
        except SQLAlchemyError as exc:
            raise _persistence_error(exc, "Ошибка чтения профиля оборудования") from exc
        return _profile_to_domain(row, items)

    async def list(
        self, *, owner_type: EquipmentOwnerType | None = None
    ) -> list[EquipmentProfile]:
        stmt = select(EquipmentProfileRow).order_by(EquipmentProfileRow.profile_key)
        if owner_type is not None:
            stmt = stmt.where(EquipmentProfileRow.owner_type == owner_type.value)
        try:
            async with self._sessions() as session:
                rows = (await session.execute(stmt)).scalars().all()
                result = []
                for row in rows:
                    items = (
                        await session.execute(
                            select(EquipmentProfileItemRow)
                            .where(EquipmentProfileItemRow.profile_id == row.id)
                            .order_by(EquipmentProfileItemRow.equipment_id)
                        )
                    ).scalars().all()
                    result.append(_profile_to_domain(row, items))
        except SQLAlchemyError as exc:
            raise _persistence_error(exc, "Ошибка списка профилей оборудования") from exc
        return result

    async def upsert(self, profile: EquipmentProfile) -> EquipmentProfile:
        """Создаёт или заменяет профиль целиком по `profile_key`."""
        try:
            async with self._sessions() as session:
                async with session.begin():
                    row = (
                        await session.execute(
                            select(EquipmentProfileRow)
                            .where(
                                EquipmentProfileRow.profile_key == profile.profile_key
                            )
                            .with_for_update()
                        )
                    ).scalar_one_or_none()
                    if row is None:
                        row = EquipmentProfileRow(profile_key=profile.profile_key)
                        session.add(row)
                    row.owner_type = profile.owner_type.value
                    row.owner_ref = profile.owner_ref
                    row.name = profile.name
                    row.assume_unlisted_unavailable = profile.assume_unlisted_unavailable
                    row.source = profile.source.value
                    row.notes = profile.notes
                    row.is_active = profile.is_active
                    await session.flush()
                    # `updated_at` пересчитывается сервером (onupdate) и после
                    # flush помечен устаревшим: без refresh его чтение ушло бы
                    # в отложенную загрузку вне async-контекста.
                    await session.refresh(row)

                    await session.execute(
                        delete(EquipmentProfileItemRow).where(
                            EquipmentProfileItemRow.profile_id == row.id
                        )
                    )
                    seen: set[str] = set()
                    for item in profile.items:
                        if item.equipment_id in seen:
                            continue
                        seen.add(item.equipment_id)
                        session.add(
                            EquipmentProfileItemRow(
                                profile_id=row.id,
                                equipment_id=item.equipment_id,
                                quantity=item.quantity,
                                availability=item.availability.value,
                                confidence=item.confidence.value,
                                extra_capabilities=list(
                                    dict.fromkeys(item.extra_capabilities)
                                ),
                                source=item.source.value,
                                source_ref=item.source_ref,
                                notes=item.notes,
                            )
                        )
                    await session.flush()
                    items = (
                        await session.execute(
                            select(EquipmentProfileItemRow)
                            .where(EquipmentProfileItemRow.profile_id == row.id)
                            .order_by(EquipmentProfileItemRow.equipment_id)
                        )
                    ).scalars().all()
                    return _profile_to_domain(row, items)
        except IntegrityError as exc:
            raise EquipmentKnowledgeError(
                "Профиль ссылается на неизвестное оборудование"
            ) from exc
        except SQLAlchemyError as exc:
            raise _persistence_error(exc, "Не удалось сохранить профиль") from exc

    async def delete(self, profile_key: str) -> bool:
        try:
            async with self._sessions() as session:
                async with session.begin():
                    result = await session.execute(
                        delete(EquipmentProfileRow).where(
                            EquipmentProfileRow.profile_key == profile_key
                        )
                    )
                    return bool(result.rowcount)
        except SQLAlchemyError as exc:
            raise _persistence_error(exc, "Не удалось удалить профиль") from exc


# --- Преобразования и нормализация -------------------------------------------


def normalize_alias(value: str) -> str:
    """Приводит синоним к сопоставимому виду: регистр, пробелы, «ё»."""
    return " ".join(value.strip().lower().replace("ё", "е").split())


def _build_specializations(index: EquipmentIndex) -> None:
    """Заполняет «родовое → все его частные случаи», включая транзитивные.

    Транзитивность нужна, потому что цепочка законна: `treadmill` →
    `cardio_machine`, и если однажды появится `cardio_machine` → более общее
    понятие, требование верхнего уровня обязано закрываться дорожкой. Цикл в
    данных не приводит к зависанию: обход помечает посещённые записи.
    """
    for equipment_id, item in index.items.items():
        seen: set[str] = set()
        parent = item.specializes
        while parent and parent not in seen and parent in index.items:
            seen.add(parent)
            index.specializations.setdefault(parent, set()).add(equipment_id)
            parent = index.items[parent].specializes


def _escape_like(value: str) -> str:
    # Без экранирования поиск по «100%» вернул бы весь словарь.
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _alias_mode(raw: str) -> AliasMatchMode:
    try:
        return AliasMatchMode(raw)
    except ValueError:
        return AliasMatchMode.EXACT


def _source(raw: str) -> KnowledgeSource:
    try:
        return KnowledgeSource(raw)
    except ValueError:
        # Значение могло быть записано более новой версией: чтение не должно
        # падать из-за неизвестного источника.
        return KnowledgeSource.ADMIN


def _capability_to_domain(row: EquipmentCapabilityRow) -> EquipmentCapability:
    return EquipmentCapability(
        capability_id=row.capability_id,
        name=row.name,
        name_ru=row.name_ru,
        description=row.description,
        is_active=row.is_active,
    )


def _item_to_domain(
    row: EquipmentItemRow,
    *,
    capabilities: list[str],
    aliases: list[EquipmentAlias],
) -> EquipmentItem:
    return EquipmentItem(
        equipment_id=row.equipment_id,
        name=row.name,
        name_ru=row.name_ru,
        category=row.category,
        description=row.description,
        capabilities=capabilities,
        aliases=aliases,
        specializes=row.specializes,
        manufacturer=row.manufacturer,
        model_name=row.model_name,
        attributes=row.attributes or {},
        source=_source(row.source),
        is_active=row.is_active,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _profile_to_domain(
    row: EquipmentProfileRow, items: list[EquipmentProfileItemRow]
) -> EquipmentProfile:
    return EquipmentProfile(
        profile_key=row.profile_key,
        owner_type=EquipmentOwnerType(row.owner_type),
        owner_ref=row.owner_ref,
        name=row.name,
        items=[
            EquipmentProfileItem(
                equipment_id=item.equipment_id,
                quantity=item.quantity,
                availability=EquipmentAvailability(item.availability),
                confidence=KnowledgeConfidence(item.confidence),
                extra_capabilities=list(item.extra_capabilities or []),
                source=_source(item.source),
                source_ref=item.source_ref,
                notes=item.notes,
            )
            for item in items
        ],
        assume_unlisted_unavailable=row.assume_unlisted_unavailable,
        source=_source(row.source),
        notes=row.notes,
        is_active=row.is_active,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
