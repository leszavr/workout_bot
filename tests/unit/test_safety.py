"""Unit-тесты Safety Framework.

Проверяются: нормализация ограничений, ALLOW / EXCLUDE / WARNING /
REQUIRES_REVIEW, несколько ограничений одновременно, детерминизм.
"""
from __future__ import annotations

import pytest

from src.application.programs.safety import (
    SafetyEngine,
    derive_characteristics,
    normalize_restrictions,
)
from src.domain.enums import MovementRestriction, SafetyDecision
from src.domain.exercise import Exercise
from src.domain.profile import FitnessProfile


def _exercise(
    external_id: str,
    *,
    name: str | None = None,
    equipment: list[str] | None = None,
    exercise_type: str | None = "strength",
    mechanic: str | None = "compound",
    primary_muscles: list[str] | None = None,
) -> Exercise:
    return Exercise(
        external_id=external_id,
        name=name or f"Exercise {external_id}",
        equipment=equipment or ["body only"],
        exercise_type=exercise_type,
        mechanic=mechanic,
        primary_muscles=primary_muscles or ["chest"],
    )


def _profile_with_restrictions(*categories: str) -> FitnessProfile:
    profile = FitnessProfile(profile_id="test-profile")
    profile.health_and_limitations.has_limitations = True
    profile.health_and_limitations.categories = list(categories)
    return profile


@pytest.fixture
def engine() -> SafetyEngine:
    return SafetyEngine()


class TestNormalization:
    def test_no_limitations_gives_empty(self):
        profile = FitnessProfile(profile_id="p")
        restrictions, notes = normalize_restrictions(profile)
        assert restrictions == set()
        assert notes == []

    def test_knee_maps_to_deep_knee_flexion(self):
        profile = _profile_with_restrictions("травма колена")
        restrictions, _ = normalize_restrictions(profile)
        assert MovementRestriction.AVOID_DEEP_KNEE_FLEXION in restrictions

    def test_hypertension_maps_to_pressure_and_cardio(self):
        profile = _profile_with_restrictions("гипертония")
        restrictions, _ = normalize_restrictions(profile)
        assert MovementRestriction.AVOID_HIGH_INTRA_ABDOMINAL_PRESSURE in restrictions
        assert MovementRestriction.AVOID_HIGH_INTENSITY_CARDIO in restrictions

    def test_hernia_maps_to_spinal_and_pressure(self):
        profile = _profile_with_restrictions("грыжа")
        restrictions, _ = normalize_restrictions(profile)
        assert MovementRestriction.AVOID_HEAVY_SPINAL_LOADING in restrictions
        assert MovementRestriction.AVOID_HIGH_INTRA_ABDOMINAL_PRESSURE in restrictions

    def test_unrecognized_text_produces_review_note(self):
        profile = _profile_with_restrictions("какое-то редкое заболевание")
        restrictions, notes = normalize_restrictions(profile)
        assert restrictions == set()
        assert any("ручное рассмотрение" in n for n in notes)

    def test_noise_words_ignored(self):
        profile = _profile_with_restrictions("нет")
        restrictions, notes = normalize_restrictions(profile)
        assert restrictions == set()
        assert notes == []

    def test_medical_clearance_adds_review_note(self):
        profile = _profile_with_restrictions("колено")
        profile.health_and_limitations.medical_clearance_required = True
        _, notes = normalize_restrictions(profile)
        assert any("врача" in n for n in notes)


class TestCharacteristics:
    def test_plyometric_is_high_impact(self):
        chars = derive_characteristics(_exercise("A", exercise_type="plyometrics"))
        assert chars.is_high_impact

    def test_barbell_squat_has_spinal_load(self):
        chars = derive_characteristics(
            _exercise("A", name="Barbell Squat", equipment=["barbell"])
        )
        assert chars.has_spinal_load
        assert chars.has_deep_knee_flexion

    def test_overhead_press_detected(self):
        chars = derive_characteristics(_exercise("A", name="Overhead Press"))
        assert chars.has_overhead_component

    def test_walking_is_not_high_intensity(self):
        chars = derive_characteristics(
            _exercise("A", name="Walking, Treadmill", exercise_type="cardio")
        )
        assert not chars.is_high_intensity_cardio

    def test_sprint_is_high_intensity(self):
        chars = derive_characteristics(
            _exercise("A", name="Sprint", exercise_type="cardio")
        )
        assert chars.is_high_intensity_cardio

    def test_other_equipment_is_uncertain(self):
        chars = derive_characteristics(_exercise("A", equipment=["other"]))
        assert chars.characteristics_uncertain


class TestSafetyEngine:
    async def test_no_restrictions_allows_all(self, engine):
        profile = FitnessProfile(profile_id="p")
        candidates = [_exercise("A"), _exercise("B")]
        pool = engine.apply(profile, candidates)
        assert len(pool.allowed) == 2
        assert pool.excluded == []
        assert pool.active_restrictions == []

    async def test_high_impact_excluded_for_knee(self, engine):
        profile = _profile_with_restrictions("колено")
        candidates = [
            _exercise("JUMP", name="Front Box Jump", exercise_type="plyometrics"),
            _exercise("SAFE", name="Bench Press"),
        ]
        pool = engine.apply(profile, candidates)
        allowed_ids = {e.external_id for e in pool.allowed}
        assert "SAFE" in allowed_ids
        assert "JUMP" not in allowed_ids
        assert any(r.exercise_external_id == "JUMP" for r in pool.excluded)

    async def test_spinal_load_excluded_for_back(self, engine):
        profile = _profile_with_restrictions("поясница")
        candidates = [
            _exercise("SQUAT", name="Barbell Squat", equipment=["barbell"]),
            _exercise("SAFE", name="Leg Extension", equipment=["machine"]),
        ]
        pool = engine.apply(profile, candidates)
        allowed_ids = {e.external_id for e in pool.allowed}
        assert "SQUAT" not in allowed_ids
        assert "SAFE" in allowed_ids

    async def test_uncertain_exercise_requires_review(self, engine):
        profile = _profile_with_restrictions("колено")
        candidates = [
            _exercise(
                "UNCERTAIN",
                name="Jump Thing",
                equipment=["other"],
                exercise_type="other",
            ),
        ]
        pool = engine.apply(profile, candidates)
        assert pool.allowed == []
        assert any(r.exercise_external_id == "UNCERTAIN" for r in pool.requires_review)

    async def test_warning_keeps_exercise(self, engine):
        # Ограничение давления: spinal_load без compound → WARNING, не EXCLUDE.
        profile = _profile_with_restrictions("давление")
        candidates = [
            _exercise(
                "WARN",
                name="Barbell Squat",
                equipment=["barbell"],
                mechanic="isolation",
            ),
        ]
        pool = engine.apply(profile, candidates)
        # has_spinal_load=True, но raises_intra_abdominal=False (isolation)
        # → правило avoid_high_intra_abdominal даёт WARNING по has_spinal_load.
        assert len(pool.allowed) == 1
        assert "WARN" in pool.warnings

    async def test_multiple_restrictions_combined(self, engine):
        profile = _profile_with_restrictions("колено", "гипертония")
        candidates = [
            _exercise("JUMP", name="Box Jump", exercise_type="plyometrics"),
            _exercise("SPRINT", name="Sprint", exercise_type="cardio"),
            _exercise("SAFE", name="Seated Row", equipment=["cable"]),
        ]
        pool = engine.apply(profile, candidates)
        allowed_ids = {e.external_id for e in pool.allowed}
        assert allowed_ids == {"SAFE"}
        assert MovementRestriction.AVOID_DEEP_KNEE_FLEXION in pool.active_restrictions
        assert MovementRestriction.AVOID_HIGH_INTENSITY_CARDIO in pool.active_restrictions

    async def test_applied_rules_recorded(self, engine):
        profile = _profile_with_restrictions("колено")
        pool = engine.apply(profile, [_exercise("A")])
        assert "safety.avoid_deep_knee_flexion" in pool.applied_rules

    async def test_deterministic(self, engine):
        profile = _profile_with_restrictions("колено")
        candidates = [_exercise(f"E{i}", name="Box Jump") for i in range(5)]
        pool1 = engine.apply(profile, candidates)
        pool2 = engine.apply(profile, candidates)
        assert [e.external_id for e in pool1.allowed] == [
            e.external_id for e in pool2.allowed
        ]
