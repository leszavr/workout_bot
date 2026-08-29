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


def _named(
    external_id: str,
    name: str,
    *,
    name_ru: str | None = None,
    aliases: list[str] | None = None,
    equipment: list[str] | None = None,
) -> Exercise:
    """Упражнение с реалистичным названием.

    Нужно там, где проверяется сопоставление свободного текста пользователя:
    у `_exercise` названия вида «Exercise A» отличаются одной буквой, и по
    значимым словам они неразличимы.
    """
    return Exercise(
        external_id=external_id,
        name=name,
        name_ru=name_ru,
        aliases=aliases or ([name_ru] if name_ru else []),
        equipment=equipment or ["body only"],
        difficulty="beginner",
        exercise_type="strength",
        primary_muscles=["chest"],
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
        """Явные исключения пользователя убирают упражнение из пула.

        Названия здесь реалистичные, а не «Exercise A» / «Exercise B»:
        сопоставление идёт по значимым словам, и фикстуры, отличающиеся одной
        буквой-различителем, проверяли бы не то поведение, которое существует.
        """
        profile = _profile()
        profile.exercise_preferences.excluded_exercises = ["Выпады"]
        exercises = [
            _named("lunge", "Barbell Lunge", name_ru="Выпад со штангой"),
            _named("bench", "Bench Press", name_ru="Жим лёжа"),
        ]
        pool = await exercise_filter.select_candidates(profile, exercises)
        assert {e.external_id for e in pool.included} == {"bench"}
        assert "не хочет выполнять" in pool.excluded[0].reason

    async def test_disliked_exercises_are_excluded(self, exercise_filter):
        """Ответ на вопрос «какие упражнения не хотите выполнять?» соблюдается.

        Регрессия: фильтр читал только `excluded_exercises`, которое анкета не
        заполняет. Ответ пользователя уходил в `disliked_exercises` и на отбор не
        влиял — человек писал «не хочу выпады» и получал программу с выпадами.
        """
        profile = _profile()
        profile.exercise_preferences.disliked_exercises = ["выпады", "бег"]
        exercises = [
            _named("lunge", "Barbell Lunge", name_ru="Выпад со штангой"),
            _named("walk_lunge", "Barbell Walking Lunge", name_ru="Ходьба выпадами со штангой"),
            _named("run", "Treadmill Running", name_ru="Бег на беговой дорожке"),
            _named("bench", "Bench Press", name_ru="Жим лёжа"),
        ]

        pool = await exercise_filter.select_candidates(profile, exercises)

        # Все варианты выпадов и бега исключены, хотя пользователь назвал их
        # одним словом и не в той форме, что в каталоге.
        assert {e.external_id for e in pool.included} == {"bench"}
        assert len(pool.excluded) == 3

    async def test_unwanted_matching_is_word_based_not_exact(self, exercise_filter):
        """Одного слова достаточно, чтобы убрать все его варианты.

        Точное совпадение строк не годится: «выпады» совпало бы ровно с одним
        названием из одиннадцати, а остальные десять попали бы в программу.
        """
        profile = _profile()
        profile.exercise_preferences.disliked_exercises = ["приседания"]
        exercises = [
            _named("front", "Front Barbell Squat", name_ru="Фронтальный присед со штангой"),
            _named("jump", "Freehand Jump Squat", name_ru="Присед с прыжком без веса"),
            _named("hack", "Hack Squat", name_ru="Гакк-присед"),
            _named("row", "Barbell Row", name_ru="Тяга штанги"),
        ]

        pool = await exercise_filter.select_candidates(profile, exercises)

        assert {e.external_id for e in pool.included} == {"row"}

    async def test_noise_answer_excludes_nothing(self, exercise_filter):
        """«Нет» — это отсутствие ответа, а не запрос на исключение.

        Без этой проверки слово-пустышка совпало бы с чем угодно и пул опустел бы.
        """
        profile = _profile()
        profile.exercise_preferences.disliked_exercises = ["нет", "-", "ничего"]
        exercises = [
            _named("lunge", "Barbell Lunge", name_ru="Выпад со штангой"),
            _named("bench", "Bench Press", name_ru="Жим лёжа"),
        ]

        pool = await exercise_filter.select_candidates(profile, exercises)

        assert len(pool.included) == 2
        assert pool.excluded == []

    async def test_unwanted_matches_english_name_and_aliases(self, exercise_filter):
        """Каталог англоязычный: сопоставление учитывает все названия.

        Русское название и синонимы обязательны — иначе запрос по-русски не нашёл
        бы ничего, а запрос по-английски не нашёл бы переведённые упражнения.
        """
        profile = _profile()
        profile.exercise_preferences.disliked_exercises = ["deadlift", "скакалка"]
        exercises = [
            _named("dl", "Barbell Deadlift", name_ru="Становая тяга со штангой"),
            _named("rope", "Rope Jumping", name_ru="Прыжки со скакалкой", aliases=["Скакалка"]),
            _named("bench", "Bench Press", name_ru="Жим лёжа"),
        ]

        pool = await exercise_filter.select_candidates(profile, exercises)

        assert {e.external_id for e in pool.included} == {"bench"}

    async def test_deterministic_order(self, exercise_filter):
        profile = _profile()
        exercises = [_exercise(f"E{i}") for i in range(10)]
        pool1 = await exercise_filter.select_candidates(profile, exercises)
        pool2 = await exercise_filter.select_candidates(profile, list(reversed(exercises)))
        assert [e.external_id for e in pool1.included] == [
            e.external_id for e in pool2.included
        ]
