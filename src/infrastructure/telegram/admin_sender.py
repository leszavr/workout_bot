"""Telegram-адаптер: отправка новой анкеты администратору.

Единственное место, где профиль сериализуется и отправляется в Telegram.
Содержимое профиля здесь не логируется.
"""
from __future__ import annotations

import json

from aiogram import Bot
from aiogram.types import BufferedInputFile

from src.application.questionnaire.labels import label
from src.domain.profile import FitnessProfile
from src.errors import NotificationError


def build_admin_summary(profile: FitnessProfile) -> str:
    client = profile.client
    goals = profile.goals
    background = profile.training_background
    location = profile.training_location
    health = profile.health_and_limitations
    return (
        "📩 Новая анкета клиента\n\n"
        f"ID: {profile.display_number or profile.profile_id or '—'}\n"
        f"Имя: {label(client.name)}\n"
        f"Возраст: {label(client.age_years)}\n"
        f"Пол: {label(client.sex)}\n"
        f"Рост: {label(client.height_cm)} см\n"
        f"Вес: {label(client.weight_kg)} кг\n"
        f"Талия: {label(client.waist_cm)} см\n"
        f"Основная цель: {label(goals.primary)}\n"
        f"Желаемый результат: {label(goals.desired_result)}\n"
        f"Срок: {label(goals.target_timeframe)}\n"
        f"Опыт: {label(background.experience_level)}\n"
        f"Частота: {background.current_frequency_per_week} раз/нед\n"
        f"Место: {label(location.primary_location)}\n"
        f"Зал: {label(location.gym_name)}\n"
        f"Есть ограничения: {'Да' if health.has_limitations else 'Нет'}\n"
        f"Медицинское подтверждение: {'Да' if health.medical_clearance_required else 'Нет'}\n"
    )


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
