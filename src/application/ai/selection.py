"""ModelSelector: выбор кандидатов для AI-задачи по приоритетам и capabilities.

Порядок: primary (priority=1) → fallback 1 → fallback 2 → ...
Отключённые провайдеры/эндпоинты/модели и модели без требуемых
capabilities исключаются. Никаких проверок по названиям моделей —
только конфигурационные capabilities.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.domain.ai.config import AIEndpoint, AIModel, AIProvider
from src.domain.ai.gateway import ModelRequirements
from src.infrastructure.persistence.postgres.ai_repository import (
    AIEndpointRepository,
    AIModelRepository,
    AIProviderRepository,
    AITaskConfigRepository,
)


@dataclass
class ModelCandidate:
    """Кандидат на выполнение AI-задачи (модель + её эндпоинт + провайдер)."""

    model: AIModel
    endpoint: AIEndpoint
    provider: AIProvider
    priority: int
    is_primary: bool


def model_satisfies(model: AIModel, requirements: ModelRequirements | None) -> bool:
    """Проверка capabilities модели без интерпретации её названия."""
    if requirements is None:
        return True
    if requirements.min_max_output_tokens is not None:
        if model.max_output_tokens is None or model.max_output_tokens < requirements.min_max_output_tokens:
            return False
    if requirements.min_context_window is not None:
        if model.context_window is None or model.context_window < requirements.min_context_window:
            return False
    if requirements.requires_json_schema and not model.supports_json_schema:
        return False
    if requirements.requires_structured_output and not model.supports_structured_output:
        return False
    if requirements.requires_streaming and not model.supports_streaming:
        return False
    return True


class ModelSelector:
    def __init__(
        self,
        *,
        task_repository: AITaskConfigRepository,
        model_repository: AIModelRepository,
        endpoint_repository: AIEndpointRepository,
        provider_repository: AIProviderRepository,
    ) -> None:
        self._tasks = task_repository
        self._models = model_repository
        self._endpoints = endpoint_repository
        self._providers = provider_repository

    async def select_candidates(
        self,
        task_type,
        requirements: ModelRequirements | None = None,
    ) -> list[ModelCandidate]:
        """Кандидаты задачи в порядке priority (1 = primary), только активные."""
        config = await self._tasks.get(task_type)
        if config is None or config.id is None:
            return []
        bindings = await self._tasks.list_bindings(config.id)

        candidates: list[ModelCandidate] = []
        for binding in bindings:
            model = await self._models.get(binding.model_id)
            if model is None or not model.enabled:
                continue
            endpoint = await self._endpoints.get(model.endpoint_id)
            if endpoint is None or not endpoint.enabled:
                continue
            provider = await self._providers.get(endpoint.provider_id)
            if provider is None or not provider.enabled:
                continue
            if not model_satisfies(model, requirements):
                continue
            candidates.append(
                ModelCandidate(
                    model=model,
                    endpoint=endpoint,
                    provider=provider,
                    priority=binding.priority,
                    is_primary=binding.is_primary,
                )
            )
        candidates.sort(key=lambda c: c.priority)
        return candidates
