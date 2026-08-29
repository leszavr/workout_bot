"""Формальные проверки качества собранной программы.

Проверяется только то, что проверяемо машиной. «Программа хорошая с точки зрения
тренера» здесь не оценивается: такое суждение автоматизировать нельзя, и
имитировать его набором эвристик значило бы выдавать догадку за проверку.

Каждая проверка отвечает на вопрос, который можно поставить к любой программе,
независимо от того, собрал её ИИ или алгоритм. Именно поэтому проверки лежат
отдельно от генераторов: у них общий предмет — результат.

Главная проверка — `unwanted_exercises`: она отвечает на исходный вопрос, бывает
ли, что пользователь прямо отказался от упражнений, а они всё равно попали в
программу.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from src.application.programs.exercise_matching import (
    matches_unwanted,
    normalize_unwanted,
)
from src.application.programs.filtering import (
    EXPERIENCE_TO_DIFFICULTY,
    resolve_available_equipment,
)
from src.application.programs.validator import (
    MAX_EXERCISES_PER_DAY,
    MIN_EXERCISES_PER_DAY,
)
from src.domain.exercise import Exercise
from src.domain.pools import SafeExercisePool
from src.domain.profile import FitnessProfile
from src.domain.program import WorkoutProgram

# Границы дня, соответствие опыта и сложности, разбор оборудования и
# сопоставление нежелательных упражнений берутся из самого приложения, а не
# переписываются здесь. Собственная копия правила означала бы, что проверка
# сверяет программу с представлением автора теста, а не с поведением системы:
# после изменения правила такая проверка молча начала бы врать.


@dataclass
class CheckResult:
    """Результат одной проверки."""

    name: str
    passed: bool
    detail: str = ""


@dataclass
class QualityReport:
    """Итог по одной программе."""

    profile_id: str
    program_id: str | None
    generator: str
    model: str | None
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    @property
    def failures(self) -> list[CheckResult]:
        return [c for c in self.checks if not c.passed]


def evaluate(
    *,
    program: WorkoutProgram,
    profile: FitnessProfile,
    pool: SafeExercisePool,
    catalog: dict[str, Exercise],
) -> QualityReport:
    """Прогоняет все проверки по одной программе."""
    report = QualityReport(
        profile_id=profile.profile_id or "",
        program_id=program.program_id,
        generator=program.generation.source.value,
        model=program.generation.model,
    )
    report.checks.extend(
        [
            _pool_membership(program, pool),
            _no_duplicates_within_day(program),
            _sessions_per_week(program, profile),
            _day_size(program),
            _equipment_available(program, profile, catalog),
            _difficulty_matches_experience(program, profile, catalog),
            _unwanted_exercises(program, profile, catalog),
            _restrictions_respected(program, pool),
        ]
    )
    return report


# --- Проверки -------------------------------------------------------------------


def _pool_membership(program: WorkoutProgram, pool: SafeExercisePool) -> CheckResult:
    """Каждое упражнение — из safe pool.

    Пул уже учитывает оборудование, сложность, ограничения и отказы, поэтому
    выход за его пределы означает, что все остальные правила обойдены сразу.
    """
    allowed = pool.allowed_ids()
    outside = [
        item.exercise_external_id
        for day in program.training_days
        for item in day.exercises
        if item.exercise_external_id not in allowed
    ]
    return CheckResult(
        "упражнения из safe pool",
        not outside,
        f"вне пула: {', '.join(sorted(set(outside))[:5])}" if outside else "",
    )


def _no_duplicates_within_day(program: WorkoutProgram) -> CheckResult:
    duplicates: list[str] = []
    for day in program.training_days:
        seen: set[str] = set()
        for item in day.exercises:
            if item.exercise_external_id in seen:
                duplicates.append(f"день {day.day_number}: {item.exercise_external_id}")
            seen.add(item.exercise_external_id)
    return CheckResult(
        "нет дублей внутри дня", not duplicates, "; ".join(duplicates[:5])
    )


def _sessions_per_week(program: WorkoutProgram, profile: FitnessProfile) -> CheckResult:
    """Число дней совпадает с тем, что человек готов выдержать.

    Программа на шесть дней для человека, назвавшего два, не будет выполняться —
    это не косметическое расхождение.
    """
    requested = profile.training_plan_preferences.sessions_per_week
    if requested is None:
        return CheckResult("число тренировок в неделю", True, "в анкете не указано")
    actual = len(program.training_days)
    return CheckResult(
        "число тренировок в неделю",
        actual == requested,
        f"в программе {actual}, в анкете {requested}" if actual != requested else "",
    )


def _day_size(program: WorkoutProgram) -> CheckResult:
    bad = [
        f"день {day.day_number}: {len(day.exercises)}"
        for day in program.training_days
        if not (MIN_EXERCISES_PER_DAY <= len(day.exercises) <= MAX_EXERCISES_PER_DAY)
    ]
    return CheckResult("размер тренировочного дня", not bad, "; ".join(bad))


def _equipment_available(
    program: WorkoutProgram, profile: FitnessProfile, catalog: dict[str, Exercise]
) -> CheckResult:
    """Всё оборудование программы доступно пользователю.

    Проверяется независимо от пула: расхождение здесь означало бы, что либо пул
    собран неверно, либо генератор вышел за его пределы.
    """
    available = resolve_available_equipment(profile)
    problems: list[str] = []
    for day in program.training_days:
        for item in day.exercises:
            exercise = catalog.get(item.exercise_external_id)
            if exercise is None:
                problems.append(f"{item.exercise_external_id}: нет в каталоге")
                continue
            missing = [e for e in exercise.equipment if e not in available]
            if missing:
                problems.append(f"{exercise.name}: требует {', '.join(missing)}")
    return CheckResult("оборудование доступно", not problems, "; ".join(problems[:5]))


def _difficulty_matches_experience(
    program: WorkoutProgram, profile: FitnessProfile, catalog: dict[str, Exercise]
) -> CheckResult:
    experience = profile.training_background.experience_level
    if experience is None:
        return CheckResult("сложность по опыту", True, "опыт в анкете не указан")
    allowed = EXPERIENCE_TO_DIFFICULTY.get(experience)
    if not allowed:
        return CheckResult("сложность по опыту", True, f"опыт {experience.value} без правила")
    problems = []
    for day in program.training_days:
        for item in day.exercises:
            exercise = catalog.get(item.exercise_external_id)
            if exercise is None or exercise.difficulty is None:
                continue
            if exercise.difficulty not in allowed:
                problems.append(f"{exercise.name}: {exercise.difficulty}")
    return CheckResult(
        "сложность по опыту",
        not problems,
        f"опыт {experience.value}, найдено: {'; '.join(problems[:5])}" if problems else "",
    )


def _unwanted_exercises(
    program: WorkoutProgram, profile: FitnessProfile, catalog: dict[str, Exercise]
) -> CheckResult:
    """Упражнений, от которых пользователь отказался, в программе нет.

    Центральная проверка всего прогона. Сравнение — то же, что в фильтре: по
    значимым словам, а не по точному совпадению строк, иначе проверка
    подтверждала бы соблюдение правила, которого нет.
    """
    preferences = profile.exercise_preferences
    unwanted = normalize_unwanted(
        [*preferences.disliked_exercises, *preferences.excluded_exercises]
    )
    if not unwanted:
        return CheckResult("нежелательные упражнения отсутствуют", True, "отказов не было")

    violations: list[str] = []
    for day in program.training_days:
        for item in day.exercises:
            exercise = catalog.get(item.exercise_external_id)
            if exercise is None:
                continue
            if matches_unwanted(
                name=exercise.name,
                name_ru=exercise.name_ru,
                aliases=exercise.aliases,
                unwanted=unwanted,
            ):
                violations.append(
                    f"день {day.day_number}: {exercise.name_ru or exercise.name}"
                )
    return CheckResult(
        "нежелательные упражнения отсутствуют",
        not violations,
        f"отказ: {'; '.join(preferences.disliked_exercises)} → в программе: "
        + "; ".join(violations[:5])
        if violations
        else "",
    )


def _restrictions_respected(
    program: WorkoutProgram, pool: SafeExercisePool
) -> CheckResult:
    """Упражнения, исключённые safety-контуром, в программу не попали.

    Отдельно от проверки пула: там сравнение с `allowed`, здесь — с явным
    списком исключённых, и по нему видно, какое именно правило нарушено.
    """
    forbidden = {record.exercise_external_id for record in pool.excluded}
    forbidden |= {record.exercise_external_id for record in pool.requires_review}
    violations = [
        item.exercise_external_id
        for day in program.training_days
        for item in day.exercises
        if item.exercise_external_id in forbidden
    ]
    detail = ""
    if violations:
        detail = f"исключённые safety: {', '.join(sorted(set(violations))[:5])}"
    elif pool.active_restrictions:
        detail = "активных ограничений: " + ", ".join(
            r.value for r in pool.active_restrictions
        )
    return CheckResult("ограничения соблюдены", not violations, detail)
