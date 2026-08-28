"""Контракт маршрутизации callback-кнопок Telegram gateway.

Почему это отдельный тест, а не проверка отдельного хендлера: aiogram отдаёт
обновление первому подошедшему хендлеру и дальше по цепочке роутеров не идёт.
Хендлер без фильтра забирает все callback'и своего роутера и всех следующих,
поэтому кнопка соседнего роутера перестаёт работать молча — без исключения,
без записи в лог и без ответа пользователю.

Именно так анкета перехватывала review/confirm/edit: `@router.callback_query()`
без фильтра стоял в `questionnaire.router`, который подключается раньше
`review.router`. Кнопки «Всё верно» и «Исправить» не работали, а анкету
нельзя было завершить.

Тест проверяет не реализацию хендлеров, а разрешение маршрута: каждая кнопка,
которую бот реально показывает, должна дойти до своего хендлера. Набор кнопок
берётся из самих клавиатур, поэтому новая кнопка без ожидаемого владельца
роняет тест, а не тихо ломает диалог.
"""
from __future__ import annotations

import datetime as dt

import pytest
from aiogram import Router
from aiogram.types import CallbackQuery, Chat, InlineKeyboardMarkup, Message, User

from apps.telegram_gateway.keyboards.inline import (
    confirm_kb,
    edit_questions_kb,
    edit_sections_kb,
    preferred_days_kb,
    question_kb,
    resume_qa_kb,
    review_kb,
    start_qa_kb,
)
from src.application.questionnaire.questions import QUESTIONS

QUESTIONNAIRE = "telegram_gateway.questionnaire"
REVIEW = "telegram_gateway.review"
START = "telegram_gateway.start"

Route = tuple[str, str]

# Кнопки с фиксированным callback_data.
EXACT_ROUTES: dict[str, Route] = {
    "start_qa": (START, "callback_start_qa"),
    "resume_qa": (START, "callback_resume_qa"),
    "restart_qa": (START, "callback_restart_qa"),
    "show_service_info": (START, "callback_service_info"),
    "skip_question": (QUESTIONNAIRE, "skip_question"),
    "days_done": (QUESTIONNAIRE, "confirm_days"),
    "review_confirm": (REVIEW, "review_confirm"),
    "review_edit": (REVIEW, "review_edit"),
    "final_confirm": (REVIEW, "final_confirm"),
    "return_to_questionnaire": (REVIEW, "return_to_questionnaire"),
}

# Кнопки, у которых callback_data содержит параметр.
PREFIX_ROUTES: tuple[tuple[str, Route], ...] = (
    ("day_", (QUESTIONNAIRE, "toggle_day")),
    ("edit_section_", (REVIEW, "select_edit_section")),
    ("edit_question_", (REVIEW, "select_question_to_edit")),
)

# Варианты ответа на вопросы: единая точка обработки в анкете.
ANSWER_OPTIONS: frozenset[str] = frozenset(
    option.callback_data for question in QUESTIONS for option in question.options
)


def expected_route(data: str) -> Route | None:
    if data in ANSWER_OPTIONS:
        return QUESTIONNAIRE, "handle_choice"
    if data in EXACT_ROUTES:
        return EXACT_ROUTES[data]
    for prefix, route in PREFIX_ROUTES:
        if data.startswith(prefix):
            return route
    return None


def _callback_data(markup: InlineKeyboardMarkup | None) -> list[str]:
    if markup is None:
        return []
    return [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data
    ]


def rendered_buttons() -> list[str]:
    """Все callback_data, которые бот реально показывает пользователю."""
    sections = {question.section for question in QUESTIONS}
    markups: list[InlineKeyboardMarkup | None] = [
        start_qa_kb(),
        resume_qa_kb(),
        review_kb(),
        confirm_kb(),
        edit_sections_kb(),
        preferred_days_kb(),
    ]
    markups.extend(edit_questions_kb(section) for section in sections)
    markups.extend(question_kb(question, skippable=True) for question in QUESTIONS)
    return sorted({data for markup in markups for data in _callback_data(markup)})


def _callback(data: str) -> CallbackQuery:
    return CallbackQuery(
        id="1",
        from_user=User(id=777, is_bot=False, first_name="Иван"),
        chat_instance="x",
        data=data,
        message=Message(
            message_id=1,
            date=dt.datetime(2026, 8, 27, tzinfo=dt.timezone.utc),
            chat=Chat(id=555, type="private"),
        ),
    )


async def resolve(routers: list[Router], data: str) -> Route | None:
    """Первый хендлер, которому aiogram отдаст callback_data."""
    event = _callback(data)
    for router in routers:
        for handler in router.callback_query.handlers:
            accepted, _ = await handler.check(event)
            if accepted:
                return router.name, handler.callback.__name__
    return None


class TestEveryRenderedButtonReachesItsHandler:
    @pytest.mark.parametrize("data", rendered_buttons())
    async def test_button_resolves_to_expected_handler(self, routers, data):
        expected = expected_route(data)
        assert expected is not None, (
            f"кнопка {data} не описана: добавьте её владельца в EXACT_ROUTES "
            "или PREFIX_ROUTES"
        )
        assert await resolve(routers, data) == expected


class TestNoHandlerSwallowsForeignCallbacks:
    async def test_unknown_callback_is_not_captured(self, routers):
        """Хендлер без фильтра забрал бы и это, обесточив следующие роутеры."""
        assert await resolve(routers, "callback_from_the_future") is None
