from __future__ import annotations

from aiogram import Bot
from aiogram.types import FSInputFile

from config import ADMIN_CHAT_ID


async def send_profile_to_admin(bot: Bot, profile: dict, profile_path: str) -> None:
    if not ADMIN_CHAT_ID:
        return

    if not profile:
        return

    profile_id = profile.get("profile_id") or "REQ-UNKNOWN"
    client = profile.get("client", {})
    goals = profile.get("goals", {})
    training = profile.get("training_background", {})
    location = profile.get("training_location", {})
    health = profile.get("health_and_limitations", {})

    text = (
        "📩 Новая анкета клиента\n\n"
        f"ID: {profile_id}\n"
        f"Имя: {client.get('name') or '—'}\n"
        f"Возраст: {client.get('age_years') or '—'}\n"
        f"Пол: {client.get('sex') or '—'}\n"
        f"Рост: {client.get('height_cm') or '—'} см\n"
        f"Вес: {client.get('weight_kg') or '—'} кг\n"
        f"Талия: {client.get('waist_cm') or '—'} см\n"
        f"Основная цель: {goals.get('primary') or '—'}\n"
        f"Желаемый результат: {goals.get('desired_result') or '—'}\n"
        f"Срок: {goals.get('target_timeframe') or '—'}\n"
        f"Опыт: {training.get('experience_level') or '—'}\n"
        f"Частота: {training.get('current_frequency_per_week') or '—'} раз/нед\n"
        f"Место: {location.get('primary_location') or '—'}\n"
        f"Зал: {location.get('gym_name') or '—'}\n"
        f"Есть ограничения: {'Да' if health.get('has_limitations') else 'Нет'}\n"
        f"Медицинское подтверждение: {'Да' if health.get('medical_clearance_required') else 'Нет'}\n"
    )

    await bot.send_message(chat_id=ADMIN_CHAT_ID, text=text)
    await bot.send_document(
        chat_id=ADMIN_CHAT_ID,
        document=FSInputFile(profile_path, filename=f"{profile_id}.json"),
        caption=f"JSON-профиль заявки {profile_id}",
    )
