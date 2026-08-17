from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def yes_no_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Да", callback_data="yes"),
                InlineKeyboardButton(text="Нет", callback_data="no"),
            ]
        ]
    )


def start_qa_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="▶ Начать анкету", callback_data="start_qa")],
            [InlineKeyboardButton(text="ℹ️ Подробнее об услуге", callback_data="show_service_info")],
        ]
    )


def resume_qa_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="▶ Продолжить анкету", callback_data="resume_qa")],
            [InlineKeyboardButton(text="🆕 Начать заново", callback_data="restart_qa")],
        ]
    )


def skip_kb() -> InlineKeyboardMarkup:
    """Клавиатура для кнопки 'Пропустить' в необязательных вопросах"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⏭️ Пропустить", callback_data="skip_question")],
        ]
    )


def sex_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Мужской", callback_data="sex_male")],
            [InlineKeyboardButton(text="Женский", callback_data="sex_female")],
            [InlineKeyboardButton(text="Не хочу указывать", callback_data="sex_not_specified")],
        ]
    )


def goal_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⚖️ Снижение веса", callback_data="goal_weight_loss")],
            [InlineKeyboardButton(text="💪 Набор мышечной массы", callback_data="goal_muscle_gain")],
            [InlineKeyboardButton(text="🏋️ Увеличение силы", callback_data="goal_strength")],
            [InlineKeyboardButton(text="❤️ Улучшение здоровья и общей формы", callback_data="goal_health_fitness")],
            [InlineKeyboardButton(text="🏃 Повышение выносливости", callback_data="goal_endurance")],
            [InlineKeyboardButton(text="🔄 Возвращение к тренировкам", callback_data="goal_return_to_training")],
            [InlineKeyboardButton(text="✍️ Другое", callback_data="goal_other")],
        ]
    )


def timeframe_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="1 месяц", callback_data="timeframe_1_month")],
            [InlineKeyboardButton(text="2–3 месяца", callback_data="timeframe_2_3_months")],
            [InlineKeyboardButton(text="3–6 месяцев", callback_data="timeframe_3_6_months")],
            [InlineKeyboardButton(text="6–12 месяцев", callback_data="timeframe_6_12_months")],
            [InlineKeyboardButton(text="Не тороплюсь", callback_data="timeframe_no_rush")],
        ]
    )


def experience_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Никогда не занимался", callback_data="exp_never")],
            [InlineKeyboardButton(text="Был длинный перерыв", callback_data="exp_long_break")],
            [InlineKeyboardButton(text="До 3 месяцев", callback_data="exp_under_3_months")],
            [InlineKeyboardButton(text="3–12 месяцев", callback_data="exp_3_12_months")],
            [InlineKeyboardButton(text="Больше 1 года", callback_data="exp_over_1_year")],
        ]
    )


def frequency_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="0 раз", callback_data="freq_none")],
            [InlineKeyboardButton(text="1 раз", callback_data="freq_1")],
            [InlineKeyboardButton(text="2 раза", callback_data="freq_2")],
            [InlineKeyboardButton(text="3 раза", callback_data="freq_3")],
            [InlineKeyboardButton(text="4 раза", callback_data="freq_4")],
            [InlineKeyboardButton(text="5+ раз", callback_data="freq_5")],
        ]
    )


def location_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Дома", callback_data="loc_home")],
            [InlineKeyboardButton(text="В зале", callback_data="loc_gym")],
            [InlineKeyboardButton(text="И дома, и в зале", callback_data="loc_both")],
        ]
    )


def sessions_per_week_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="1 раз", callback_data="sessions_1")],
            [InlineKeyboardButton(text="2 раза", callback_data="sessions_2")],
            [InlineKeyboardButton(text="3 раза", callback_data="sessions_3")],
            [InlineKeyboardButton(text="4 раза", callback_data="sessions_4")],
            [InlineKeyboardButton(text="5 раз", callback_data="sessions_5")],
            [InlineKeyboardButton(text="6 раз", callback_data="sessions_6")],
        ]
    )


def preferred_time_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Утро", callback_data="time_pref_morning")],
            [InlineKeyboardButton(text="День", callback_data="time_pref_afternoon")],
            [InlineKeyboardButton(text="Вечер", callback_data="time_pref_evening")],
            [InlineKeyboardButton(text="Любое время", callback_data="time_pref_any")],
        ]
    )


def session_duration_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="30–45 мин", callback_data="duration_45")],
            [InlineKeyboardButton(text="45–60 мин", callback_data="duration_60")],
            [InlineKeyboardButton(text="60–75 мин", callback_data="duration_75")],
            [InlineKeyboardButton(text="75–90 мин", callback_data="duration_90")],
            [InlineKeyboardButton(text="90–120 мин", callback_data="duration_120")],
            [InlineKeyboardButton(text="2 часа и больше", callback_data="duration_150")],
        ]
    )


def preferred_days_kb(selected_days: list[str] | None = None) -> InlineKeyboardMarkup:
    selected_days = set(selected_days or [])
    days = [
        ("Пн", "mon"),
        ("Вт", "tue"),
        ("Ср", "wed"),
        ("Чт", "thu"),
        ("Пт", "fri"),
        ("Сб", "sat"),
        ("Вс", "sun"),
    ]
    rows = []
    for label, value in days:
        text = f"✅ {label}" if value in selected_days else label
        rows.append([InlineKeyboardButton(text=text, callback_data=f"day_{value}")])
    rows.append([InlineKeyboardButton(text="Готово", callback_data="days_done")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def limitations_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Да, есть ограничения", callback_data="limit_yes")],
            [InlineKeyboardButton(text="Нет, всё в норме", callback_data="limit_no")],
        ]
    )


def medical_clearance_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Есть рекомендации врача", callback_data="med_clear_restricted")],
            [InlineKeyboardButton(text="Рекомендаций нет", callback_data="med_clear_no_recommendations")],
        ]
    )


def cardio_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Нравится", callback_data="cardio_love")],
            [InlineKeyboardButton(text="Нормально отношусь", callback_data="cardio_okay")],
            [InlineKeyboardButton(text="Не люблю", callback_data="cardio_dislike")],
            [InlineKeyboardButton(text="Не хочу", callback_data="cardio_exclude")],
            [InlineKeyboardButton(text="Только ходьба", callback_data="cardio_walking_only")],
        ]
    )


def daily_activity_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Сидячая работа, мало двигаюсь", callback_data="activity_sedentary")],
            [InlineKeyboardButton(text="Немного хожу в течение дня", callback_data="activity_light")],
            [InlineKeyboardButton(text="Много хожу или активная работа", callback_data="activity_moderate")],
            [InlineKeyboardButton(text="Тяжёлая физическая работа", callback_data="activity_high")],
            [InlineKeyboardButton(text="Другое / затрудняюсь ответить", callback_data="activity_other")],
        ]
    )


def review_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Всё верно", callback_data="review_confirm")],
            [InlineKeyboardButton(text="✏️ Исправить", callback_data="review_edit")],
        ]
    )


def edit_sections_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="👤 О себе", callback_data="edit_section_client")],
            [InlineKeyboardButton(text="🎯 Цели", callback_data="edit_section_goals")],
            [InlineKeyboardButton(text="🏋️ Опыт тренировок", callback_data="edit_section_training")],
            [InlineKeyboardButton(text="📍 Место и график", callback_data="edit_section_location")],
            [InlineKeyboardButton(text="⚕️ Здоровье", callback_data="edit_section_health")],
            [InlineKeyboardButton(text="💪 Предпочтения и образ жизни", callback_data="edit_section_lifestyle")],
            [InlineKeyboardButton(text="📝 Дополнительно", callback_data="edit_section_additional")],
        ]
    )


def edit_questions_kb(section: str) -> InlineKeyboardMarkup:
    questions = {
        "client": [("Имя", "q01_name"), ("Возраст", "q02_age"), ("Пол", "q03_sex"), ("Рост", "q04_height"), ("Вес", "q05_weight"), ("Талия", "q06_waist")],
        "goals": [("Основная цель", "q07_primary_goal"), ("Дополнительные цели", "q08_secondary_goals"), ("Желаемый результат", "q09_desired_result"), ("Срок", "q10_timeframe")],
        "training": [("Опыт", "q11_experience"), ("Частота тренировок", "q12_current_frequency"), ("Текущая тренировка", "q13_current_activity"), ("Упражнения", "q14_current_exercises"), ("Рабочие веса", "q15_working_weights")],
        "location": [("Место тренировок", "q16_location"), ("Название зала", "q17_gym_name"), ("Оборудование", "q18_equipment"), ("Фото оборудования", "q19_equipment_photos"), ("Оборудование дома", "q18b_home_equipment"), ("Тренировок в неделю", "q20_sessions_per_week"), ("Дни", "q21_preferred_days"), ("Длительность", "q22_session_duration"), ("Время", "q23_preferred_time")],
        "health": [("Наличие ограничений", "q24_has_limitations"), ("Описание ограничений", "q25_limitation_categories"), ("Нежелательные движения", "q27_movements_to_avoid"), ("Рекомендации врача", "q28_medical_clearance"), ("Текст рекомендаций врача", "q28_doctor_recommendations")],
        "lifestyle": [("Предпочитаемые упражнения", "q29_preferred_exercises"), ("Нежелательные упражнения", "q30_disliked_exercises"), ("Упражнения для освоения", "q31_exercise_goals"), ("Повседневная активность", "q32_daily_activity"), ("Кардио", "q33_cardio_preference"), ("Комментарий по кардио", "q34_cardio_notes")],
        "additional": [("Ограничения расписания", "q35_schedule_constraints"), ("Другая информация", "q36_free_text")],
    }
    rows = [[InlineKeyboardButton(text=label, callback_data=f"edit_question_{question}")] for label, question in questions[section]]
    rows.append([InlineKeyboardButton(text="↩️ К разделам", callback_data="review_edit")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Подтвердить", callback_data="final_confirm")],
            [InlineKeyboardButton(text="↩️ Вернуться к анкете", callback_data="return_to_questionnaire")],
        ]
    )
