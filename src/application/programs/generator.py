"""ProgramGenerator: контракт генерации программ + детерминированная реализация.

Контракт ``ProgramGenerator`` — ключевая абстракция этапа: в будущем
``AIProgramGenerator`` реализует тот же интерфейс, и ни профиль, ни каталог,
ни репозиторий, ни API, ни валидаторы меняться не будут.

``DeterministicProgramGenerator`` — реально работающий алгоритм (не mock):
он строит валидную программу ТОЛЬКО из упражнений SafeExercisePool,
детерминированно (без случайности), с учётом цели, опыта и количества
тренировок профиля.
"""
from __future__ import annotations

import asyncio
from typing import Protocol

from src.domain.enums import ExperienceLevel, PrimaryGoal, ProgramStatus
from src.domain.exercise import Exercise
from src.domain.pools import SafeExercisePool
from src.domain.profile import FitnessProfile
from src.domain.program import (
    GENERATOR_VERSION,
    GenerationInfo,
    ProgramExercise,
    ProgressionPlan,
    TrainingDay,
    WorkoutProgram,
)
from src.errors import ProgramGenerationError

# --- Группировка упражнений по двигательной роли -------------------------------

_LEG_MUSCLES = frozenset(
    {"quadriceps", "hamstrings", "glutes", "calves", "abductors", "adductors"}
)
_PUSH_MUSCLES = frozenset({"chest", "shoulders", "triceps"})
_PULL_MUSCLES = frozenset({"lats", "middle back", "biceps", "forearms", "traps"})
_CORE_MUSCLES = frozenset({"abdominals", "lower back"})

ROLE_LEG = "legs"
ROLE_PUSH = "push"
ROLE_PULL = "pull"
ROLE_CORE = "core"
ROLE_CARDIO = "cardio"
ROLE_OTHER = "other"


def classify_role(exercise: Exercise) -> str:
    """Детерминированная классификация упражнения по двигательной роли."""
    if exercise.exercise_type == "cardio":
        return ROLE_CARDIO
    if exercise.exercise_type == "stretching":
        return ROLE_OTHER
    muscles = set(exercise.primary_muscles)
    if muscles & _LEG_MUSCLES:
        return ROLE_LEG
    if muscles & _PUSH_MUSCLES:
        return ROLE_PUSH
    if muscles & _PULL_MUSCLES:
        return ROLE_PULL
    if muscles & _CORE_MUSCLES:
        return ROLE_CORE
    return ROLE_OTHER


# --- Параметры нагрузки по цели -------------------------------------------------

# goal → (sets, reps_min, reps_max, rest_seconds)
GOAL_PRESCRIPTIONS: dict[PrimaryGoal | None, tuple[int, int, int, int]] = {
    PrimaryGoal.STRENGTH: (4, 4, 6, 150),
    PrimaryGoal.MUSCLE_GAIN: (3, 8, 12, 75),
    PrimaryGoal.WEIGHT_LOSS: (3, 12, 15, 60),
    PrimaryGoal.ENDURANCE: (3, 15, 20, 45),
    PrimaryGoal.HEALTH_FITNESS: (2, 10, 15, 60),
    PrimaryGoal.RETURN_TO_TRAINING: (2, 10, 12, 75),
    PrimaryGoal.OTHER: (3, 10, 12, 75),
    None: (3, 10, 12, 75),
}

# Цель → приоритет ролей при ранжировании (чем раньше, тем выше приоритет).
GOAL_ROLE_PRIORITY: dict[PrimaryGoal | None, tuple[str, ...]] = {
    PrimaryGoal.STRENGTH: (ROLE_LEG, ROLE_PUSH, ROLE_PULL, ROLE_CORE, ROLE_CARDIO, ROLE_OTHER),
    PrimaryGoal.MUSCLE_GAIN: (ROLE_PUSH, ROLE_PULL, ROLE_LEG, ROLE_CORE, ROLE_CARDIO, ROLE_OTHER),
    PrimaryGoal.WEIGHT_LOSS: (ROLE_CARDIO, ROLE_LEG, ROLE_PUSH, ROLE_PULL, ROLE_CORE, ROLE_OTHER),
    PrimaryGoal.ENDURANCE: (ROLE_CARDIO, ROLE_LEG, ROLE_CORE, ROLE_PUSH, ROLE_PULL, ROLE_OTHER),
    PrimaryGoal.HEALTH_FITNESS: (ROLE_LEG, ROLE_PUSH, ROLE_PULL, ROLE_CORE, ROLE_CARDIO, ROLE_OTHER),
    PrimaryGoal.RETURN_TO_TRAINING: (ROLE_LEG, ROLE_PUSH, ROLE_PULL, ROLE_CORE, ROLE_CARDIO, ROLE_OTHER),
    PrimaryGoal.OTHER: (ROLE_LEG, ROLE_PUSH, ROLE_PULL, ROLE_CORE, ROLE_CARDIO, ROLE_OTHER),
    None: (ROLE_LEG, ROLE_PUSH, ROLE_PULL, ROLE_CORE, ROLE_CARDIO, ROLE_OTHER),
}

# Цель → длительность программы в неделях.
GOAL_DURATION_WEEKS: dict[PrimaryGoal | None, int] = {
    PrimaryGoal.STRENGTH: 12,
    PrimaryGoal.MUSCLE_GAIN: 12,
    PrimaryGoal.WEIGHT_LOSS: 10,
    PrimaryGoal.ENDURANCE: 10,
    PrimaryGoal.HEALTH_FITNESS: 8,
    PrimaryGoal.RETURN_TO_TRAINING: 6,
    PrimaryGoal.OTHER: 8,
    None: 8,
}

# Опыт → максимальная сложность выбираемых упражнений уже учтена фильтром;
# здесь опыт влияет на объём тренировки.
EXPERIENCE_EXERCISES_PER_DAY: dict[ExperienceLevel | None, int] = {
    ExperienceLevel.NEVER: 4,
    ExperienceLevel.LONG_BREAK: 4,
    ExperienceLevel.UNDER_3_MONTHS: 5,
    ExperienceLevel.THREE_TWELVE_MONTHS: 6,
    ExperienceLevel.OVER_1_YEAR: 6,
    None: 5,
}

MIN_POOL_SIZE = 4


class ProgramGenerator(Protocol):
    """Контракт генератора программ. Реализации: Deterministic (сейчас), AI (будущее)."""

    async def generate(
        self,
        profile: FitnessProfile,
        pool: SafeExercisePool,
    ) -> WorkoutProgram: ...


class DeterministicProgramGenerator:
    """Детерминированный генератор: без случайности, только из SafeExercisePool."""

    async def generate(
        self,
        profile: FitnessProfile,
        pool: SafeExercisePool,
    ) -> WorkoutProgram:
        # Интерфейс асинхронный (AI-генератор будет выполнять сетевые вызовы);
        # детерминированная реализация уступает управление event loop.
        await asyncio.sleep(0)
        if len(pool.allowed) < MIN_POOL_SIZE:
            raise ProgramGenerationError(
                f"Безопасный пул слишком мал: {len(pool.allowed)} упражнений "
                f"(минимум {MIN_POOL_SIZE}). Генерация невозможна."
            )

        goal = profile.goals.primary
        sessions = self._sessions_per_week(profile)
        days = self._build_days(profile, pool, sessions)

        program = WorkoutProgram(
            profile_id=profile.profile_id or "",
            status=ProgramStatus.GENERATED,
            generation=GenerationInfo(
                safe_pool_size=len(pool.allowed),
                candidate_pool_total=None,
            ),
            title=self._title(goal, sessions),
            description=self._description(goal, sessions, pool),
            duration_weeks=GOAL_DURATION_WEEKS.get(goal, 8),
            training_days_per_week=sessions,
            training_days=days,
            progression=self._progression(goal),
            safety_notes=self._safety_notes(pool),
        )
        program.touch()
        return program

    # --- Параметры профиля ------------------------------------------------------

    @staticmethod
    def _sessions_per_week(profile: FitnessProfile) -> int:
        requested = profile.training_plan_preferences.sessions_per_week
        if requested <= 0:
            requested = 3
        return max(1, min(requested, 5))

    # --- Построение дней ---------------------------------------------------------

    def _build_days(
        self,
        profile: FitnessProfile,
        pool: SafeExercisePool,
        sessions: int,
    ) -> list[TrainingDay]:
        by_role = self._group_by_role(profile, pool)
        goal = profile.goals.primary
        sets, reps_min, reps_max, rest = GOAL_PRESCRIPTIONS.get(goal, (3, 10, 12, 75))
        per_day = EXPERIENCE_EXERCISES_PER_DAY.get(
            profile.training_background.experience_level, 5
        )

        if sessions <= 2:
            return [
                self._full_body_day(i, by_role, pool, per_day, sets, reps_min, reps_max, rest)
                for i in range(1, sessions + 1)
            ]

        days: list[TrainingDay] = [
            self._split_day(
                1,
                "Ноги и жимовые движения",
                (ROLE_LEG, ROLE_PUSH, ROLE_CORE),
                by_role,
                pool,
                per_day,
                sets,
                reps_min,
                reps_max,
                rest,
            ),
            self._split_day(
                2,
                "Тяговые движения и корпус",
                (ROLE_PULL, ROLE_CORE, ROLE_CARDIO),
                by_role,
                pool,
                per_day,
                sets,
                reps_min,
                reps_max,
                rest,
            ),
        ]
        for i in range(3, sessions + 1):
            days.append(
                self._full_body_day(i, by_role, pool, per_day, sets, reps_min, reps_max, rest)
            )
        return days

    def _group_by_role(
        self, profile: FitnessProfile, pool: SafeExercisePool
    ) -> dict[str, list[Exercise]]:
        """Группирует разрешённые упражнения по ролям с ранжированием по цели."""
        goal = profile.goals.primary
        priority = GOAL_ROLE_PRIORITY.get(goal, GOAL_ROLE_PRIORITY[None])
        groups: dict[str, list[Exercise]] = {role: [] for role in priority}
        for exercise in pool.allowed:
            groups.setdefault(classify_role(exercise), []).append(exercise)
        # Детерминированная сортировка: compound-упражнения выше, затем имя.
        for exercises in groups.values():
            exercises.sort(
                key=lambda e: (0 if e.mechanic == "compound" else 1, e.name)
            )
        return groups

    def _pick(
        self,
        role: str,
        by_role: dict[str, list[Exercise]],
        used: set[str],
        count: int,
    ) -> list[Exercise]:
        """Выбирает до count упражнений роли, избегая дублей внутри дня."""
        picked: list[Exercise] = []
        for exercise in by_role.get(role, []):
            if exercise.external_id in used:
                continue
            picked.append(exercise)
            used.add(exercise.external_id)
            if len(picked) >= count:
                break
        return picked

    def _split_day(
        self,
        day_number: int,
        title: str,
        roles: tuple[str, ...],
        by_role: dict[str, list[Exercise]],
        pool: SafeExercisePool,
        per_day: int,
        sets: int,
        reps_min: int,
        reps_max: int,
        rest: int,
    ) -> TrainingDay:
        used: set[str] = set()
        exercises: list[Exercise] = []
        # Распределяем квоты по ролям поровну, остаток — первому роли.
        base, extra = divmod(per_day, len(roles))
        for idx, role in enumerate(roles):
            quota = base + (1 if idx < extra else 0)
            exercises.extend(self._pick(role, by_role, used, quota))
        # Если квоты не заполнены — добираем из любых ролей.
        if len(exercises) < per_day:
            for role in roles:
                exercises.extend(
                    self._pick(role, by_role, used, per_day - len(exercises))
                )
                if len(exercises) >= per_day:
                    break
        if not exercises:
            raise ProgramGenerationError(
                f"Не удалось наполнить день {day_number}: пул упражнений исчерпан."
            )
        return TrainingDay(
            day_number=day_number,
            title=title,
            focus=" + ".join(roles),
            exercises=self._to_program_exercises(
                exercises, pool, sets, reps_min, reps_max, rest
            ),
        )

    def _full_body_day(
        self,
        day_number: int,
        by_role: dict[str, list[Exercise]],
        pool: SafeExercisePool,
        per_day: int,
        sets: int,
        reps_min: int,
        reps_max: int,
        rest: int,
    ) -> TrainingDay:
        return self._split_day(
            day_number,
            f"Full body {day_number}",
            (ROLE_LEG, ROLE_PUSH, ROLE_PULL, ROLE_CORE, ROLE_CARDIO),
            by_role,
            pool,
            per_day,
            sets,
            reps_min,
            reps_max,
            rest,
        )

    @staticmethod
    def _to_program_exercises(
        exercises: list[Exercise],
        pool: SafeExercisePool,
        sets: int,
        reps_min: int,
        reps_max: int,
        rest: int,
    ) -> list[ProgramExercise]:
        return [
            ProgramExercise(
                exercise_external_id=e.external_id,
                exercise_source=e.source,
                order=i,
                sets=sets,
                repetitions_min=reps_min,
                repetitions_max=reps_max,
                rest_seconds=rest,
                notes=(
                    "Предупреждение safety-слоя: выполнять с осторожностью"
                    if e.external_id in pool.warnings
                    else None
                ),
            )
            for i, e in enumerate(exercises, start=1)
        ]

    # --- Тексты -------------------------------------------------------------------

    @staticmethod
    def _title(goal: PrimaryGoal | None, sessions: int) -> str:
        goal_titles = {
            PrimaryGoal.STRENGTH: "Развитие силы",
            PrimaryGoal.MUSCLE_GAIN: "Набор мышечной массы",
            PrimaryGoal.WEIGHT_LOSS: "Снижение веса",
            PrimaryGoal.ENDURANCE: "Развитие выносливости",
            PrimaryGoal.HEALTH_FITNESS: "Общая физическая подготовка",
            PrimaryGoal.RETURN_TO_TRAINING: "Возвращение к тренировкам",
            PrimaryGoal.OTHER: "Персональная программа",
        }
        name = goal_titles.get(goal, "Персональная программа")
        return f"{name}: {sessions} тренировки в неделю"

    @staticmethod
    def _description(goal: PrimaryGoal | None, sessions: int, pool: SafeExercisePool) -> str:
        return (
            "Программа сформирована детерминированным алгоритмом на основе "
            f"{len(pool.allowed)} упражнений безопасного пула. "
            f"Режим: {sessions} тренировки в неделю. "
            "Нагрузка подобрана по цели и уровню подготовки из анкеты."
        )

    @staticmethod
    def _progression(goal: PrimaryGoal | None) -> ProgressionPlan:
        if goal in (PrimaryGoal.STRENGTH, PrimaryGoal.MUSCLE_GAIN):
            return ProgressionPlan(
                description=(
                    "Повышайте рабочий вес, когда удаётся выполнить верхнюю "
                    "границу повторений во всех подходах с корректной техникой."
                ),
                weekly_increase_percent=2.5,
            )
        return ProgressionPlan(
            description=(
                "Начните с нижней границы повторений и умеренного веса; "
                "увеличивайте объём только при комфортном восстановлении."
            ),
            weekly_increase_percent=5.0,
        )

    @staticmethod
    def _safety_notes(pool: SafeExercisePool) -> list[str]:
        notes: list[str] = []
        if pool.active_restrictions:
            notes.append(
                "Учтённые ограничения движений: "
                + ", ".join(r.value for r in pool.active_restrictions)
            )
        if pool.warnings:
            notes.append(
                f"Упражнений с предупреждениями: {len(pool.warnings)} — "
                "см. детали в карточке программы."
            )
        notes.extend(pool.review_notes)
        if not notes:
            notes.append(
                "Ограничения не указаны. Программа не является медицинской "
                "рекомендацией — при ухудшении самочувствия обратитесь к врачу."
            )
        return notes
