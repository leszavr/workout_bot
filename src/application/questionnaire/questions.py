"""Единый декларативный источник истины анкеты.

Порядок вопросов, тексты, подсказки, обязательность, тип, варианты ответа,
валидация, парсинг и условия пропуска определены ОДНИМ списком ``QUESTIONS``.
Больше нет нескольких рассинхронизированных структур
(QUESTION_ORDER / QUESTION_TEXT / MANDATORY_QUESTIONS / NEXT_STATE / TEXT_QUESTIONS).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Callable

from src.domain.consents import ConsentRecord  # noqa: F401  (реэкспорт для сервиса)
from src.domain.enums import (
    CardioPreference,
    DailyActivityLevel,
    ExperienceLevel,
    PreferredTrainingTime,
    PrimaryGoal,
    Sex,
    TargetTimeframe,
    TrainingLocationType,
)
from src.domain.profile import (
    FitnessProfile,
    LimitationDetail,
    WorkingWeight,
)


class QuestionKind(StrEnum):
    TEXT = "text"
    CHOICE = "choice"
    MULTISELECT = "multiselect"
    PHOTOS = "photos"


@dataclass(frozen=True)
class Option:
    callback_data: str
    label: str
    value: Any


@dataclass(frozen=True)
class QuestionDefinition:
    id: str
    section: str
    field: str
    text: str
    kind: QuestionKind
    required: bool = False
    hint: str = ""
    options: tuple[Option, ...] = field(default_factory=tuple)
    validate: Callable[[str], str | None] | None = None
    parse: Callable[[str], Any] | None = None
    skip_if: Callable[[FitnessProfile], bool] | None = None
    apply: Callable[[FitnessProfile, Any], None] | None = None

    def is_active(self, profile: FitnessProfile) -> bool:
        return not (self.skip_if is not None and self.skip_if(profile))

    def option_by_data(self, callback_data: str) -> Option | None:
        for opt in self.options:
            if opt.callback_data == callback_data:
                return opt
        return None

    def set_value(self, profile: FitnessProfile, value: Any) -> None:
        if self.apply is not None:
            self.apply(profile, value)
        else:
            setattr(getattr(profile, self.section), self.field, value)


# --- Парсеры текстовых ответов -------------------------------------------------

def parse_text(text: str) -> str:
    return text.strip()


def parse_int(text: str) -> int:
    return int(text)


def parse_float1(text: str) -> float:
    return round(float(text), 1)


def parse_int_from_float(text: str) -> int:
    return int(float(text))


def parse_list(text: str) -> list[str]:
    return [item.strip() for item in re.split(r"[,;\n]+", text) if item.strip()]


# --- Валидаторы (возвращают текст ошибки или None) ----------------------------

def v_name(text: str) -> str | None:
    if 2 <= len(text) <= 50:
        return None
    return "Имя должно содержать от 2 до 50 символов. Попробуйте ещё раз."


def v_age(text: str) -> str | None:
    try:
        value = int(text)
    except ValueError:
        return "Введите целое число от 14 до 100."
    if 14 <= value <= 100:
        return None
    return "Возраст должен быть в диапазоне 14–100."


def v_height(text: str) -> str | None:
    try:
        value = int(text)
    except ValueError:
        return "Введите рост 120–250 см."
    if 120 <= value <= 250:
        return None
    return "Рост должен быть в диапазоне 120–250 см."


def v_weight(text: str) -> str | None:
    try:
        value = float(text)
    except ValueError:
        return "Введите вес 30–300 кг."
    if 30 <= value <= 300:
        return None
    return "Вес должен быть в диапазоне 30–300 кг."


def v_waist(text: str) -> str | None:
    try:
        value = float(text)
    except ValueError:
        return "Введите значение от 40 до 200 см."
    if 40 <= value <= 200:
        return None
    return "Окружность талии должна быть 40–200 см."


def v_goal_result(text: str) -> str | None:
    if len(text) >= 5:
        return None
    return "Опишите результат подробнее, минимум 5 символов."


# --- Кастомные apply ----------------------------------------------------------

def _apply_primary_goal(profile: FitnessProfile, value: Any) -> None:
    profile.goals.primary = value
    profile.goals.primary_custom = None


def _apply_working_weights(profile: FitnessProfile, value: Any) -> None:
    profile.training_background.known_working_weights = [
        WorkingWeight(
            exercise="Указано вручную",
            weight=0.0,
            unit="kg",
            sets_reps="",
            notes=value or None,
        )
    ]


def _apply_home_equipment(profile: FitnessProfile, value: Any) -> None:
    profile.training_location.custom_equipment_description = ", ".join(value) or None


def _apply_limitation_categories(profile: FitnessProfile, value: Any) -> None:
    health = profile.health_and_limitations
    if value:
        health.categories = [value]
        health.details = [LimitationDetail(category="general", user_description=value)]
    else:
        health.categories = []
        health.details = []


def _apply_medical_clearance(profile: FitnessProfile, value: Any) -> None:
    profile.health_and_limitations.medical_clearance_required = bool(value)
    profile.health_and_limitations.doctor_recommendations = None


# --- Условия пропуска ---------------------------------------------------------

def _skip_if_no_current_training(p: FitnessProfile) -> bool:
    return p.training_background.current_frequency_per_week == 0


def _skip_if_home(p: FitnessProfile) -> bool:
    return p.training_location.primary_location == TrainingLocationType.HOME


def _skip_if_gym(p: FitnessProfile) -> bool:
    return p.training_location.primary_location == TrainingLocationType.GYM


def _skip_if_no_limitations(p: FitnessProfile) -> bool:
    return not p.health_and_limitations.has_limitations


def _skip_if_no_clearance(p: FitnessProfile) -> bool:
    return not p.health_and_limitations.medical_clearance_required


# --- Единый список вопросов (порядок = порядок в списке) ----------------------

QUESTIONS: list[QuestionDefinition] = [
    QuestionDefinition(
        id="q01_name", section="client", field="name",
        text="Как вас зовут? *", kind=QuestionKind.TEXT, required=True,
        hint="Введите имя от 2 до 50 символов.",
        validate=v_name, parse=parse_text,
    ),
    QuestionDefinition(
        id="q02_age", section="client", field="age_years",
        text="Сколько вам лет? *", kind=QuestionKind.TEXT, required=True,
        hint="Введите целое число от 14 до 100.",
        validate=v_age, parse=parse_int,
    ),
    QuestionDefinition(
        id="q03_sex", section="client", field="sex",
        text="Ваш пол?", kind=QuestionKind.CHOICE,
        options=(
            Option("sex_male", "Мужской", Sex.MALE),
            Option("sex_female", "Женский", Sex.FEMALE),
            Option("sex_not_specified", "Не хочу указывать", Sex.NOT_SPECIFIED),
        ),
    ),
    QuestionDefinition(
        id="q04_height", section="client", field="height_cm",
        text="Укажите рост (см) *", kind=QuestionKind.TEXT, required=True,
        hint="Введите рост в сантиметрах: 120–250.",
        validate=v_height, parse=parse_int,
    ),
    QuestionDefinition(
        id="q05_weight", section="client", field="weight_kg",
        text="Укажите текущий вес (кг) *", kind=QuestionKind.TEXT, required=True,
        hint="Введите вес в кг: 30–300.",
        validate=v_weight, parse=parse_float1,
    ),
    QuestionDefinition(
        id="q06_waist", section="client", field="waist_cm",
        text="Окружность талии (см) — необязательно", kind=QuestionKind.TEXT,
        hint="Введите значение от 40 до 200 см.",
        validate=v_waist, parse=parse_int_from_float,
    ),
    QuestionDefinition(
        id="q07_primary_goal", section="goals", field="primary",
        text="Какова ваша основная цель? *", kind=QuestionKind.CHOICE, required=True,
        apply=_apply_primary_goal,
        options=(
            Option("goal_weight_loss", "⚖️ Снижение веса", PrimaryGoal.WEIGHT_LOSS),
            Option("goal_muscle_gain", "💪 Набор мышечной массы", PrimaryGoal.MUSCLE_GAIN),
            Option("goal_strength", "🏋️ Увеличение силы", PrimaryGoal.STRENGTH),
            Option("goal_health_fitness", "❤️ Улучшение здоровья и общей формы", PrimaryGoal.HEALTH_FITNESS),
            Option("goal_endurance", "🏃 Повышение выносливости", PrimaryGoal.ENDURANCE),
            Option("goal_return_to_training", "🔄 Возвращение к тренировкам", PrimaryGoal.RETURN_TO_TRAINING),
            Option("goal_other", "✍️ Другое", PrimaryGoal.OTHER),
        ),
    ),
    QuestionDefinition(
        id="q08_secondary_goals", section="goals", field="secondary",
        text="Есть ли дополнительные цели?", kind=QuestionKind.TEXT,
        hint="Например: улучшить осанку, повысить выносливость. Перечислите через запятую или с новой строки.",
        parse=parse_list,
    ),
    QuestionDefinition(
        id="q09_desired_result", section="goals", field="desired_result",
        text="Опишите, какой результат будет для вас хорошим через 3–6 месяцев? *",
        kind=QuestionKind.TEXT, required=True,
        hint="Напишите коротко, что для вас считается хорошим результатом.",
        validate=v_goal_result, parse=parse_text,
    ),
    QuestionDefinition(
        id="q10_timeframe", section="goals", field="target_timeframe",
        text="За какой срок хотите достичь идеального для вас результата? *",
        kind=QuestionKind.CHOICE, required=True,
        options=(
            Option("timeframe_1_month", "1 месяц", TargetTimeframe.ONE_MONTH),
            Option("timeframe_2_3_months", "2–3 месяца", TargetTimeframe.TWO_THREE_MONTHS),
            Option("timeframe_3_6_months", "3–6 месяцев", TargetTimeframe.THREE_SIX_MONTHS),
            Option("timeframe_6_12_months", "6–12 месяцев", TargetTimeframe.SIX_TWELVE_MONTHS),
            Option("timeframe_no_rush", "Не тороплюсь", TargetTimeframe.NO_RUSH),
        ),
    ),
    QuestionDefinition(
        id="q11_experience", section="training_background", field="experience_level",
        text="Как давно вы тренируетесь регулярно? *", kind=QuestionKind.CHOICE, required=True,
        options=(
            Option("exp_never", "Никогда не занимался", ExperienceLevel.NEVER),
            Option("exp_long_break", "Был длинный перерыв", ExperienceLevel.LONG_BREAK),
            Option("exp_under_3_months", "До 3 месяцев", ExperienceLevel.UNDER_3_MONTHS),
            Option("exp_3_12_months", "3–12 месяцев", ExperienceLevel.THREE_TWELVE_MONTHS),
            Option("exp_over_1_year", "Больше 1 года", ExperienceLevel.OVER_1_YEAR),
        ),
    ),
    QuestionDefinition(
        id="q12_current_frequency", section="training_background", field="current_frequency_per_week",
        text="Сколько раз в неделю тренируетесь сейчас?", kind=QuestionKind.CHOICE,
        options=(
            Option("freq_none", "0 раз", 0),
            Option("freq_1", "1 раз", 1),
            Option("freq_2", "2 раза", 2),
            Option("freq_3", "3 раза", 3),
            Option("freq_4", "4 раза", 4),
            Option("freq_5", "5+ раз", 5),
        ),
    ),
    QuestionDefinition(
        id="q13_current_activity", section="training_background", field="current_activity_description",
        text="Как обычно выглядит ваша тренировка?", kind=QuestionKind.TEXT,
        hint="Например: тренажёры 40 минут + кардио 20 минут.",
        parse=parse_text, skip_if=_skip_if_no_current_training,
    ),
    QuestionDefinition(
        id="q14_current_exercises", section="training_background", field="current_exercises",
        text="Какие упражнения вы выполняете сейчас?", kind=QuestionKind.TEXT,
        hint="Например: приседания, жим гантелей, ходьба. Перечислите через запятую или каждое с новой строки.",
        parse=parse_list, skip_if=_skip_if_no_current_training,
    ),
    QuestionDefinition(
        id="q15_working_weights", section="training_background", field="known_working_weights",
        text="Если знаете свои рабочие веса — укажите их", kind=QuestionKind.TEXT,
        hint="Например: Жим ногами — 120 кг × 12, Тяга верхнего блока — 50 кг × 12.",
        apply=_apply_working_weights, skip_if=_skip_if_no_current_training,
    ),
    QuestionDefinition(
        id="q16_location", section="training_location", field="primary_location",
        text="Где планируете заниматься? *", kind=QuestionKind.CHOICE, required=True,
        options=(
            Option("loc_home", "Дома", TrainingLocationType.HOME),
            Option("loc_gym", "В зале", TrainingLocationType.GYM),
            Option("loc_both", "И дома, и в зале", TrainingLocationType.BOTH),
        ),
    ),
    QuestionDefinition(
        id="q17_gym_name", section="training_location", field="gym_name",
        text="Название зала (необязательно)", kind=QuestionKind.TEXT,
        hint="Например: World Class Владивосток.",
        parse=parse_text, skip_if=_skip_if_home,
    ),
    QuestionDefinition(
        id="q18_equipment", section="training_location", field="available_equipment",
        text="Какое оборудование доступно?", kind=QuestionKind.TEXT,
        hint="Перечислите через запятую или каждое с новой строки. Например: гантели, скамья, беговая дорожка.",
        parse=parse_list, skip_if=_skip_if_home,
    ),
    QuestionDefinition(
        id="q19_equipment_photos", section="training_location", field="equipment_photos",
        text="Можете прислать фотографии тренажёров вашего зала?", kind=QuestionKind.PHOTOS,
        hint="Можно отправить до 10 фотографий по одной.",
        skip_if=_skip_if_home,
    ),
    QuestionDefinition(
        id="q18b_home_equipment", section="training_location", field="custom_equipment_description",
        text="Какое оборудование есть дома?", kind=QuestionKind.TEXT,
        hint="Например: гантели, резиновые петли, коврик. Перечислите через запятую или каждое с новой строки.",
        parse=parse_list, apply=_apply_home_equipment, skip_if=_skip_if_gym,
    ),
    QuestionDefinition(
        id="q20_sessions_per_week", section="training_plan_preferences", field="sessions_per_week",
        text="Сколько раз в неделю готовы заниматься?", kind=QuestionKind.CHOICE,
        options=tuple(Option(f"sessions_{i}", f"{i} раз" if i > 1 else "1 раз", i) for i in range(1, 7)),
    ),
    QuestionDefinition(
        id="q21_preferred_days", section="training_plan_preferences", field="preferred_days",
        text="Какие дни удобны? *", kind=QuestionKind.MULTISELECT, required=True,
    ),
    QuestionDefinition(
        id="q22_session_duration", section="training_plan_preferences", field="session_duration_minutes",
        text="Сколько времени готовы уделять одной тренировке?", kind=QuestionKind.CHOICE,
        options=(
            Option("duration_45", "30–45 мин", 45),
            Option("duration_60", "45–60 мин", 60),
            Option("duration_75", "60–75 мин", 75),
            Option("duration_90", "75–90 мин", 90),
            Option("duration_120", "90–120 мин", 120),
            Option("duration_150", "2 часа и больше", 150),
        ),
    ),
    QuestionDefinition(
        id="q23_preferred_time", section="training_plan_preferences", field="preferred_training_time",
        text="В какое время суток предпочитаете тренироваться? *", kind=QuestionKind.CHOICE, required=True,
        options=(
            Option("time_pref_morning", "Утро", PreferredTrainingTime.MORNING),
            Option("time_pref_afternoon", "День", PreferredTrainingTime.AFTERNOON),
            Option("time_pref_evening", "Вечер", PreferredTrainingTime.EVENING),
            Option("time_pref_any", "Любое время", PreferredTrainingTime.ANY),
        ),
    ),
    QuestionDefinition(
        id="q24_has_limitations", section="health_and_limitations", field="has_limitations",
        text="Есть ли особенности здоровья, которые нужно учитывать?", kind=QuestionKind.CHOICE,
        options=(
            Option("limit_yes", "Да, есть ограничения", True),
            Option("limit_no", "Нет, всё в норме", False),
        ),
    ),
    QuestionDefinition(
        id="q25_limitation_categories", section="health_and_limitations", field="categories",
        text="Какие особенности здоровья или ограничения нужно учитывать? *",
        kind=QuestionKind.TEXT, required=True,
        hint="Например: боли в пояснице, травма колена, повышенное давление. Если ограничений нет — напишите «Нет».",
        apply=_apply_limitation_categories, skip_if=_skip_if_no_limitations,
    ),
    QuestionDefinition(
        id="q27_movements_to_avoid", section="health_and_limitations", field="movements_to_avoid",
        text="Есть ли движения, которые вам нельзя или нежелательно выполнять? *",
        kind=QuestionKind.TEXT, required=True,
        hint="Например: бег, прыжки, приседания со штангой. Если таких движений нет — напишите «Нет».",
        parse=parse_list,
    ),
    QuestionDefinition(
        id="q28_medical_clearance", section="health_and_limitations", field="medical_clearance_required",
        text="Есть ли рекомендации врача по физической нагрузке? *",
        kind=QuestionKind.CHOICE, required=True, apply=_apply_medical_clearance,
        options=(
            Option("med_clear_restricted", "Есть рекомендации врача", True),
            Option("med_clear_no_recommendations", "Рекомендаций нет", False),
        ),
    ),
    QuestionDefinition(
        id="q28_doctor_recommendations", section="health_and_limitations", field="doctor_recommendations",
        text="Кратко опишите рекомендации врача", kind=QuestionKind.TEXT, required=True,
        hint="Например: избегать силовых нагрузок высокой интенсивности, контролировать давление.",
        parse=parse_text, skip_if=_skip_if_no_clearance,
    ),
    QuestionDefinition(
        id="q29_preferred_exercises", section="exercise_preferences", field="preferred_exercises",
        text="Какие упражнения вам нравятся или вы хотели бы включить?", kind=QuestionKind.TEXT,
        hint="Например: тренажёры, жим ногами, плавание. Перечислите через запятую или каждое с новой строки.",
        parse=parse_list,
    ),
    QuestionDefinition(
        id="q30_disliked_exercises", section="exercise_preferences", field="disliked_exercises",
        text="Какие упражнения вы не любите или не хотите выполнять?", kind=QuestionKind.TEXT,
        hint="Например: бег, выпады, берпи. Перечислите через запятую или каждое с новой строки.",
        parse=parse_list,
    ),
    QuestionDefinition(
        id="q31_exercise_goals", section="exercise_preferences", field="exercise_goals",
        text="Есть ли упражнения, которые вы хотите освоить?", kind=QuestionKind.TEXT,
        hint="Например: научиться подтягиваться на турнике.",
        parse=parse_list,
    ),
    QuestionDefinition(
        id="q32_daily_activity", section="lifestyle", field="daily_activity_level",
        text="Какова ваша обычная физическая активность вне тренировок?", kind=QuestionKind.CHOICE,
        options=(
            Option("activity_sedentary", "Сидячая работа, мало двигаюсь", DailyActivityLevel.SEDENTARY),
            Option("activity_light", "Немного хожу в течение дня", DailyActivityLevel.LIGHT_WALKING),
            Option("activity_moderate", "Много хожу или активная работа", DailyActivityLevel.ACTIVE_WALKING),
            Option("activity_high", "Тяжёлая физическая работа", DailyActivityLevel.PHYSICAL_WORK),
            Option("activity_other", "Другое / затрудняюсь ответить", DailyActivityLevel.VERY_ACTIVE),
        ),
    ),
    QuestionDefinition(
        id="q33_cardio_preference", section="lifestyle", field="cardio_preference",
        text="Как вы относитесь к кардио? *", kind=QuestionKind.CHOICE, required=True,
        options=(
            Option("cardio_love", "Нравится", CardioPreference.LOVE),
            Option("cardio_okay", "Нормально отношусь", CardioPreference.OKAY),
            Option("cardio_dislike", "Не люблю", CardioPreference.DISLIKE),
            Option("cardio_exclude", "Не хочу", CardioPreference.EXCLUDE),
            Option("cardio_walking_only", "Только ходьба", CardioPreference.WALKING_ONLY),
        ),
    ),
    QuestionDefinition(
        id="q34_cardio_notes", section="lifestyle", field="cardio_notes",
        text="Уточнение по кардио (необязательно)", kind=QuestionKind.TEXT,
        hint="Например: бег противопоказан, могу только эллипс.",
        parse=parse_text,
    ),
    QuestionDefinition(
        id="q35_schedule_constraints", section="additional_information", field="schedule_constraints",
        text="Есть ли ограничения по расписанию?", kind=QuestionKind.TEXT,
        hint="Например: в понедельник могу только 40 минут, работаю посменно.",
        parse=parse_text,
    ),
    QuestionDefinition(
        id="q36_free_text", section="additional_information", field="free_text",
        text="Есть ли что-то ещё, что важно учесть?", kind=QuestionKind.TEXT,
        hint="Например: хочу тренироваться без прыжков, предпочитаю короткие тренировки утром.",
        parse=parse_text,
    ),
]


QUESTIONS_BY_ID: dict[str, QuestionDefinition] = {q.id: q for q in QUESTIONS}


def active_questions(profile: FitnessProfile) -> list[QuestionDefinition]:
    return [q for q in QUESTIONS if q.is_active(profile)]


def next_question_id(profile: FitnessProfile, current_id: str) -> str | None:
    """Возвращает id следующего активного вопроса или None (анкета завершена → review)."""
    questions = active_questions(profile)
    ids = [q.id for q in questions]
    if current_id not in ids:
        return None
    index = ids.index(current_id)
    if index + 1 < len(ids):
        return ids[index + 1]
    return None


def question_progress(profile: FitnessProfile, question_id: str) -> str:
    questions = active_questions(profile)
    ids = [q.id for q in questions]
    if question_id not in ids:
        return ""
    return f"Вопрос {ids.index(question_id) + 1} из {len(ids)}\n\n"
