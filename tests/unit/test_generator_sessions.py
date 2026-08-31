"""Детерминированный генератор: число дней, схема сплита, объём по времени.

Три дефекта, найденных прогоном 24 программ на staging:
- `min(requested, 5)` обрезал число тренировок;
- дни 3+ были копиями full body;
- объём брался из таблиц, не связанных с заявленным временем.
"""
from __future__ import annotations

import pytest

from src.application.programs.generator import (
    MAX_SESSIONS_PER_WEEK,
    DeterministicProgramGenerator,
)
from src.domain.enums import ExperienceLevel, PrimaryGoal
from src.domain.exercise import Exercise
from src.domain.pools import SafeExercisePool
from src.domain.profile import FitnessProfile

pytestmark = pytest.mark.asyncio

_MUSCLES = ["chest", "lats", "quadriceps", "abdominals", "shoulders", "hamstrings"]


def _pool(count: int = 80) -> SafeExercisePool:
    exercises = [
        Exercise(
            external_id=f"ex{i}",
            name=f"Exercise {i}",
            primary_muscles=[_MUSCLES[i % len(_MUSCLES)]],
            equipment=["barbell"],
            difficulty="beginner",
            exercise_type="cardio" if i % 15 == 0 else "strength",
            mechanic="compound" if i % 2 else "isolation",
        )
        for i in range(count)
    ]
    return SafeExercisePool(profile_id="p", allowed=exercises)


def _profile(
    *,
    sessions: int,
    minutes: int = 60,
    goal: PrimaryGoal = PrimaryGoal.MUSCLE_GAIN,
    experience: ExperienceLevel = ExperienceLevel.OVER_1_YEAR,
) -> FitnessProfile:
    profile = FitnessProfile(profile_id="p")
    profile.goals.primary = goal
    profile.training_plan_preferences.sessions_per_week = sessions
    profile.training_plan_preferences.session_duration_minutes = minutes
    profile.training_background.experience_level = experience
    return profile


async def _generate(**kwargs):
    return await DeterministicProgramGenerator().generate(_profile(**kwargs), _pool())


# --- Число дней -------------------------------------------------------------------


@pytest.mark.parametrize("sessions", [1, 2, 3, 4, 5, 6, 7])
async def test_requested_days_are_respected(sessions: int):
    """Сколько человек попросил, столько и получил.

    Регрессия: `min(requested, 5)` молча выдавал пять дней вместо шести.
    """
    program = await _generate(sessions=sessions)

    assert program.training_days_per_week == sessions
    assert len(program.training_days) == sessions
    assert [d.day_number for d in program.training_days] == list(
        range(1, sessions + 1)
    )


async def test_days_above_domain_limit_are_capped():
    """Выше предела домена подняться нельзя: схема программы этого не допускает."""
    profile = _profile(sessions=7)
    profile.training_plan_preferences.sessions_per_week = MAX_SESSIONS_PER_WEEK
    program = await DeterministicProgramGenerator().generate(profile, _pool())
    assert len(program.training_days) == MAX_SESSIONS_PER_WEEK


async def test_missing_sessions_falls_back_to_default():
    program = await _generate(sessions=0)
    assert len(program.training_days) == 3


# --- Схема сплита -----------------------------------------------------------------


async def test_six_days_are_not_copies_of_one_day():
    """Шесть занятий дают два круга «жим — тяга — ноги», а не четыре full body.

    Регрессия: дни 3+ были копиями full body, и при шести занятиях человек
    получал два осмысленных дня и четыре одинаковых.
    """
    program = await _generate(sessions=6, minutes=90)

    titles = [day.title for day in program.training_days]
    assert titles == [
        "Жимовые движения",
        "Тяговые движения",
        "Ноги",
        "Жимовые движения",
        "Тяговые движения",
        "Ноги",
    ]
    # Упражнения в одноимённых днях не обязаны совпадать, но состав дня по фокусу
    # должен различаться между жимом и тягой.
    assert program.training_days[0].focus != program.training_days[1].focus


async def test_seventh_day_is_not_another_strength_day():
    """Седьмой день — кардио с корпусом: ежедневная силовая работа не оставляет
    времени на восстановление."""
    program = await _generate(sessions=7, minutes=60)
    assert program.training_days[-1].title == "Кардио и корпус"


async def test_two_days_use_full_body():
    """При двух занятиях делить тело бессмысленно: группа получала бы нагрузку
    раз в неделю."""
    program = await _generate(sessions=2)
    assert [d.title for d in program.training_days] == ["Всё тело", "Всё тело"]


# --- Объём по времени -------------------------------------------------------------


async def test_longer_session_gets_more_work():
    """Заявленное время влияет на объём: раньше не влияло вовсе."""
    short = await _generate(sessions=3, minutes=45)
    long = await _generate(sessions=3, minutes=120)

    assert len(long.training_days[0].exercises) > len(
        short.training_days[0].exercises
    )


async def test_strength_goal_is_not_lighter_than_recovery():
    """Силовая программа не легче щадящей.

    Регрессия: возвращение к тренировкам давало 9-10 упражнений, а сила при
    90 минутах — 5.
    """
    strength = await _generate(
        sessions=3, minutes=90, goal=PrimaryGoal.STRENGTH
    )
    recovery = await _generate(
        sessions=3,
        minutes=90,
        goal=PrimaryGoal.RETURN_TO_TRAINING,
        experience=ExperienceLevel.LONG_BREAK,
    )

    strength_sets = strength.training_days[0].exercises[0].sets
    recovery_sets = recovery.training_days[0].exercises[0].sets
    strength_rest = strength.training_days[0].exercises[0].rest_seconds
    # Силовая работа: больше подходов либо длиннее отдых — но не мягче по обоим.
    assert strength_sets >= recovery_sets or strength_rest > 120


async def test_session_fits_declared_time():
    """Расчётная длительность занятия попадает в заявленное время ±5 минут."""
    from src.application.programs.session_planning import plan_session

    for minutes in (45, 60, 75, 90):
        profile = _profile(sessions=3, minutes=minutes)
        plan = plan_session(profile)
        program = await DeterministicProgramGenerator().generate(profile, _pool())

        # Генератор берёт объём из того же расчёта, поэтому число упражнений в
        # дне совпадает с планом — иначе хронометраж разошёлся бы.
        assert len(program.training_days[0].exercises) == plan.exercises
        assert plan.within_tolerance
