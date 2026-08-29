"""Нормативы объёма тренировки: от заявленного времени к структуре занятия.

Единственный источник расчёта объёма — и для алгоритма, и для промпта ИИ. Раньше
такого источника не было вовсе: алгоритм брал число упражнений из таблицы
«опыт → N», ИИ не получал про время ни слова. Результат на 24 реальных
программах: при заявленных 90 минутах собиралось занятие на 44 минуты, при
60 — на 4 минуты, а у мягкой программы возвращения к тренировкам объём выходил
вдвое больше, чем у силовой.

Расчёт идёт от времени, потому что время — это то, что человек назвал прямо.
Цель и опыт задают, как это время расходуется: сила требует длинного отдыха и
меньшего числа упражнений, выносливость — короткого отдыха и большего.

Модуль не решает, какие упражнения выбрать: он отвечает только на вопрос
«сколько работы вмещается в занятие». Выбор упражнений остаётся за генератором,
а у ИИ — за моделью.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from src.domain.enums import ExperienceLevel, PrimaryGoal
from src.domain.profile import FitnessProfile

# Длительность занятия, если человек её не назвал. 60 минут — середина
# предлагаемого в анкете диапазона (30–150) и типовая тренировка.
DEFAULT_SESSION_MINUTES = 60

# Разминка и заминка. Не подбираются персонально по каталогу: растяжка и
# мобилизация исключены из пула основной работы, а рекомендация даётся текстом.
# Но время они занимают, и не учитывать его — значит переполнять занятие.
WARMUP_MINUTES = 8
COOLDOWN_MINUTES = 5

# Сколько секунд занимает один рабочий подход без отдыха. Оценка по числу
# повторений: 3 секунды на повторение — обычный темп силовой работы.
SECONDS_PER_REPETITION = 3
MIN_SET_SECONDS = 30

# Переход между упражнениями: смена снаряда, настройка тренажёра. Без этой
# добавки расчёт систематически недооценивает занятие.
TRANSITION_SECONDS = 60

# Границы, за которые расчёт не выходит ни при какой длительности. Верхняя
# совпадает с ограничением домена (`TrainingDay.exercises` max_length=15).
# Ниже четырёх упражнений занятие перестаёт быть тренировкой: не покрываются
# основные группы движений даже в full body.
MIN_EXERCISES = 4
MAX_EXERCISES = 12

# Допустимое расхождение расчётной длительности занятия с заявленной. Пять минут
# — продуктовое требование: человек назвал время, и программа обязана его
# занимать, а не заканчиваться вдвое раньше.
TOLERANCE_MINUTES = 5

# Диапазон подходов, в котором расчёт вправе отклониться от предписания цели,
# чтобы попасть в заявленное время. Подходы, а не число упражнений, — потому что
# добавлять движения ради заполнения времени значит менять характер тренировки:
# щадящая программа с 13 упражнениями перестаёт быть щадящей, а силовая с 12 —
# силовой. Лишний подход того же упражнения характер сохраняет.
MIN_SETS = 2
MAX_SETS = 6


@dataclass(frozen=True)
class GoalPrescription:
    """Как цель расходует время занятия.

    `sets`, `reps` и `rest_seconds` — параметры одного упражнения; они же
    определяют, сколько упражнений поместится в заявленное время.
    """

    sets: int
    reps_min: int
    reps_max: int
    rest_seconds: int
    # Название темпа для промпта и описания программы: администратору и
    # пользователю важна не таблица, а смысл.
    tempo: str


# Цель → параметры нагрузки. Числа не произвольные: сила — малые повторы и
# длинный отдых, выносливость — высокие повторы и короткий отдых, набор массы —
# средний диапазон.
GOAL_PRESCRIPTIONS: dict[PrimaryGoal | None, GoalPrescription] = {
    PrimaryGoal.STRENGTH: GoalPrescription(4, 4, 6, 150, "силовой, с полным восстановлением между подходами"),
    PrimaryGoal.MUSCLE_GAIN: GoalPrescription(4, 8, 12, 90, "объёмный, с умеренным отдыхом"),
    PrimaryGoal.WEIGHT_LOSS: GoalPrescription(3, 12, 15, 60, "плотный, с коротким отдыхом"),
    PrimaryGoal.ENDURANCE: GoalPrescription(3, 15, 20, 45, "высокоповторный, почти без пауз"),
    PrimaryGoal.HEALTH_FITNESS: GoalPrescription(3, 10, 15, 75, "умеренный, с комфортным отдыхом"),
    PrimaryGoal.RETURN_TO_TRAINING: GoalPrescription(2, 10, 12, 90, "щадящий, с длинным отдыхом"),
    PrimaryGoal.OTHER: GoalPrescription(3, 10, 12, 75, "умеренный"),
    None: GoalPrescription(3, 10, 12, 75, "умеренный"),
}

# Опыт → поправка к числу упражнений. Новичку нужен не меньший объём работы, а
# меньшее число разных движений: техника осваивается на повторении, а не на
# разнообразии. Поправка мягкая, потому что основной ограничитель — время.
EXPERIENCE_ADJUSTMENT: dict[ExperienceLevel | None, int] = {
    ExperienceLevel.NEVER: -1,
    ExperienceLevel.LONG_BREAK: -1,
    ExperienceLevel.UNDER_3_MONTHS: -1,
    ExperienceLevel.THREE_TWELVE_MONTHS: 0,
    ExperienceLevel.OVER_1_YEAR: 1,
    None: 0,
}

# Верхняя граница числа упражнений для щадящих целей. Расчёт «сколько поместится»
# для возвращения к тренировкам даёт 12 упражнений: два подхода занимают мало
# времени, и формально они укладываются. Но человеку после перерыва предлагать
# 12 разных движений неправильно независимо от арифметики — это уже не щадящая
# программа. Ограничение действует только там, где цель прямо означает
# осторожность.
GENTLE_GOALS = frozenset(
    {PrimaryGoal.RETURN_TO_TRAINING, PrimaryGoal.HEALTH_FITNESS}
)
GENTLE_MAX_EXERCISES = 7


@dataclass(frozen=True)
class SessionPlan:
    """Расчётная структура одного занятия.

    `capped` = True означает, что заявленное время не удалось занять, не выходя
    за разумные пределы объёма: например, 150 минут щадящей программы после
    перерыва. Такой случай не скрывается подгонкой чисел — программа сообщает
    человеку фактическую длительность, а не обещает заявленную.
    """

    total_minutes: int
    warmup_minutes: int
    cooldown_minutes: int
    main_minutes: int
    exercises: int
    prescription: GoalPrescription
    capped: bool = False

    @property
    def estimated_main_minutes(self) -> int:
        """Расчётная длительность основной части при этом объёме."""
        return round(self.exercises * exercise_seconds(self.prescription) / 60)

    @property
    def estimated_total_minutes(self) -> int:
        """Расчётная длительность занятия целиком, включая разминку и заминку."""
        return self.warmup_minutes + self.estimated_main_minutes + self.cooldown_minutes

    @property
    def deviation_minutes(self) -> int:
        """Насколько расчёт расходится с заявленным временем."""
        return self.estimated_total_minutes - self.total_minutes

    @property
    def within_tolerance(self) -> bool:
        return abs(self.deviation_minutes) <= TOLERANCE_MINUTES

    @property
    def target_range(self) -> tuple[int, int]:
        """Границы, в которые должно попасть занятие."""
        return (
            self.total_minutes - TOLERANCE_MINUTES,
            self.total_minutes + TOLERANCE_MINUTES,
        )


def session_minutes(profile: FitnessProfile) -> int:
    """Длительность занятия из анкеты либо значение по умолчанию.

    Ноль в профиле означает «не указано»: поле не nullable, а вопрос
    необязательный.
    """
    declared = profile.training_plan_preferences.session_duration_minutes
    return declared if declared and declared > 0 else DEFAULT_SESSION_MINUTES


def exercise_seconds(prescription: GoalPrescription, sets: int | None = None) -> int:
    """Сколько секунд занимает одно упражнение целиком.

    Подходы, отдых между ними и переход к следующему упражнению. Отдых после
    последнего подхода не учитывается: он и есть переход.
    """
    count = prescription.sets if sets is None else sets
    reps = (prescription.reps_min + prescription.reps_max) // 2
    work = max(MIN_SET_SECONDS, reps * SECONDS_PER_REPETITION)
    return count * work + (count - 1) * prescription.rest_seconds + TRANSITION_SECONDS


def plan_session(profile: FitnessProfile) -> SessionPlan:
    """Считает структуру занятия под профиль.

    Занятие обязано попасть в заявленное время с допуском ±5 минут. Это
    ограничение, а не пожелание: человек назвал время, и программа, которая
    заканчивается вдвое раньше, его требование не выполняет.

    Порядок подбора. Сначала берётся число упражнений, которое вмещается при
    предписанных цели подходах; затем, если занятие вышло короче допуска,
    добавляются подходы, а не упражнения. Добавлять движения ради заполнения
    времени нельзя: щадящая программа с 13 упражнениями перестаёт быть щадящей,
    а силовая с 12 — силовой. Лишний подход того же упражнения характер
    тренировки сохраняет.

    Отвечает на вопрос «сколько работы вмещается», а не «какой». Выбор
    упражнений остаётся за генератором, а у ИИ — за моделью.
    """
    total = session_minutes(profile)
    base = GOAL_PRESCRIPTIONS.get(profile.goals.primary, GOAL_PRESCRIPTIONS[None])

    # Разминка и заминка сокращаются, если занятие очень короткое: у
    # 30-минутной тренировки 13 минут на подготовку не остаётся.
    warmup, cooldown = (5, 3) if total <= 45 else (WARMUP_MINUTES, COOLDOWN_MINUTES)
    main_available = max(10, total - warmup - cooldown)

    ceiling = MAX_EXERCISES
    if profile.goals.primary in GENTLE_GOALS:
        ceiling = min(ceiling, GENTLE_MAX_EXERCISES)
    adjustment = EXPERIENCE_ADJUSTMENT.get(
        profile.training_background.experience_level, 0
    )

    fits = int(main_available * 60 / exercise_seconds(base))
    ceiling_exercises = max(MIN_EXERCISES, min(ceiling, fits, fits + adjustment))

    # Подбор пары «упражнения × подходы» под заявленное время.
    #
    # Перебор, а не формула: время нелинейно зависит от подходов (отдых считается
    # между подходами, а не после каждого), и одна формула не даёт попадания в
    # допуск ±5 минут.
    #
    # Число упражнений перебирается сверху вниз, а подходы — снизу вверх, потому
    # что приоритеты не равны. Число упражнений задаёт характер тренировки:
    # щадящей программе не подходят 12 движений, силовой — 12 тоже. Подходы
    # характер сохраняют. Поэтому сначала берётся максимум упражнений, который
    # уместен для цели, и время добирается подходами; уменьшать упражнения
    # приходится только тогда, когда даже минимум подходов не влезает.
    target = main_available * 60
    tolerance = TOLERANCE_MINUTES * 60
    best: tuple[int, int] | None = None
    best_gap = None
    for exercises in range(ceiling_exercises, MIN_EXERCISES - 1, -1):
        for sets in range(MIN_SETS, MAX_SETS + 1):
            estimate = exercises * exercise_seconds(base, sets)
            gap = abs(estimate - target)
            if gap <= tolerance:
                # Первое попадание в допуск при наибольшем числе упражнений и
                # наименьшем числе подходов: дальше перебор только удаляется.
                best, best_gap = (exercises, sets), gap
                break
            if best_gap is None or gap < best_gap:
                best, best_gap = (exercises, sets), gap
        if best_gap is not None and best_gap <= tolerance:
            break

    assert best is not None  # диапазоны непустые, перебор всегда даёт результат
    exercises, chosen_sets = best
    prescription = replace(base, sets=chosen_sets)

    plan = SessionPlan(
        total_minutes=total,
        warmup_minutes=warmup,
        cooldown_minutes=cooldown,
        main_minutes=main_available,
        exercises=exercises,
        prescription=prescription,
    )
    if plan.within_tolerance:
        return plan
    # Занятие получилось длиннее заявленного за пределами допуска — этого быть не
    # должно: перебор обязан был выбрать меньший объём. Единственный случай —
    # минимум объёма (4 упражнения × 2 подхода) уже дольше заявленного времени,
    # то есть человек назвал время, за которое тренировка не проводится.
    # Пересчёт длительности здесь уместен ровно так же: обещать нельзя.

    # Заявленное время не занять в разумных пределах объёма. Так бывает на
    # сочетаниях вроде «150 минут» и «возвращение к тренировкам»: 12 упражнений
    # по 6 подходов — уже предел, а времени всё равно остаётся вдвое больше.
    #
    # Растягивать занятие дальше нельзя: щадящая программа на 150 минут щадящей
    # не будет, а увеличивать отдых до получаса — не тренировка. Поэтому план
    # остаётся честным по объёму, а длительность пересчитывается по фактической
    # работе и помечается `capped`. Человек увидит реальное время, а не обещание,
    # которого программа не выполняет.
    return replace(
        plan,
        total_minutes=plan.estimated_total_minutes,
        main_minutes=plan.estimated_main_minutes,
        capped=True,
    )
