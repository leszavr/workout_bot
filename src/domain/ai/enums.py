"""Enum-типы AI-инфраструктуры."""
from __future__ import annotations

from enum import StrEnum


class AIProtocol(StrEnum):
    """Протокол интеграции. Модель/бренд НЕ являются типом интеграции."""

    OPENAI_COMPATIBLE = "openai_compatible"
    ANTHROPIC = "anthropic"  # задел на будущее, адаптера пока нет
    CUSTOM = "custom"  # задел на будущее, адаптера пока нет


class AITaskType(StrEnum):
    """Типы AI-задач. Сейчас реально используется только workout_generation."""

    WORKOUT_GENERATION = "workout_generation"
    PROGRAM_ADJUSTMENT = "program_adjustment"
    PROFILE_ANALYSIS = "profile_analysis"
    EXERCISE_EXPLANATION = "exercise_explanation"
    USER_CHAT = "user_chat"
    FEEDBACK_ANALYSIS = "feedback_analysis"


class AIUsageStatus(StrEnum):
    """Статус AI-вызова для token accounting."""

    SUCCESS = "success"
    ERROR = "error"


class AIResponseFormat(StrEnum):
    """Желаемый формат ответа (capability-зависимый)."""

    TEXT = "text"
    JSON = "json"
