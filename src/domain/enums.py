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


CONSENT_DOCUMENT_VERSION = "1.0"
