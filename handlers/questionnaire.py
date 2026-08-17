from __future__ import annotations

from datetime import datetime, timezone
import re

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config import MAX_TEXT_LENGTH
from keyboards.inline import (
    cardio_kb,
    confirm_kb,
    daily_activity_kb,
    edit_questions_kb,
    edit_sections_kb,
    experience_kb,
    frequency_kb,
    goal_kb,
    limitations_kb,
    location_kb,
    medical_clearance_kb,
    preferred_days_kb,
    preferred_time_kb,
    review_kb,
    session_duration_kb,
    sessions_per_week_kb,
    sex_kb,
    skip_kb,
    timeframe_kb,
)
from services.admin_notifier import send_profile_to_admin
from services.profile_builder import build_empty_profile, set_profile_timestamps
from services.storage import log_user_response, save_profile
from states.questionnaire_states import QuestionnaireStates

router = Router()

QUESTION_ORDER = [
    "q01_name", "q02_age", "q03_sex", "q04_height", "q05_weight", "q06_waist",
    "q07_primary_goal", "q08_secondary_goals", "q09_desired_result", "q10_timeframe",
    "q11_experience", "q12_current_frequency", "q13_current_activity", "q14_current_exercises",
    "q15_working_weights", "q16_location", "q17_gym_name", "q18_equipment", "q19_equipment_photos",
    "q18b_home_equipment", "q20_sessions_per_week", "q21_preferred_days", "q22_session_duration",
    "q23_preferred_time", "q24_has_limitations", "q25_limitation_categories",
    "q27_movements_to_avoid", "q28_medical_clearance", "q28_doctor_recommendations", "q29_preferred_exercises", "q30_disliked_exercises",
    "q31_exercise_goals", "q32_daily_activity", "q33_cardio_preference", "q34_cardio_notes",
    "q35_schedule_constraints", "q36_free_text",
]

QUESTION_TEXT = {
    "q01_name": "Как вас зовут? *",
    "q02_age": "Сколько вам лет? *",
    "q03_sex": "Ваш пол?",
    "q04_height": "Укажите рост (см) *",
    "q05_weight": "Укажите текущий вес (кг) *",
    "q06_waist": "Окружность талии (см) — необязательно",
    "q07_primary_goal": "Какова ваша основная цель? *",
    "q08_secondary_goals": "Есть ли дополнительные цели?",
    "q09_desired_result": "Опишите, какой результат будет для вас хорошим через 3–6 месяцев? *",
    "q10_timeframe": "За какой срок хотите достичь идеального для вас результата? *",
    "q11_experience": "Как давно вы тренируетесь регулярно? *",
    "q12_current_frequency": "Сколько раз в неделю тренируетесь сейчас?",
    "q13_current_activity": "Как обычно выглядит ваша тренировка?",
    "q14_current_exercises": "Какие упражнения вы выполняете сейчас?",
    "q15_working_weights": "Если знаете свои рабочие веса — укажите их",
    "q16_location": "Где планируете заниматься? *",
    "q17_gym_name": "Название зала (необязательно)",
    "q18_equipment": "Какое оборудование доступно?",
    "q19_equipment_photos": "Можете прислать фотографии тренажёров вашего зала?",
    "q18b_home_equipment": "Какое оборудование есть дома?",
    "q20_sessions_per_week": "Сколько раз в неделю готовы заниматься?",
    "q21_preferred_days": "Какие дни удобны? *",
    "q22_session_duration": "Сколько времени готовы уделять одной тренировке?",
    "q23_preferred_time": "В какое время суток предпочитаете тренироваться? *",
    "q24_has_limitations": "Есть ли особенности здоровья, которые нужно учитывать?",
    "q25_limitation_categories": "Какие особенности здоровья или ограничения нужно учитывать? *",
    "q27_movements_to_avoid": "Есть ли движения, которые вам нельзя или нежелательно выполнять? *",
    "q28_medical_clearance": "Есть ли рекомендации врача по физической нагрузке? *",
    "q28_doctor_recommendations": "Кратко опишите рекомендации врача",
    "q29_preferred_exercises": "Какие упражнения вам нравятся или вы хотели бы включить?",
    "q30_disliked_exercises": "Какие упражнения вы не любите или не хотите выполнять?",
    "q31_exercise_goals": "Есть ли упражнения, которые вы хотите освоить?",
    "q32_daily_activity": "Какова ваша обычная физическая активность вне тренировок?",
    "q33_cardio_preference": "Как вы относитесь к кардио? *",
    "q34_cardio_notes": "Уточнение по кардио (необязательно)",
    "q35_schedule_constraints": "Есть ли ограничения по расписанию?",
    "q36_free_text": "Есть ли что-то ещё, что важно учесть?",
}

MANDATORY_QUESTIONS = {
    "q01_name", "q02_age", "q04_height", "q05_weight", "q07_primary_goal",
    "q09_desired_result", "q10_timeframe", "q11_experience", "q16_location",
    "q21_preferred_days", "q23_preferred_time", "q25_limitation_categories",
    "q27_movements_to_avoid", "q28_medical_clearance", "q28_doctor_recommendations",
    "q33_cardio_preference"
}

NEXT_STATE = {
    "q01_name": "q02_age",
    "q02_age": "q03_sex",
    "q03_sex": "q04_height",
    "q04_height": "q05_weight",
    "q05_weight": "q06_waist",
    "q06_waist": "q07_primary_goal",
    "q07_primary_goal": "q08_secondary_goals",
    "q08_secondary_goals": "q09_desired_result",
    "q09_desired_result": "q10_timeframe",
    "q10_timeframe": "q11_experience",
    "q11_experience": "q12_current_frequency",
    "q12_current_frequency": "q13_current_activity",
    "q13_current_activity": "q14_current_exercises",
    "q14_current_exercises": "q15_working_weights",
    "q15_working_weights": "q16_location",
    "q16_location": "q17_gym_name",
    "q17_gym_name": "q18_equipment",
    "q18_equipment": "q19_equipment_photos",
    "q19_equipment_photos": "q18b_home_equipment",
    "q18b_home_equipment": "q20_sessions_per_week",
    "q20_sessions_per_week": "q21_preferred_days",
    "q21_preferred_days": "q22_session_duration",
    "q22_session_duration": "q23_preferred_time",
    "q23_preferred_time": "q24_has_limitations",
    "q24_has_limitations": "q25_limitation_categories",
    "q25_limitation_categories": "q27_movements_to_avoid",
    "q27_movements_to_avoid": "q28_medical_clearance",
    "q28_medical_clearance": "q28_doctor_recommendations",
    "q28_doctor_recommendations": "q29_preferred_exercises",
    "q29_preferred_exercises": "q30_disliked_exercises",
    "q30_disliked_exercises": "q31_exercise_goals",
    "q31_exercise_goals": "q32_daily_activity",
    "q32_daily_activity": "q33_cardio_preference",
    "q33_cardio_preference": "q34_cardio_notes",
    "q34_cardio_notes": "q35_schedule_constraints",
    "q35_schedule_constraints": "q36_free_text",
    "q36_free_text": "review",
}

TEXT_QUESTIONS = {
    "q01_name", "q02_age", "q04_height", "q05_weight", "q06_waist",
    "q08_secondary_goals", "q09_desired_result", "q13_current_activity", "q14_current_exercises",
    "q15_working_weights", "q17_gym_name", "q18_equipment", "q18b_home_equipment", "q19_equipment_photos",
    "q25_limitation_categories", "q27_movements_to_avoid", "q28_doctor_recommendations", "q29_preferred_exercises",
    "q30_disliked_exercises", "q31_exercise_goals", "q34_cardio_notes",
    "q35_schedule_constraints", "q36_free_text",
}


def _get_profile(state: FSMContext) -> dict:
    data = state.get_data()
    return data.get("profile") or build_empty_profile()


def _split_list_answer(value: str) -> list[str]:
    return [item.strip() for item in re.split(r"[,;\n]+", value) if item.strip()]


def _active_questions(profile: dict) -> list[str]:
    questions = list(QUESTION_ORDER)
    background = profile["training_background"]
    location = profile["training_location"].get("primary_location")
    health = profile["health_and_limitations"]

    if background.get("current_frequency_per_week") == 0:
        for question_id in ("q13_current_activity", "q14_current_exercises", "q15_working_weights"):
            questions.remove(question_id)
    if location == "home":
        for question_id in ("q17_gym_name", "q18_equipment", "q19_equipment_photos"):
            questions.remove(question_id)
    elif location == "gym":
        questions.remove("q18b_home_equipment")
    if not health.get("has_limitations"):
        questions.remove("q25_limitation_categories")
    if not health.get("medical_clearance_required"):
        questions.remove("q28_doctor_recommendations")
    return questions


def _question_progress(profile: dict, question_id: str) -> str:
    questions = _active_questions(profile)
    if question_id not in questions:
        return ""
    return f"Вопрос {questions.index(question_id) + 1} из {len(questions)}\n\n"


def _next_question_id(profile: dict, question_id: str) -> str:
    if question_id == "q12_current_frequency" and profile["training_background"].get("current_frequency_per_week") == 0:
        return "q16_location"
    if question_id == "q16_location":
        return "q18b_home_equipment" if profile["training_location"].get("primary_location") == "home" else "q17_gym_name"
    if question_id == "q19_equipment_photos":
        location = profile["training_location"].get("primary_location")
        return "q18b_home_equipment" if location in {"home", "both"} else "q20_sessions_per_week"
    if question_id == "q18b_home_equipment":
        return "q20_sessions_per_week"
    if question_id == "q24_has_limitations" and not profile["health_and_limitations"].get("has_limitations"):
        return "q27_movements_to_avoid"
    return NEXT_STATE.get(question_id, "review")


async def _continue_questionnaire(
    message: Message | CallbackQuery,
    state: FSMContext,
    profile: dict,
    question_id: str,
) -> None:
    profile["questionnaire"]["last_question_id"] = question_id
    await state.update_data(profile=profile)

    if (await state.get_data()).get("editing_question"):
        await state.update_data(editing_question=None)
        await state.set_state(QuestionnaireStates.review)
        await render_review(message.message if isinstance(message, CallbackQuery) else message, state)
        return

    next_state = _next_question_id(profile, question_id)
    await state.set_state(getattr(QuestionnaireStates, next_state))
    if next_state == "review":
        await render_review(message.message if isinstance(message, CallbackQuery) else message, state)
    else:
        await ask_question(message, state, next_state)


def _state_to_question_id(current_state: object | None) -> str | None:
    if current_state is None:
        return None
    if hasattr(current_state, "state"):
        current_state = current_state.state
    current_state = str(current_state)
    if current_state.startswith("QuestionnaireStates:"):
        current_state = current_state.replace("QuestionnaireStates:", "", 1)
    if ":" in current_state:
        return current_state.rsplit(":", 1)[-1]
    return current_state


async def ask_question(message: Message | CallbackQuery, _state: FSMContext, question_id: str) -> None:
    question_text = QUESTION_TEXT.get(question_id, "Введите ответ")
    if isinstance(message, CallbackQuery):
        target = message.message
    else:
        target = message
    hint = {
        "q01_name": "Введите имя от 2 до 50 символов.",
        "q02_age": "Введите целое число от 14 до 100.",
        "q04_height": "Введите рост в сантиметрах: 120–250.",
        "q05_weight": "Введите вес в кг: 30–300.",
        "q06_waist": "Введите значение от 40 до 200 см.",
        "q08_secondary_goals": "Например: улучшить осанку, повысить выносливость. Перечислите через запятую или с новой строки.",
        "q09_desired_result": "Напишите коротко, что для вас считается хорошим результатом.",
        "q13_current_activity": "Например: тренажёры 40 минут + кардио 20 минут.",
        "q14_current_exercises": "Например: приседания, жим гантелей, ходьба. Перечислите через запятую или каждое с новой строки.",
        "q15_working_weights": "Например: Жим ногами — 120 кг × 12, Тяга верхнего блока — 50 кг × 12.",
        "q17_gym_name": "Например: World Class Владивосток.",
        "q18_equipment": "Перечислите через запятую или каждое с новой строки. Например: гантели, скамья, беговая дорожка.",
        "q19_equipment_photos": "Можно отправить до 10 фотографий по одной.",
        "q18b_home_equipment": "Например: гантели, резиновые петли, коврик. Перечислите через запятую или каждое с новой строки.",
        "q25_limitation_categories": "Например: боли в пояснице, травма колена, повышенное давление. Если ограничений нет — напишите «Нет». ",
        "q27_movements_to_avoid": "Например: бег, прыжки, приседания со штангой. Если таких движений нет — напишите «Нет».",
        "q28_doctor_recommendations": "Например: избегать силовых нагрузок высокой интенсивности, контролировать давление.",
        "q29_preferred_exercises": "Например: тренажёры, жим ногами, плавание. Перечислите через запятую или каждое с новой строки.",
        "q30_disliked_exercises": "Например: бег, выпады, берпи. Перечислите через запятую или каждое с новой строки.",
        "q31_exercise_goals": "Например: научиться подтягиваться на турнике.",
        "q35_schedule_constraints": "Например: в понедельник могу только 40 минут, работаю посменно.",
        "q34_cardio_notes": "Например: бег противопоказан, могу только эллипс.",
        "q36_free_text": "Например: хочу тренироваться без прыжков, предпочитаю короткие тренировки утром.",
    }.get(question_id, "")
    keyboard_map = {
        "q03_sex": sex_kb,
        "q07_primary_goal": goal_kb,
        "q10_timeframe": timeframe_kb,
        "q11_experience": experience_kb,
        "q12_current_frequency": frequency_kb,
        "q16_location": location_kb,
        "q20_sessions_per_week": sessions_per_week_kb,
        "q21_preferred_days": preferred_days_kb,
        "q22_session_duration": session_duration_kb,
        "q23_preferred_time": preferred_time_kb,
        "q24_has_limitations": limitations_kb,
        "q32_daily_activity": daily_activity_kb,
        "q28_medical_clearance": medical_clearance_kb,
        "q33_cardio_preference": cardio_kb,
    }
    keyboard_factory = keyboard_map.get(question_id)
    reply_markup = keyboard_factory() if keyboard_factory else None
    
    if question_id not in MANDATORY_QUESTIONS:
        if reply_markup is None:
            reply_markup = skip_kb()
        else:
            reply_markup.inline_keyboard.append(skip_kb().inline_keyboard[0])
    
    profile = (await _state.get_data()).get("profile") or build_empty_profile()
    await target.answer(f"{_question_progress(profile, question_id)}{question_text}\n\n{hint}", reply_markup=reply_markup)


def _save_text_answer(profile: dict, question_id: str, value: str) -> None:
    value = value.strip()[:MAX_TEXT_LENGTH]
    answer_map = {
        "q01_name": lambda: profile["client"].__setitem__("name", value),
        "q02_age": lambda: profile["client"].__setitem__("age_years", int(value)),
        "q04_height": lambda: profile["client"].__setitem__("height_cm", int(value)),
        "q05_weight": lambda: profile["client"].__setitem__("weight_kg", round(float(value), 1)),
        "q06_waist": lambda: profile["client"].__setitem__("waist_cm", int(float(value))),
        "q08_secondary_goals": lambda: profile["goals"].__setitem__("secondary", _split_list_answer(value)),
        "q09_desired_result": lambda: profile["goals"].__setitem__("desired_result", value),
        "q13_current_activity": lambda: profile["training_background"].__setitem__("current_activity_description", value or None),
        "q14_current_exercises": lambda: profile["training_background"].__setitem__("current_exercises", _split_list_answer(value)),
        "q15_working_weights": lambda: profile["training_background"].__setitem__("known_working_weights", [{"exercise": "Указано вручную", "weight": 0.0, "unit": "kg", "sets_reps": "", "notes": value or None}]),
        "q17_gym_name": lambda: profile["training_location"].__setitem__("gym_name", value or None),
        "q18_equipment": lambda: profile["training_location"].__setitem__("available_equipment", _split_list_answer(value)),
        "q18b_home_equipment": lambda: profile["training_location"].__setitem__("custom_equipment_description", ", ".join(_split_list_answer(value)) or None),
        "q19_equipment_photos": lambda: profile["training_location"].__setitem__("equipment_photos", [value]),
        "q25_limitation_categories": lambda: profile["health_and_limitations"].update({
            "categories": [value] if value else [],
            "details": [{
                "category": "general",
                "user_description": value,
                "triggers": None,
                "current_status": None,
            }] if value else [],
        }),
        "q27_movements_to_avoid": lambda: profile["health_and_limitations"].__setitem__("movements_to_avoid", _split_list_answer(value)),
        "q28_doctor_recommendations": lambda: profile["health_and_limitations"].__setitem__("doctor_recommendations", value or None),
        "q29_preferred_exercises": lambda: profile["exercise_preferences"].__setitem__("preferred_exercises", _split_list_answer(value)),
        "q30_disliked_exercises": lambda: profile["exercise_preferences"].__setitem__("disliked_exercises", _split_list_answer(value)),
        "q31_exercise_goals": lambda: profile["exercise_preferences"].__setitem__("exercise_goals", _split_list_answer(value)),
        "q34_cardio_notes": lambda: profile["lifestyle"].__setitem__("cardio_notes", value or None),
        "q35_schedule_constraints": lambda: profile["additional_information"].__setitem__("schedule_constraints", value or None),
        "q36_free_text": lambda: profile["additional_information"].__setitem__("free_text", value or None),
    }
    if question_id in answer_map:
        answer_map[question_id]()


async def render_review(message: Message, state: FSMContext) -> None:
    profile = (await state.get_data()).get("profile") or build_empty_profile()

    def value(item: object, suffix: str = "") -> str:
        if item in (None, "", [], {}):
            return "—"
        if isinstance(item, list):
            return ", ".join(str(part) for part in item) or "—"
        return f"{item}{suffix}"

    labels = {
        "male": "Мужской", "female": "Женский", "not_specified": "Не указан",
        "weight_loss": "Снижение веса", "muscle_gain": "Набор мышечной массы",
        "strength": "Увеличение силы", "health_fitness": "Здоровье и общая форма",
        "endurance": "Повышение выносливости", "return_to_training": "Возвращение к тренировкам",
        "other": "Другое", "never": "Никогда не занимался", "long_break": "Был длинный перерыв",
        "under_3_months": "До 3 месяцев", "3_12_months": "3–12 месяцев", "over_1_year": "Больше года",
        "home": "Дома", "gym": "В зале", "both": "Дома и в зале",
        "morning": "Утро", "afternoon": "День", "evening": "Вечер", "any": "Любое время",
        "sedentary": "Сидячая работа, мало движения", "light_walking": "Немного хожу в течение дня",
        "active_walking": "Много хожу или активная работа", "physical_work": "Тяжёлая физическая работа",
        "very_active": "Очень высокая активность",
        "love": "Нравится", "okay": "Нормально отношусь", "dislike": "Не люблю",
        "exclude": "Не хочу", "walking_only": "Только ходьба",
    }

    def labelled(item: object, suffix: str = "") -> str:
        return value(labels.get(item, item), suffix)

    client = profile["client"]
    goals = profile["goals"]
    background = profile["training_background"]
    plan = profile["training_plan_preferences"]
    location = profile["training_location"]
    health = profile["health_and_limitations"]
    preferences = profile["exercise_preferences"]
    lifestyle = profile["lifestyle"]
    additional = profile["additional_information"]

    summary = (
        "📋 <b>Ваша анкета</b>\n\n"
        "<b>👤 О вас</b>\n"
        f"Имя: {value(client['name'])}\nВозраст: {value(client['age_years'], ' лет')}\n"
        f"Пол: {labelled(client['sex'])}\nРост: {value(client['height_cm'], ' см')}\n"
        f"Вес: {value(client['weight_kg'], ' кг')}\nТалия: {value(client['waist_cm'], ' см')}\n\n"
        "<b>🎯 Цели</b>\n"
        f"Основная: {labelled(goals['primary'])}\nДополнительные: {value(goals['secondary'])}\n"
        f"Желаемый результат: {value(goals['desired_result'])}\nСрок: {value(goals['target_timeframe'])}\n\n"
        "<b>🏋️ Опыт и тренировки</b>\n"
        f"Опыт: {labelled(background['experience_level'])}\nЧастота сейчас: {value(background['current_frequency_per_week'], ' раз/нед.') }\n"
        f"Текущая активность: {value(background['current_activity_description'])}\nУпражнения: {value(background['current_exercises'])}\n"
        f"Рабочие веса: {value(background['known_working_weights'])}\n\n"
        "<b>📍 Место и график</b>\n"
        f"Место: {labelled(location['primary_location'])}\nЗал: {value(location['gym_name'])}\n"
        f"Оборудование: {value(location['available_equipment'])}\nДомашнее оборудование: {value(location['custom_equipment_description'])}\n"
        f"Фото оборудования: {len(location['equipment_photos']) or '—'}\nТренировок в неделю: {value(plan['sessions_per_week'])}\n"
        f"Удобные дни: {value(plan['preferred_days'])}\nДлительность: {value(plan['session_duration_minutes'], ' мин')}\n"
        f"Время: {labelled(plan['preferred_training_time'])}\n\n"
        "<b>⚕️ Здоровье</b>\n"
        f"Ограничения: {'Есть' if health['has_limitations'] else 'Нет'}\n"
        f"Описание: {value(health['categories'])}\nНежелательные движения: {value(health['movements_to_avoid'])}\n"
        f"Рекомендации врача: {value(health['doctor_recommendations'])}\n\n"
        "<b>💪 Предпочтения и образ жизни</b>\n"
        f"Нравятся упражнения: {value(preferences['preferred_exercises'])}\nНе нравятся: {value(preferences['disliked_exercises'])}\n"
        f"Хочу освоить: {value(preferences['exercise_goals'])}\nАктивность вне тренировок: {labelled(lifestyle['daily_activity_level'])}\n"
        f"Кардио: {labelled(lifestyle['cardio_preference'])}\nКомментарий по кардио: {value(lifestyle['cardio_notes'])}\n\n"
        "<b>📝 Дополнительно</b>\n"
        f"Ограничения по расписанию: {value(additional['schedule_constraints'])}\n"
        f"Другая информация: {value(additional['free_text'])}"
    )
    await message.answer(summary, reply_markup=review_kb(), parse_mode="HTML")


def _validate_name(text: str) -> tuple[bool, str | None]:
    if 2 <= len(text) <= 50:
        return True, None
    return False, "Имя должно содержать от 2 до 50 символов. Попробуйте ещё раз."


def _validate_age(text: str) -> tuple[bool, str | None]:
    try:
        value = int(text)
    except ValueError:
        return False, "Введите целое число от 14 до 100."
    if 14 <= value <= 100:
        return True, None
    return False, "Возраст должен быть в диапазоне 14–100."


def _validate_height(text: str) -> tuple[bool, str | None]:
    try:
        value = int(text)
    except ValueError:
        return False, "Введите рост 120–250 см."
    if 120 <= value <= 250:
        return True, None
    return False, "Рост должен быть в диапазоне 120–250 см."


def _validate_weight(text: str) -> tuple[bool, str | None]:
    try:
        value = float(text)
    except ValueError:
        return False, "Введите вес 30–300 кг."
    if 30 <= value <= 300:
        return True, None
    return False, "Вес должен быть в диапазоне 30–300 кг."


def _validate_waist(text: str) -> tuple[bool, str | None]:
    if text.lower() in {"пропустить", "skip"}:
        return True, None
    try:
        value = float(text)
    except ValueError:
        return False, "Введите значение от 40 до 200 см."
    if 40 <= value <= 200:
        return True, None
    return False, "Окружность талии должна быть 40–200 см."


def _validate_goal_result(text: str) -> tuple[bool, str | None]:
    if len(text) >= 5:
        return True, None
    return False, "Опишите результат подробнее, минимум 5 символов."


QUESTION_VALIDATORS = {
    "q01_name": _validate_name,
    "q02_age": _validate_age,
    "q04_height": _validate_height,
    "q05_weight": _validate_weight,
    "q06_waist": _validate_waist,
    "q09_desired_result": _validate_goal_result,
}


def _validate_question_text(question_id: str, text: str) -> tuple[bool, str | None]:
    validator = QUESTION_VALIDATORS.get(question_id)
    if validator is None:
        return True, None
    return validator(text)


@router.message(F.photo)
async def handle_photo_upload(message: Message, state: FSMContext) -> None:
    current = _state_to_question_id(await state.get_state())
    if current != "q19_equipment_photos":
        return
    profile = (await state.get_data()).get("profile") or build_empty_profile()
    photo_file = message.photo[-1]
    profile["training_location"]["equipment_photos"].append(photo_file.file_id)
    await message.answer("✅ Фото добавлено. Продолжаем анкету.")
    await _continue_questionnaire(message, state, profile, current)


@router.message(F.text)
async def handle_text_answers(message: Message, state: FSMContext) -> None:
    question_id = _state_to_question_id(await state.get_state())
    if question_id is None:
        return

    profile = (await state.get_data()).get("profile") or build_empty_profile()

    if question_id not in TEXT_QUESTIONS:
        await message.answer("Неожиданный тип данных. Введите корректный ответ или выберите кнопку из диалога.")
        return

    text = message.text.strip()
    
    # Проверка на пропуск вопроса (для необязательных вопросов)
    if text.lower() in {"пропустить", "skip"} and question_id not in MANDATORY_QUESTIONS:
        await message.answer("⏭️ Вопрос пропущен")
        await _continue_questionnaire(message, state, profile, question_id)
        return
    
    ok, error_text = _validate_question_text(question_id, text)
    if not ok:
        await message.answer(error_text)
        return

    if question_id == "q06_waist" and text.lower() in {"пропустить", "skip"}:
        profile["client"]["waist_cm"] = None
    elif question_id == "q06_waist":
        profile["client"]["waist_cm"] = int(float(text))
    else:
        _save_text_answer(profile, question_id, text)

    await message.answer("✅ Записал: ответ сохранён")
    await _continue_questionnaire(message, state, profile, question_id)


@router.callback_query(F.data == "skip_question")
async def skip_optional_question(callback: CallbackQuery, state: FSMContext) -> None:
    """Пропустить необязательный вопрос через кнопку"""
    question_id = _state_to_question_id(await state.get_state())
    if question_id is None or question_id in MANDATORY_QUESTIONS:
        return
    
    profile = (await state.get_data()).get("profile") or build_empty_profile()
    await callback.message.edit_text("⏭️ Вопрос пропущен")
    await _continue_questionnaire(callback, state, profile, question_id)
    await callback.answer()


@router.callback_query(F.data.startswith("sex_"))
async def handle_sex(callback: CallbackQuery, state: FSMContext) -> None:
    profile = (await state.get_data()).get("profile") or build_empty_profile()
    sex_map = {
        "sex_male": "male",
        "sex_female": "female",
        "sex_not_specified": "not_specified",
    }
    profile["client"]["sex"] = sex_map.get(callback.data, "not_specified")
    await callback.message.edit_text("✅ Записал: пол сохранён")
    await _continue_questionnaire(callback, state, profile, "q03_sex")
    await callback.answer()


@router.callback_query(F.data.startswith("goal_"))
async def handle_goal(callback: CallbackQuery, state: FSMContext) -> None:
    profile = (await state.get_data()).get("profile") or build_empty_profile()
    goal_map = {
        "goal_weight_loss": "weight_loss",
        "goal_muscle_gain": "muscle_gain",
        "goal_strength": "strength",
        "goal_health_fitness": "health_fitness",
        "goal_endurance": "endurance",
        "goal_return_to_training": "return_to_training",
        "goal_other": "other",
    }
    profile["goals"]["primary"] = goal_map.get(callback.data, "other")
    profile["goals"]["primary_custom"] = None if callback.data != "goal_other" else "Уточните вручную"
    await callback.message.edit_text("✅ Записал: основная цель сохранена")
    await _continue_questionnaire(callback, state, profile, "q07_primary_goal")
    await callback.answer()


@router.callback_query(F.data.startswith("timeframe_"))
async def handle_timeframe(callback: CallbackQuery, state: FSMContext) -> None:
    profile = (await state.get_data()).get("profile") or build_empty_profile()
    value = callback.data.replace("timeframe_", "")
    mapping = {
        "1_month": "1_month",
        "2_3_months": "2_3_months",
        "3_6_months": "3_6_months",
        "6_12_months": "6_12_months",
        "no_rush": "no_rush",
    }
    profile["goals"]["target_timeframe"] = mapping.get(value, "no_rush")
    await callback.message.edit_text("✅ Записал: срок достижения цели сохранён")
    await _continue_questionnaire(callback, state, profile, "q10_timeframe")
    await callback.answer()


@router.callback_query(F.data.startswith("exp_"))
async def handle_experience(callback: CallbackQuery, state: FSMContext) -> None:
    mapping = {
        "exp_never": "never",
        "exp_long_break": "long_break",
        "exp_under_3_months": "under_3_months",
        "exp_3_12_months": "3_12_months",
        "exp_over_1_year": "over_1_year",
    }
    profile = (await state.get_data()).get("profile") or build_empty_profile()
    profile["training_background"]["experience_level"] = mapping.get(callback.data, "never")
    await callback.message.edit_text("✅ Записал: опыт тренировок сохранён")
    await _continue_questionnaire(callback, state, profile, "q11_experience")
    await callback.answer()


@router.callback_query(F.data.startswith("freq_"))
async def handle_current_frequency(callback: CallbackQuery, state: FSMContext) -> None:
    profile = (await state.get_data()).get("profile") or build_empty_profile()
    value = callback.data.replace("freq_", "")
    if value == "none":
        profile["training_background"]["current_frequency_per_week"] = 0
    else:
        try:
            profile["training_background"]["current_frequency_per_week"] = int(value)
        except ValueError:
            profile["training_background"]["current_frequency_per_week"] = 0
    await callback.message.edit_text("✅ Записал: текущая частота тренировок сохранена")
    await _continue_questionnaire(callback, state, profile, "q12_current_frequency")
    await callback.answer()


@router.callback_query(F.data.startswith("loc_"))
async def handle_location(callback: CallbackQuery, state: FSMContext) -> None:
    profile = (await state.get_data()).get("profile") or build_empty_profile()
    mapping = {
        "loc_home": "home",
        "loc_gym": "gym",
        "loc_both": "both",
    }
    profile["training_location"]["primary_location"] = mapping.get(callback.data, "home")
    profile["questionnaire"]["last_question_id"] = "q16_location"
    next_state = _next_question_id(profile, "q16_location")
    await state.update_data(profile=profile)
    await state.set_state(getattr(QuestionnaireStates, next_state))
    await callback.message.edit_text("✅ Записал: место тренировок сохранено")
    await ask_question(callback, state, next_state)
    await callback.answer()


@router.callback_query(F.data.startswith("sessions_"))
async def handle_sessions_per_week(callback: CallbackQuery, state: FSMContext) -> None:
    profile = (await state.get_data()).get("profile") or build_empty_profile()
    value = callback.data.replace("sessions_", "")
    try:
        profile["training_plan_preferences"]["sessions_per_week"] = int(value)
    except ValueError:
        profile["training_plan_preferences"]["sessions_per_week"] = 1
    await callback.message.edit_text("✅ Записал: желаемое число тренировок сохранено")
    await _continue_questionnaire(callback, state, profile, "q20_sessions_per_week")
    await callback.answer()


@router.callback_query(F.data.startswith("day_"))
async def handle_preferred_days_selection(callback: CallbackQuery, state: FSMContext) -> None:
    profile = (await state.get_data()).get("profile") or build_empty_profile()
    selected = list(profile["training_plan_preferences"].get("preferred_days") or [])
    day_value = callback.data.replace("day_", "")
    day_labels = {
        "mon": "Пн",
        "tue": "Вт",
        "wed": "Ср",
        "thu": "Чт",
        "fri": "Пт",
        "sat": "Сб",
        "sun": "Вс",
    }
    if day_value in selected:
        selected.remove(day_value)
        action = "удалён"
    else:
        selected.append(day_value)
        action = "добавлен"
    profile["training_plan_preferences"]["preferred_days"] = selected
    await state.update_data(profile=profile)
    day_label = day_labels.get(day_value, day_value)
    await callback.message.edit_reply_markup(reply_markup=preferred_days_kb(selected))
    await callback.answer(f"День {day_label} {action}")


@router.callback_query(F.data == "days_done")
async def handle_preferred_days_done(callback: CallbackQuery, state: FSMContext) -> None:
    profile = (await state.get_data()).get("profile") or build_empty_profile()
    selected = profile["training_plan_preferences"].get("preferred_days") or []
    if not selected:
        await callback.answer("Выберите хотя бы один день недели.", show_alert=True)
        return
    await callback.message.edit_text("✅ Выбраны удобные дни недели")
    await _continue_questionnaire(callback, state, profile, "q21_preferred_days")
    await callback.answer("Дни недели сохранены")


@router.callback_query(F.data.startswith("duration_"))
async def handle_session_duration(callback: CallbackQuery, state: FSMContext) -> None:
    profile = (await state.get_data()).get("profile") or build_empty_profile()
    duration_map = {
        "duration_45": 45,
        "duration_60": 60,
        "duration_75": 75,
        "duration_90": 90,
        "duration_120": 120,
        "duration_150": 150,
    }
    profile["training_plan_preferences"]["session_duration_minutes"] = duration_map.get(callback.data, 60)
    await callback.message.edit_text("✅ Записал: длительность тренировки сохранена")
    await _continue_questionnaire(callback, state, profile, "q22_session_duration")
    await callback.answer()


@router.callback_query(F.data.startswith("time_pref_"))
async def handle_preferred_time(callback: CallbackQuery, state: FSMContext) -> None:
    mapping = {
        "time_pref_morning": "morning",
        "time_pref_afternoon": "afternoon",
        "time_pref_evening": "evening",
        "time_pref_any": "any",
    }
    profile = (await state.get_data()).get("profile") or build_empty_profile()
    profile["training_plan_preferences"]["preferred_training_time"] = mapping.get(callback.data, "any")
    await callback.message.edit_text("✅ Записал: предпочтительное время сохранено")
    await _continue_questionnaire(callback, state, profile, "q23_preferred_time")
    await callback.answer()


@router.callback_query(F.data.startswith("limit_"))
async def handle_limitations(callback: CallbackQuery, state: FSMContext) -> None:
    profile = (await state.get_data()).get("profile") or build_empty_profile()
    value = callback.data.replace("limit_", "")
    profile["health_and_limitations"]["has_limitations"] = value == "yes"
    await state.update_data(profile=profile)
    if (await state.get_data()).get("editing_question"):
        await _continue_questionnaire(callback, state, profile, "q24_has_limitations")
        await callback.answer()
        return
    if value == "yes":
        await state.set_state(QuestionnaireStates.q25_limitation_categories)
        await callback.message.edit_text("✅ Записал: учёт ограничений отмечен")
        await ask_question(callback.message, state, "q25_limitation_categories")
    else:
        await state.set_state(QuestionnaireStates.q27_movements_to_avoid)
        await callback.message.edit_text("✅ Ограничения не указаны")
        await ask_question(callback.message, state, "q27_movements_to_avoid")
    await callback.answer()


@router.callback_query(F.data.startswith("med_clear_"))
async def handle_medical_clearance(callback: CallbackQuery, state: FSMContext) -> None:
    profile = (await state.get_data()).get("profile") or build_empty_profile()
    mapping = {
        "med_clear_restricted": True,
        "med_clear_no_recommendations": False,
    }
    has_recommendations = mapping.get(callback.data, False)
    profile["health_and_limitations"]["medical_clearance_required"] = has_recommendations
    profile["health_and_limitations"]["doctor_recommendations"] = None
    await callback.message.edit_text("✅ Записал: рекомендации врача сохранены")
    if has_recommendations:
        await state.update_data(profile=profile)
        await state.set_state(QuestionnaireStates.q28_doctor_recommendations)
        await ask_question(callback, state, "q28_doctor_recommendations")
    else:
        await _continue_questionnaire(callback, state, profile, "q28_doctor_recommendations")
    await callback.answer()


@router.callback_query(F.data.startswith("cardio_"))
async def handle_cardio_preference(callback: CallbackQuery, state: FSMContext) -> None:
    mapping = {
        "cardio_love": "love",
        "cardio_okay": "okay",
        "cardio_dislike": "dislike",
        "cardio_exclude": "exclude",
        "cardio_walking_only": "walking_only",
    }
    profile = (await state.get_data()).get("profile") or build_empty_profile()
    profile["lifestyle"]["cardio_preference"] = mapping.get(callback.data, "okay")
    await callback.message.edit_text("✅ Записал: отношение к кардио сохранено")
    await _continue_questionnaire(callback, state, profile, "q33_cardio_preference")
    await callback.answer()


@router.callback_query(F.data.startswith("activity_"))
async def handle_daily_activity(callback: CallbackQuery, state: FSMContext) -> None:
    mapping = {
        "activity_sedentary": "sedentary",
        "activity_light": "light_walking",
        "activity_moderate": "active_walking",
        "activity_high": "physical_work",
        "activity_other": "very_active",
    }
    profile = (await state.get_data()).get("profile") or build_empty_profile()
    profile["lifestyle"]["daily_activity_level"] = mapping.get(callback.data, "very_active")
    await callback.message.edit_text("✅ Записал: уровень активности сохранён")
    await _continue_questionnaire(callback, state, profile, "q32_daily_activity")
    await callback.answer()


@router.callback_query(F.data == "review_confirm")
async def review_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    profile = (await state.get_data()).get("profile") or build_empty_profile()
    profile["consents"]["data_processing_confirmed"] = True
    profile["consents"]["health_information_confirmed"] = True
    profile["consents"]["accuracy_confirmed"] = True
    profile["questionnaire"]["completed"] = True
    profile["questionnaire"]["completion_status"] = "confirmed"
    profile["review"]["client_summary_confirmed"] = True
    profile = set_profile_timestamps(profile)
    if profile.get("profile_id") is None:
        profile["profile_id"] = f"REQ-{datetime.now(timezone.utc).strftime('%Y%m%d')}-00001"
    await state.update_data(profile=profile)
    save_profile(profile)
    await callback.message.edit_text(
        "Подтверждаю, что:\n✅ Указанные данные верны\n✅ Информация о здоровье указана корректно\n✅ Я понимаю, что программа не заменяет консультацию врача\n\n",
        reply_markup=confirm_kb(),
    )
    await callback.answer("Подтверждение принято")


@router.callback_query(F.data == "final_confirm")
async def final_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    profile = (await state.get_data()).get("profile") or build_empty_profile()
    profile["questionnaire"]["completed"] = True
    profile["questionnaire"]["completion_status"] = "confirmed"
    profile["review"]["client_summary_confirmed"] = True
    profile = set_profile_timestamps(profile)
    profile_path = save_profile(profile)
    await send_profile_to_admin(callback.bot, profile, profile_path)
    await callback.message.edit_text(
        "✅ Спасибо! Ваша анкета принята. Номер: "
        f"{profile.get('profile_id') or 'REQ-UNKNOWN'}\n\n"
        "Тренер свяжется с вами в течение 24 часов."
    )
    await callback.answer("Анкета сохранена")


@router.callback_query(F.data == "return_to_questionnaire")
async def return_to_questionnaire(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(QuestionnaireStates.review)
    await render_review(callback.message, state)
    await callback.answer()


@router.callback_query(F.data == "review_edit")
async def review_edit(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.message.edit_text("Выберите раздел, который хотите исправить:", reply_markup=edit_sections_kb())
    await callback.answer()


@router.callback_query(F.data.startswith("edit_section_"))
async def select_edit_section(callback: CallbackQuery) -> None:
    section = callback.data.removeprefix("edit_section_")
    await callback.message.edit_text("Выберите вопрос:", reply_markup=edit_questions_kb(section))
    await callback.answer()


@router.callback_query(F.data.startswith("edit_question_"))
async def select_question_to_edit(callback: CallbackQuery, state: FSMContext) -> None:
    target = callback.data.removeprefix("edit_question_")
    if target not in QUESTION_ORDER:
        await callback.answer("Этот вопрос больше не используется.", show_alert=True)
        return
    await state.update_data(editing_question=target)
    await state.set_state(getattr(QuestionnaireStates, target))
    await callback.message.edit_text("Исправьте ответ:")
    await ask_question(callback.message, state, target)
    await callback.answer()


@router.message(F.photo)
async def handle_photo(message: Message, state: FSMContext) -> None:
    profile = (await state.get_data()).get("profile") or build_empty_profile()
    if profile.get("profile_id") is None:
        profile["profile_id"] = f"REQ-{datetime.now(timezone.utc).strftime('%Y%m%d')}-00001"
    photo_dir = __import__("pathlib").Path("data/photos") / str(profile["profile_id"])
    photo_dir.mkdir(parents=True, exist_ok=True)
    file = await message.bot.get_file(message.photo[-1].file_id)
    path = await message.bot.download_file(file.file_path)
    target_path = photo_dir / (file.file_id + ".jpg")
    target_path.write_bytes(path.read_bytes())
    existing = profile["training_location"].get("equipment_photos", [])
    existing.append(str(target_path))
    profile["training_location"]["equipment_photos"] = existing[:10]
    await state.update_data(profile=profile)
    await message.answer("✅ Фото сохранено. Можно отправить ещё одно или продолжить анкету.")
