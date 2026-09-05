"""Unit-тесты нормализации и entity resolution внешних источников.

Проверяется главное свойство слоя: дедупликация не теряет упражнения и не плодит
их. Оба направления ошибки проверяются явно, потому что они несимметричны по
последствиям: ложное объединение молча уничтожает упражнение, ложное разделение
даёт дубль в каталоге.

Случаи взяты из требования этапа дословно (`Barbell Bench Press` против
`Barbell Bench Press - Medium Grip`, `Dumbbell Biceps Curl` против
`Dumbbell Curl`) и из реальных расхождений источников, найденных при dry-run.
"""
from __future__ import annotations

import pytest

from src.application.ingestion.candidates import (
    CandidateMedia,
    ExternalExerciseCandidate,
)
from src.application.ingestion.matching import (
    ExerciseMatcher,
    build_canonical_features,
    build_equipment_context,
)
from src.application.ingestion.muscles import (
    MuscleRelation,
    map_muscles,
    resolve_muscle,
)
from src.application.ingestion.normalization import (
    core_tokens,
    latin_name_key,
    name_key,
    normalize_name,
    semantic_variant_tokens,
    steps_to_technique,
    strip_presentation_markers,
    transliterate,
)
from src.domain.exercise import Exercise

SOURCE_KEY = "test/source"
SOURCE_VERSION = "v1"

# Словарь оборудования тестов. Соответствие «формулировка → canonical ID» здесь
# задано таблицей, а не обращением к базе: сопоставление не должно зависеть от
# PostgreSQL, и тест это фиксирует.
EQUIPMENT_ALIASES = {
    "barbell": "barbell",
    "dumbbell": "dumbbell",
    "dumbbells": "dumbbell",
    "cable": "cable_machine",
    "band": "resistance_band",
    "bands": "resistance_band",
    "body weight": "bodyweight",
    "body only": "bodyweight",
    "machine": "resistance_machine",
    "leverage machine": "resistance_machine",
    "lever": "resistance_machine",
    "smith machine": "smith_machine",
    "stability ball": "exercise_ball",
    "exercise ball": "exercise_ball",
    "bosu ball": "bosu_ball",
    "medicine ball": "medicine_ball",
    "kettlebell": "kettlebell",
}


def resolve_values(values: list[str]) -> frozenset[str]:
    found = set()
    for value in values:
        mapped = EQUIPMENT_ALIASES.get((value or "").strip().lower())
        if mapped:
            found.add(mapped)
    return frozenset(found)


def resolve_phrases(phrases: list[str]) -> frozenset[str]:
    return resolve_values(phrases)


def candidate(
    name: str,
    *,
    record_id: str = "1",
    equipment: tuple[str, ...] = (),
    target: str | None = None,
    secondary: tuple[str, ...] = (),
    technique: str | None = "1. Шаг один длиной больше шестидесяти символов для порога\n2. Шаг два",
    technique_ru: str | None = "1. Русский шаг",
    media: tuple[CandidateMedia, ...] = (),
) -> ExternalExerciseCandidate:
    return ExternalExerciseCandidate(
        source_key=SOURCE_KEY,
        source_version=SOURCE_VERSION,
        source_record_id=record_id,
        raw_name=name,
        name=normalize_name(name),
        technique=technique,
        technique_ru=technique_ru,
        equipment_values=equipment,
        primary_muscle_values=(target,) if target else (),
        secondary_muscle_values=secondary,
        media=media,
    )


def exercise(
    external_id: str,
    name: str,
    *,
    equipment: list[str] | None = None,
    primary: list[str] | None = None,
    secondary: list[str] | None = None,
    name_ru: str | None = None,
    aliases: list[str] | None = None,
    technique: str | None = "1. Шаг",
    technique_ru: str | None = None,
) -> Exercise:
    return Exercise(
        external_id=external_id,
        source="leszavr/workout",
        name=name,
        name_ru=name_ru,
        aliases=aliases or [],
        technique=technique,
        technique_ru=technique_ru,
        primary_muscles=primary or [],
        secondary_muscles=secondary or [],
        equipment=equipment or [],
    )


def matcher_for(exercises: list[Exercise], links: dict | None = None) -> ExerciseMatcher:
    features = build_canonical_features(exercises, resolve_values, resolve_phrases)
    return ExerciseMatcher(features, source_links=links)


def match(
    matcher: ExerciseMatcher, item: ExternalExerciseCandidate
):
    context = build_equipment_context(
        name=item.name,
        declared_values=list(item.equipment_values),
        resolve_values=resolve_values,
        resolve_phrases=resolve_phrases,
    )
    return matcher.match(item, equipment=context)


# --- Нормализация ---------------------------------------------------------------


def test_word_order_does_not_change_key():
    assert name_key("Barbell Bench Press") == name_key("Bench Press - Barbell")


def test_significant_token_changes_key():
    assert name_key("Barbell Bench Press") != name_key(
        "Barbell Bench Press - Medium Grip"
    )


def test_abbreviation_is_expanded():
    assert name_key("DB Biceps Curl") == name_key("Dumbbell Biceps Curl")


def test_muscle_name_is_not_part_of_movement_core():
    assert core_tokens("Dumbbell Biceps Curl") == core_tokens("Dumbbell Curl")


def test_movement_phrase_keeps_body_part():
    # `Chest Press` и `Shoulder Press` не должны слиться: слово части тела здесь
    # определяет движение, а не мышцу.
    assert core_tokens("Chest Press") != core_tokens("Shoulder Press")


def test_grip_is_semantic_variant():
    assert "grip" in semantic_variant_tokens("Barbell Bench Press - Medium Grip")
    assert semantic_variant_tokens("Barbell Bench Press") == frozenset()


def test_equipment_word_is_not_semantic_variant():
    # Снаряд сравнивается по canonical ID, а не по слову: `band` и `bands` — одно
    # и то же, и различие написания не является различием упражнения.
    assert semantic_variant_tokens("Band Bench Press") == semantic_variant_tokens(
        "Bench Press - With Bands"
    )


def test_presentation_markers_are_stripped_from_key():
    assert name_key("barbell full squat (male)") == name_key("barbell full squat")
    assert name_key("inchworm v. 2") == name_key("inchworm")
    # В человекочитаемом названии пометка сохраняется: она показывает источник.
    assert "(male)" in normalize_name("barbell full squat (male)")
    assert strip_presentation_markers("barbell full squat (male)") == (
        "barbell full squat"
    )


def test_transliteration_maps_cyrillic_to_latin():
    assert transliterate("Присед") == "prised"
    assert latin_name_key("Скручивание") == name_key("skruchivanie")


def test_steps_become_numbered_technique():
    assert steps_to_technique(["Первый", "Второй"]) == "1. Первый\n2. Второй"
    assert steps_to_technique([]) is None
    assert steps_to_technique(["", "  "]) is None


def test_mojibake_is_cleaned():
    assert "degrees" in normalize_name("sled 45в° leg press")


# --- Сопоставление: истинные совпадения -----------------------------------------


def test_word_order_variant_matches_same_exercise():
    matcher = matcher_for(
        [exercise("Bench_Press", "Bench Press - Barbell", equipment=["barbell"],
                  primary=["chest"])]
    )
    result = match(
        matcher,
        candidate("Barbell Bench Press", equipment=("barbell",), target="pectorals"),
    )
    assert result.matched
    assert result.external_id == "Bench_Press"
    assert "normalized_name_match" in result.reasons


def test_abbreviated_name_matches_full_name():
    matcher = matcher_for(
        [exercise("Dumbbell_Curl", "Dumbbell Curl", equipment=["dumbbell"],
                  primary=["biceps"])]
    )
    for name in ("DB Biceps Curl", "Dumbbell Biceps Curl", "Dumbbell Curls"):
        result = match(
            matcher, candidate(name, equipment=("dumbbell",), target="biceps")
        )
        assert result.matched, name


def test_russian_canonical_name_is_matched_via_transliteration():
    """Русское название caталога опознаётся по латинской записи источника.

    Проверяется именно вклад транслитерации: без русского названия та же запись
    не находится, потому что английские названия у них разные.
    """
    with_russian = matcher_for(
        [
            exercise(
                "Sit-Up",
                "Sit-Up",
                name_ru="Скручивание",
                equipment=["body only"],
                primary=["abdominals"],
            )
        ]
    )
    without_russian = matcher_for(
        [
            exercise(
                "Sit-Up",
                "Sit-Up",
                equipment=["body only"],
                primary=["abdominals"],
            )
        ]
    )
    item = candidate("Skruchivanie", equipment=("body weight",), target="abs")
    assert match(with_russian, item).matched
    assert match(without_russian, item).external_id is None


def test_cyrillic_candidate_matches_russian_canonical_name():
    matcher = matcher_for(
        [
            exercise(
                "Sit-Up",
                "Sit-Up",
                name_ru="Скручивание",
                equipment=["body only"],
                primary=["abdominals"],
            )
        ]
    )
    result = match(
        matcher, candidate("Скручивание", equipment=("body weight",), target="abs")
    )
    assert result.matched


def test_alias_match_finds_exercise():
    matcher = matcher_for(
        [
            exercise(
                "Pushups",
                "Pushups",
                aliases=["Push Up"],
                equipment=["body only"],
                primary=["chest"],
            )
        ]
    )
    result = match(
        matcher, candidate("Push-Up", equipment=("body weight",), target="pectorals")
    )
    assert result.matched


def test_equipment_naming_difference_is_not_a_different_exercise():
    matcher = matcher_for(
        [
            exercise(
                "Bench_Press_With_Bands",
                "Bench Press - With Bands",
                equipment=["bands"],
                primary=["chest"],
            )
        ]
    )
    result = match(
        matcher, candidate("Band Bench Press", equipment=("band",), target="pectorals")
    )
    assert result.matched


def test_target_muscle_disagreement_does_not_break_name_match():
    # Источники расходятся в выборе главной мышцы приседа: `glutes` против
    # `quadriceps`. Набор задействованных мышц при этом один.
    matcher = matcher_for(
        [
            exercise(
                "Barbell_Full_Squat",
                "Barbell Full Squat",
                equipment=["barbell"],
                primary=["quadriceps"],
                secondary=["glutes", "hamstrings"],
            )
        ]
    )
    result = match(
        matcher,
        candidate(
            "barbell full squat",
            equipment=("barbell",),
            target="glutes",
            secondary=("quadriceps", "hamstrings"),
        ),
    )
    assert result.matched


# --- Сопоставление: ложные совпадения -------------------------------------------


def test_grip_variant_is_not_the_same_exercise():
    matcher = matcher_for(
        [
            exercise(
                "Barbell_Bench_Press",
                "Barbell Bench Press",
                equipment=["barbell"],
                primary=["chest"],
            )
        ]
    )
    result = match(
        matcher,
        candidate(
            "Barbell Bench Press - Medium Grip",
            equipment=("barbell",),
            target="pectorals",
        ),
    )
    assert not result.matched
    assert result.variant_of == "Barbell_Bench_Press"


def test_different_movement_does_not_match():
    matcher = matcher_for(
        [
            exercise(
                "Barbell_Bench_Press",
                "Barbell Bench Press",
                equipment=["barbell"],
                primary=["chest"],
            )
        ]
    )
    result = match(
        matcher, candidate("Cable Chest Fly", equipment=("cable",), target="pectorals")
    )
    assert result.external_id is None


def test_equipment_mismatch_blocks_identity():
    matcher = matcher_for(
        [exercise("Barbell_Curl", "Barbell Curl", equipment=["barbell"],
                  primary=["biceps"])]
    )
    result = match(
        matcher, candidate("Cable Curl", equipment=("cable",), target="biceps")
    )
    assert not result.matched
    assert "equipment_differs" in result.reasons


def test_muscle_mismatch_blocks_core_only_identity():
    # Ядро `press` совпадает, различителей нет, оборудование одно и то же — и
    # только мышца разводит эти упражнения. Без проверки мышцы жим на плечи
    # объединился бы с жимом на трицепс.
    matcher = matcher_for(
        [
            exercise(
                "Seated_Triceps_Press",
                "Seated Triceps Press",
                equipment=["dumbbell"],
                primary=["triceps"],
            )
        ]
    )
    result = match(
        matcher,
        candidate("Seated Dumbbell Press", equipment=("dumbbell",), target="delts"),
    )
    assert not result.matched
    assert "target_differs" in result.reasons


def test_extra_equipment_in_name_is_a_variant():
    # Фитбол объявлен только названием, полем — блок. Это дополнительное
    # оборудование, а не другая формулировка.
    matcher = matcher_for(
        [
            exercise(
                "Cable_Incline_Fly",
                "Cable Incline Fly",
                equipment=["cable"],
                primary=["chest"],
            )
        ]
    )
    result = match(
        matcher,
        candidate(
            "Cable Incline Fly (on stability ball)",
            equipment=("cable",),
            target="pectorals",
        ),
    )
    assert not result.matched


# --- Идемпотентность сопоставления ----------------------------------------------


def test_recorded_source_link_is_strongest_signal():
    """Связь предыдущего импорта опознаётся даже при полностью другом названии."""
    matcher = matcher_for(
        [exercise("Some_Exercise", "Completely Different Name")],
        links={(SOURCE_KEY, "42"): ("Some_Exercise", "leszavr/workout")},
    )
    result = match(matcher, candidate("Nothing In Common", record_id="42"))
    assert result.matched
    assert result.reasons == ["existing_source_link"]
    assert result.confidence == 1.0


def test_matching_is_deterministic_across_calls():
    matcher = matcher_for(
        [
            exercise("A", "Barbell Row", equipment=["barbell"], primary=["middle back"]),
            exercise("B", "Dumbbell Row", equipment=["dumbbell"], primary=["middle back"]),
        ]
    )
    item = candidate("Barbell Row", equipment=("barbell",), target="upper back")
    first = match(matcher, item)
    second = match(matcher, item)
    assert (first.external_id, first.confidence) == (second.external_id, second.confidence)


def test_best_candidate_prefers_identity_over_score():
    """Тождество сильнее оценки: похожая запись не вытесняет точное совпадение."""
    matcher = matcher_for(
        [
            exercise(
                "Exact",
                "Barbell Row",
                equipment=["barbell"],
                primary=["middle back"],
                secondary=["biceps"],
            ),
            exercise(
                "Variant",
                "Bent Over Barbell Row",
                equipment=["barbell"],
                primary=["middle back"],
                secondary=["biceps", "forearms", "lats"],
            ),
        ]
    )
    result = match(
        matcher,
        candidate(
            "Barbell Row",
            equipment=("barbell",),
            target="upper back",
            secondary=("biceps", "forearms", "lats"),
        ),
    )
    assert result.external_id == "Exact"


# --- Нормализация мышц ----------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("abs", "abdominals"),
        ("pectorals", "chest"),
        ("delts", "shoulders"),
        ("quads", "quadriceps"),
        ("latissimus dorsi", "lats"),
    ],
)
def test_muscle_synonyms_map_to_canonical(value: str, expected: str):
    resolution = resolve_muscle(value)
    assert resolution.relation is MuscleRelation.EXACT
    assert resolution.canonical == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [("obliques", "abdominals"), ("soleus", "calves"), ("rhomboids", "middle back")],
)
def test_narrower_muscle_maps_to_group_as_inferred(value: str, expected: str):
    resolution = resolve_muscle(value)
    assert resolution.relation is MuscleRelation.BROADER
    assert resolution.canonical == expected


@pytest.mark.parametrize("value", ["upper back", "core", "hip flexors", "spine"])
def test_ambiguous_muscle_is_not_resolved(value: str):
    resolution = resolve_muscle(value)
    assert resolution.relation is MuscleRelation.AMBIGUOUS
    assert resolution.canonical is None
    assert not resolution.usable
    assert len(resolution.candidates) > 1


@pytest.mark.parametrize("value", ["cardiovascular system", "serratus anterior", "feet"])
def test_unmapped_muscle_stays_unmapped(value: str):
    resolution = resolve_muscle(value)
    assert resolution.relation is MuscleRelation.UNMAPPED
    assert resolution.canonical is None


def test_muscle_mapping_reports_gaps_and_removes_duplicates():
    result = map_muscles(["chest", "pectorals", "upper back", "feet", "obliques"])
    assert result.canonical == ["chest", "abdominals"]
    assert result.inferred == ["abdominals"]
    assert "upper back" in result.ambiguous
    assert "feet" in result.unmapped
    assert result.has_gaps


def test_core_only_match_requires_confirming_fact():
    """Совпадение ядра без подтверждения содержанием не является тождеством.

    У `roller back stretch` и `back pec stretch` ядро одинаково (`back`,
    `stretch`), различителей нет ни у одной. Без требования подтверждения
    оборудованием или мышцами растяжка на валике объявлялась бы тем же
    упражнением, что растяжка груди у стены.
    """
    matcher = matcher_for(
        [
            exercise(
                "Back_Pec_Stretch",
                "Back Pec Stretch",
                equipment=["body only"],
                primary=["lats"],
            )
        ]
    )
    result = match(
        matcher,
        candidate(
            "Roller Back Stretch",
            equipment=("roller",),  # словарь тестов такого значения не знает
            target="spine",  # неоднозначное обозначение, canonical не получит
        ),
    )
    assert not result.matched
