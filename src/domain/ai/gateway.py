"""DTO AI Gateway: AIRequest / AIResponse / ModelRequirements.

Gateway работает с собственными DTO, не привязанными к формату OpenAI.
Специфика протоколов изолирована в адаптерах.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from src.domain.ai.enums import AIResponseFormat, AITaskType


class AIMessage(BaseModel):
    """Сообщение диалога (role не привязан к конкретному провайдеру)."""

    model_config = ConfigDict(extra="forbid")

    role: str = Field(pattern=r"^(system|user|assistant)$")
    content: str


class ModelRequirements(BaseModel):
    """Capability-требования к модели (вместо проверки названий)."""

    model_config = ConfigDict(extra="forbid")

    min_max_output_tokens: int | None = Field(default=None, ge=1)
    min_context_window: int | None = Field(default=None, ge=1)
    requires_json_schema: bool = False
    requires_structured_output: bool = False
    requires_streaming: bool = False


class AIRequest(BaseModel):
    """Запрос к AI Gateway."""

    model_config = ConfigDict(extra="forbid")

    task_type: AITaskType
    messages: list[AIMessage] = Field(min_length=1)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1)
    response_format: AIResponseFormat = AIResponseFormat.TEXT
    requirements: ModelRequirements | None = None
    profile_id: str | None = Field(default=None, max_length=64)
    program_id: str | None = Field(default=None, max_length=64)


class AIResponse(BaseModel):
    """Ответ AI Gateway."""

    model_config = ConfigDict(extra="forbid")

    content: str
    model: str
    provider: str
    endpoint: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    latency_ms: int | None = None
    raw_metadata: dict = Field(default_factory=dict)
