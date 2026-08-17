"""Модели конфигурации AI: Provider / Endpoint / Model / Task / Prompt.

API key НЕ является полем AIEndpoint: эндпоинт хранит только ссылку на
секрет (secret_reference), сам секрет живёт в SecretStore. Это гарантирует,
что ключ не может случайно попасть в сериализацию, логи или API-ответы.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from src.domain.ai.enums import AIProtocol, AITaskType


class AIProvider(BaseModel):
    """Логический поставщик AI (например, любой OpenAI-compatible gateway)."""

    model_config = ConfigDict(extra="forbid")

    id: int | None = None
    name: str = Field(min_length=1, max_length=100)
    slug: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_-]+$")
    protocol: AIProtocol = AIProtocol.OPENAI_COMPATIBLE
    enabled: bool = True
    priority: int = Field(default=100, ge=0, le=1000)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class AIEndpoint(BaseModel):
    """Техническая точка подключения провайдера.

    Секрет хранится отдельно (SecretStore); здесь — только ссылка на него.
    """

    model_config = ConfigDict(extra="forbid")

    id: int | None = None
    provider_id: int
    name: str = Field(min_length=1, max_length=100)
    base_url: str = Field(min_length=8, max_length=500)
    secret_reference: str | None = Field(
        default=None, max_length=128, description="Ключ в SecretStore, не сам секрет"
    )
    timeout_seconds: int = Field(default=60, ge=1, le=600)
    max_retries: int = Field(default=2, ge=0, le=5)
    enabled: bool = True
    priority: int = Field(default=100, ge=0, le=1000)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class AIModel(BaseModel):
    """Конфигурация модели на эндпоинте.

    ``model_id`` — произвольная строка, ожидаемая эндпоинтом; backend её
    не интерпретирует. Поведение определяется capabilities, а не названием.
    """

    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    id: int | None = None
    endpoint_id: int
    model_id: str = Field(min_length=1, max_length=200)
    display_name: str = Field(min_length=1, max_length=200)
    enabled: bool = True
    priority: int = Field(default=100, ge=0, le=1000)
    context_window: int | None = Field(default=None, ge=1, le=10_000_000)
    max_output_tokens: int | None = Field(default=None, ge=1, le=1_000_000)
    supports_structured_output: bool = False
    supports_json_schema: bool = False
    supports_streaming: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None


class AITaskConfig(BaseModel):
    """Конфигурация AI-задачи (не глобальная «активная модель»)."""

    model_config = ConfigDict(extra="forbid")

    id: int | None = None
    task_type: AITaskType
    enabled: bool = False
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1, le=1_000_000)
    timeout_seconds: int = Field(default=120, ge=1, le=600)
    prompt_version: int | None = Field(default=None, ge=1)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class AITaskModelBinding(BaseModel):
    """Привязка модели к задаче: priority=1 → primary, 2+ → fallback."""

    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    id: int | None = None
    task_config_id: int
    model_id: int
    priority: int = Field(ge=1, le=100)
    is_primary: bool = False


class PromptTemplate(BaseModel):
    """Версионируемый шаблон промпта. Версия неизменяема после создания."""

    model_config = ConfigDict(extra="forbid")

    id: int | None = None
    task_type: AITaskType
    version: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=200)
    system_prompt: str = Field(min_length=1)
    user_template: str = Field(min_length=1)
    enabled: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None


class AIUsageRecord(BaseModel):
    """Учёт AI-вызова. НЕ хранит prompt, ответ, ключи и персональные данные."""

    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    id: int | None = None
    task_type: AITaskType
    provider_id: int | None = None
    endpoint_id: int | None = None
    model_id: int | None = None
    profile_id: str | None = Field(default=None, max_length=64)
    program_id: str | None = Field(default=None, max_length=64)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    latency_ms: int | None = Field(default=None, ge=0)
    status: str = "success"
    error_type: str | None = Field(default=None, max_length=100)
    created_at: datetime | None = None
