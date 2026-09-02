"""Сводка новой анкеты для администратора.

Вынесено из `admin_sender.py` вместе с переносом Telegram-транспорта в Gateway:
формирование текста — это application-уровень (какие поля показать
администратору), а не транспорт. После выноса Gateway за сетевую границу текст
собирает Backend в RU, а отправляет Gateway в EU, поэтому у функции больше нет
ничего общего с aiogram.

Содержимое профиля здесь не логируется.
"""
from __future__ import annotations

from src.application.questionnaire.labels import label
from src.domain.profile import FitnessProfile


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
