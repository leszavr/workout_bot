"""Unit-тесты качества внешних записей, решений и правил слияния.

Проверяется, что качество не сводится к числу: запись без техники отклоняется
независимо от остальных полей, сомнительная запись не попадает в каталог
автоматически, а обогащение не перезаписывает проверенные данные caталога
непроверенными.
"""
from __future__ import annotations

import pytest

from src.application.ingestion.candidates import (
    CandidateMedia,
    ExternalExerciseCandidate,
)
from src.application.ingestion.equipment_tags import (
    TAG_BARBELL,
    TAG_BODY_ONLY,
    TAG_MACHINE,
    TAG_OTHER,
    field_tags,
)
from src.application.ingestion.matching import MatchResult
from src.application.ingestion.merge_policy import (
    FIELD_ALIASES,
    FIELD_MEDIA,
    FIELD_PRIMARY_MUSCLES,
    FIELD_TECHNIQUE,
    FIELD_TECHNIQUE_RU,
    build_enrichment_plan,
    decide,
)
from src.application.ingestion.quality import QualityScorer
from src.domain.ingestion import IngestionDecision, QualityStatus

FULL_TECHNIQUE = (
    "1. Лягте на скамью и возьмите штангу хватом чуть шире плеч.\n"
    "2. Опустите штангу к середине груди, затем выжмите вверх."
)
FULL_TECHNIQUE_RU = FULL_TECHNIQUE

EQUIPMENT = {
    "barbell": "barbell",
    "dumbbell": "dumbbell",
    "body weight": "bodyweight",
    "leverage machine": "resistance_machine",
    "cable": "cable_machine",
}


def resolve(values: list[str]) -> tuple[frozenset[str], tuple[str, ...]]:
    found: set[str] = set()
    unmapped: list[str] = []
    for value in values:
        cleaned = (value or "").strip().lower()
        if not cleaned:
            continue
        mapped = EQUIPMENT.get(cleaned)
        if mapped:
            found.add(mapped)
        else:
            unmapped.append(cleaned)
    return frozenset(found), tuple(unmapped)


scorer = QualityScorer(resolve)


def candidate(
    name: str = "Barbell Bench Press",
    *,
    technique: str | None = FULL_TECHNIQUE,
    technique_ru: str | None = FULL_TECHNIQUE_RU,
    equipment: tuple[str, ...] = ("barbell",),
    target: str | None = "pectorals",
    secondary: tuple[str, ...] = ("triceps",),
    media: tuple[CandidateMedia, ...] = (
        CandidateMedia(media_type="image", relative_path="images/1.jpg"),
    ),
    description: str | None = None,
    raw_name: str | None = None,
) -> ExternalExerciseCandidate:
    return ExternalExerciseCandidate(
        source_key="test/source",
        source_version="v1",
        source_record_id="1",
        raw_name=raw_name or name,
        name=name,
        description=description,
        technique=technique,
        technique_ru=technique_ru,
        equipment_values=equipment,
        primary_muscle_values=(target,) if target else (),
        secondary_muscle_values=secondary,
        media=media,
    )


# --- Оценка качества ------------------------------------------------------------


def test_complete_record_is_ready():
    assessment = scorer.assess(candidate())
    assert assessment.status is QualityStatus.READY
    assert assessment.score >= 0.9
    assert not assessment.questionable


def test_missing_technique_is_rejected_regardless_of_other_fields():
    """Отсутствие техники — отказ, а не низкий балл.

    У записи заполнено всё остальное: оборудование, мышцы, медиа, описание. Если
    качество считалось бы только суммой весов, она набрала бы проходной балл — и
    в программу пользователя попало бы упражнение без объяснения, как его
    выполнять.
    """
    assessment = scorer.assess(
        candidate(technique=None, description="Описание есть")
    )
    assert assessment.status is QualityStatus.REJECT
    assert "technique_missing" in assessment.reasons


def test_truncated_technique_is_questionable():
    assessment = scorer.assess(candidate(technique="1. Опустить и поднять"))
    assert assessment.questionable
    assert "technique_too_short" in assessment.reasons
    assert assessment.status is QualityStatus.REVIEW


def test_missing_russian_technique_lowers_score_but_not_status():
    assessment = scorer.assess(candidate(technique_ru=None))
    assert "technique_ru_missing" in assessment.reasons
    assert assessment.status is QualityStatus.READY


def test_unmapped_equipment_is_reported_not_silently_dropped():
    assessment = scorer.assess(candidate(equipment=("weighted",)))
    assert assessment.equipment_ids == frozenset()
    assert assessment.unmapped_equipment == ("weighted",)
    assert "equipment_unmapped" in assessment.reasons


def test_missing_equipment_differs_from_unmapped_equipment():
    assessment = scorer.assess(candidate(equipment=()))
    assert "equipment_missing" in assessment.reasons
    assert assessment.unmapped_equipment == ()


def test_missing_media_lowers_score_only():
    with_media = scorer.assess(candidate())
    without_media = scorer.assess(candidate(media=()))
    assert without_media.score < with_media.score
    assert "media_missing" in without_media.reasons


def test_broken_encoding_makes_record_questionable():
    assessment = scorer.assess(candidate(name="sled 45 degrees press", raw_name="sled 45в° press"))
    assert assessment.questionable
    assert "name_encoding_broken" in assessment.reasons


def test_ambiguous_muscles_are_reported_and_not_used():
    assessment = scorer.assess(candidate(target="upper back", secondary=("core",)))
    assert assessment.primary_muscles == ()
    assert "upper back" in assessment.ambiguous_muscles
    assert "core" in assessment.ambiguous_muscles


def test_low_quality_record_is_rejected_by_score():
    assessment = scorer.assess(
        candidate(
            technique="1. Один шаг\n2. Второй шаг для длины строки более порога",
            technique_ru=None,
            equipment=("unknown thing",),
            target=None,
            secondary=(),
            media=(),
        )
    )
    assert assessment.status is QualityStatus.REJECT


# --- Решения --------------------------------------------------------------------


def matched(confidence: float = 0.95) -> MatchResult:
    return MatchResult(
        external_id="Existing",
        source="leszavr/workout",
        confidence=confidence,
        reasons=["normalized_name_match"],
        identical=True,
    )


def test_existing_without_new_fields():
    decision, note = decide(candidate(), matched(), scorer.assess(candidate()), enrichment=None)
    assert decision is IngestionDecision.EXISTING
    assert "не добавляют" in note


def test_enrichable_when_plan_changes_fields():
    plan = build_enrichment_plan(
        candidate(),
        scorer.assess(candidate()),
        canonical_name="Жим штанги лёжа",
        canonical_technique=None,
        canonical_technique_ru=None,
        canonical_description=None,
        canonical_aliases=[],
        canonical_primary=["chest"],
        canonical_secondary=["triceps"],
        canonical_has_media=True,
    )
    decision, note = decide(
        candidate(), matched(), scorer.assess(candidate()), enrichment=plan
    )
    assert decision is IngestionDecision.ENRICHABLE
    assert FIELD_TECHNIQUE in note


def test_low_quality_wins_over_match():
    """Запись без техники не обогащает существующее упражнение.

    Совпадение найдено с уверенностью 0.95, но обогащать нечем: техники нет.
    """
    poor = candidate(technique=None)
    decision, _ = decide(poor, matched(), scorer.assess(poor), enrichment=None)
    assert decision is IngestionDecision.LOW_QUALITY


def test_questionable_record_is_not_imported_automatically():
    suspicious = candidate(technique="1. Коротко")
    decision, _ = decide(
        suspicious, MatchResult(), scorer.assess(suspicious), enrichment=None
    )
    assert decision is IngestionDecision.QUESTIONABLE


def test_weakly_confirmed_identity_goes_to_review():
    """Тождество, опирающееся на неизвестность, решает человек."""
    weak = MatchResult(
        external_id="Existing",
        source="leszavr/workout",
        confidence=0.4,
        reasons=["movement_core_match", "equipment_unknown", "target_unknown"],
        identical=True,
    )
    decision, note = decide(candidate(), weak, scorer.assess(candidate()), enrichment=None)
    assert decision is IngestionDecision.UNKNOWN
    assert "решение за человеком" in note


def test_variant_of_existing_is_a_new_exercise():
    """Различие способа выполнения даёт отдельное упражнение, а не дубль."""
    variant = MatchResult(
        external_id="Existing",
        source="leszavr/workout",
        confidence=0.6,
        reasons=["movement_core_match", "variant_tokens_differ"],
        identical=False,
        variant_of="Existing",
    )
    decision, note = decide(
        candidate(), variant, scorer.assess(candidate()), enrichment=None
    )
    assert decision is IngestionDecision.NEW_RELEVANT
    assert "Existing" in note


def test_no_match_with_ready_quality_is_new():
    decision, _ = decide(
        candidate(), MatchResult(), scorer.assess(candidate()), enrichment=None
    )
    assert decision is IngestionDecision.NEW_RELEVANT


def test_review_quality_without_match_is_unknown():
    """Пограничное качество без соответствия — не новое упражнение, а review."""
    weak = candidate(equipment=("weighted",), target=None, media=())
    assessment = scorer.assess(weak)
    assert assessment.status is QualityStatus.REVIEW
    decision, _ = decide(weak, MatchResult(), assessment, enrichment=None)
    assert decision is IngestionDecision.UNKNOWN


# --- Правила слияния ------------------------------------------------------------


def plan_for(**kwargs):
    defaults = dict(
        canonical_name="Жим штанги лёжа",
        canonical_technique=None,
        canonical_technique_ru=None,
        canonical_description=None,
        canonical_aliases=[],
        canonical_primary=[],
        canonical_secondary=[],
        canonical_has_media=False,
    )
    defaults.update(kwargs)
    item = defaults.pop("candidate", candidate())
    return build_enrichment_plan(item, scorer.assess(item), **defaults)


def test_missing_technique_is_filled():
    plan = plan_for()
    assert plan.fields[FIELD_TECHNIQUE] == FULL_TECHNIQUE
    assert plan.reasons[FIELD_TECHNIQUE] == "filled_missing_value"


def test_longer_technique_replaces_shorter():
    plan = plan_for(canonical_technique="1. Один шаг")
    assert FIELD_TECHNIQUE in plan.fields
    assert plan.reasons[FIELD_TECHNIQUE] == "more_complete_than_canonical"


def test_equal_length_technique_is_not_replaced():
    """Другая формулировка не является лучшей: canonical каталог вычитан."""
    plan = plan_for(canonical_technique="1. Первый шаг\n2. Второй шаг")
    assert FIELD_TECHNIQUE not in plan.fields


def test_russian_technique_is_added_when_absent():
    plan = plan_for(canonical_technique=FULL_TECHNIQUE)
    assert FIELD_TECHNIQUE_RU in plan.fields


def test_external_name_is_kept_as_alias():
    """В план кладётся добавляемое название, а не готовый список.

    Одно упражнение обогащают несколько внешних записей, и готовый список,
    посчитанный от одного исходного состояния, затирал бы синоним, добавленный
    предыдущей записью.
    """
    plan = plan_for(canonical_aliases=["Жим лёжа"])
    assert plan.fields[FIELD_ALIASES] == "Barbell Bench Press"


def test_alias_is_not_duplicated():
    plan = plan_for(canonical_aliases=["Barbell Bench Press"])
    assert FIELD_ALIASES not in plan.fields


def test_muscles_are_not_overwritten_when_canonical_knows_them():
    """Мышцы caталога не заменяются: генератор относит упражнение к роли по ним."""
    plan = plan_for(canonical_primary=["shoulders"])
    assert FIELD_PRIMARY_MUSCLES not in plan.fields


def test_muscles_are_filled_when_canonical_has_none():
    plan = plan_for(canonical_primary=[])
    assert plan.fields[FIELD_PRIMARY_MUSCLES] == ("chest",)


def test_media_is_added_only_when_canonical_has_none():
    assert plan_for(canonical_has_media=False).media
    assert not plan_for(canonical_has_media=True).media
    assert FIELD_MEDIA in plan_for(canonical_has_media=False).reasons


def test_plan_without_changes_reports_nothing():
    plan = plan_for(
        canonical_technique=FULL_TECHNIQUE + "\n3. Третий шаг",
        canonical_technique_ru=FULL_TECHNIQUE_RU + "\n3. Третий шаг",
        canonical_description="Есть",
        canonical_aliases=["Barbell Bench Press"],
        canonical_primary=["chest"],
        canonical_secondary=["triceps"],
        canonical_has_media=True,
    )
    assert not plan.changes_anything


# --- Значения поля equipment ----------------------------------------------------


@pytest.mark.parametrize(
    ("equipment_ids", "expected"),
    [
        (frozenset({"barbell"}), [TAG_BARBELL]),
        (frozenset({"bodyweight"}), [TAG_BODY_ONLY]),
        (frozenset({"smith_machine"}), [TAG_MACHINE]),
        (frozenset({"leg_press"}), [TAG_MACHINE]),
        (frozenset({"treadmill"}), [TAG_MACHINE]),
    ],
)
def test_canonical_equipment_maps_to_field_vocabulary(equipment_ids, expected):
    assert field_tags(equipment_ids, has_unmapped_values=False) == expected


def test_unmapped_source_value_becomes_other():
    """`weighted` не исчезает: он означает «оборудование нужно, какое — неясно»."""
    assert field_tags(frozenset(), has_unmapped_values=True) == [TAG_OTHER]


def test_equipment_outside_field_vocabulary_becomes_other():
    assert field_tags(frozenset({"battle_ropes"}), has_unmapped_values=False) == [
        TAG_OTHER
    ]


def test_known_and_unknown_equipment_are_both_reported():
    tags = field_tags(frozenset({"barbell", "battle_ropes"}), has_unmapped_values=False)
    assert tags == [TAG_BARBELL, TAG_OTHER]


def test_alias_equal_to_canonical_name_is_not_added():
    """Синоним, равный названию, не добавляется: иначе импорт не идемпотентен."""
    plan = plan_for(canonical_name="Barbell Bench Press")
    assert FIELD_ALIASES not in plan.fields
