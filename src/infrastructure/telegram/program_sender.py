"""Telegram-транспорт для отправки HTML-документа программы пользователю.

Единственное место, где программа сериализуется в файл и отправляется
в Telegram. Реализует контракт DeliverySender, поэтому delivery-сервис
не зависит от aiogram.
"""
from __future__ import annotations

from aiogram import Bot
from aiogram.types import BufferedInputFile

from src.application.programs.telegram_delivery import ProgramDocument


class TelegramProgramSender:
    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def __call__(self, chat_id: str, document: ProgramDocument) -> int:
        message = await self._bot.send_document(
            chat_id=chat_id,
            document=BufferedInputFile(document.bytes_content, filename=document.filename),
            caption=document.caption,
        )
        return message.message_id
