"""Telegram-адаптер: отправка новой анкеты администратору.

Единственное место, где профиль сериализуется и отправляется в Telegram.
Содержимое профиля здесь не логируется.
"""
from __future__ import annotations

import json

from aiogram import Bot
from aiogram.types import BufferedInputFile

from src.domain.profile import FitnessProfile
from src.errors import NotificationError
from src.infrastructure.telegram.admin_summary import build_admin_summary


class TelegramAdminSender:
    def __init__(self, bot: Bot, admin_chat_id: str) -> None:
        self._bot = bot
        self._admin_chat_id = admin_chat_id

    async def __call__(self, profile: FitnessProfile) -> None:
        if not self._admin_chat_id:
            return
        try:
            await self._bot.send_message(chat_id=self._admin_chat_id, text=build_admin_summary(profile))
            payload = json.dumps(
                profile.model_dump(mode="json"), ensure_ascii=False, indent=2
            ).encode("utf-8")
            number = profile.display_number or profile.profile_id or "profile"
            await self._bot.send_document(
                chat_id=self._admin_chat_id,
                document=BufferedInputFile(payload, filename=f"{number}.json"),
                caption=f"JSON-профиль заявки {number}",
            )
        except Exception as exc:  # noqa: BLE001 — нормализуем в NotificationError
            raise NotificationError(f"Не удалось отправить уведомление администратору: {exc}") from exc
