"""Unit-тесты Exercise Filtering Engine.

Проверяются: gym/home/смешанное место, ограниченное оборудование,
уровни подготовки, отсутствие подходящих упражнений, объяснимость исключений.
"""
from __future__ import annotations

import pytest

from src.application.programs.filtering import (
    ExerciseFilter,
    normalize_equipment_text,
    resolve_available_equipment,
)
from src.domain.enums import (
    CardioPreference,
    ExperienceLevel,
    TrainingLocationType,
)
from src.domain.exercise import Exercise
from src.domain.profile import FitnessProfile


def _exercise(
    external_id: str,
    *,
    equipment: list[str] | None = None,
    difficulty: str | None = "beginner",
    exercise_type: str | None = "strength",
    primary_muscles: list[str] | None = None,
    is_active: bool = True,
) -> Exercise:
    return Exercise(
        external_id=external_id,
        name=f"Exercise {external_id}",
        equipment=equipment or ["body only"],
        difficulty=difficulty,
        exercise_type=exercise_type,
        primary_muscles=primary_muscles or ["chest"],
        is_active=is_active,
    )


def _profile(
    location: TrainingLocationType = TrainingLocationType.GYM,
    equipment: list[str] | None = None,
    experience: ExperienceLevel | None = ExperienceLevel.OVER_1_YEAR,
) -> FitnessProfile:
    profile = FitnessProfile(profile_id="test-profile")
    profile.training_location.primary_location = location
    profile.training_location.available_equipment = equipment or []
    profile.training_background.experience_level = experience
    return profile


@pytest.fixture
def exercise_filter() -> ExerciseFilter:
    return ExerciseFilter()


class TestEquipmentNormalization:
    def test_russian_text_maps_to_catalog_tags(self):
        tags = normalize_equipment_text("гантели, штанга, резиновые петли")
        assert "dumbbell" in tags
        assert "barbell" in tags
        assert "bands" in tags

    def test_empty_text_gives_no_tags(self):
        assert normalize_equipment_text(None) == set()
        assert normalize_equipment_text("") == set()

    def test_gym_without_list_assumes_full_gym(self):
        profile = _profile(location=TrainingLocationType.GYM, equipment=[])
        available = resolve_available_equipment(profile)
        assert "machine" in available
        assert "cable" in available
        assert "barbell" in available

    def test_home_only_body_by_default(self):
        profile = _profile(location=TrainingLocationType.HOME, equipment=[])
        available = resolve_available_equipment(profile)
        assert available == {"body only"}

    def test_home_with_listed_equipment(self):
        profile = _profile(location=TrainingLocationType.HOME, equipment=[])
        profile.training_location.custom_equipment_description = "гантели и турник"
        available = resolve_available_equipment(profile)
        assert "dumbbell" in available
        assert "body only" in available


class TestFiltering:
    async def test_gym_user_gets_machine_exercises(self, exercise_filter):
        profile = _profile(location=TrainingLocationType.GYM)
        exercises = [
            _exercise("A", equipment=["machine"]),
            _exercise("B", equipment=["cable"]),
        ]
        pool = await exercise_filter.select_candidates(profile, exercises)
        assert {e.external_id for e in pool.included} == {"A", "B"}
        assert pool.excluded == []

    async def test_home_user_excludes_machine(self, exercise_filter):
        profile = _profile(location=TrainingLocationType.HOME, equipment=[])
        exercises = [
            _exercise("A", equipment=["machine"]),
            _exercise("B", equipment=["body only"]),
        ]
        pool = await exercise_filter.select_candidates(profile, exercises)
        assert {e.external_id for e in pool.included} == {"B"}
        assert len(pool.excluded) == 1
        assert pool.excluded[0].exercise_external_id == "A"
        assert "оборудован" in pool.excluded[0].reason

    async def test_limited_equipment(self, exercise_filter):
        profile = _profile(location=TrainingLocationType.HOME, equipment=[])
        profile.training_location.custom_equipment_description = "только гантели"
        exercises = [
            _exercise("A", equipment=["dumbbell"]),
            _exercise("B", equipment=["barbell"]),
            _exercise("C", equipment=["cable"]),
        ]
        pool = await exercise_filter.select_candidates(profile, exercises)
        # A: dumbbell доступен. B: нет barbell. C: нет cable.
        assert {e.external_id for e in pool.included} == {"A"}
        assert len(pool.excluded) == 2

    async def test_beginner_excludes_expert(self, exercise_filter):
        profile = _profile(experience=ExperienceLevel.NEVER)
        exercises = [
            _exercise("A", difficulty="beginner"),
            _exercise("B", difficulty="expert"),
            _exercise("C", difficulty="intermediate"),
        ]
        pool = await exercise_filter.select_candidates(profile, exercises)
        assert {e.external_id for e in pool.included} == {"A"}
        assert any("сложность" in r.reason for r in pool.excluded)

    async def test_intermediate_allows_expert(self, exercise_filter):
        profile = _profile(experience=ExperienceLevel.THREE_TWELVE_MONTHS)
        exercises = [
            _exercise("A", difficulty="beginner"),
            _exercise("B", difficulty="expert"),
        ]
        pool = await exercise_filter.select_candidates(profile, exercises)
        assert {e.external_id for e in pool.included} == {"A", "B"}

    async def test_no_suitable_exercises(self, exercise_filter):
        profile = _profile(location=TrainingLocationType.HOME, equipment=[])
        exercises = [
            _exercise("A", equipment=["machine"]),
            _exercise("B", equipment=["cable"]),
        ]
        pool = await exercise_filter.select_candidates(profile, exercises)
        assert pool.included == []
        assert len(pool.excluded) == 2
        assert pool.total_exercises == 2

    async def test_inactive_exercise_excluded(self, exercise_filter):
        profile = _profile()
        exercises = [_exercise("A", is_active=False)]
        pool = await exercise_filter.select_candidates(profile, exercises)
        assert pool.included == []
        assert "деактивировано" in pool.excluded[0].reason

    async def test_cardio_excluded_by_preference(self, exercise_filter):
        profile = _profile()
        profile.lifestyle.cardio_preference = CardioPreference.EXCLUDE
        exercises = [
            _exercise("A", exercise_type="cardio"),
            _exercise("B", exercise_type="strength"),
        ]
        pool = await exercise_filter.select_candidates(profile, exercises)
        assert {e.external_id for e in pool.included} == {"B"}
        assert "кардио" in pool.excluded[0].reason

    async def test_user_excluded_exercises(self, exercise_filter):
        profile = _profile()
        profile.exercise_preferences.excluded_exercises = ["Exercise A"]
        exercises = [
            _exercise("A"),
            _exercise("B"),
        ]
        pool = await exercise_filter.select_candidates(profile, exercises)
        assert {e.external_id for e in pool.included} == {"B"}
        assert "исключений пользователя" in pool.excluded[0].reason

    async def test_deterministic_order(self, exercise_filter):
        profile = _profile()
        exercises = [_exercise(f"E{i}") for i in range(10)]
        pool1 = await exercise_filter.select_candidates(profile, exercises)
        pool2 = await exercise_filter.select_candidates(profile, list(reversed(exercises)))
        assert [e.external_id for e in pool1.included] == [
            e.external_id for e in pool2.included
        ]
