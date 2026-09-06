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

from src.application.programs.session_planning import plan_session
from src.domain.enums import (
    PrimaryGoal,
    ProgramStatus,
    movement_restriction_title,
)
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

# Роли — внутренние ключи группировки. Их видит пользователь в поле focus,
# поэтому наружу отдаём русские названия.
ROLE_TITLES: dict[str, str] = {
    ROLE_LEG: "ноги",
    ROLE_PUSH: "жимовые движения",
    ROLE_PULL: "тяговые движения",
    ROLE_CORE: "корпус",
    ROLE_CARDIO: "кардио",
    ROLE_OTHER: "прочее",
}


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



# --- Схемы сплита по числу занятий ----------------------------------------------
#
# От числа занятий зависит, сколько раз за неделю мышечная группа получает
# нагрузку, — это и есть смысл сплита. Раньше схемы не было: первые два дня были
# осмысленными, а третий и последующие копировали full body, поэтому при шести
# занятиях человек получал четыре одинаковых дня.
#
# Схемы соответствуют методике (раздел 1.1): «условно разделить тело на 2 части»
# при малом числе занятий, на 3 при большем. Полный разбор —
# docs/methodology/TRAINING_PRINCIPLES.md.

_FULL_BODY = ("Всё тело", (ROLE_LEG, ROLE_PUSH, ROLE_PULL, ROLE_CORE, ROLE_CARDIO))
_UPPER = ("Верх тела", (ROLE_PUSH, ROLE_PULL, ROLE_CORE))
_LOWER = ("Низ тела", (ROLE_LEG, ROLE_CORE, ROLE_CARDIO))
_PUSH = ("Жимовые движения", (ROLE_PUSH, ROLE_CORE))
_PULL = ("Тяговые движения", (ROLE_PULL, ROLE_CORE))
_LEGS = ("Ноги", (ROLE_LEG, ROLE_CORE))
_CARDIO_CORE = ("Кардио и корпус", (ROLE_CARDIO, ROLE_CORE, ROLE_LEG))

DaySchema = tuple[str, tuple[str, ...]]


def _one_or_two_days(sessions: int) -> list[DaySchema]:
    """Одно-два занятия: только full body.

    Делить тело на части бессмысленно: при двух занятиях каждая часть получала бы
    нагрузку раз в неделю, чего недостаточно для любой цели.
    """
    return [_FULL_BODY] * sessions


def _three_days(_: int) -> list[DaySchema]:
    """Три занятия: жим — тяга — ноги.

    Классический трёхдневный сплит: каждая группа раз в неделю, но с полным
    объёмом за занятие.
    """
    return [_PUSH, _PULL, _LEGS]


def _four_days(_: int) -> list[DaySchema]:
    """Четыре занятия: верх — низ — верх — низ.

    Каждая часть тела дважды в неделю: при четырёх занятиях это даёт лучшую
    частоту стимуляции, чем деление на четыре отдельные группы.
    """
    return [_UPPER, _LOWER, _UPPER, _LOWER]


def _five_days(_: int) -> list[DaySchema]:
    """Пять занятий: жим — тяга — ноги — верх — низ."""
    return [_PUSH, _PULL, _LEGS, _UPPER, _LOWER]


def _six_days(_: int) -> list[DaySchema]:
    """Шесть занятий: два круга «жим — тяга — ноги».

    Каждая группа дважды в неделю — режим, для которого шесть занятий и нужны.
    """
    return [_PUSH, _PULL, _LEGS, _PUSH, _PULL, _LEGS]


def _seven_days(_: int) -> list[DaySchema]:
    """Семь занятий: два круга «жим — тяга — ноги» и день кардио с корпусом.

    Седьмой день не повторяет силовую работу: ежедневная силовая нагрузка без
    выходного не оставляет времени на восстановление.
    """
    return [_PUSH, _PULL, _LEGS, _PUSH, _PULL, _LEGS, _CARDIO_CORE]


SPLIT_SCHEMES = {
    1: _one_or_two_days,
    2: _one_or_two_days,
    3: _three_days,
    4: _four_days,
    5: _five_days,
    6: _six_days,
    7: _seven_days,
    # Значение по умолчанию: full body по числу занятий. Недостижимо при текущих
    # границах, но оставлено, чтобы расширение диапазона в анкете не приводило к
    # исключению вместо программы.
    None: _one_or_two_days,
}


MIN_POOL_SIZE = 4

# Границы числа тренировок в неделю. Верхняя совпадает с ограничением домена
# (`WorkoutProgram.training_days_per_week` ≤ 7): генератор не вправе сужать то,
# что анкета уже разрешила выбрать.
MAX_SESSIONS_PER_WEEK = 7
DEFAULT_SESSIONS_PER_WEEK = 3


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
        """Число тренировок из анкеты, без обрезки.

        Раньше здесь стояло `min(requested, 5)`, и человек, выбравший шесть
        занятий, получал пять — молча, без объяснения. Ограничение осталось от
        Stage 3A и продуктовым не было: анкета предлагает до шести, домен
        допускает до семи, а ИИ на тех же профилях собирал шесть дней корректно.

        Верхняя граница — предел домена (`training_days_per_week` ≤ 7), а не
        решение генератора: назначать её здесь значило бы второй раз решать
        вопрос, уже решённый в анкете.
        """
        requested = profile.training_plan_preferences.sessions_per_week
        if requested <= 0:
            requested = DEFAULT_SESSIONS_PER_WEEK
        return max(1, min(requested, MAX_SESSIONS_PER_WEEK))

    # --- Построение дней ---------------------------------------------------------

    def _build_days(
        self,
        profile: FitnessProfile,
        pool: SafeExercisePool,
        sessions: int,
    ) -> list[TrainingDay]:
        """Строит дни по схеме сплита, соответствующей числу занятий.

        Раньше третий и последующие дни были копиями full body: при шести
        занятиях человек получал два осмысленных дня и четыре одинаковых. Схема
        выбирается по числу дней, потому что от него зависит, сколько раз за
        неделю мышечная группа получает нагрузку — а это и есть смысл сплита.

        Объём и нагрузка берутся из расчёта занятия (`session_planning`): он
        учитывает заявленное время, цель и опыт. Прежние таблицы
        «опыт → N упражнений» и «цель → подходы» давали объём, не связанный со
        временем: при заявленных 90 минутах собиралось занятие на 44.
        """
        by_role = self._group_by_role(profile, pool)
        plan = plan_session(profile)
        prescription = plan.prescription
        per_day = plan.exercises

        schema = SPLIT_SCHEMES.get(sessions, SPLIT_SCHEMES[None])
        return [
            self._split_day(
                index,
                title,
                roles,
                by_role,
                pool,
                per_day,
                prescription.sets,
                prescription.reps_min,
                prescription.reps_max,
                prescription.rest_seconds,
            )
            for index, (title, roles) in enumerate(schema(sessions), start=1)
        ]

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
        #
        # Имя сравнивается без учёта регистра. Каталог содержит упражнения из
        # разных источников с разным стилем написания, и сравнение с учётом
        # регистра ставило бы весь один источник после всего другого: в Python
        # строчные буквы идут после заглавных. Пул при этом урезается по
        # длительности занятия, и в программу попадали бы упражнения только
        # одного источника.
        for exercises in groups.values():
            exercises.sort(
                key=lambda e: (0 if e.mechanic == "compound" else 1, e.name.lower())
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
            focus=", ".join(ROLE_TITLES.get(role, role) for role in roles),
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
            "Всё тело",
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
            f"Программу собрал алгоритм подбора: из {len(pool.allowed)} упражнений, "
            "разрешённых по ответам анкеты, выбраны подходящие по цели и уровню "
            f"подготовки. Режим: {sessions} тренировки в неделю."
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
                "Из подбора исключены движения: "
                + ", ".join(
                    movement_restriction_title(r) for r in pool.active_restrictions
                )
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
