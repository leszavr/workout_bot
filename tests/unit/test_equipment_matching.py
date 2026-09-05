"""Unit-тесты сопоставления оборудования и импорта знания из каталога.

Проверяется главное свойство новой модели: словарь синонимов — это данные, а не
Python-код, и сопоставление ничего не теряет молча. Значение источника, которому
не нашлось canonical ID, обязано остаться видимым, а не исчезнуть.
"""
from __future__ import annotations

import pytest

from src.application.equipment.import_service import (
    AMBIGUOUS_CATALOG_VALUE,
    EquipmentKnowledgeImporter,
)
from src.application.equipment.matching import EquipmentMatcher
from src.domain.equipment import (
    AliasMatchMode,
    EquipmentAlias,
    EquipmentItem,
    EquipmentRequirement,
    KnowledgeConfidence,
    KnowledgeSource,
    UnmappedReason,
)
from src.domain.exercise import Exercise
from src.infrastructure.persistence.postgres.equipment_repository import (
    EquipmentIndex,
    normalize_alias,
)

# Словарь тестов повторяет структуру реального: значения каталога приходят
# полными совпадениями, формулировки пользователя — основами слов.
VOCABULARY: tuple[tuple[str, str, tuple[tuple[str, AliasMatchMode], ...]], ...] = (
    (
        "barbell",
        "free_weight",
        (("barbell", AliasMatchMode.EXACT), ("штанг", AliasMatchMode.STEM)),
    ),
    (
        "dumbbell",
        "free_weight",
        (("dumbbell", AliasMatchMode.EXACT), ("гантел", AliasMatchMode.STEM)),
    ),
    (
        "bodyweight",
        "bodyweight",
        (("body only", AliasMatchMode.EXACT),),
    ),
    (
        "ez_curl_bar",
        "free_weight",
        (("e-z curl bar", AliasMatchMode.EXACT),),
    ),
    (
        "medicine_ball",
        "ball",
        (
            ("medicine ball", AliasMatchMode.EXACT),
            # Неоднозначный exact-синоним: таблица синонимов уникальна по паре
            # (alias, equipment_id) и допускает несколько целей. Сопоставление
            # обязано вернуть все, а не выбрать первую.
            ("ball", AliasMatchMode.EXACT),
            ("мяч", AliasMatchMode.STEM),
        ),
    ),
    (
        "exercise_ball",
        "ball",
        (
            ("exercise ball", AliasMatchMode.EXACT),
            ("ball", AliasMatchMode.EXACT),
            ("мяч", AliasMatchMode.STEM),
        ),
    ),
    (
        "ab_wheel",
        "accessory",
        (("ab roller", AliasMatchMode.EXACT),),
    ),
    (
        "weight_sled",
        "strongman",
        (("sled", AliasMatchMode.EXACT),),
    ),
    (
        "plyo_box",
        "support",
        (("box", AliasMatchMode.EXACT),),
    ),
)


@pytest.fixture
def index() -> EquipmentIndex:
    result = EquipmentIndex()
    for equipment_id, category, aliases in VOCABULARY:
        result.items[equipment_id] = EquipmentItem(
            equipment_id=equipment_id,
            name=equipment_id,
            name_ru=equipment_id,
            category=category,
            aliases=[
                EquipmentAlias(
                    alias=alias, match_mode=mode, source=KnowledgeSource.SEED
                )
                for alias, mode in aliases
            ],
        )
        for alias, mode in aliases:
            bucket = (
                result.exact_aliases
                if mode is AliasMatchMode.EXACT
                else result.stem_aliases
            )
            bucket.setdefault(normalize_alias(alias), set()).add(equipment_id)
    return result


@pytest.fixture
def matcher(index: EquipmentIndex) -> EquipmentMatcher:
    return EquipmentMatcher(index)


def _exercise(
    external_id: str,
    *,
    name: str | None = None,
    equipment: list[str] | None = None,
    exercise_type: str = "strength",
) -> Exercise:
    return Exercise(
        external_id=external_id,
        name=name or external_id.replace("_", " "),
        equipment=equipment or [],
        exercise_type=exercise_type,
        primary_muscles=["chest"],
        difficulty="beginner",
    )


# --- Нормализация ---------------------------------------------------------------


def test_normalize_alias_folds_case_spacing_and_yo():
    assert normalize_alias("  Тренажёр   Смита ") == "тренажер смита"
    assert normalize_alias("BODY ONLY") == "body only"


# --- Значения каталога ----------------------------------------------------------


def test_catalog_value_matched_exactly(matcher):
    match = matcher.match_catalog_value("barbell")
    assert match.single == "barbell"
    assert not match.ambiguous


def test_catalog_value_with_hyphen_and_case(matcher):
    assert matcher.match_catalog_value("E-Z Curl Bar").single == "ez_curl_bar"


def test_catalog_value_body_only_maps_to_bodyweight(matcher):
    assert matcher.match_catalog_value("body only").single == "bodyweight"


def test_catalog_value_other_is_not_matched(matcher):
    """`other` не оборудование, а отсутствие сведений о нём."""
    match = matcher.match_catalog_value(AMBIGUOUS_CATALOG_VALUE)
    assert not match.matched


def test_catalog_value_canonical_id_is_accepted(matcher):
    """Уже нормализованное значение сопоставляется само с собой."""
    assert matcher.match_catalog_value("weight_sled").single == "weight_sled"


def test_ambiguous_alias_returns_all_targets(matcher):
    """«Мяч» законно означает и медбол, и фитбол."""
    result = matcher.match_text("есть большой мяч")
    assert not result.confident
    assert result.ambiguous["мяч"] == ("exercise_ball", "medicine_ball")


# --- Свободный текст ------------------------------------------------------------


def test_free_text_matches_by_stem(matcher):
    result = matcher.match_text("две гантели по 16 кг и штанга")
    assert result.confident == {"dumbbell", "barbell"}


def test_free_text_matches_exact_alias_inside_phrase(matcher):
    result = matcher.match_text("в зале есть barbell и ab roller")
    assert result.confident == {"barbell", "ab_wheel"}


def test_short_alias_does_not_match_inside_word(matcher):
    """`box` внутри `boxing` — не тумба; границы слова обязательны."""
    result = matcher.match_text("boxing training")
    assert "plyo_box" not in result.confident


def test_plural_form_of_exact_alias_matches(matcher):
    result = matcher.match_text("Atlas Stones and sleds")
    assert "weight_sled" in result.confident


def test_empty_text_yields_nothing(matcher):
    assert not matcher.match_text(None).any_match
    assert not matcher.match_text("   ").any_match


def test_confident_match_overrides_ambiguity_across_values(matcher):
    """Однозначное совпадение сильнее неоднозначного в другой фразе."""
    result = matcher.match_values(["мяч", "medicine ball"])
    assert "medicine_ball" in result.confident
    assert "мяч" not in result.ambiguous


# --- Импорт из каталога ---------------------------------------------------------


def test_catalog_value_becomes_confirmed_requirement(index):
    importer = EquipmentKnowledgeImporter(index)
    plan = importer.build_plan([_exercise("Bench_Press", equipment=["barbell"])])
    assert len(plan.requirements) == 1
    requirement = plan.requirements[0]
    assert requirement.equipment_id == "barbell"
    assert requirement.requirement is EquipmentRequirement.REQUIRED
    assert requirement.confidence is KnowledgeConfidence.CONFIRMED
    assert requirement.source is KnowledgeSource.CATALOG_IMPORT
    assert plan.report.mapped_exercises == 1


def test_other_value_is_recorded_as_ambiguous_not_dropped(index):
    """Информация не теряется молча: `other` остаётся видимым пробелом."""
    importer = EquipmentKnowledgeImporter(index)
    plan = importer.build_plan(
        [_exercise("Atlas_Stones", equipment=[AMBIGUOUS_CATALOG_VALUE])]
    )
    assert not plan.requirements
    assert len(plan.unmapped) == 1
    assert plan.unmapped[0].reason is UnmappedReason.AMBIGUOUS
    assert plan.report.unknown_exercises == 1


def test_unknown_value_is_recorded_as_unmapped(index):
    importer = EquipmentKnowledgeImporter(index)
    plan = importer.build_plan([_exercise("X", equipment=["hydraulic press"])])
    assert plan.unmapped[0].reason is UnmappedReason.UNMAPPED
    assert plan.report.unmapped_details["hydraulic press"] == 1


def test_ambiguous_value_becomes_alternative_group(index):
    """Значение с несколькими целями — это «одно из», а не выбор первого."""
    importer = EquipmentKnowledgeImporter(index)
    plan = importer.build_plan([_exercise("Ball_Crunch", equipment=["ball"])])
    groups = {r.alternative_group for r in plan.requirements}
    assert groups == {1}
    assert all(
        r.requirement is EquipmentRequirement.ALTERNATIVE for r in plan.requirements
    )
    assert {r.equipment_id for r in plan.requirements} == {
        "medicine_ball",
        "exercise_ball",
    }
    # И одновременно помечено как требующее уточнения.
    assert plan.unmapped[0].reason is UnmappedReason.AMBIGUOUS


def test_name_inference_used_when_catalog_value_is_missing(index):
    """`Ab_Roller` без значения оборудования получает вывод по названию."""
    importer = EquipmentKnowledgeImporter(index)
    plan = importer.build_plan(
        [_exercise("Ab_Roller", name="Ab Roller", equipment=[AMBIGUOUS_CATALOG_VALUE])]
    )
    inferred = [r for r in plan.requirements if r.source is KnowledgeSource.NAME_INFERENCE]
    assert [r.equipment_id for r in inferred] == ["ab_wheel"]
    assert inferred[0].confidence is KnowledgeConfidence.INFERRED


def test_catalog_value_wins_over_name_inference(index):
    """Значение источника сильнее догадки по названию."""
    importer = EquipmentKnowledgeImporter(index)
    plan = importer.build_plan(
        [_exercise("Dumbbell_Bench_Press", name="Dumbbell Bench Press", equipment=["barbell"])]
    )
    assert [r.equipment_id for r in plan.requirements] == ["barbell"]


def test_strength_exercise_without_equipment_stays_unknown(index):
    """Пропущенный снаряд в силовом упражнении опаснее пробела в данных."""
    importer = EquipmentKnowledgeImporter(index)
    plan = importer.build_plan(
        [_exercise("Scapular_Pull-Up", equipment=[], exercise_type="strength")]
    )
    assert not plan.requirements
    assert plan.report.unknown_exercises == 1


def test_stretching_without_equipment_becomes_inferred_bodyweight(index):
    importer = EquipmentKnowledgeImporter(index)
    plan = importer.build_plan(
        [_exercise("Cat_Stretch", equipment=[], exercise_type="stretching")]
    )
    assert [r.equipment_id for r in plan.requirements] == ["bodyweight"]
    assert plan.requirements[0].confidence is KnowledgeConfidence.INFERRED


def test_duplicate_catalog_values_produce_single_requirement(index):
    importer = EquipmentKnowledgeImporter(index)
    plan = importer.build_plan(
        [_exercise("Squat", equipment=["barbell", "Barbell"])]
    )
    assert len(plan.requirements) == 1
    assert plan.report.duplicates_skipped == 1


def test_report_counts_catalog_values(index):
    importer = EquipmentKnowledgeImporter(index)
    plan = importer.build_plan(
        [
            _exercise("A", equipment=["barbell"]),
            _exercise("B", equipment=["barbell"]),
            _exercise("C", equipment=["dumbbell"]),
        ]
    )
    assert plan.report.value_counts == {"barbell": 2, "dumbbell": 1}
    assert plan.report.mapped_values == {"barbell": "barbell", "dumbbell": "dumbbell"}
