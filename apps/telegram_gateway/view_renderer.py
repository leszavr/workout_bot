"""Отображение `TelegramView` в вызовы Bot API.

Единственное место Gateway, которое знает про aiogram. Всё остальное — транспорт
до Backend.

Здесь нет ни одного решения о содержании: текст, кнопки и получатель приходят
готовыми. Есть решения о том, как показать — правкой сообщения или новым, и что
делать, если Telegram отказался править.
"""
from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from src.domain.telegram_contract import (
    TelegramMessage,
    TelegramMessageKind,
    TelegramView,
)

logger = logging.getLogger(__name__)


def to_markup(message: TelegramMessage) -> InlineKeyboardMarkup | None:
    if not message.buttons:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=button.label, callback_data=button.action)
                for button in row
            ]
            for row in message.buttons
        ]
    )


async def render(
    view: TelegramView,
    *,
    bot: Bot,
    chat_id: str,
    source: Message | CallbackQuery | None = None,
) -> None:
    """Показывает вид пользователю.

    `source` — сообщение или нажатие, к которому относится ответ. Нужно правке и
    удалению: у Telegram нет операции «изменить последнее сообщение», ей нужен
    конкретный `message_id`.

    Toast отправляется первым: Telegram требует ответить на нажатие в течение
    нескольких секунд, иначе кнопка остаётся с индикатором ожидания. Ответ на
    нажатие не должен ждать отправки сообщений.
    """
    if isinstance(source, CallbackQuery):
        try:
            await source.answer(view.toast or "", show_alert=view.toast_alert)
        except TelegramBadRequest:
            # Нажатие устарело (прошло больше времени, чем Telegram хранит
            # callback). Показ сообщений это не отменяет.
            logger.debug("event=callback_answer_expired")

    current = _message_of(source)
    for message in view.messages:
        await _render_one(message, bot=bot, chat_id=chat_id, current=current)


async def _render_one(
    message: TelegramMessage,
    *,
    bot: Bot,
    chat_id: str,
    current: Message | None,
) -> None:
    target_chat = message.chat_id or chat_id
    parse_mode = "HTML" if message.html else None
    markup = to_markup(message)

    if message.document is not None:
        # Документ отправляется из памяти: файл в EU не сохраняется.
        await bot.send_document(
            chat_id=target_chat,
            document=BufferedInputFile(
                message.document.text_content.encode("utf-8"),
                filename=message.document.filename,
            ),
            caption=message.document.caption or None,
        )
        return

    if message.delete_current and current is not None:
        try:
            await current.delete()
        except TelegramBadRequest:
            # Сообщение уже удалено или слишком старое для удаления: показ
            # следующего экрана это не должно останавливать.
            logger.debug("event=message_delete_failed")

    if (
        message.kind is TelegramMessageKind.EDIT
        and current is not None
        and message.chat_id is None
        and not message.delete_current
    ):
        try:
            if message.text:
                await current.edit_text(
                    message.text, reply_markup=markup, parse_mode=parse_mode
                )
            else:
                # Пустой текст при правке означает «поменяй только клавиатуру»:
                # так переключаются дни недели без перерисовки вопроса.
                await current.edit_reply_markup(reply_markup=markup)
            return
        except TelegramBadRequest as exc:
            # Правка невозможна: сообщение не изменилось, устарело или было
            # удалено пользователем. Молча пропустить нельзя — человек ждёт
            # реакции, поэтому отправляем новым сообщением.
            logger.debug("event=message_edit_failed error=%s", exc.__class__.__name__)

    if not message.text:
        return

    await bot.send_message(
        chat_id=target_chat,
        text=message.text,
        reply_markup=markup,
        parse_mode=parse_mode,
    )


def _message_of(source: Message | CallbackQuery | None) -> Message | None:
    if isinstance(source, CallbackQuery):
        return source.message
    return source
