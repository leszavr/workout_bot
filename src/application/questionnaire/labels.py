"""Русские подписи значений enum для отображения (review, уведомление админа)."""
from __future__ import annotations

RU_LABELS: dict[str, str] = {
    "male": "Мужской", "female": "Женский", "not_specified": "Не указан",
    "weight_loss": "Снижение веса", "muscle_gain": "Набор мышечной массы",
    "strength": "Увеличение силы", "health_fitness": "Здоровье и общая форма",
    "endurance": "Повышение выносливости", "return_to_training": "Возвращение к тренировкам",
    "other": "Другое",
    "never": "Никогда не занимался", "long_break": "Был длинный перерыв",
    "under_3_months": "До 3 месяцев", "3_12_months": "3–12 месяцев", "over_1_year": "Больше года",
    "1_month": "1 месяц", "2_3_months": "2–3 месяца", "3_6_months": "3–6 месяцев",
    "6_12_months": "6–12 месяцев", "no_rush": "Не тороплюсь",
    "home": "Дома", "gym": "В зале", "both": "Дома и в зале",
    "morning": "Утро", "afternoon": "День", "evening": "Вечер", "any": "Любое время",
    "sedentary": "Сидячая работа, мало движения", "light_walking": "Немного хожу в течение дня",
    "active_walking": "Много хожу или активная работа", "physical_work": "Тяжёлая физическая работа",
    "very_active": "Очень высокая активность",
    "love": "Нравится", "okay": "Нормально отношусь", "dislike": "Не люблю",
    "exclude": "Не хочу", "walking_only": "Только ходьба",
    "mon": "Пн", "tue": "Вт", "wed": "Ср", "thu": "Чт", "fri": "Пт", "sat": "Сб", "sun": "Вс",
}


def label(value: object) -> str:
    if value in (None, "", [], {}):
        return "—"
    key = getattr(value, "value", value)
    return str(RU_LABELS.get(str(key), key))
