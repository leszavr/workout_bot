"""Enum-типы AI-инфраструктуры."""
from __future__ import annotations

from enum import StrEnum


class AIProtocol(StrEnum):
    """Протокол интеграции. Модель/бренд НЕ являются типом интеграции.

    Реализован только `openai_compatible`. Остальные значения оставлены,
    потому что могут встречаться в уже сохранённых строках базы: убрать их
    из enum — значит получить ошибку чтения такой записи. В интерфейсе они
    не предлагаются, а сервер не даёт включить задачу на протоколе без
    адаптера.
    """

    OPENAI_COMPATIBLE = "openai_compatible"
    ANTHROPIC = "anthropic"
    CUSTOM = "custom"


class AITaskType(StrEnum):
    """Типы AI-задач.

    Значения оставлены для совместимости с сохранёнными строками, но
    системой поддерживается только `workout_generation` (см.
    `IMPLEMENTED_TASK_TYPES`). Всё остальное не показывается в интерфейсе
    и не принимается на запись: настройка, которая ни на что не влияет,
    вредна.
    """

    WORKOUT_GENERATION = "workout_generation"
    PROGRAM_ADJUSTMENT = "program_adjustment"
    PROFILE_ANALYSIS = "profile_analysis"
    EXERCISE_EXPLANATION = "exercise_explanation"
    USER_CHAT = "user_chat"
    FEEDBACK_ANALYSIS = "feedback_analysis"


# Задачи, которые код действительно выполняет. Единственный источник истины
# для API и интерфейса: остальные типы наружу не выдаются.
IMPLEMENTED_TASK_TYPES = frozenset({AITaskType.WORKOUT_GENERATION})


class AIUsageStatus(StrEnum):
    """Статус AI-вызова для token accounting."""

    SUCCESS = "success"
    ERROR = "error"


class AIResponseFormat(StrEnum):
    """Желаемый формат ответа (capability-зависимый)."""

    TEXT = "text"
    JSON = "json"


class AIFallbackReason(StrEnum):
    """Машиночитаемая причина, по которой программу сгенерировал не AI.

    Разделены два класса причин:

    - *configuration/readiness* — AI не вызывался вообще, потому что
      конфигурация заведомо нерабочая. Дорогой запрос не выполняется;
    - *runtime* — AI был признан готовым, попытка была сделана, но
      завершилась ошибкой.

    Это разделение важно для эксплуатации: первое лечится настройкой,
    второе — разбором инцидента у провайдера.
    """

    # --- Configuration / readiness: AI не вызывался -------------------------
    AI_NOT_CONFIGURED = "ai_not_configured"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    ENDPOINT_UNAVAILABLE = "endpoint_unavailable"
    CONNECTION_NOT_TESTED = "connection_not_tested"
    MODEL_UNAVAILABLE = "model_unavailable"
    UNSUPPORTED_PROTOCOL = "unsupported_protocol"
    TASK_DISABLED = "task_disabled"
    TASK_NOT_READY = "task_not_ready"
    GENERATOR_NOT_CONFIGURED = "generator_not_configured"

    # --- Runtime: AI вызывался и не смог -----------------------------------
    AI_TIMEOUT = "ai_timeout"
    AI_RATE_LIMITED = "ai_rate_limited"
    AI_CONNECTION_FAILED = "ai_connection_failed"
    AI_RUNTIME_FAILURE = "ai_runtime_failure"
    AI_INVALID_RESPONSE = "ai_invalid_response"
    AI_VALIDATION_FAILED = "ai_validation_failed"


# Причины, при которых AI-запрос не выполнялся: fallback произошёл до вызова.
CONFIGURATION_FALLBACK_REASONS = frozenset(
    {
        AIFallbackReason.AI_NOT_CONFIGURED,
        AIFallbackReason.PROVIDER_UNAVAILABLE,
        AIFallbackReason.ENDPOINT_UNAVAILABLE,
        AIFallbackReason.CONNECTION_NOT_TESTED,
        AIFallbackReason.MODEL_UNAVAILABLE,
        AIFallbackReason.UNSUPPORTED_PROTOCOL,
        AIFallbackReason.TASK_DISABLED,
        AIFallbackReason.TASK_NOT_READY,
        AIFallbackReason.GENERATOR_NOT_CONFIGURED,
    }
)


class AIHealthState(StrEnum):
    """Состояние инфраструктуры provider/endpoint.

    Отделено от конфигурационного `enabled`: включённый провайдер может быть
    недоступен, а выключенный — не проверяется вообще.
    """

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    NOT_TESTED = "not_tested"
    DISABLED = "disabled"
    UNSUPPORTED = "unsupported"


class AIModelAvailability(StrEnum):
    """Доступность конкретной модели.

    Считается отдельно от health эндпоинта: провайдер может быть HEALTHY,
    а модель при этом DISABLED. Обратное тоже верно — при недоступном
    провайдере модель не может считаться AVAILABLE.
    """

    AVAILABLE = "available"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    NOT_TESTED = "not_tested"
    DISABLED = "disabled"
    UNSUPPORTED = "unsupported"

