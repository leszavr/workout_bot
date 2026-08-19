"""Alert-отправитель в Telegram для ошибок pipeline (Stage 5)."""
from __future__ import annotations

from aiogram import Bot

from src.application.notifications.program_alerts import (
    ProgramAlert,
    format_alert,
)


class TelegramAlertSender:
    def __init__(self, bot: Bot, admin_chat_id: str) -> None:
        self._bot = bot
        self._admin_chat_id = admin_chat_id

    async def __call__(self, alert: ProgramAlert) -> None:
        await self._bot.send_message(chat_id=self._admin_chat_id, text=format_alert(alert))
