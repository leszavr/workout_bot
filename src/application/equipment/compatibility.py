"""Детерминированное сопоставление упражнений и фактически доступного оборудования.

Модуль отвечает на один вопрос: можно ли выполнить упражнение тем оборудованием,
которое есть. Ответ вычисляется из данных базы знаний и не зависит ни от AI, ни
от формулировок пользователя. AI получает результат как факт и не участвует в
его получении.

Три статуса вместо двух — не удобство, а корректность. Отсутствие знания о
требованиях упражнения и отсутствие ответа пользователя про тренажёр не являются
доказательством несовместимости, и превращать их в «нельзя» значит вычёркивать
упражнения по причине, которой никто не устанавливал. Поэтому:

- COMPATIBLE — все обязательные требования закрыты;
- INCOMPATIBLE — есть требование, про которое известно, что оборудования нет;
- UNKNOWN — требования неизвестны либо наличие оборудования не установлено.

Требование закрывается тремя способами:

1. прямым совпадением оборудования;
2. частным случаем требуемого: требование `resistance_machine` («силовой
   тренажёр») закрывает `leg_press`, потому что жим ногами и есть силовой
   тренажёр. Отношение задано данными (`equipment_items.specializes`) и
   направлено в одну сторону: частное закрывает родовое, обратное неверно.
   Без этого правила упражнение «жим ногами», у которого источник каталога
   указывает родовое `machine`, объявлялось бы невыполнимым человеку, у которого
   жим ногами есть;
3. возможностью: требование «нужна наклонная опора» закрывает любое
   оборудование, у которого эта возможность есть, — в том числе тренажёр другого
   производителя с другим названием. Дополнительная возможность конкретного
   экземпляра из профиля равноправна возможности типа: у скамьи в конкретном
   зале может быть регулировка, которой у базового типа нет.

Неявной подмены одного оборудования другим сверх этого нет, и это решение, а не
упущение. Правило «доступное покрывает возможности требуемого» проверялось и
отвергнуто: гантели покрывают `free_weight` штанги, и жим штанги лёжа оказывался
выполнимым с гантелями — система выдавала другое упражнение за то же самое.
Правило «совпадают категория и набор возможностей» отвергнуто по той же причине:
его результат зависел бы от того, насколько подробно заполнен словарь, то есть
совместимость менялась бы от полноты данных, а не от фактов о зале.

Функциональная взаимозаменяемость сверх специализации выражается в данных явно и
остаётся проверяемой: требованием возможности (`нужна фиксированная траектория и
регулируемое сопротивление`) либо группой ALTERNATIVE (`chest_press_machine`
или `resistance_machine`). Оба варианта редактируются в админке и видны
администратору.

ALTERNATIVE обрабатывается группами: группа закрыта, если доступен хотя бы один
её вариант. OPTIONAL на статус не влияет вовсе — оно описывает удобство, а не
выполнимость, и попадает в разбор для объяснения.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from src.domain.equipment import (
    CompatibilityReason,
    CompatibilityResult,
    EquipmentAvailability,
    EquipmentCompatibilityStatus,
    EquipmentProfile,
    EquipmentRequirement,
    ExerciseEquipmentRequirement,
    RequirementCheck,
)
from src.infrastructure.persistence.postgres.equipment_repository import EquipmentIndex

# Оборудование, означающее «снаряд не нужен». Ссылка на конкретный ID словаря
# здесь допустима: это не перечисление оборудования, а один граничный случай —
# требование, которое выполнено всегда.
BODYWEIGHT_ID = "bodyweight"


@dataclass(frozen=True)
class AvailableEquipment:
    """Что фактически доступно, с различением «нет» и «неизвестно».

    ``assume_unlisted_unavailable`` отвечает, как трактовать оборудование, о
    котором в профиле не сказано ничего. Для домашнего профиля, где человек
    перечислил всё, что у него есть, это «нет». Для зала, о котором известно
    только название, это «неизвестно»: придумывать отсутствие тренажёра
    deterministic-слою запрещено.
    """

    available: frozenset[str] = frozenset()
    unavailable: frozenset[str] = frozenset()
    # equipment_id -> дополнительные возможности конкретного экземпляра.
    extra_capabilities: dict[str, frozenset[str]] = field(default_factory=dict)
    assume_unlisted_unavailable: bool = False

    def availability_of(self, equipment_id: str) -> EquipmentAvailability:
        if equipment_id in self.available:
            return EquipmentAvailability.AVAILABLE
        if equipment_id in self.unavailable:
            return EquipmentAvailability.UNAVAILABLE
        if self.assume_unlisted_unavailable:
            return EquipmentAvailability.UNAVAILABLE
        return EquipmentAvailability.UNKNOWN


def available_from_profile(profile: EquipmentProfile) -> AvailableEquipment:
    """Переводит профиль оборудования в набор для проверки совместимости."""
    available: set[str] = set()
    unavailable: set[str] = set()
    extra: dict[str, frozenset[str]] = {}
    for item in profile.items:
        if item.availability is EquipmentAvailability.AVAILABLE:
            available.add(item.equipment_id)
            if item.extra_capabilities:
                extra[item.equipment_id] = frozenset(item.extra_capabilities)
        elif item.availability is EquipmentAvailability.UNAVAILABLE:
            unavailable.add(item.equipment_id)
        # UNKNOWN не попадает ни в один набор: это отсутствие сведений.
    return AvailableEquipment(
        available=frozenset(available),
        unavailable=frozenset(unavailable),
        extra_capabilities=extra,
        assume_unlisted_unavailable=profile.assume_unlisted_unavailable,
    )


class EquipmentCompatibilityService:
    """Проверка «упражнение × доступное оборудование» без участия AI.

    Сервис не обращается к базе: словарь и требования передаются готовыми.
    Так одна проверка не превращается в запросы, а результат воспроизводим в
    тестах без PostgreSQL.
    """

    def __init__(self, index: EquipmentIndex) -> None:
        self._index = index

    # --- Публичный интерфейс -------------------------------------------------

    def check(
        self,
        *,
        exercise_external_id: str,
        exercise_source: str,
        requirements: list[ExerciseEquipmentRequirement],
        available: AvailableEquipment,
    ) -> CompatibilityResult:
        if not requirements:
            # Требования неизвестны. Это не «оборудование не нужно»: у 77
            # упражнений каталога поле equipment пусто, и среди них есть как
            # действительно бесснарядные, так и те, где снаряд просто не указан.
            return CompatibilityResult(
                exercise_external_id=exercise_external_id,
                exercise_source=exercise_source,
                status=EquipmentCompatibilityStatus.UNKNOWN,
                reason=CompatibilityReason.REQUIREMENTS_UNKNOWN,
            )

        checks: list[RequirementCheck] = []
        missing: set[str] = set()
        matched: set[str] = set()
        unknown: set[str] = set()

        mandatory = [
            r for r in requirements if r.requirement is EquipmentRequirement.REQUIRED
        ]
        optional = [
            r for r in requirements if r.requirement is EquipmentRequirement.OPTIONAL
        ]
        groups: dict[int, list[ExerciseEquipmentRequirement]] = {}
        for requirement in requirements:
            if requirement.requirement is EquipmentRequirement.ALTERNATIVE:
                # Группа гарантирована валидатором модели и CHECK-ограничением.
                groups.setdefault(requirement.alternative_group or 0, []).append(
                    requirement
                )

        blocked = False
        indeterminate = False
        used_alternative = False
        used_specialization = False

        for requirement in mandatory:
            resolution = self._resolve(requirement, available)
            checks.append(resolution.as_check(requirement))
            if resolution.availability is EquipmentAvailability.AVAILABLE:
                matched.add(resolution.satisfied_by or self._target(requirement))
                used_specialization = used_specialization or resolution.specialized
            elif resolution.availability is EquipmentAvailability.UNAVAILABLE:
                blocked = True
                missing.add(self._target(requirement))
            else:
                indeterminate = True
                unknown.add(self._target(requirement))

        for group in sorted(groups):
            variants = groups[group]
            resolutions = [
                (requirement, self._resolve(requirement, available))
                for requirement in variants
            ]
            for requirement, resolution in resolutions:
                checks.append(resolution.as_check(requirement))
            satisfied = [
                resolution
                for _, resolution in resolutions
                if resolution.availability is EquipmentAvailability.AVAILABLE
            ]
            if satisfied:
                used_alternative = True
                matched.update(
                    r.satisfied_by for r in satisfied if r.satisfied_by is not None
                )
                continue
            if any(
                resolution.availability is EquipmentAvailability.UNKNOWN
                for _, resolution in resolutions
            ):
                # Хотя бы один вариант группы не установлен: группа могла бы
                # быть закрыта, и объявлять несовместимость нельзя.
                indeterminate = True
                unknown.update(
                    self._target(requirement)
                    for requirement, resolution in resolutions
                    if resolution.availability is EquipmentAvailability.UNKNOWN
                )
                continue
            blocked = True
            missing.update(self._target(requirement) for requirement, _ in resolutions)

        for requirement in optional:
            resolution = self._resolve(requirement, available)
            checks.append(resolution.as_check(requirement))
            if resolution.availability is EquipmentAvailability.AVAILABLE:
                matched.add(resolution.satisfied_by or self._target(requirement))

        # Порядок решений: подтверждённое отсутствие сильнее неизвестности.
        # Иначе упражнение со недостающей штангой и неуказанной скамьёй
        # выглядело бы «возможно выполнимым».
        if blocked:
            status = EquipmentCompatibilityStatus.INCOMPATIBLE
            reason = (
                CompatibilityReason.NO_ALTERNATIVE_AVAILABLE
                if groups and not used_alternative
                else CompatibilityReason.REQUIRED_EQUIPMENT_MISSING
            )
        elif indeterminate:
            status = EquipmentCompatibilityStatus.UNKNOWN
            reason = CompatibilityReason.AVAILABILITY_UNKNOWN
        elif used_alternative:
            status = EquipmentCompatibilityStatus.COMPATIBLE
            reason = CompatibilityReason.ALTERNATIVE_EQUIPMENT_AVAILABLE
        elif mandatory:
            status = EquipmentCompatibilityStatus.COMPATIBLE
            if self._only_bodyweight(mandatory):
                reason = CompatibilityReason.NO_EQUIPMENT_NEEDED
            elif used_specialization:
                # Требование закрыто частным случаем родового: причина называет
                # это прямо, иначе «всё необходимое есть» не объясняло бы, почему
                # подошёл тренажёр с другим идентификатором.
                reason = CompatibilityReason.SPECIALIZED_EQUIPMENT_AVAILABLE
            else:
                reason = CompatibilityReason.ALL_REQUIRED_AVAILABLE
        else:
            # Остались только OPTIONAL: обязательного оборудования нет.
            status = EquipmentCompatibilityStatus.COMPATIBLE
            reason = CompatibilityReason.NO_EQUIPMENT_NEEDED

        return CompatibilityResult(
            exercise_external_id=exercise_external_id,
            exercise_source=exercise_source,
            status=status,
            reason=reason,
            missing=sorted(missing),
            matched=sorted(matched),
            unknown=sorted(unknown),
            checks=checks,
        )

    def check_many(
        self,
        *,
        requirements_by_exercise: dict[tuple[str, str], list[ExerciseEquipmentRequirement]],
        available: AvailableEquipment,
    ) -> dict[tuple[str, str], CompatibilityResult]:
        return {
            key: self.check(
                exercise_external_id=key[0],
                exercise_source=key[1],
                requirements=value,
                available=available,
            )
            for key, value in requirements_by_exercise.items()
        }

    # --- Разбор одного требования --------------------------------------------

    @dataclass(frozen=True)
    class _Resolution:
        availability: EquipmentAvailability
        satisfied_by: str | None = None
        # Требование закрыто частным случаем родового, а не прямым совпадением.
        # Хранится, чтобы причина решения объясняла, почему тренажёр подошёл.
        specialized: bool = False

        def as_check(
            self, requirement: ExerciseEquipmentRequirement
        ) -> RequirementCheck:
            return RequirementCheck(
                requirement=requirement.requirement,
                alternative_group=requirement.alternative_group,
                equipment_id=requirement.equipment_id,
                capability_id=requirement.capability_id,
                availability=self.availability,
                satisfied_by=self.satisfied_by,
            )

    def _resolve(
        self, requirement: ExerciseEquipmentRequirement, available: AvailableEquipment
    ) -> _Resolution:
        if requirement.equipment_id:
            return self._resolve_equipment(requirement.equipment_id, available)
        return self._resolve_capability(requirement.capability_id or "", available)

    def _resolve_equipment(
        self, equipment_id: str, available: AvailableEquipment
    ) -> _Resolution:
        if equipment_id == BODYWEIGHT_ID:
            # Собственный вес есть всегда: это не оборудование, а его отсутствие.
            return self._Resolution(EquipmentAvailability.AVAILABLE, BODYWEIGHT_ID)

        direct = available.availability_of(equipment_id)
        if direct is EquipmentAvailability.AVAILABLE:
            return self._Resolution(EquipmentAvailability.AVAILABLE, equipment_id)

        # Частный случай требуемого: `leg_press` закрывает требование
        # `resistance_machine`, потому что жим ногами и есть силовой тренажёр.
        # Обратное неверно, и обратный обход здесь не выполняется.
        specializations = self._index.specializations_of(equipment_id)
        if any(
            available.availability_of(candidate) is EquipmentAvailability.AVAILABLE
            for candidate in specializations
        ):
            # Закрытым считается само родовое требование, а не конкретный
            # тренажёр: требование `resistance_machine` не выбирает между жимом
            # ногами и разгибанием ног, и называть один из них «тем, чем закрыто»
            # значило бы сообщать решение, которого система не принимала.
            return self._Resolution(
                EquipmentAvailability.AVAILABLE, equipment_id, specialized=True
            )

        if not self._index.exists(equipment_id):
            # Требование ссылается на оборудование, которого нет в словаре:
            # это дефект данных, а не факт отсутствия у пользователя.
            return self._Resolution(EquipmentAvailability.UNKNOWN)
        if direct is EquipmentAvailability.UNAVAILABLE and any(
            available.availability_of(candidate) is EquipmentAvailability.UNKNOWN
            for candidate in specializations
        ):
            # Родовое оборудование отмечено отсутствующим, но про его частный
            # случай ничего не сказано: он мог бы закрыть требование, и объявлять
            # несовместимость нельзя.
            return self._Resolution(EquipmentAvailability.UNKNOWN)
        return self._Resolution(direct)

    def _resolve_capability(
        self, capability_id: str, available: AvailableEquipment
    ) -> _Resolution:
        if not capability_id:
            return self._Resolution(EquipmentAvailability.UNKNOWN)
        providers = self._index.providers.get(capability_id, set())
        extra_providers = {
            equipment_id
            for equipment_id, capabilities in available.extra_capabilities.items()
            if capability_id in capabilities
        }
        candidates = providers | extra_providers
        if not candidates:
            # Возможность существует, но ни одно оборудование её не даёт:
            # словарь неполон, и это неизвестность, а не отказ.
            return self._Resolution(EquipmentAvailability.UNKNOWN)
        for equipment_id in sorted(candidates):
            if available.availability_of(equipment_id) is EquipmentAvailability.AVAILABLE:
                return self._Resolution(EquipmentAvailability.AVAILABLE, equipment_id)
        if any(
            available.availability_of(equipment_id) is EquipmentAvailability.UNKNOWN
            for equipment_id in candidates
        ):
            return self._Resolution(EquipmentAvailability.UNKNOWN)
        return self._Resolution(EquipmentAvailability.UNAVAILABLE)

    @staticmethod
    def _only_bodyweight(requirements: list[ExerciseEquipmentRequirement]) -> bool:
        return all(r.equipment_id == BODYWEIGHT_ID for r in requirements)

    @staticmethod
    def _target(requirement: ExerciseEquipmentRequirement) -> str:
        return requirement.equipment_id or requirement.capability_id or "unknown"
