"""Unit-тесты Equipment Compatibility Engine.

Главная проверяемая гарантия — различение UNKNOWN и INCOMPATIBLE. Отсутствие
знания о требованиях упражнения и отсутствие ответа пользователя про тренажёр не
являются доказательством несовместимости, и превращать их в «нельзя» значит
вычёркивать упражнения по причине, которой никто не устанавливал.

Тесты не обращаются к базе: словарь собирается вручную, потому что движок обязан
быть детерминированным и воспроизводимым без PostgreSQL.
"""
from __future__ import annotations

import pytest

from src.application.equipment.compatibility import (
    AvailableEquipment,
    EquipmentCompatibilityService,
    available_from_profile,
)
from src.domain.equipment import (
    AliasMatchMode,
    CompatibilityReason,
    EquipmentAlias,
    EquipmentAvailability,
    EquipmentCapability,
    EquipmentCompatibilityStatus,
    EquipmentItem,
    EquipmentProfile,
    EquipmentProfileItem,
    EquipmentRequirement,
    ExerciseEquipmentRequirement,
    KnowledgeSource,
)
from src.infrastructure.persistence.postgres.equipment_repository import (
    EquipmentIndex,
    _build_specializations,
)

EXERCISE = "Bench_Press"
SOURCE = "leszavr/workout"


def _capability(capability_id: str) -> EquipmentCapability:
    return EquipmentCapability(
        capability_id=capability_id, name=capability_id, name_ru=capability_id
    )


def _item(
    equipment_id: str,
    *,
    category: str = "machine",
    capabilities: list[str] | None = None,
    aliases: list[tuple[str, AliasMatchMode]] | None = None,
    specializes: str | None = None,
) -> EquipmentItem:
    return EquipmentItem(
        equipment_id=equipment_id,
        name=equipment_id,
        name_ru=equipment_id,
        category=category,
        capabilities=capabilities or [],
        specializes=specializes,
        aliases=[
            EquipmentAlias(alias=alias, match_mode=mode, source=KnowledgeSource.SEED)
            for alias, mode in (aliases or [])
        ],
    )


@pytest.fixture
def index() -> EquipmentIndex:
    """Небольшой словарь, покрывающий все ветви решения движка."""
    items = [
        _item("bodyweight", category="bodyweight", capabilities=["ground_support"]),
        _item("barbell", category="free_weight", capabilities=["free_weight"]),
        _item("dumbbell", category="free_weight", capabilities=["free_weight"]),
        _item("flat_bench", category="bench", capabilities=["flat_support"]),
        _item(
            "adjustable_bench",
            category="bench",
            capabilities=["flat_support", "incline_support", "adjustable_angle"],
        ),
        _item("incline_bench", category="bench", capabilities=["incline_support"]),
        _item(
            "cable_machine",
            category="cable",
            capabilities=["adjustable_resistance", "adjustable_height"],
        ),
        _item(
            "resistance_machine",
            category="machine",
            capabilities=["fixed_path", "adjustable_resistance"],
        ),
        # Частные случаи родового «силового тренажёра»: источник каталога
        # называет их одним словом `machine`.
        _item(
            "chest_press_machine",
            category="machine",
            capabilities=["fixed_path", "adjustable_resistance"],
            specializes="resistance_machine",
        ),
        _item(
            "leg_press",
            category="machine",
            capabilities=["fixed_path", "adjustable_resistance"],
            specializes="resistance_machine",
        ),
        _item("cardio_machine", category="cardio", capabilities=["continuous_cardio"]),
        _item(
            "treadmill",
            category="cardio",
            capabilities=["continuous_cardio"],
            specializes="cardio_machine",
        ),
    ]
    result = EquipmentIndex()
    for item in items:
        result.items[item.equipment_id] = item
        for capability_id in item.capabilities:
            result.capabilities.setdefault(capability_id, _capability(capability_id))
            result.providers.setdefault(capability_id, set()).add(item.equipment_id)
    _build_specializations(result)
    return result


@pytest.fixture
def service(index: EquipmentIndex) -> EquipmentCompatibilityService:
    return EquipmentCompatibilityService(index)


def _requirement(
    *,
    equipment_id: str | None = None,
    capability_id: str | None = None,
    requirement: EquipmentRequirement = EquipmentRequirement.REQUIRED,
    group: int | None = None,
) -> ExerciseEquipmentRequirement:
    return ExerciseEquipmentRequirement(
        exercise_external_id=EXERCISE,
        exercise_source=SOURCE,
        equipment_id=equipment_id,
        capability_id=capability_id,
        requirement=requirement,
        alternative_group=group,
    )


def _check(service, requirements, available):
    return service.check(
        exercise_external_id=EXERCISE,
        exercise_source=SOURCE,
        requirements=requirements,
        available=available,
    )


# --- UNKNOWN vs INCOMPATIBLE ----------------------------------------------------


def test_no_requirements_is_unknown_not_compatible(service):
    """Требования неизвестны — это не «оборудование не нужно»."""
    result = _check(service, [], AvailableEquipment(available=frozenset({"barbell"})))
    assert result.status is EquipmentCompatibilityStatus.UNKNOWN
    assert result.reason is CompatibilityReason.REQUIREMENTS_UNKNOWN


def test_unlisted_equipment_is_unknown_by_default(service):
    """Пользователь не сказал про штангу — значит неизвестно, а не «нет»."""
    result = _check(
        service,
        [_requirement(equipment_id="barbell")],
        AvailableEquipment(available=frozenset({"dumbbell"})),
    )
    assert result.status is EquipmentCompatibilityStatus.UNKNOWN
    assert result.reason is CompatibilityReason.AVAILABILITY_UNKNOWN
    assert result.unknown == ["barbell"]


def test_unlisted_becomes_unavailable_when_profile_is_exhaustive(service):
    """Домашний профиль перечисляет всё: отсутствие позиции значит «нет»."""
    result = _check(
        service,
        [_requirement(equipment_id="barbell")],
        AvailableEquipment(
            available=frozenset({"dumbbell"}), assume_unlisted_unavailable=True
        ),
    )
    assert result.status is EquipmentCompatibilityStatus.INCOMPATIBLE
    assert result.reason is CompatibilityReason.REQUIRED_EQUIPMENT_MISSING
    assert result.missing == ["barbell"]


def test_explicit_unavailable_is_incompatible(service):
    result = _check(
        service,
        [_requirement(equipment_id="barbell")],
        AvailableEquipment(unavailable=frozenset({"barbell"})),
    )
    assert result.status is EquipmentCompatibilityStatus.INCOMPATIBLE
    assert result.missing == ["barbell"]


def test_confirmed_absence_wins_over_unknown(service):
    """Подтверждённое отсутствие сильнее неизвестности.

    Иначе упражнение с недостающей штангой и неуказанной скамьёй выглядело бы
    «возможно выполнимым».
    """
    result = _check(
        service,
        [
            _requirement(equipment_id="barbell"),
            _requirement(equipment_id="flat_bench"),
        ],
        AvailableEquipment(unavailable=frozenset({"barbell"})),
    )
    assert result.status is EquipmentCompatibilityStatus.INCOMPATIBLE
    assert "barbell" in result.missing
    assert "flat_bench" in result.unknown


def test_unknown_equipment_id_is_unknown_not_incompatible(service):
    """Требование ссылается на оборудование вне словаря: это дефект данных."""
    result = _check(
        service,
        [_requirement(equipment_id="unknown_device")],
        AvailableEquipment(
            available=frozenset({"barbell"}), assume_unlisted_unavailable=True
        ),
    )
    assert result.status is EquipmentCompatibilityStatus.UNKNOWN


# --- REQUIRED -------------------------------------------------------------------


def test_all_required_available(service):
    result = _check(
        service,
        [
            _requirement(equipment_id="barbell"),
            _requirement(equipment_id="flat_bench"),
        ],
        AvailableEquipment(available=frozenset({"barbell", "flat_bench"})),
    )
    assert result.status is EquipmentCompatibilityStatus.COMPATIBLE
    assert result.reason is CompatibilityReason.ALL_REQUIRED_AVAILABLE
    assert result.matched == ["barbell", "flat_bench"]


def test_bodyweight_is_always_available(service):
    """Собственный вес — это отсутствие оборудования, а не оборудование."""
    result = _check(
        service,
        [_requirement(equipment_id="bodyweight")],
        AvailableEquipment(assume_unlisted_unavailable=True),
    )
    assert result.status is EquipmentCompatibilityStatus.COMPATIBLE
    assert result.reason is CompatibilityReason.NO_EQUIPMENT_NEEDED


def test_missing_equipment_is_reported(service):
    result = _check(
        service,
        [
            _requirement(equipment_id="barbell"),
            _requirement(equipment_id="incline_bench"),
        ],
        AvailableEquipment(
            available=frozenset({"barbell"}), assume_unlisted_unavailable=True
        ),
    )
    assert result.status is EquipmentCompatibilityStatus.INCOMPATIBLE
    assert result.missing == ["incline_bench"]
    assert result.matched == ["barbell"]


# --- OPTIONAL -------------------------------------------------------------------


def test_optional_equipment_does_not_block(service):
    """OPTIONAL описывает удобство, а не выполнимость."""
    result = _check(
        service,
        [
            _requirement(equipment_id="bodyweight"),
            _requirement(
                equipment_id="flat_bench", requirement=EquipmentRequirement.OPTIONAL
            ),
        ],
        AvailableEquipment(assume_unlisted_unavailable=True),
    )
    assert result.status is EquipmentCompatibilityStatus.COMPATIBLE


def test_optional_available_appears_in_matched(service):
    result = _check(
        service,
        [
            _requirement(equipment_id="bodyweight"),
            _requirement(
                equipment_id="flat_bench", requirement=EquipmentRequirement.OPTIONAL
            ),
        ],
        AvailableEquipment(available=frozenset({"flat_bench"})),
    )
    assert "flat_bench" in result.matched


def test_only_optional_requirements_means_no_equipment_needed(service):
    result = _check(
        service,
        [
            _requirement(
                equipment_id="flat_bench", requirement=EquipmentRequirement.OPTIONAL
            )
        ],
        AvailableEquipment(assume_unlisted_unavailable=True),
    )
    assert result.status is EquipmentCompatibilityStatus.COMPATIBLE
    assert result.reason is CompatibilityReason.NO_EQUIPMENT_NEEDED


# --- ALTERNATIVE ----------------------------------------------------------------


def _curl_alternatives() -> list[ExerciseEquipmentRequirement]:
    return [
        _requirement(
            equipment_id=equipment_id,
            requirement=EquipmentRequirement.ALTERNATIVE,
            group=1,
        )
        for equipment_id in ("dumbbell", "barbell", "cable_machine")
    ]


def test_alternative_group_satisfied_by_one_variant(service):
    result = _check(
        service,
        _curl_alternatives(),
        AvailableEquipment(
            available=frozenset({"dumbbell"}), assume_unlisted_unavailable=True
        ),
    )
    assert result.status is EquipmentCompatibilityStatus.COMPATIBLE
    assert result.reason is CompatibilityReason.ALTERNATIVE_EQUIPMENT_AVAILABLE
    assert result.matched == ["dumbbell"]


def test_alternative_group_all_missing_is_incompatible(service):
    result = _check(
        service,
        _curl_alternatives(),
        AvailableEquipment(
            available=frozenset({"flat_bench"}), assume_unlisted_unavailable=True
        ),
    )
    assert result.status is EquipmentCompatibilityStatus.INCOMPATIBLE
    assert result.reason is CompatibilityReason.NO_ALTERNATIVE_AVAILABLE
    assert set(result.missing) == {"dumbbell", "barbell", "cable_machine"}


def test_alternative_group_with_unknown_variant_is_unknown(service):
    """Хотя бы один вариант не установлен — группа могла бы быть закрыта."""
    result = _check(
        service,
        _curl_alternatives(),
        AvailableEquipment(unavailable=frozenset({"dumbbell", "barbell"})),
    )
    assert result.status is EquipmentCompatibilityStatus.UNKNOWN
    assert result.unknown == ["cable_machine"]


def test_two_alternative_groups_both_must_be_satisfied(service):
    requirements = [
        _requirement(
            equipment_id="dumbbell",
            requirement=EquipmentRequirement.ALTERNATIVE,
            group=1,
        ),
        _requirement(
            equipment_id="barbell",
            requirement=EquipmentRequirement.ALTERNATIVE,
            group=1,
        ),
        _requirement(
            equipment_id="flat_bench",
            requirement=EquipmentRequirement.ALTERNATIVE,
            group=2,
        ),
        _requirement(
            equipment_id="adjustable_bench",
            requirement=EquipmentRequirement.ALTERNATIVE,
            group=2,
        ),
    ]
    result = _check(
        service,
        requirements,
        AvailableEquipment(
            available=frozenset({"dumbbell"}), assume_unlisted_unavailable=True
        ),
    )
    assert result.status is EquipmentCompatibilityStatus.INCOMPATIBLE
    assert set(result.missing) == {"flat_bench", "adjustable_bench"}


def test_alternative_without_group_is_rejected_by_model():
    """ALTERNATIVE без группы нечитаемо: три строки неотличимы от одной группы."""
    with pytest.raises(ValueError):
        ExerciseEquipmentRequirement(
            exercise_external_id=EXERCISE,
            equipment_id="dumbbell",
            requirement=EquipmentRequirement.ALTERNATIVE,
        )


def test_requirement_must_target_exactly_one_entity():
    with pytest.raises(ValueError):
        ExerciseEquipmentRequirement(
            exercise_external_id=EXERCISE,
            equipment_id="dumbbell",
            capability_id="free_weight",
        )
    with pytest.raises(ValueError):
        ExerciseEquipmentRequirement(exercise_external_id=EXERCISE)


# --- Capabilities ---------------------------------------------------------------


def test_capability_requirement_matched_by_any_provider(service):
    """Требование возможности закрывает любое оборудование с этой возможностью."""
    result = _check(
        service,
        [_requirement(capability_id="incline_support")],
        AvailableEquipment(
            available=frozenset({"adjustable_bench"}), assume_unlisted_unavailable=True
        ),
    )
    assert result.status is EquipmentCompatibilityStatus.COMPATIBLE
    assert result.matched == ["adjustable_bench"]


def test_capability_requirement_unavailable_when_no_provider_present(service):
    result = _check(
        service,
        [_requirement(capability_id="incline_support")],
        AvailableEquipment(
            available=frozenset({"flat_bench"}), assume_unlisted_unavailable=True
        ),
    )
    assert result.status is EquipmentCompatibilityStatus.INCOMPATIBLE
    assert result.missing == ["incline_support"]


def test_capability_without_providers_is_unknown(service, index):
    """Возможность есть в словаре, но ни одно оборудование её не даёт."""
    index.capabilities["exotic_capability"] = _capability("exotic_capability")
    result = _check(
        service,
        [_requirement(capability_id="exotic_capability")],
        AvailableEquipment(
            available=frozenset({"barbell"}), assume_unlisted_unavailable=True
        ),
    )
    assert result.status is EquipmentCompatibilityStatus.UNKNOWN


def test_extra_capability_of_profile_item_satisfies_requirement(service):
    """У экземпляра в конкретном зале может быть регулировка, которой нет у типа."""
    result = _check(
        service,
        [_requirement(capability_id="incline_support")],
        AvailableEquipment(
            available=frozenset({"flat_bench"}),
            extra_capabilities={"flat_bench": frozenset({"incline_support"})},
            assume_unlisted_unavailable=True,
        ),
    )
    assert result.status is EquipmentCompatibilityStatus.COMPATIBLE
    assert result.matched == ["flat_bench"]


def test_equipment_requirement_is_not_silently_substituted(service):
    """Требование конкретного оборудования не закрывается «похожим».

    Правило «доступное покрывает возможности требуемого» проверялось и
    отвергнуто: гантели покрывают `free_weight` штанги, и жим штанги лёжа
    оказывался выполнимым с гантелями.
    """
    result = _check(
        service,
        [_requirement(equipment_id="barbell")],
        AvailableEquipment(
            available=frozenset({"dumbbell"}), assume_unlisted_unavailable=True
        ),
    )
    assert result.status is EquipmentCompatibilityStatus.INCOMPATIBLE
    assert result.missing == ["barbell"]


def test_interchangeability_is_expressed_by_capability(service):
    """Взаимозаменяемость выражается требованием возможности, а не догадкой.

    Так тренажёр другого производителя закрывает требование, и это утверждение
    записано в данных, а не выведено движком.
    """
    result = _check(
        service,
        [
            _requirement(capability_id="fixed_path"),
            _requirement(capability_id="adjustable_resistance"),
        ],
        AvailableEquipment(
            available=frozenset({"resistance_machine"}),
            assume_unlisted_unavailable=True,
        ),
    )
    assert result.status is EquipmentCompatibilityStatus.COMPATIBLE
    assert result.matched == ["resistance_machine"]


def test_interchangeability_can_be_expressed_by_alternative_group(service):
    requirements = [
        _requirement(
            equipment_id=equipment_id,
            requirement=EquipmentRequirement.ALTERNATIVE,
            group=1,
        )
        for equipment_id in ("chest_press_machine", "resistance_machine")
    ]
    result = _check(
        service,
        requirements,
        AvailableEquipment(
            available=frozenset({"resistance_machine"}),
            assume_unlisted_unavailable=True,
        ),
    )
    assert result.status is EquipmentCompatibilityStatus.COMPATIBLE
    assert result.reason is CompatibilityReason.ALTERNATIVE_EQUIPMENT_AVAILABLE


# --- Специализация оборудования -------------------------------------------------


def test_specialized_equipment_satisfies_generic_requirement(service):
    """Жим ногами закрывает требование «силовой тренажёр».

    Источник каталога указывает родовое `machine`, и без этого правила человек с
    жимом ногами получал бы «не подходит» на упражнение «жим ногами».
    """
    result = _check(
        service,
        [_requirement(equipment_id="resistance_machine")],
        AvailableEquipment(
            available=frozenset({"leg_press"}), assume_unlisted_unavailable=True
        ),
    )
    assert result.status is EquipmentCompatibilityStatus.COMPATIBLE
    assert result.reason is CompatibilityReason.SPECIALIZED_EQUIPMENT_AVAILABLE
    # Закрыто само родовое требование: выбирать между жимом ногами и разгибанием
    # ног система не может и не должна делать вид, что выбрала.
    assert result.matched == ["resistance_machine"]


def test_generic_equipment_does_not_satisfy_specialized_requirement(service):
    """Отношение направлено в одну сторону: родовое не закрывает частное.

    Упражнению, которому нужен именно жим ногами, абстрактный «силовой тренажёр»
    не подходит: у человека может быть блочная тяга.
    """
    result = _check(
        service,
        [_requirement(equipment_id="leg_press")],
        AvailableEquipment(
            available=frozenset({"resistance_machine"}),
            assume_unlisted_unavailable=True,
        ),
    )
    assert result.status is EquipmentCompatibilityStatus.INCOMPATIBLE
    assert result.missing == ["leg_press"]


def test_specialization_is_transitive(service):
    """Цепочка `treadmill` → `cardio_machine` закрывает требование верхнего уровня."""
    result = _check(
        service,
        [_requirement(equipment_id="cardio_machine")],
        AvailableEquipment(
            available=frozenset({"treadmill"}), assume_unlisted_unavailable=True
        ),
    )
    assert result.status is EquipmentCompatibilityStatus.COMPATIBLE
    assert result.matched == ["cardio_machine"]


def test_generic_unavailable_but_specialization_unknown_is_unknown(service):
    """Родовое отмечено отсутствующим, про частное не сказано — это неизвестность."""
    result = _check(
        service,
        [_requirement(equipment_id="resistance_machine")],
        AvailableEquipment(unavailable=frozenset({"resistance_machine"})),
    )
    assert result.status is EquipmentCompatibilityStatus.UNKNOWN


def test_generic_and_all_specializations_unavailable_is_incompatible(service):
    result = _check(
        service,
        [_requirement(equipment_id="resistance_machine")],
        AvailableEquipment(
            available=frozenset({"barbell"}), assume_unlisted_unavailable=True
        ),
    )
    assert result.status is EquipmentCompatibilityStatus.INCOMPATIBLE
    assert result.missing == ["resistance_machine"]


# --- Профиль как источник доступности -------------------------------------------


def test_profile_unknown_items_are_not_unavailable():
    profile = EquipmentProfile(
        profile_key="gym-1",
        name="Зал",
        items=[
            EquipmentProfileItem(
                equipment_id="barbell", availability=EquipmentAvailability.AVAILABLE
            ),
            EquipmentProfileItem(
                equipment_id="leg_press", availability=EquipmentAvailability.UNKNOWN
            ),
            EquipmentProfileItem(
                equipment_id="hack_squat",
                availability=EquipmentAvailability.UNAVAILABLE,
            ),
        ],
    )
    available = available_from_profile(profile)
    assert available.availability_of("barbell") is EquipmentAvailability.AVAILABLE
    assert available.availability_of("leg_press") is EquipmentAvailability.UNKNOWN
    assert available.availability_of("hack_squat") is EquipmentAvailability.UNAVAILABLE
    # Позиция, которой в профиле нет вообще, тоже неизвестна.
    assert available.availability_of("cable_machine") is EquipmentAvailability.UNKNOWN


def test_profile_extra_capabilities_are_carried_over():
    profile = EquipmentProfile(
        profile_key="gym-2",
        name="Зал",
        items=[
            EquipmentProfileItem(
                equipment_id="flat_bench", extra_capabilities=["incline_support"]
            )
        ],
    )
    available = available_from_profile(profile)
    assert available.extra_capabilities["flat_bench"] == frozenset({"incline_support"})


def test_exhaustive_profile_flag_is_carried_over():
    profile = EquipmentProfile(
        profile_key="home-1",
        name="Дом",
        assume_unlisted_unavailable=True,
        items=[EquipmentProfileItem(equipment_id="dumbbell")],
    )
    available = available_from_profile(profile)
    assert available.assume_unlisted_unavailable is True
    assert available.availability_of("barbell") is EquipmentAvailability.UNAVAILABLE


# --- Пакетная проверка ----------------------------------------------------------


def test_check_many_returns_result_per_exercise(service):
    requirements = {
        ("A", SOURCE): [
            ExerciseEquipmentRequirement(
                exercise_external_id="A", equipment_id="barbell"
            )
        ],
        ("B", SOURCE): [],
    }
    results = service.check_many(
        requirements_by_exercise=requirements,
        available=AvailableEquipment(available=frozenset({"barbell"})),
    )
    assert results[("A", SOURCE)].status is EquipmentCompatibilityStatus.COMPATIBLE
    assert results[("B", SOURCE)].status is EquipmentCompatibilityStatus.UNKNOWN


def test_checks_explain_every_requirement(service):
    result = _check(
        service,
        [
            _requirement(equipment_id="barbell"),
            _requirement(
                equipment_id="flat_bench", requirement=EquipmentRequirement.OPTIONAL
            ),
        ],
        AvailableEquipment(available=frozenset({"barbell"})),
    )
    assert len(result.checks) == 2
    kinds = {check.requirement for check in result.checks}
    assert kinds == {EquipmentRequirement.REQUIRED, EquipmentRequirement.OPTIONAL}
