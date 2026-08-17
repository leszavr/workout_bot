"""Inline-клавиатуры Telegram gateway.

Клавиатуры вопросов строятся из единого описания QUESTIONS,
чтобы кнопки не могли разойтись с бизнес-логикой.
"""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from src.application.questionnaire.questions import (
    QUESTIONS,
    QUESTIONS_BY_ID,
    QuestionDefinition,
)

DAY_LABELS = {
    "mon": "Пн", "tue": "Вт", "wed": "Ср", "thu": "Чт",
    "fri": "Пт", "sat": "Сб", "sun": "Вс",
}


def skip_button() -> InlineKeyboardButton:
    return InlineKeyboardButton(text="⏭️ Пропустить", callback_data="skip_question")


def question_kb(question: QuestionDefinition, skippable: bool) -> InlineKeyboardMarkup | None:
    """Клавиатура для вопроса: варианты из QUESTIONS + кнопка пропуска для необязательных."""
    rows: list[list[InlineKeyboardButton]] = []
    for option in question.options:
        rows.append([InlineKeyboardButton(text=option.label, callback_data=option.callback_data)])
    if skippable:
        rows.append([skip_button()])
    if not rows:
        return None
    return InlineKeyboardMarkup(inline_keyboard=rows)


def preferred_days_kb(selected_days: list[str] | None = None) -> InlineKeyboardMarkup:
    selected = set(selected_days or [])
    rows = []
    for value, day_label in DAY_LABELS.items():
        text = f"✅ {day_label}" if value in selected else day_label
        rows.append([InlineKeyboardButton(text=text, callback_data=f"day_{value}")])
    rows.append([InlineKeyboardButton(text="Готово", callback_data="days_done")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


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


def review_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Всё верно", callback_data="review_confirm")],
            [InlineKeyboardButton(text="✏️ Исправить", callback_data="review_edit")],
        ]
    )


SECTION_LABELS = {
    "client": "👤 О себе",
    "goals": "🎯 Цели",
    "training_background": "🏋️ Опыт тренировок",
    "training_location": "📍 Место",
    "training_plan_preferences": "📅 График",
    "health_and_limitations": "⚕️ Здоровье",
    "exercise_preferences": "💪 Предпочтения",
    "lifestyle": "🏃 Образ жизни",
    "additional_information": "📝 Дополнительно",
}


def edit_sections_kb() -> InlineKeyboardMarkup:
    sections = []
    for question in QUESTIONS:
        if question.section not in sections:
            sections.append(question.section)
    rows = [
        [InlineKeyboardButton(text=SECTION_LABELS.get(s, s), callback_data=f"edit_section_{s}")]
        for s in sections
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def edit_questions_kb(section: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=q.text.replace(" *", ""), callback_data=f"edit_question_{q.id}")]
        for q in QUESTIONS
        if q.section == section
    ]
    rows.append([InlineKeyboardButton(text="↩️ К разделам", callback_data="review_edit")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Подтвердить", callback_data="final_confirm")],
            [InlineKeyboardButton(text="↩️ Вернуться к анкете", callback_data="return_to_questionnaire")],
        ]
    )
