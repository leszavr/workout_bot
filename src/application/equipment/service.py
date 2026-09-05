"""Сервис базы знаний об оборудовании: словарь, требования, альтернативы, health.

Слой существует, чтобы правила знания не расползлись по HTTP-обработчикам.
Проверки, которые нельзя доверить ни базе, ни вводу, живут здесь:

- ссылки на оборудование и возможности обязаны существовать (иначе требование
  ссылается в пустоту, и совместимость навсегда остаётся UNKNOWN);
- альтернатива обязана указывать на существующее упражнение каталога;
- невыполнимые комбинации требований отклоняются на записи, а не обнаруживаются
  потом метрикой.

Сервис не знает про HTTP и не формирует ответы: он возвращает доменные модели.
"""
from __future__ import annotations

from src.application.equipment.compatibility import (
    AvailableEquipment,
    EquipmentCompatibilityService,
    available_from_profile,
)
from src.domain.equipment import (
    CompatibilityResult,
    EquipmentAvailability,
    EquipmentCapability,
    EquipmentItem,
    EquipmentProfile,
    EquipmentRequirement,
    EquipmentUsage,
    ExerciseAlternative,
    ExerciseEquipmentRequirement,
    KnowledgeBaseHealth,
)
from src.infrastructure.persistence.postgres.equipment_repository import (
    EquipmentIndex,
    EquipmentKnowledgeError,
    EquipmentQuery,
    EquipmentRepository,
)
from src.infrastructure.persistence.postgres.exercise_knowledge_repository import (
    ExerciseKnowledgeRepository,
    ExerciseRef,
)
from src.infrastructure.persistence.postgres.exercise_repository import (
    ExerciseQuery,
    ExerciseRepository,
)

# Оборудование «собственный вес» несовместимо с обязательным снарядом: это не
# оборудование, а его отсутствие.
BODYWEIGHT_ID = "bodyweight"


class EquipmentKnowledgeService:
    def __init__(
        self,
        *,
        equipment: EquipmentRepository,
        knowledge: ExerciseKnowledgeRepository,
        exercises: ExerciseRepository,
    ) -> None:
        self._equipment = equipment
        self._knowledge = knowledge
        self._exercises = exercises

    # --- Словарь --------------------------------------------------------------

    async def list_capabilities(self) -> list[EquipmentCapability]:
        return await self._equipment.list_capabilities()

    async def search_equipment(
        self, query: EquipmentQuery, *, limit: int, offset: int
    ) -> tuple[int, list[EquipmentUsage]]:
        return await self._equipment.search(query, limit=limit, offset=offset)

    async def get_equipment(self, equipment_id: str) -> EquipmentItem | None:
        return await self._equipment.get(equipment_id)

    async def categories(self) -> list[dict]:
        return await self._equipment.categories()

    async def save_equipment(self, item: EquipmentItem) -> EquipmentItem:
        if item.specializes:
            if item.specializes == item.equipment_id:
                raise EquipmentKnowledgeError(
                    "Оборудование не может специализировать само себя"
                )
            index = await self._equipment.load_index()
            if not index.exists(item.specializes):
                raise EquipmentKnowledgeError(
                    f"Неизвестное родовое оборудование: {item.specializes}"
                )
            # Цикл в специализации сделал бы «частный случай» бессмысленным:
            # обе записи закрывали бы требование друг друга.
            if item.equipment_id in _ancestors(index, item.specializes):
                raise EquipmentKnowledgeError(
                    "Цикл в специализации оборудования недопустим"
                )
        return await self._equipment.upsert(item)

    async def deactivate_equipment(self, equipment_id: str) -> bool:
        return await self._equipment.deactivate(equipment_id)

    async def delete_equipment(self, equipment_id: str) -> bool:
        return await self._equipment.delete(equipment_id)

    async def load_index(self) -> EquipmentIndex:
        return await self._equipment.load_index()

    # --- Требования упражнения ------------------------------------------------

    async def list_requirements(
        self, ref: ExerciseRef
    ) -> list[ExerciseEquipmentRequirement]:
        return await self._knowledge.list_requirements(ref)

    async def replace_requirements(
        self, ref: ExerciseRef, requirements: list[ExerciseEquipmentRequirement]
    ) -> list[ExerciseEquipmentRequirement]:
        await self._ensure_exercise_exists(ref)
        index = await self._equipment.load_index()
        self._validate_requirements(requirements, index)
        return await self._knowledge.replace_requirements(ref, requirements)

    def _validate_requirements(
        self,
        requirements: list[ExerciseEquipmentRequirement],
        index: EquipmentIndex,
    ) -> None:
        unknown_equipment = sorted(
            {
                r.equipment_id
                for r in requirements
                if r.equipment_id and not index.exists(r.equipment_id)
            }
        )
        if unknown_equipment:
            raise EquipmentKnowledgeError(
                f"Неизвестное оборудование: {', '.join(unknown_equipment)}"
            )
        unknown_capabilities = sorted(
            {
                r.capability_id
                for r in requirements
                if r.capability_id and r.capability_id not in index.capabilities
            }
        )
        if unknown_capabilities:
            raise EquipmentKnowledgeError(
                f"Неизвестные возможности: {', '.join(unknown_capabilities)}"
            )

        mandatory = {
            r.equipment_id
            for r in requirements
            if r.requirement is EquipmentRequirement.REQUIRED and r.equipment_id
        }
        if BODYWEIGHT_ID in mandatory and len(mandatory) > 1:
            # Комбинация невыполнима по построению: упражнение не может
            # одновременно требовать снаряд и его отсутствие. Отклоняется на
            # записи, а не обнаруживается позже метрикой.
            raise EquipmentKnowledgeError(
                "Невозможная комбинация: собственный вес и оборудование "
                "одновременно обязательны"
            )

    # --- Альтернативы ---------------------------------------------------------

    async def list_alternatives(self, ref: ExerciseRef) -> list[ExerciseAlternative]:
        return await self._knowledge.list_alternatives(ref)

    # --- Совместимость --------------------------------------------------------

    async def check_compatibility(
        self, refs: list[ExerciseRef], available: AvailableEquipment
    ) -> dict[tuple[str, str], CompatibilityResult]:
        index = await self._equipment.load_index()
        requirements = await self._knowledge.requirements_for(refs)
        service = EquipmentCompatibilityService(index)
        return service.check_many(
            requirements_by_exercise=requirements, available=available
        )

    async def check_against_profile(
        self, refs: list[ExerciseRef], profile: EquipmentProfile
    ) -> dict[tuple[str, str], CompatibilityResult]:
        return await self.check_compatibility(refs, available_from_profile(profile))

    async def available_from_equipment_ids(
        self, equipment_ids: list[str], *, assume_unlisted_unavailable: bool
    ) -> AvailableEquipment:
        """Собирает набор доступного оборудования из явного перечисления.

        Нужно Exercise Explorer: администратор выбирает оборудование галочками, и
        такой выбор — это перечень доступного, а не профиль пользователя.
        """
        index = await self._equipment.load_index()
        known = {e for e in equipment_ids if index.exists(e)}
        return AvailableEquipment(
            available=frozenset(known),
            assume_unlisted_unavailable=assume_unlisted_unavailable,
        )

    # --- Health ---------------------------------------------------------------

    async def health(self) -> KnowledgeBaseHealth:
        counters = await self._knowledge.health_counters()
        index = await self._equipment.load_index()
        usage = await self._knowledge.requirement_counts_by_equipment()
        aliases = sum(len(item.aliases) for item in index.items.values())
        unused = sum(
            1 for equipment_id in index.items if not usage.get(equipment_id)
        )
        # Ссылки на возможности, которых нет в словаре. Ограничение FK это
        # запрещает, поэтому в норме здесь ноль; метрика существует, потому что
        # словарь пополняется миграциями и скриптами, и молчаливое расхождение
        # обесценило бы остальные числа.
        invalid_capabilities = 0
        for item in index.items.values():
            invalid_capabilities += sum(
                1 for c in item.capabilities if c not in index.capabilities
            )
        return KnowledgeBaseHealth(
            exercises_total=counters["exercises_total"],
            exercises_active=counters["exercises_active"],
            equipment_known=counters["equipment_known"],
            equipment_unknown=counters["equipment_unknown"],
            equipment_confirmed=counters["equipment_confirmed"],
            equipment_inferred=counters["equipment_inferred"],
            exercises_with_alternatives=counters["exercises_with_alternatives"],
            equipment_items_total=counters["equipment_items_total"],
            equipment_items_active=counters["equipment_items_active"],
            equipment_items_unused=unused,
            capabilities_total=len(index.capabilities),
            aliases_total=aliases,
            requirements_total=counters["requirements_total"],
            alternatives_total=counters["alternatives_total"],
            unmapped_values=counters["unmapped_values"],
            unmapped_exercises=counters["unmapped_exercises"],
            orphan_equipment_references=counters["orphan_equipment_references"],
            invalid_capability_references=invalid_capabilities,
            impossible_requirement_combinations=counters[
                "impossible_requirement_combinations"
            ],
            duplicate_requirements=0,
        )

    async def unmapped_summary(self) -> list[dict]:
        return await self._knowledge.unmapped_summary()

    # --- Вспомогательное ------------------------------------------------------

    async def _ensure_exercise_exists(self, ref: ExerciseRef) -> None:
        exercise = await self._exercises.get_by_external_id(ref.external_id, ref.source)
        if exercise is None:
            raise EquipmentKnowledgeError(
                f"Упражнение {ref.external_id} не найдено в каталоге"
            )

    async def exercise_ids_for_equipment_filter(
        self,
        *,
        equipment_ids: list[str],
        requirement_kinds: list[str] | None,
        capability_ids: list[str],
        knowledge_state: str | None = None,
    ) -> frozenset[str] | None:
        """Набор external_id для фильтров каталога, вычисляемых базой знаний.

        Возвращает None, если ни один такой фильтр не задан: тогда каталог не
        ограничивается. Пустой набор означает «под фильтр не подошло ничего» и
        отличается от None намеренно — иначе фильтр без совпадений показывал бы
        весь каталог.

        Наборы соединяются через AND: «упражнения с блочным тренажёром» и
        «упражнения с регулируемым сопротивлением» — это одно упражнение с обоими
        признаками, а не объединение двух списков.
        """
        sets: list[set[str]] = []
        if equipment_ids:
            sets.append(
                await self._knowledge.exercise_ids_with_requirements(
                    equipment_ids, requirements=requirement_kinds
                )
            )
        if capability_ids:
            sets.append(
                await self._knowledge.exercise_ids_with_capability_requirements(
                    capability_ids
                )
            )
        if knowledge_state == "unknown":
            sets.append(await self._knowledge.exercise_ids_without_requirements())
        elif knowledge_state == "known":
            sets.append(await self._knowledge.exercise_ids_with_any_requirements())
        if not sets:
            return None
        result = sets[0]
        for other in sets[1:]:
            result &= other
        return frozenset(result)

    async def compatibility_for_catalog(
        self,
        *,
        external_ids: list[str],
        source: str,
        available: AvailableEquipment,
    ) -> dict[str, CompatibilityResult]:
        """Совместимость для страницы каталога: ключ — external_id.

        Проверяется только показанная страница: считать статус для всего каталога
        ради 50 строк на экране незачем.
        """
        refs = [ExerciseRef(external_id, source) for external_id in external_ids]
        results = await self.check_compatibility(refs, available)
        return {key[0]: value for key, value in results.items()}


async def collect_unknown_requirement_ids(
    knowledge: ExerciseKnowledgeRepository,
) -> set[str]:
    """Упражнения без записанных требований.

    Отдельная функция, потому что используется и фильтром Explorer, и отчётом
    health, и держать её методом сервиса ради одного вызова незачем.
    """
    return await knowledge.exercise_ids_without_requirements()


def availability_summary(profile: EquipmentProfile) -> dict[str, int]:
    """Сколько позиций профиля в каждом состоянии наличия."""
    summary = {state.value: 0 for state in EquipmentAvailability}
    for item in profile.items:
        summary[item.availability.value] += 1
    return summary


async def all_exercise_refs(
    exercises: ExerciseRepository, *, limit: int = 5000
) -> list[ExerciseRef]:
    _, items = await exercises.search(ExerciseQuery(is_active=None), limit=limit)
    return [ExerciseRef(e.external_id, e.source) for e in items]


def _ancestors(index: EquipmentIndex, equipment_id: str) -> set[str]:
    """Все родовые записи выше указанной по цепочке специализации."""
    result: set[str] = set()
    current = equipment_id
    while current and current not in result and current in index.items:
        result.add(current)
        current = index.items[current].specializes or ""
    return result
