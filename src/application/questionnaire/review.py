"""Рендер сводки анкеты (review). Возвращает HTML-строку, не зависит от Telegram."""
from __future__ import annotations

from src.application.questionnaire.labels import label
from src.domain.profile import FitnessProfile


def _value(item: object, suffix: str = "") -> str:
    if item in (None, "", [], {}):
        return "—"
    if isinstance(item, list):
        return ", ".join(str(part) for part in item) or "—"
    return f"{item}{suffix}"


def render_review_html(profile: FitnessProfile) -> str:
    client = profile.client
    goals = profile.goals
    background = profile.training_background
    plan = profile.training_plan_preferences
    location = profile.training_location
    health = profile.health_and_limitations
    preferences = profile.exercise_preferences
    lifestyle = profile.lifestyle
    additional = profile.additional_information

    weights = [
        f"{w.exercise}: {w.notes or f'{w.weight} {w.unit}'}"
        for w in background.known_working_weights
    ]

    return (
        "📋 <b>Ваша анкета</b>\n\n"
        "<b>👤 О вас</b>\n"
        f"Имя: {_value(client.name)}\nВозраст: {_value(client.age_years, ' лет')}\n"
        f"Пол: {label(client.sex)}\nРост: {_value(client.height_cm, ' см')}\n"
        f"Вес: {_value(client.weight_kg, ' кг')}\nТалия: {_value(client.waist_cm, ' см')}\n\n"
        "<b>🎯 Цели</b>\n"
        f"Основная: {label(goals.primary)}\nДополнительные: {_value(goals.secondary)}\n"
        f"Желаемый результат: {_value(goals.desired_result)}\nСрок: {label(goals.target_timeframe)}\n\n"
        "<b>🏋️ Опыт и тренировки</b>\n"
        f"Опыт: {label(background.experience_level)}\nЧастота сейчас: {_value(background.current_frequency_per_week, ' раз/нед.')}\n"
        f"Текущая активность: {_value(background.current_activity_description)}\nУпражнения: {_value(background.current_exercises)}\n"
        f"Рабочие веса: {_value(weights)}\n\n"
        "<b>📍 Место и график</b>\n"
        f"Место: {label(location.primary_location)}\nЗал: {_value(location.gym_name)}\n"
        f"Оборудование: {_value(location.available_equipment)}\nДомашнее оборудование: {_value(location.custom_equipment_description)}\n"
        f"Фото оборудования: {len(location.equipment_photos) or '—'}\nТренировок в неделю: {_value(plan.sessions_per_week)}\n"
        f"Удобные дни: {_value([label(d) for d in plan.preferred_days])}\nДлительность: {_value(plan.session_duration_minutes, ' мин')}\n"
        f"Время: {label(plan.preferred_training_time)}\n\n"
        "<b>⚕️ Здоровье</b>\n"
        f"Ограничения: {'Есть' if health.has_limitations else 'Нет'}\n"
        f"Описание: {_value(health.categories)}\nНежелательные движения: {_value(health.movements_to_avoid)}\n"
        f"Рекомендации врача: {_value(health.doctor_recommendations)}\n\n"
        "<b>💪 Предпочтения и образ жизни</b>\n"
        f"Нравятся упражнения: {_value(preferences.preferred_exercises)}\nНе нравятся: {_value(preferences.disliked_exercises)}\n"
        f"Хочу освоить: {_value(preferences.exercise_goals)}\nАктивность вне тренировок: {label(lifestyle.daily_activity_level)}\n"
        f"Кардио: {label(lifestyle.cardio_preference)}\nКомментарий по кардио: {_value(lifestyle.cardio_notes)}\n\n"
        "<b>📝 Дополнительно</b>\n"
        f"Ограничения по расписанию: {_value(additional.schedule_constraints)}\n"
        f"Другая информация: {_value(additional.free_text)}"
    )
