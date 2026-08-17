"""Фабрика зависимостей AI-слоя (этап 3B).

Собирает репозитории, SecretStore, реестр адаптеров, ModelSelector,
AIGateway и AIConfigurationService в стиле существующих фабрик проекта.
Никаких singleton-глобалов, несовместимых с тестированием: все
компоненты можно собрать вручную с подменёнными зависимостями.
"""
from __future__ import annotations

from dataclasses import dataclass

import httpx

from src.application.ai.admin_service import AIConfigurationService
from src.application.ai.gateway import AIGateway
from src.application.ai.selection import ModelSelector
from src.infrastructure.ai.adapters import build_default_registry
from src.infrastructure.ai.secrets import EncryptedDbSecretStore
from src.infrastructure.persistence.postgres.ai_repository import (
    AIAuditRepository,
    AIEndpointRepository,
    AIModelRepository,
    AIProviderRepository,
    AITaskConfigRepository,
    AIUsageRepository,
    PromptTemplateRepository,
)
from src.infrastructure.persistence.postgres.db import get_session_factory
from src.infrastructure.persistence.postgres.models import AISecretRow


@dataclass
class AIComponents:
    """Собранный AI-слой: gateway + админ-сервис + репозитории."""

    gateway: AIGateway
    admin: AIConfigurationService
    providers: AIProviderRepository
    endpoints: AIEndpointRepository
    models: AIModelRepository
    tasks: AITaskConfigRepository
    prompts: PromptTemplateRepository
    usage: AIUsageRepository
    audit: AIAuditRepository


def build_ai_components(http_client: httpx.AsyncClient | None = None) -> AIComponents:
    session_factory = get_session_factory()

    providers = AIProviderRepository(session_factory)
    endpoints = AIEndpointRepository(session_factory)
    models = AIModelRepository(session_factory)
    tasks = AITaskConfigRepository(session_factory)
    prompts = PromptTemplateRepository(session_factory)
    usage = AIUsageRepository(session_factory)
    audit = AIAuditRepository(session_factory)
    secret_store = EncryptedDbSecretStore(session_factory, AISecretRow.__table__)

    selector = ModelSelector(
        task_repository=tasks,
        model_repository=models,
        endpoint_repository=endpoints,
        provider_repository=providers,
    )
    gateway = AIGateway(
        selector=selector,
        adapter_registry=build_default_registry(http_client),
        secret_store=secret_store,
        task_repository=tasks,
        usage_repository=usage,
        endpoint_repository=endpoints,
        provider_repository=providers,
        model_repository=models,
    )
    admin = AIConfigurationService(
        providers=providers,
        endpoints=endpoints,
        models=models,
        tasks=tasks,
        prompts=prompts,
        usage=usage,
        audit=audit,
        secret_store=secret_store,
    )
    return AIComponents(
        gateway=gateway,
        admin=admin,
        providers=providers,
        endpoints=endpoints,
        models=models,
        tasks=tasks,
        prompts=prompts,
        usage=usage,
        audit=audit,
    )
