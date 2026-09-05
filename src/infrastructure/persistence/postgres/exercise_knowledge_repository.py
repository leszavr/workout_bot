"""Репозиторий знания об упражнениях: требования к оборудованию и альтернативы.

Отдельно от словаря оборудования: словарь описывает мир, эти таблицы —
утверждения про конкретные упражнения. Читаются они тоже отдельно: проверка
совместимости берёт требования пачкой по списку упражнений, а словарь — целиком
один раз.

Ссылка на упражнение всюду каноническая: пара (external_id, source). Surrogate
`exercises.id` не является каноническим идентификатором и меняется при
пересоздании каталога.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, func, literal, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.domain.equipment import (
    EquipmentRequirement,
    ExerciseAlternative,
    ExerciseEquipmentRequirement,
    KnowledgeConfidence,
    KnowledgeSource,
    SubstitutionType,
    UnmappedEquipmentValue,
    UnmappedReason,
)
from src.errors import ProfilePersistenceError
from src.infrastructure.persistence.postgres.models import (
    EquipmentCapabilityLinkRow,
    EquipmentItemRow,
    ExerciseAlternativeRow,
    ExerciseEquipmentRequirementRow,
    ExerciseRow,
    UnmappedEquipmentValueRow,
)

DEFAULT_SOURCE = "leszavr/workout"

# Размер порции для массовой вставки. PostgreSQL ограничивает число параметров
# запроса (65535), и у альтернатив 9 колонок: 4119 строк одним запросом дают
# 37 тысяч параметров и падают на драйвере ещё до сервера. Порция подобрана с
# запасом под самую широкую таблицу этого модуля.
INSERT_CHUNK_SIZE = 1000


def _chunks(values: list, size: int = INSERT_CHUNK_SIZE):
    for start in range(0, len(values), size):
        yield values[start : start + size]


class ExerciseKnowledgeError(Exception):
    """Нарушение правил знания об упражнениях (не ошибка инфраструктуры)."""


def _persistence_error(exc: SQLAlchemyError, what: str) -> ProfilePersistenceError:
    return ProfilePersistenceError(f"{what}: {exc.__class__.__name__}")


@dataclass(frozen=True)
class ExerciseRef:
    """Каноническая ссылка на упражнение."""

    external_id: str
    source: str = DEFAULT_SOURCE

    def as_key(self) -> tuple[str, str]:
        return (self.external_id, self.source)


class ExerciseKnowledgeRepository:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._sessions = session_factory

    # --- Требования к оборудованию -------------------------------------------

    async def requirements_for(
        self, refs: list[ExerciseRef]
    ) -> dict[tuple[str, str], list[ExerciseEquipmentRequirement]]:
        """Требования пачкой: один запрос на список упражнений.

        Пул кандидатов содержит сотни упражнений, и проверка совместимости
        каждого отдельным запросом превратила бы одну генерацию в сотни
        обращений к базе.
        """
        if not refs:
            return {}
        keys = {ref.as_key() for ref in refs}
        external_ids = sorted({key[0] for key in keys})
        stmt = select(ExerciseEquipmentRequirementRow).where(
            ExerciseEquipmentRequirementRow.exercise_external_id.in_(external_ids)
        )
        try:
            async with self._sessions() as session:
                rows = (await session.execute(stmt)).scalars().all()
        except SQLAlchemyError as exc:
            raise _persistence_error(exc, "Ошибка чтения требований") from exc

        result: dict[tuple[str, str], list[ExerciseEquipmentRequirement]] = {
            key: [] for key in keys
        }
        for row in rows:
            key = (row.exercise_external_id, row.exercise_source)
            if key not in result:
                # Строка относится к другому источнику каталога с тем же
                # external_id: она не про запрошенное упражнение.
                continue
            result[key].append(_requirement_to_domain(row))
        return result

    async def list_requirements(
        self, ref: ExerciseRef
    ) -> list[ExerciseEquipmentRequirement]:
        stmt = (
            select(ExerciseEquipmentRequirementRow)
            .where(
                ExerciseEquipmentRequirementRow.exercise_external_id == ref.external_id,
                ExerciseEquipmentRequirementRow.exercise_source == ref.source,
            )
            .order_by(
                ExerciseEquipmentRequirementRow.requirement,
                ExerciseEquipmentRequirementRow.alternative_group,
                ExerciseEquipmentRequirementRow.equipment_id,
                ExerciseEquipmentRequirementRow.capability_id,
            )
        )
        try:
            async with self._sessions() as session:
                rows = (await session.execute(stmt)).scalars().all()
        except SQLAlchemyError as exc:
            raise _persistence_error(exc, "Ошибка чтения требований") from exc
        return [_requirement_to_domain(r) for r in rows]

    async def replace_requirements(
        self, ref: ExerciseRef, requirements: list[ExerciseEquipmentRequirement]
    ) -> list[ExerciseEquipmentRequirement]:
        """Заменяет набор требований упражнения целиком.

        Замена, а не частичное обновление: администратор редактирует набор
        требований как единое утверждение об упражнении, и «добавить строку» без
        удаления противоречащей ей дало бы невыполнимую комбинацию.
        """
        try:
            async with self._sessions() as session:
                async with session.begin():
                    await session.execute(
                        delete(ExerciseEquipmentRequirementRow).where(
                            ExerciseEquipmentRequirementRow.exercise_external_id
                            == ref.external_id,
                            ExerciseEquipmentRequirementRow.exercise_source
                            == ref.source,
                        )
                    )
                    seen: set[tuple] = set()
                    for requirement in requirements:
                        key = (
                            requirement.equipment_id,
                            requirement.capability_id,
                            requirement.requirement.value,
                        )
                        if key in seen:
                            continue
                        seen.add(key)
                        session.add(
                            ExerciseEquipmentRequirementRow(
                                exercise_external_id=ref.external_id,
                                exercise_source=ref.source,
                                equipment_id=requirement.equipment_id,
                                capability_id=requirement.capability_id,
                                requirement=requirement.requirement.value,
                                alternative_group=requirement.alternative_group,
                                confidence=requirement.confidence.value,
                                source=requirement.source.value,
                                notes=requirement.notes,
                            )
                        )
        except IntegrityError as exc:
            raise ExerciseKnowledgeError(
                "Требование ссылается на неизвестное оборудование или возможность"
            ) from exc
        except SQLAlchemyError as exc:
            raise _persistence_error(exc, "Не удалось сохранить требования") from exc
        return await self.list_requirements(ref)

    async def bulk_insert_requirements(
        self, requirements: list[ExerciseEquipmentRequirement]
    ) -> int:
        """Идемпотентная вставка требований для импорта.

        Конфликт по уникальному ключу игнорируется: повторный импорт не должен
        ни падать, ни перезаписывать правки администратора.
        """
        if not requirements:
            return 0
        values = [
            {
                "exercise_external_id": r.exercise_external_id,
                "exercise_source": r.exercise_source,
                "equipment_id": r.equipment_id,
                "capability_id": r.capability_id,
                "requirement": r.requirement.value,
                "alternative_group": r.alternative_group,
                "confidence": r.confidence.value,
                "source": r.source.value,
                "notes": r.notes,
            }
            for r in requirements
        ]
        try:
            async with self._sessions() as session:
                async with session.begin():
                    written = 0
                    for chunk in _chunks(values):
                        stmt = pg_insert(ExerciseEquipmentRequirementRow).values(chunk)
                        stmt = stmt.on_conflict_do_nothing(
                            constraint="uq_exercise_requirement"
                        )
                        result = await session.execute(stmt)
                        written += result.rowcount or 0
                    return written
        except IntegrityError as exc:
            raise ExerciseKnowledgeError(
                "Требование ссылается на неизвестное оборудование или возможность"
            ) from exc
        except SQLAlchemyError as exc:
            raise _persistence_error(exc, "Не удалось записать требования") from exc

    async def delete_requirements_by_source(self, sources: list[str]) -> int:
        """Удаляет требования указанного происхождения.

        Нужно повторному импорту: пересчитать выведенные правилом требования
        можно, только убрав предыдущий результат того же правила. Требования,
        заведённые администратором, не затрагиваются.
        """
        if not sources:
            return 0
        try:
            async with self._sessions() as session:
                async with session.begin():
                    result = await session.execute(
                        delete(ExerciseEquipmentRequirementRow).where(
                            ExerciseEquipmentRequirementRow.source.in_(sources)
                        )
                    )
                    return result.rowcount or 0
        except SQLAlchemyError as exc:
            raise _persistence_error(exc, "Не удалось удалить требования") from exc

    async def requirement_counts_by_equipment(self) -> dict[str, int]:
        """Сколько упражнений ссылается на каждую единицу оборудования."""
        stmt = (
            select(
                ExerciseEquipmentRequirementRow.equipment_id,
                func.count(
                    func.distinct(ExerciseEquipmentRequirementRow.exercise_external_id)
                ),
            )
            .where(ExerciseEquipmentRequirementRow.equipment_id.is_not(None))
            .group_by(ExerciseEquipmentRequirementRow.equipment_id)
        )
        try:
            async with self._sessions() as session:
                rows = (await session.execute(stmt)).all()
        except SQLAlchemyError as exc:
            raise _persistence_error(exc, "Ошибка счётчиков требований") from exc
        return {row[0]: row[1] for row in rows}

    async def exercise_ids_with_requirements(
        self, equipment_ids: list[str], *, requirements: list[str] | None = None
    ) -> set[str]:
        """external_id упражнений, требующих любое из указанного оборудования."""
        if not equipment_ids:
            return set()
        stmt = select(
            func.distinct(ExerciseEquipmentRequirementRow.exercise_external_id)
        ).where(ExerciseEquipmentRequirementRow.equipment_id.in_(equipment_ids))
        if requirements:
            stmt = stmt.where(
                ExerciseEquipmentRequirementRow.requirement.in_(requirements)
            )
        try:
            async with self._sessions() as session:
                rows = (await session.execute(stmt)).scalars().all()
        except SQLAlchemyError as exc:
            raise _persistence_error(exc, "Ошибка выборки по оборудованию") from exc
        return set(rows)

    async def exercise_ids_with_capability_requirements(
        self, capability_ids: list[str]
    ) -> set[str]:
        """external_id упражнений, требующих возможность прямо или через оборудование.

        Требование может быть выражено двумя способами: «нужна наклонная опора»
        и «нужна наклонная скамья». Фильтр по возможности обязан находить оба,
        иначе результат зависит от того, как заполнили данные.
        """
        if not capability_ids:
            return set()
        direct = select(
            func.distinct(ExerciseEquipmentRequirementRow.exercise_external_id)
        ).where(ExerciseEquipmentRequirementRow.capability_id.in_(capability_ids))
        via_equipment = (
            select(func.distinct(ExerciseEquipmentRequirementRow.exercise_external_id))
            .join(
                EquipmentCapabilityLinkRow,
                EquipmentCapabilityLinkRow.equipment_id
                == ExerciseEquipmentRequirementRow.equipment_id,
            )
            .where(EquipmentCapabilityLinkRow.capability_id.in_(capability_ids))
        )
        try:
            async with self._sessions() as session:
                first = (await session.execute(direct)).scalars().all()
                second = (await session.execute(via_equipment)).scalars().all()
        except SQLAlchemyError as exc:
            raise _persistence_error(exc, "Ошибка выборки по возможности") from exc
        return set(first) | set(second)

    async def exercise_ids_without_requirements(self) -> set[str]:
        """Упражнения, о требованиях которых база знаний ничего не знает.

        Это ровно те упражнения, для которых совместимость возвращает UNKNOWN, и
        отдельный фильтр по ним нужен, чтобы пробел в данных был видим.
        """
        requirement_exists = (
            select(ExerciseEquipmentRequirementRow.id)
            .where(
                ExerciseEquipmentRequirementRow.exercise_external_id
                == ExerciseRow.external_id,
                ExerciseEquipmentRequirementRow.exercise_source == ExerciseRow.source,
            )
            .exists()
        )
        stmt = select(ExerciseRow.external_id).where(~requirement_exists)
        try:
            async with self._sessions() as session:
                rows = (await session.execute(stmt)).scalars().all()
        except SQLAlchemyError as exc:
            raise _persistence_error(exc, "Ошибка выборки без требований") from exc
        return set(rows)

    async def exercise_ids_with_any_requirements(self) -> set[str]:
        """Упражнения, у которых записано хотя бы одно требование.

        Отдельный запрос вместо дополнения к предыдущему набору: дополнение
        потребовало бы вычитать весь каталог, а нужен только список
        идентификаторов из таблицы требований.
        """
        stmt = select(
            func.distinct(ExerciseEquipmentRequirementRow.exercise_external_id)
        )
        try:
            async with self._sessions() as session:
                rows = (await session.execute(stmt)).scalars().all()
        except SQLAlchemyError as exc:
            raise _persistence_error(exc, "Ошибка выборки с требованиями") from exc
        return set(rows)

    # --- Незакрытые значения источника ---------------------------------------

    async def record_unmapped(self, values: list[UnmappedEquipmentValue]) -> int:
        if not values:
            return 0
        payload = [
            {
                "exercise_external_id": v.exercise_external_id,
                "exercise_source": v.exercise_source,
                "raw_value": v.raw_value,
                "reason": v.reason.value,
                "notes": v.notes,
            }
            for v in values
        ]
        try:
            async with self._sessions() as session:
                async with session.begin():
                    written = 0
                    for chunk in _chunks(payload):
                        stmt = pg_insert(UnmappedEquipmentValueRow).values(chunk)
                        stmt = stmt.on_conflict_do_nothing(
                            constraint="uq_unmapped_equipment_value"
                        )
                        result = await session.execute(stmt)
                        written += result.rowcount or 0
                    return written
        except SQLAlchemyError as exc:
            raise _persistence_error(exc, "Не удалось записать unmapped") from exc

    async def clear_unmapped(self) -> int:
        try:
            async with self._sessions() as session:
                async with session.begin():
                    result = await session.execute(delete(UnmappedEquipmentValueRow))
                    return result.rowcount or 0
        except SQLAlchemyError as exc:
            raise _persistence_error(exc, "Не удалось очистить unmapped") from exc

    async def list_unmapped(
        self, *, limit: int = 200, offset: int = 0
    ) -> tuple[int, list[UnmappedEquipmentValue]]:
        stmt = (
            select(UnmappedEquipmentValueRow)
            .order_by(
                UnmappedEquipmentValueRow.raw_value,
                UnmappedEquipmentValueRow.exercise_external_id,
            )
            .limit(limit)
            .offset(offset)
        )
        count_stmt = select(func.count()).select_from(UnmappedEquipmentValueRow)
        try:
            async with self._sessions() as session:
                total = (await session.execute(count_stmt)).scalar_one()
                rows = (await session.execute(stmt)).scalars().all()
        except SQLAlchemyError as exc:
            raise _persistence_error(exc, "Ошибка чтения unmapped") from exc
        return total, [
            UnmappedEquipmentValue(
                exercise_external_id=r.exercise_external_id,
                exercise_source=r.exercise_source,
                raw_value=r.raw_value,
                reason=UnmappedReason(r.reason),
                notes=r.notes,
            )
            for r in rows
        ]

    async def unmapped_summary(self) -> list[dict]:
        """Незакрытые значения, сгруппированные по строке источника."""
        stmt = (
            select(
                UnmappedEquipmentValueRow.raw_value,
                UnmappedEquipmentValueRow.reason,
                func.count().label("count"),
            )
            .group_by(
                UnmappedEquipmentValueRow.raw_value, UnmappedEquipmentValueRow.reason
            )
            .order_by(func.count().desc(), UnmappedEquipmentValueRow.raw_value)
        )
        try:
            async with self._sessions() as session:
                rows = (await session.execute(stmt)).all()
        except SQLAlchemyError as exc:
            raise _persistence_error(exc, "Ошибка сводки unmapped") from exc
        return [
            {"raw_value": r[0], "reason": r[1], "count": r[2]} for r in rows
        ]

    # --- Альтернативы --------------------------------------------------------

    async def list_alternatives(self, ref: ExerciseRef) -> list[ExerciseAlternative]:
        stmt = (
            select(ExerciseAlternativeRow)
            .where(
                ExerciseAlternativeRow.exercise_external_id == ref.external_id,
                ExerciseAlternativeRow.exercise_source == ref.source,
            )
            .order_by(
                ExerciseAlternativeRow.score.desc(),
                ExerciseAlternativeRow.alternative_external_id,
            )
        )
        try:
            async with self._sessions() as session:
                rows = (await session.execute(stmt)).scalars().all()
        except SQLAlchemyError as exc:
            raise _persistence_error(exc, "Ошибка чтения альтернатив") from exc
        return [_alternative_to_domain(r) for r in rows]

    async def alternatives_for(
        self, refs: list[ExerciseRef]
    ) -> dict[tuple[str, str], list[ExerciseAlternative]]:
        if not refs:
            return {}
        keys = {ref.as_key() for ref in refs}
        stmt = select(ExerciseAlternativeRow).where(
            ExerciseAlternativeRow.exercise_external_id.in_(
                sorted({k[0] for k in keys})
            )
        )
        try:
            async with self._sessions() as session:
                rows = (await session.execute(stmt)).scalars().all()
        except SQLAlchemyError as exc:
            raise _persistence_error(exc, "Ошибка чтения альтернатив") from exc
        result: dict[tuple[str, str], list[ExerciseAlternative]] = {
            key: [] for key in keys
        }
        for row in rows:
            key = (row.exercise_external_id, row.exercise_source)
            if key in result:
                result[key].append(_alternative_to_domain(row))
        for values in result.values():
            values.sort(key=lambda a: (-a.score, a.alternative_external_id))
        return result

    async def bulk_insert_alternatives(
        self, alternatives: list[ExerciseAlternative]
    ) -> int:
        if not alternatives:
            return 0
        values = [
            {
                "exercise_external_id": a.exercise_external_id,
                "exercise_source": a.exercise_source,
                "alternative_external_id": a.alternative_external_id,
                "alternative_source": a.alternative_source,
                "substitution": a.substitution.value,
                "score": a.score,
                "rationale": a.rationale,
                "source": a.source.value,
                "notes": a.notes,
            }
            for a in alternatives
        ]
        try:
            async with self._sessions() as session:
                async with session.begin():
                    written = 0
                    for chunk in _chunks(values):
                        stmt = pg_insert(ExerciseAlternativeRow).values(chunk)
                        stmt = stmt.on_conflict_do_nothing(
                            constraint="uq_exercise_alternative_pair"
                        )
                        result = await session.execute(stmt)
                        written += result.rowcount or 0
                    return written
        except SQLAlchemyError as exc:
            raise _persistence_error(exc, "Не удалось записать альтернативы") from exc

    async def delete_alternatives_by_source(self, sources: list[str]) -> int:
        if not sources:
            return 0
        try:
            async with self._sessions() as session:
                async with session.begin():
                    result = await session.execute(
                        delete(ExerciseAlternativeRow).where(
                            ExerciseAlternativeRow.source.in_(sources)
                        )
                    )
                    return result.rowcount or 0
        except SQLAlchemyError as exc:
            raise _persistence_error(exc, "Не удалось удалить альтернативы") from exc

    async def exercise_ids_with_alternatives(self) -> set[str]:
        stmt = select(func.distinct(ExerciseAlternativeRow.exercise_external_id))
        try:
            async with self._sessions() as session:
                rows = (await session.execute(stmt)).scalars().all()
        except SQLAlchemyError as exc:
            raise _persistence_error(exc, "Ошибка выборки альтернатив") from exc
        return set(rows)

    # --- Диагностика целостности ---------------------------------------------

    async def health_counters(self) -> dict[str, int]:
        """Счётчики целостности и полноты базы знаний.

        Считаются запросами: захардкоженная метрика показывала бы состояние на
        момент написания кода, а не текущее.
        """
        requirement_exists = (
            select(ExerciseEquipmentRequirementRow.id)
            .where(
                ExerciseEquipmentRequirementRow.exercise_external_id
                == ExerciseRow.external_id,
                ExerciseEquipmentRequirementRow.exercise_source == ExerciseRow.source,
            )
            .exists()
        )
        confirmed_exists = (
            select(ExerciseEquipmentRequirementRow.id)
            .where(
                ExerciseEquipmentRequirementRow.exercise_external_id
                == ExerciseRow.external_id,
                ExerciseEquipmentRequirementRow.exercise_source == ExerciseRow.source,
                ExerciseEquipmentRequirementRow.confidence
                == KnowledgeConfidence.CONFIRMED.value,
            )
            .exists()
        )
        # Orphan-ссылка: требование указывает на упражнение, которого в каталоге
        # нет. Составного FK к каталогу нет намеренно (пересоздание каталога —
        # штатная операция), поэтому целостность измеряется, а не гарантируется.
        exercise_exists = (
            select(ExerciseRow.id)
            .where(
                ExerciseRow.external_id
                == ExerciseEquipmentRequirementRow.exercise_external_id,
                ExerciseRow.source == ExerciseEquipmentRequirementRow.exercise_source,
            )
            .exists()
        )
        alternative_target_exists = (
            select(ExerciseRow.id)
            .where(
                ExerciseRow.external_id
                == ExerciseAlternativeRow.alternative_external_id,
                ExerciseRow.source == ExerciseAlternativeRow.alternative_source,
            )
            .exists()
        )
        # Невыполнимая комбинация: требуется и оборудование, и его отсутствие
        # (bodyweight) как обязательные. Это противоречие в данных, а не
        # состояние пользователя.
        impossible = (
            select(func.count(func.distinct(_impossible.c.external_id)))
            .select_from(_impossible)
        )
        try:
            async with self._sessions() as session:
                exercises_total = (
                    await session.execute(select(func.count()).select_from(ExerciseRow))
                ).scalar_one()
                exercises_active = (
                    await session.execute(
                        select(func.count())
                        .select_from(ExerciseRow)
                        .where(ExerciseRow.is_active.is_(True))
                    )
                ).scalar_one()
                known = (
                    await session.execute(
                        select(func.count())
                        .select_from(ExerciseRow)
                        .where(requirement_exists)
                    )
                ).scalar_one()
                confirmed = (
                    await session.execute(
                        select(func.count())
                        .select_from(ExerciseRow)
                        .where(confirmed_exists)
                    )
                ).scalar_one()
                with_alternatives = (
                    await session.execute(
                        select(
                            func.count(
                                func.distinct(
                                    ExerciseAlternativeRow.exercise_external_id
                                )
                            )
                        )
                    )
                ).scalar_one()
                requirements_total = (
                    await session.execute(
                        select(func.count()).select_from(
                            ExerciseEquipmentRequirementRow
                        )
                    )
                ).scalar_one()
                alternatives_total = (
                    await session.execute(
                        select(func.count()).select_from(ExerciseAlternativeRow)
                    )
                ).scalar_one()
                unmapped_values = (
                    await session.execute(
                        select(func.count()).select_from(UnmappedEquipmentValueRow)
                    )
                ).scalar_one()
                unmapped_exercises = (
                    await session.execute(
                        select(
                            func.count(
                                func.distinct(
                                    UnmappedEquipmentValueRow.exercise_external_id
                                )
                            )
                        )
                    )
                ).scalar_one()
                orphan_requirements = (
                    await session.execute(
                        select(func.count())
                        .select_from(ExerciseEquipmentRequirementRow)
                        .where(~exercise_exists)
                    )
                ).scalar_one()
                orphan_alternatives = (
                    await session.execute(
                        select(func.count())
                        .select_from(ExerciseAlternativeRow)
                        .where(~alternative_target_exists)
                    )
                ).scalar_one()
                impossible_count = (await session.execute(impossible)).scalar_one()
                equipment_total = (
                    await session.execute(
                        select(func.count()).select_from(EquipmentItemRow)
                    )
                ).scalar_one()
                equipment_active = (
                    await session.execute(
                        select(func.count())
                        .select_from(EquipmentItemRow)
                        .where(EquipmentItemRow.is_active.is_(True))
                    )
                ).scalar_one()
        except SQLAlchemyError as exc:
            raise _persistence_error(exc, "Ошибка метрик базы знаний") from exc

        return {
            "exercises_total": exercises_total,
            "exercises_active": exercises_active,
            "equipment_known": known,
            "equipment_unknown": exercises_total - known,
            "equipment_confirmed": confirmed,
            "equipment_inferred": known - confirmed,
            "exercises_with_alternatives": with_alternatives,
            "requirements_total": requirements_total,
            "alternatives_total": alternatives_total,
            "unmapped_values": unmapped_values,
            "unmapped_exercises": unmapped_exercises,
            "orphan_equipment_references": orphan_requirements + orphan_alternatives,
            "impossible_requirement_combinations": impossible_count,
            "equipment_items_total": equipment_total,
            "equipment_items_active": equipment_active,
        }


# Подзапрос «упражнение требует одновременно собственный вес и снаряд как
# обязательные». Определён на уровне модуля, потому что читается как отдельное
# утверждение о данных, а не как часть метода.
_required = (
    select(
        ExerciseEquipmentRequirementRow.exercise_external_id.label("external_id"),
        ExerciseEquipmentRequirementRow.equipment_id.label("equipment_id"),
    )
    .where(
        ExerciseEquipmentRequirementRow.requirement
        == EquipmentRequirement.REQUIRED.value,
        ExerciseEquipmentRequirementRow.equipment_id.is_not(None),
    )
    .subquery("required_equipment")
)
_impossible = (
    select(_required.c.external_id)
    .group_by(_required.c.external_id)
    .having(
        func.bool_or(_required.c.equipment_id == literal("bodyweight"))
        & func.bool_or(_required.c.equipment_id != literal("bodyweight"))
    )
    .subquery("impossible_requirements")
)


def _requirement_to_domain(
    row: ExerciseEquipmentRequirementRow,
) -> ExerciseEquipmentRequirement:
    return ExerciseEquipmentRequirement(
        exercise_external_id=row.exercise_external_id,
        exercise_source=row.exercise_source,
        equipment_id=row.equipment_id,
        capability_id=row.capability_id,
        requirement=EquipmentRequirement(row.requirement),
        alternative_group=row.alternative_group,
        confidence=KnowledgeConfidence(row.confidence),
        source=_source(row.source),
        notes=row.notes,
    )


def _alternative_to_domain(row: ExerciseAlternativeRow) -> ExerciseAlternative:
    return ExerciseAlternative(
        exercise_external_id=row.exercise_external_id,
        exercise_source=row.exercise_source,
        alternative_external_id=row.alternative_external_id,
        alternative_source=row.alternative_source,
        substitution=SubstitutionType(row.substitution),
        score=row.score,
        rationale=row.rationale or {},
        source=_source(row.source),
        notes=row.notes,
    )


def _source(raw: str) -> KnowledgeSource:
    try:
        return KnowledgeSource(raw)
    except ValueError:
        return KnowledgeSource.ADMIN
