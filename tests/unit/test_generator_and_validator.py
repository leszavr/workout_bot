"""Unit-тесты DeterministicProgramGenerator и ProgramValidator.

Проверяются: создание валидной программы, использование только SafeExercisePool,
число тренировок по профилю, детерминизм, ошибки валидации.
"""
from __future__ import annotations

import pytest

from src.application.programs.generator import DeterministicProgramGenerator
from src.application.programs.validator import ProgramValidator
from src.domain.enums import ExperienceLevel, PrimaryGoal, ProgramStatus
from src.domain.exercise import Exercise
from src.domain.pools import SafeExercisePool
from src.domain.profile import FitnessProfile
from src.domain.program import (
    ProgramExercise,
    TrainingDay,
    WorkoutProgram,
)
from src.errors import ProgramGenerationError


def _exercise(
    external_id: str,
    *,
    primary_muscles: list[str],
    exercise_type: str = "strength",
    mechanic: str = "compound",
) -> Exercise:
    return Exercise(
        external_id=external_id,
        name=f"Exercise {external_id}",
        primary_muscles=primary_muscles,
        exercise_type=exercise_type,
        mechanic=mechanic,
        equipment=["body only"],
    )


def _diverse_pool(profile_id: str = "test-profile", size: int = 20) -> SafeExercisePool:
    """Пул с упражнениями всех ролей."""
    roles = {
        "quadriceps": "leg",
        "chest": "push",
        "lats": "pull",
        "abdominals": "core",
    }
    exercises: list[Exercise] = []
    for i in range(size):
        muscle = list(roles.keys())[i % len(roles)]
        exercises.append(
            _exercise(f"E{i:02d}", primary_muscles=[muscle])
        )
    # Немного кардио.
    exercises.append(_exercise("CARDIO1", primary_muscles=["quadriceps"], exercise_type="cardio"))
    return SafeExercisePool(profile_id=profile_id, allowed=exercises)


def _profile(
    sessions: int = 3,
    goal: PrimaryGoal | None = PrimaryGoal.MUSCLE_GAIN,
    experience: ExperienceLevel | None = ExperienceLevel.THREE_TWELVE_MONTHS,
) -> FitnessProfile:
    profile = FitnessProfile(profile_id="test-profile")
    profile.training_plan_preferences.sessions_per_week = sessions
    profile.goals.primary = goal
    profile.training_background.experience_level = experience
    return profile


@pytest.fixture
def generator() -> DeterministicProgramGenerator:
    return DeterministicProgramGenerator()


@pytest.fixture
def validator() -> ProgramValidator:
    return ProgramValidator()


class TestGenerator:
    async def test_creates_valid_program(self, generator):
        profile = _profile(sessions=3)
        pool = _diverse_pool()
        program = await generator.generate(profile, pool)
        assert program.status is ProgramStatus.GENERATED
        assert program.training_days_per_week == 3
        assert len(program.training_days) == 3
        assert program.generation.safe_pool_size == len(pool.allowed)

    async def test_uses_only_safe_pool_exercises(self, generator):
        profile = _profile()
        pool = _diverse_pool()
        program = await generator.generate(profile, pool)
        allowed_ids = pool.allowed_ids()
        for day in program.training_days:
            for item in day.exercises:
                assert item.exercise_external_id in allowed_ids

    async def test_sessions_match_profile(self, generator):
        for sessions in (1, 2, 3, 4, 5):
            profile = _profile(sessions=sessions)
            program = await generator.generate(profile, _diverse_pool())
            assert program.training_days_per_week == sessions
            assert len(program.training_days) == sessions

    async def test_sessions_clamped_to_range(self, generator):
        profile = _profile(sessions=0)  # не указано → дефолт 3
        program = await generator.generate(profile, _diverse_pool())
        assert program.training_days_per_week == 3

    async def test_no_duplicates_within_day(self, generator):
        profile = _profile(sessions=3)
        program = await generator.generate(profile, _diverse_pool())
        for day in program.training_days:
            ids = [e.exercise_external_id for e in day.exercises]
            assert len(ids) == len(set(ids))

    async def test_deterministic(self, generator):
        profile = _profile()
        pool = _diverse_pool()
        p1 = await generator.generate(profile, pool)
        p2 = await generator.generate(profile, pool)
        assert p1.model_dump() == p2.model_dump()

    async def test_empty_pool_raises(self, generator):
        profile = _profile()
        pool = SafeExercisePool(profile_id="test-profile", allowed=[])
        with pytest.raises(ProgramGenerationError):
            await generator.generate(profile, pool)

    async def test_small_pool_raises(self, generator):
        profile = _profile()
        pool = SafeExercisePool(
            profile_id="test-profile",
            allowed=[_exercise("A", primary_muscles=["chest"])],
        )
        with pytest.raises(ProgramGenerationError):
            await generator.generate(profile, pool)

    async def test_goal_affects_reps(self, generator):
        strength = await generator.generate(
            _profile(goal=PrimaryGoal.STRENGTH), _diverse_pool()
        )
        endurance = await generator.generate(
            _profile(goal=PrimaryGoal.ENDURANCE), _diverse_pool()
        )
        s_reps = strength.training_days[0].exercises[0].repetitions_max
        e_reps = endurance.training_days[0].exercises[0].repetitions_max
        assert s_reps < e_reps

    async def test_safety_notes_include_restrictions(self, generator):
        from src.domain.enums import MovementRestriction

        profile = _profile()
        pool = _diverse_pool()
        pool.active_restrictions = [MovementRestriction.AVOID_DEEP_KNEE_FLEXION]
        program = await generator.generate(profile, pool)
        # В программе — формулировка для человека, а не код ограничения.
        assert any("без глубокого сгибания колен" in n for n in program.safety_notes)


class TestValidator:
    async def test_valid_program_passes(self, generator, validator):
        profile = _profile()
        pool = _diverse_pool()
        program = await generator.generate(profile, pool)
        catalog_ids = pool.allowed_ids()
        result = validator.validate(program, pool, profile, catalog_ids)
        assert result.valid
        assert result.issues == []

    async def test_exercise_not_in_pool_fails(self, generator, validator):
        profile = _profile()
        pool = _diverse_pool()
        program = await generator.generate(profile, pool)
        # Подменяем упражнение на существующее в каталоге, но не в пуле.
        program.training_days[0].exercises[0] = ProgramExercise(
            exercise_external_id="NOT_IN_POOL",
            order=1,
            sets=3,
            repetitions_min=8,
            repetitions_max=12,
            rest_seconds=60,
        )
        catalog_ids = pool.allowed_ids() | {"NOT_IN_POOL"}
        result = validator.validate(program, pool, profile, catalog_ids)
        assert not result.valid
        assert any(i.code == "exercise_not_allowed" for i in result.issues)

    async def test_exercise_not_in_catalog_fails(self, generator, validator):
        profile = _profile()
        pool = _diverse_pool()
        program = await generator.generate(profile, pool)
        program.training_days[0].exercises[0] = ProgramExercise(
            exercise_external_id="GHOST",
            order=1,
            sets=3,
            repetitions_min=8,
            repetitions_max=12,
            rest_seconds=60,
        )
        result = validator.validate(program, pool, profile, pool.allowed_ids())
        assert not result.valid
        assert any(i.code == "exercise_not_found" for i in result.issues)

    async def test_duplicate_exercise_fails(self, generator, validator):
        profile = _profile()
        pool = _diverse_pool()
        program = await generator.generate(profile, pool)
        first = program.training_days[0].exercises[0]
        program.training_days[0].exercises.append(
            first.model_copy(update={"order": 99})
        )
        result = validator.validate(program, pool, profile, pool.allowed_ids())
        assert not result.valid
        assert any(i.code == "duplicate_exercise" for i in result.issues)

    async def test_days_count_mismatch_fails(self, generator, validator):
        profile = _profile()
        pool = _diverse_pool()
        program = await generator.generate(profile, pool)
        program.training_days_per_week = 5  # дней на самом деле 3
        result = validator.validate(program, pool, profile, pool.allowed_ids())
        assert not result.valid
        assert any(i.code == "days_count" for i in result.issues)

    async def test_profile_mismatch_fails(self, generator, validator):
        profile = _profile()
        pool = _diverse_pool()
        program = await generator.generate(profile, pool)
        other_profile = _profile()
        other_profile.profile_id = "another-profile"
        result = validator.validate(program, pool, other_profile, pool.allowed_ids())
        assert not result.valid
        assert any(i.code == "profile_mismatch" for i in result.issues)

    def test_schema_validation_from_dict(self, validator):
        program = WorkoutProgram(
            program_id="p1",
            profile_id="prof",
            title="Test",
            duration_weeks=8,
            training_days_per_week=1,
            training_days=[
                TrainingDay(
                    day_number=1,
                    title="Day 1",
                    focus="full body",
                    exercises=[
                        ProgramExercise(
                            exercise_external_id="E1",
                            order=1,
                            sets=3,
                            repetitions_min=8,
                            repetitions_max=12,
                            rest_seconds=60,
                        )
                    ],
                )
            ],
        )
        payload = program.model_dump(mode="json")
        restored, result = validator.validate_schema(payload)
        assert result.valid
        assert restored is not None
        assert restored.title == "Test"

    def test_schema_validation_rejects_bad_payload(self, validator):
        restored, result = validator.validate_schema({"title": "x"})
        assert restored is None
        assert not result.valid
        assert any(i.code == "schema" for i in result.issues)


class TestValidatorExerciseSource:
    """Ссылка на упражнение канонична как пара external_id + source.

    Программа с чужим source сохраняется и внешне выглядит корректной, но
    каталог по ней не находится: пользователь получает карточки без названий,
    техники и предупреждений. Проверка обязательна именно здесь, потому что
    источник приходит из AI-вывода.
    """

    async def test_matching_source_passes(self, generator, validator):
        profile = _profile()
        pool = _diverse_pool()
        program = await generator.generate(profile, pool)

        result = validator.validate(
            program, pool, profile, pool.allowed_ids(), pool.allowed_sources()
        )

        assert result.valid

    async def test_foreign_source_fails(self, generator, validator):
        profile = _profile()
        pool = _diverse_pool()
        program = await generator.generate(profile, pool)
        exercise = program.training_days[0].exercises[0]
        program.training_days[0].exercises[0] = exercise.model_copy(
            update={"exercise_source": "workout"}
        )

        result = validator.validate(
            program, pool, profile, pool.allowed_ids(), pool.allowed_sources()
        )

        assert not result.valid
        issue = next(i for i in result.issues if i.code == "exercise_source_mismatch")
        assert "workout" in issue.message

    async def test_source_check_is_optional(self, generator, validator):
        """Без карты источников проверка не выполняется: старые вызовы не ломаются."""
        profile = _profile()
        pool = _diverse_pool()
        program = await generator.generate(profile, pool)
        exercise = program.training_days[0].exercises[0]
        program.training_days[0].exercises[0] = exercise.model_copy(
            update={"exercise_source": "workout"}
        )

        result = validator.validate(program, pool, profile, pool.allowed_ids())

        assert result.valid

    def test_pool_exposes_sources(self):
        pool = _diverse_pool()
        sources = pool.allowed_sources()
        assert set(sources) == pool.allowed_ids()
        assert set(sources.values()) == {"leszavr/workout"}
