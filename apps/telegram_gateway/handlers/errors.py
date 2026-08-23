"""Обработчик недоступного FSM-хранилища.

Без него сбой Redis выглядит для пользователя как «бот не отвечает»: aiogram
логирует исключение и продолжает polling, а ответ на вопрос анкеты просто
исчезает. Здесь пользователь получает безопасное сообщение, а детали
подключения остаются в логах.
"""
from __future__ import annotations

import logging

from aiogram import Bot, Router
from aiogram.filters import ExceptionTypeFilter
from aiogram.types import ErrorEvent
from aiogram.types.update import UpdateTypeLookupError

from src.errors import FSMStorageError

logger = logging.getLogger(__name__)
router = Router(name="telegram_gateway.errors")

STORAGE_UNAVAILABLE_TEXT = (
    "⚠️ Сервис временно недоступен, ответ не сохранён. "
    "Повторите попытку чуть позже — анкета не потеряна."
)


@router.errors(ExceptionTypeFilter(FSMStorageError))
async def handle_fsm_storage_error(event: ErrorEvent, bot: Bot) -> bool:
    """Возвращает True: обновление обработано, повторять его не нужно."""
    chat_id = _chat_id(event)
    logger.error(
        "event=fsm_storage_error update_id=%s error_class=%s",
        event.update.update_id,
        type(event.exception).__name__,
    )
    if chat_id is None:
        return True
    try:
        await bot.send_message(chat_id, STORAGE_UNAVAILABLE_TEXT)
    except Exception:  # noqa: BLE001 — Telegram может быть недоступен вместе с Redis
        logger.exception("event=fsm_storage_error_notice_failed")
    return True


def _chat_id(event: ErrorEvent) -> int | None:
    """Не у всех обновлений есть чат (например, poll), отвечать тогда некуда."""
    try:
        event_object = event.update.event
    except UpdateTypeLookupError:
        return None
    chat = getattr(event_object, "chat", None)
    return chat.id if chat is not None else None
