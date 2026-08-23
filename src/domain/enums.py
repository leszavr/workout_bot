"""Enum-типы предметной области.

Наборы значений зафиксированы здесь и используются в Pydantic-моделях,
описании вопросов и клавиатурах Telegram.
"""
from __future__ import annotations

from enum import StrEnum


class Sex(StrEnum):
    MALE = "male"
    FEMALE = "female"
    NOT_SPECIFIED = "not_specified"


class PrimaryGoal(StrEnum):
    WEIGHT_LOSS = "weight_loss"
    MUSCLE_GAIN = "muscle_gain"
    STRENGTH = "strength"
    HEALTH_FITNESS = "health_fitness"
    ENDURANCE = "endurance"
    RETURN_TO_TRAINING = "return_to_training"
    OTHER = "other"


class TargetTimeframe(StrEnum):
    ONE_MONTH = "1_month"
    TWO_THREE_MONTHS = "2_3_months"
    THREE_SIX_MONTHS = "3_6_months"
    SIX_TWELVE_MONTHS = "6_12_months"
    NO_RUSH = "no_rush"


class ExperienceLevel(StrEnum):
    NEVER = "never"
    LONG_BREAK = "long_break"
    UNDER_3_MONTHS = "under_3_months"
    THREE_TWELVE_MONTHS = "3_12_months"
    OVER_1_YEAR = "over_1_year"


class TrainingLocationType(StrEnum):
    HOME = "home"
    GYM = "gym"
    BOTH = "both"


class PreferredTrainingTime(StrEnum):
    MORNING = "morning"
    AFTERNOON = "afternoon"
    EVENING = "evening"
    ANY = "any"


class DailyActivityLevel(StrEnum):
    SEDENTARY = "sedentary"
    LIGHT_WALKING = "light_walking"
    ACTIVE_WALKING = "active_walking"
    PHYSICAL_WORK = "physical_work"
    VERY_ACTIVE = "very_active"


class CardioPreference(StrEnum):
    LOVE = "love"
    OKAY = "okay"
    DISLIKE = "dislike"
    EXCLUDE = "exclude"
    WALKING_ONLY = "walking_only"


class Weekday(StrEnum):
    MON = "mon"
    TUE = "tue"
    WED = "wed"
    THU = "thu"
    FRI = "fri"
    SAT = "sat"
    SUN = "sun"


class CompletionStatus(StrEnum):
    DRAFT = "draft"
    IN_PROGRESS = "in_progress"
    CONFIRMED = "confirmed"


class ConsentScope(StrEnum):
    DATA_PROCESSING = "data_processing"
    HEALTH_INFORMATION = "health_information"
    ACCURACY = "accuracy"


# --- Программы тренировок (этап 3A) -------------------------------------------


class ProgramStatus(StrEnum):
    """Жизненный цикл версии программы."""

    DRAFT = "draft"
    GENERATED = "generated"
    VALIDATED = "validated"
    ACTIVE = "active"
    ARCHIVED = "archived"
    FAILED = "failed"


class GenerationSource(StrEnum):
    """Источник генерации программы. AI появится на следующем этапе."""

    DETERMINISTIC = "deterministic"
    MANUAL = "manual"
    AI = "ai"


class ProgramDeliveryStatus(StrEnum):
    """Жизненный цикл доставки программы пользователю (Stage 5)."""

    PENDING = "pending"
    SENDING = "sending"
    SENT = "sent"
    FAILED = "failed"


class SafetyDecision(StrEnum):
    """Решение safety-правила по упражнению."""

    ALLOW = "allow"
    EXCLUDE = "exclude"
    WARNING = "warning"
    REQUIRES_REVIEW = "requires_review"


class MovementRestriction(StrEnum):
    """Нормализованные ограничения движений (не медицинские диагнозы).

    Профиль указывает ограничение в свободной форме; слой нормализации
    переводит его в один или несколько типов движений, которых следует избегать.
    """

    AVOID_HIGH_IMPACT = "avoid_high_impact"
    AVOID_HEAVY_SPINAL_LOADING = "avoid_heavy_spinal_loading"
    AVOID_OVERHEAD_LOADING = "avoid_overhead_loading"
    AVOID_DEEP_KNEE_FLEXION = "avoid_deep_knee_flexion"
    AVOID_HIGH_INTRA_ABDOMINAL_PRESSURE = "avoid_high_intra_abdominal_pressure"
    AVOID_HIGH_INTENSITY_CARDIO = "avoid_high_intensity_cardio"


# Ограничения попадают в текст программы, который читает человек,
# поэтому у каждого есть русская формулировка.
MOVEMENT_RESTRICTION_TITLES: dict[MovementRestriction, str] = {
    MovementRestriction.AVOID_HIGH_IMPACT: "без ударной нагрузки (прыжки, бег)",
    MovementRestriction.AVOID_HEAVY_SPINAL_LOADING: "без тяжёлой нагрузки на позвоночник",
    MovementRestriction.AVOID_OVERHEAD_LOADING: "без работы с весом над головой",
    MovementRestriction.AVOID_DEEP_KNEE_FLEXION: "без глубокого сгибания колен",
    MovementRestriction.AVOID_HIGH_INTRA_ABDOMINAL_PRESSURE: (
        "без сильного напряжения брюшного пресса"
    ),
    MovementRestriction.AVOID_HIGH_INTENSITY_CARDIO: "без высокоинтенсивного кардио",
}


def movement_restriction_title(restriction: MovementRestriction) -> str:
    return MOVEMENT_RESTRICTION_TITLES.get(restriction, restriction.value)


CONSENT_DOCUMENT_VERSION = "1.0"
