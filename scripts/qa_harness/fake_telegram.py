"""Поддельный Telegram: перехват вызовов Bot API без сети.

Нужен, чтобы прогонять анкету через настоящий `Dispatcher` с настоящими
хендлерами и настоящим FSM. Прямой вызов `QuestionnaireService` этого не даёт: он
обходит хендлеры, клавиатуры, `callback_data` и переходы состояний — то есть
именно тот слой, где живут ошибки транспорта.

Реализовано как `BaseSession`: aiogram вызывает `make_request` вместо HTTP, и
запросы записываются. Побочный эффект — из ответов бота видно, какие кнопки он
показал, поэтому имитатор пользователя может нажимать реальные `callback_data`, а
не угадывать их из кода.

Сеть не используется вовсе: токен фиктивный, к api.telegram.org обращений нет.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import TelegramMethod
from aiogram.types import (
    Chat,
    File,
    InlineKeyboardMarkup,
    Message,
    User,
)


@dataclass
class SentMessage:
    """Сообщение, которое бот отправил или изменил."""

    method: str
    chat_id: int
    text: str
    # Кнопки: (подпись, callback_data). Имитатор нажимает именно их.
    buttons: list[tuple[str, str]] = field(default_factory=list)
    # Имя файла для send_document: по нему видно, что программа доставлена.
    document_filename: str | None = None

    def button(self, callback_data: str) -> bool:
        return any(data == callback_data for _, data in self.buttons)


class FakeTelegramSession(BaseSession):
    """Сессия, которая отвечает вместо Telegram и записывает вызовы.

    Ответы минимальны, но валидны для aiogram: он разбирает их в типы методов,
    и «почти правильный» ответ падает на валидации, а не молча.
    """

    def __init__(self) -> None:
        super().__init__()
        self.sent: list[SentMessage] = []
        self._message_id = 0

    # --- Публичное для теста ------------------------------------------------------

    @property
    def last(self) -> SentMessage:
        if not self.sent:
            raise AssertionError("Бот не отправил ни одного сообщения")
        return self.sent[-1]

    def texts(self) -> list[str]:
        return [m.text for m in self.sent]

    def documents(self) -> list[str]:
        return [m.document_filename for m in self.sent if m.document_filename]

    def find_button(self, callback_data: str) -> bool:
        """Показывал ли бот такую кнопку в последних сообщениях."""
        return any(m.button(callback_data) for m in reversed(self.sent))

    def clear(self) -> None:
        self.sent.clear()

    # --- BaseSession --------------------------------------------------------------

    async def close(self) -> None:  # noqa: D102 — контракт BaseSession
        return None

    async def make_request(
        self, bot: Bot, method: TelegramMethod[Any], timeout: int | None = None
    ) -> Any:
        name = type(method).__name__
        handler = getattr(self, f"_on_{name}", None)
        if handler is not None:
            return handler(bot, method)
        # Неизвестный метод не подменяется «чем-нибудь»: тихий ответ скрыл бы
        # обращение, которого тест не ожидал.
        raise NotImplementedError(
            f"FakeTelegramSession не умеет отвечать на {name}. "
            "Добавьте обработчик, если этот вызов ожидаем."
        )

    async def stream_content(self, *args: Any, **kwargs: Any):  # noqa: D102, ANN201
        raise NotImplementedError("Скачивание файлов в имитаторе не поддерживается")

    # --- Ответы на конкретные методы ----------------------------------------------

    def _on_SendMessage(self, bot: Bot, method: Any) -> Message:  # noqa: N802
        self._record("send_message", method.chat_id, method.text, method.reply_markup)
        return self._message(bot, method.chat_id, method.text)

    def _on_EditMessageText(self, bot: Bot, method: Any) -> Message:  # noqa: N802
        self._record("edit_message_text", method.chat_id, method.text, method.reply_markup)
        return self._message(bot, method.chat_id, method.text)

    def _on_EditMessageReplyMarkup(self, bot: Bot, method: Any) -> Message:  # noqa: N802
        # Текст не меняется: перерисовывается только клавиатура (выбор дней).
        previous = self.sent[-1].text if self.sent else ""
        self._record("edit_reply_markup", method.chat_id, previous, method.reply_markup)
        return self._message(bot, method.chat_id, previous)

    def _on_DeleteMessage(self, bot: Bot, method: Any) -> bool:  # noqa: N802
        return True

    def _on_AnswerCallbackQuery(self, bot: Bot, method: Any) -> bool:  # noqa: N802
        # Всплывающий ответ на нажатие: пользователю он ничего не сообщает по сути,
        # но `show_alert=True` используется для ошибок валидации, поэтому пишем.
        if method.text:
            self._record("answer_callback", 0, method.text, None)
        return True

    def _on_SendDocument(self, bot: Bot, method: Any) -> Message:  # noqa: N802
        filename = getattr(method.document, "filename", None)
        self._record(
            "send_document",
            method.chat_id,
            method.caption or "",
            None,
            document_filename=filename,
        )
        return self._message(bot, method.chat_id, method.caption or "")

    def _on_GetFile(self, bot: Bot, method: Any) -> File:  # noqa: N802
        return File(file_id=method.file_id, file_unique_id="unique", file_path="photo.jpg")

    # --- Служебное ----------------------------------------------------------------

    def _record(
        self,
        method: str,
        chat_id: Any,
        text: str | None,
        reply_markup: Any,
        *,
        document_filename: str | None = None,
    ) -> None:
        buttons: list[tuple[str, str]] = []
        if isinstance(reply_markup, InlineKeyboardMarkup):
            buttons = [
                (button.text, button.callback_data)
                for row in reply_markup.inline_keyboard
                for button in row
                if button.callback_data
            ]
        self.sent.append(
            SentMessage(
                method=method,
                chat_id=int(chat_id) if str(chat_id).lstrip("-").isdigit() else 0,
                text=text or "",
                buttons=buttons,
                document_filename=document_filename,
            )
        )

    def _message(self, bot: Bot, chat_id: Any, text: str) -> Message:
        self._message_id += 1
        numeric_chat = int(chat_id) if str(chat_id).lstrip("-").isdigit() else 0
        return Message(
            message_id=self._message_id,
            date=dt.datetime.now(dt.timezone.utc),
            chat=Chat(id=numeric_chat, type="private"),
            from_user=User(id=bot.id, is_bot=True, first_name="bot"),
            text=text,
        ).as_(bot)
