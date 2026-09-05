"""Unit-тесты вывода альтернативных упражнений.

Проверяется главное требование этапа: система различает степень замены и не
выдаёт похожее упражнение за полную. «Полная замена» — утверждение, на которое
опирается подстановка упражнения вместо недоступного, и путать её с «похожим
движением» значит менять программу без ведома пользователя.
"""
from __future__ import annotations

import pytest

from src.application.equipment.alternatives import (
    MAX_ALTERNATIVES,
    ExerciseAlternativesBuilder,
)
from src.domain.equipment import (
    EquipmentItem,
    EquipmentRequirement,
    ExerciseEquipmentRequirement,
    SubstitutionType,
)
from src.domain.exercise import Exercise
from src.infrastructure.persistence.postgres.equipment_repository import EquipmentIndex

SOURCE = "leszavr/workout"

# (equipment_id, category, capabilities)
VOCABULARY = (
    ("barbell", "free_weight", ("free_weight",)),
    ("dumbbell", "free_weight", ("free_weight",)),
    ("flat_bench", "bench", ("flat_support",)),
    ("chest_press_machine", "machine", ("fixed_path",)),
    ("bodyweight", "bodyweight", ("ground_support",)),
)


@pytest.fixture
def index() -> EquipmentIndex:
    result = EquipmentIndex()
    for equipment_id, category, capabilities in VOCABULARY:
        result.items[equipment_id] = EquipmentItem(
            equipment_id=equipment_id,
            name=equipment_id,
            name_ru=equipment_id,
            category=category,
            capabilities=list(capabilities),
        )
    return result


@pytest.fixture
def builder(index: EquipmentIndex) -> ExerciseAlternativesBuilder:
    return ExerciseAlternativesBuilder(index)


def _exercise(
    external_id: str,
    *,
    primary: list[str] | None = None,
    secondary: list[str] | None = None,
    force: str | None = "push",
    mechanic: str | None = "compound",
    difficulty: str | None = "beginner",
    exercise_type: str = "strength",
) -> Exercise:
    return Exercise(
        external_id=external_id,
        name=external_id.replace("_", " "),
        primary_muscles=primary or ["chest"],
        secondary_muscles=secondary or ["triceps"],
        force=force,
        mechanic=mechanic,
        difficulty=difficulty,
        exercise_type=exercise_type,
    )


def _requirements(
    external_id: str, equipment_ids: list[str]
) -> list[ExerciseEquipmentRequirement]:
    return [
        ExerciseEquipmentRequirement(
            exercise_external_id=external_id,
            exercise_source=SOURCE,
            equipment_id=equipment_id,
            requirement=EquipmentRequirement.REQUIRED,
        )
        for equipment_id in equipment_ids
    ]


def _build(builder, exercises, requirements):
    return builder.build(exercises, requirements)


def _find(alternatives, source_id: str, target_id: str):
    for alternative in alternatives:
        if (
            alternative.exercise_external_id == source_id
            and alternative.alternative_external_id == target_id
        ):
            return alternative
    return None


# --- Тип замены -----------------------------------------------------------------


def test_identical_equipment_gives_exact_substitute(builder):
    left = _exercise("Barbell_Bench_Press")
    right = _exercise("Barbell_Wide_Bench_Press")
    alternatives, _ = _build(
        builder,
        [left, right],
        {
            (left.external_id, SOURCE): _requirements(left.external_id, ["barbell"]),
            (right.external_id, SOURCE): _requirements(right.external_id, ["barbell"]),
        },
    )
    match = _find(alternatives, left.external_id, right.external_id)
    assert match is not None
    assert match.substitution is SubstitutionType.EXACT


def test_same_category_different_equipment_is_similar_not_exact(builder):
    """Гантели и штанга — оба свободный вес, но это не одно упражнение.

    По категории оборудования жим гантелей объявлялся бы полной заменой жима
    штанги. Требуется равенство набора оборудования.
    """
    left = _exercise("Barbell_Bench_Press")
    right = _exercise("Dumbbell_Bench_Press")
    alternatives, _ = _build(
        builder,
        [left, right],
        {
            (left.external_id, SOURCE): _requirements(left.external_id, ["barbell"]),
            (right.external_id, SOURCE): _requirements(right.external_id, ["dumbbell"]),
        },
    )
    match = _find(alternatives, left.external_id, right.external_id)
    assert match is not None
    assert match.substitution is SubstitutionType.SIMILAR


def test_different_stability_is_similar(builder):
    """Тренажёр требует меньшей стабилизации, чем свободный вес."""
    left = _exercise("Barbell_Bench_Press")
    right = _exercise("Chest_Press_Machine")
    alternatives, _ = _build(
        builder,
        [left, right],
        {
            (left.external_id, SOURCE): _requirements(left.external_id, ["barbell"]),
            (right.external_id, SOURCE): _requirements(
                right.external_id, ["chest_press_machine"]
            ),
        },
    )
    match = _find(alternatives, left.external_id, right.external_id)
    assert match is not None
    assert match.substitution is SubstitutionType.SIMILAR


def test_different_pattern_is_partial(builder):
    """Разный характер усилия или механика — только частичная замена."""
    left = _exercise("Barbell_Bench_Press", force="push", mechanic="compound")
    right = _exercise("Cable_Crossover", force="push", mechanic="isolation")
    alternatives, _ = _build(
        builder,
        [left, right],
        {
            (left.external_id, SOURCE): _requirements(left.external_id, ["barbell"]),
            (right.external_id, SOURCE): _requirements(right.external_id, ["barbell"]),
        },
    )
    match = _find(alternatives, left.external_id, right.external_id)
    assert match is not None
    assert match.substitution is SubstitutionType.PARTIAL


def test_unknown_requirements_never_produce_exact(builder):
    """Пробел в данных не должен становиться утверждением о полной замене."""
    left = _exercise("Unknown_A")
    right = _exercise("Unknown_B")
    alternatives, _ = _build(builder, [left, right], {})
    match = _find(alternatives, left.external_id, right.external_id)
    assert match is not None
    assert match.substitution is SubstitutionType.SIMILAR


# --- Отбор кандидатов -----------------------------------------------------------


def test_different_primary_muscles_are_not_alternatives(builder):
    left = _exercise("Bench_Press", primary=["chest"])
    right = _exercise("Calf_Raise", primary=["calves"])
    alternatives, report = _build(
        builder,
        [left, right],
        {
            (left.external_id, SOURCE): _requirements(left.external_id, ["barbell"]),
            (right.external_id, SOURCE): _requirements(right.external_id, ["barbell"]),
        },
    )
    assert alternatives == []
    assert report.exercises_with_alternatives == 0


def test_partial_muscle_overlap_is_not_alternative(builder):
    """Упражнение на грудь и трицепс не заменяет упражнение только на грудь."""
    left = _exercise("Chest_Only", primary=["chest"])
    right = _exercise("Chest_And_Triceps", primary=["chest", "triceps"])
    alternatives, _ = _build(
        builder,
        [left, right],
        {
            (left.external_id, SOURCE): _requirements(left.external_id, ["barbell"]),
            (right.external_id, SOURCE): _requirements(right.external_id, ["barbell"]),
        },
    )
    assert _find(alternatives, left.external_id, right.external_id) is None


def test_exercise_is_not_its_own_alternative(builder):
    single = _exercise("Only_One")
    alternatives, _ = _build(
        builder,
        [single],
        {(single.external_id, SOURCE): _requirements(single.external_id, ["barbell"])},
    )
    assert alternatives == []


def test_exercise_without_primary_muscles_is_skipped(builder):
    left = Exercise(external_id="No_Muscles", name="No muscles", primary_muscles=[])
    right = _exercise("Bench_Press")
    alternatives, _ = _build(builder, [left, right], {})
    assert all(
        a.exercise_external_id != left.external_id for a in alternatives
    )


def test_alternatives_are_capped_per_exercise(builder):
    exercises = [_exercise(f"Variant_{i}") for i in range(MAX_ALTERNATIVES + 4)]
    requirements = {
        (e.external_id, SOURCE): _requirements(e.external_id, ["barbell"])
        for e in exercises
    }
    alternatives, report = _build(builder, exercises, requirements)
    for exercise in exercises:
        count = sum(
            1 for a in alternatives if a.exercise_external_id == exercise.external_id
        )
        assert count == MAX_ALTERNATIVES
    assert report.alternatives_total == len(exercises) * MAX_ALTERNATIVES


def test_lower_score_candidates_are_dropped_first(builder):
    """При переполнении остаются лучшие совпадения, а не произвольные."""
    base = _exercise("Base")
    twins = [_exercise(f"Twin_{i}") for i in range(MAX_ALTERNATIVES)]
    weak = _exercise("Weak", difficulty="expert", secondary=["shoulders"])
    exercises = [base, *twins, weak]
    requirements = {
        (e.external_id, SOURCE): _requirements(e.external_id, ["barbell"])
        for e in exercises
    }
    alternatives, _ = _build(builder, exercises, requirements)
    for_base = {
        a.alternative_external_id
        for a in alternatives
        if a.exercise_external_id == base.external_id
    }
    assert weak.external_id not in for_base


# --- Обоснование ----------------------------------------------------------------


def test_rationale_explains_the_match(builder):
    left = _exercise("Barbell_Bench_Press")
    right = _exercise("Dumbbell_Bench_Press")
    alternatives, _ = _build(
        builder,
        [left, right],
        {
            (left.external_id, SOURCE): _requirements(left.external_id, ["barbell"]),
            (right.external_id, SOURCE): _requirements(right.external_id, ["dumbbell"]),
        },
    )
    match = _find(alternatives, left.external_id, right.external_id)
    assert match is not None
    rationale = match.rationale
    assert rationale["same_pattern"] is True
    assert rationale["same_equipment"] is False
    assert rationale["equipment_known"] is True
    assert rationale["equipment"] == ["dumbbell"]
    assert rationale["primary_muscles"] == ["chest"]


def test_score_is_bounded(builder):
    exercises = [_exercise("A"), _exercise("B", difficulty="expert")]
    requirements = {
        (e.external_id, SOURCE): _requirements(e.external_id, ["barbell"])
        for e in exercises
    }
    alternatives, _ = _build(builder, exercises, requirements)
    assert alternatives
    assert all(0.0 <= a.score <= 1.0 for a in alternatives)


def test_report_counts_substitution_types(builder):
    left = _exercise("Barbell_Bench_Press")
    right = _exercise("Dumbbell_Bench_Press")
    _, report = _build(
        builder,
        [left, right],
        {
            (left.external_id, SOURCE): _requirements(left.external_id, ["barbell"]),
            (right.external_id, SOURCE): _requirements(right.external_id, ["dumbbell"]),
        },
    )
    assert report.exercises_total == 2
    assert report.exercises_with_alternatives == 2
    assert sum(report.by_substitution.values()) == report.alternatives_total
