"""Структурированные ошибки AI-слоя.

Все ошибки AI наследуют AIError; адаптеры и gateway не скрывают
разные типы сбоев под одним Exception.
"""
from __future__ import annotations

from src.errors import WorkoutBotError


class AIError(WorkoutBotError):
    """Базовая ошибка AI-слоя."""


class AIConfigurationError(AIError):
    """Некорректная конфигурация (нет провайдера/модели/секрета)."""


class AIConnectionError(AIError):
    """Не удалось установить соединение с эндпоинтом."""


class AITimeoutError(AIError):
    """Превышен таймаут запроса."""


class AIRateLimitError(AIError):
    """Провайдер вернул rate limit (429)."""


class AIProviderError(AIError):
    """Провайдер вернул ошибку (4xx/5xx, кроме rate limit)."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class AIInvalidResponseError(AIError):
    """Ответ провайдера не соответствует ожидаемой структуре."""


class AIUnsupportedProtocolError(AIError):
    """Для протокола не зарегистрирован адаптер."""
