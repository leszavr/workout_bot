"""Сборка retry-контура worker'а.

Worker не собирает generation pipeline сам: оркестратор он берёт из
существующей единственной фабрики (`build_generation_orchestrator`). Это то же
требование, что закреплено архитектурным тестом для Telegram и Admin API, — у
генерации одна точка сборки.

Telegram Bot здесь нет и быть не может: после выноса Gateway за сетевую границу
доступа к `api.telegram.org` из RU-сегмента нет. Worker восстанавливает
застрявшие доставки, а отправку выполняет Gateway, забирая задания из очереди.
"""
from __future__ import annotations

import logging

from src.application.programs.retry_service import (
    DeliveryRecoveryService,
    GenerationRetryService,
    RetryCoordinator,
)
from src.infrastructure.config import (
    WORKER_BATCH_SIZE,
    WORKER_COMPONENT_ID,
    WORKER_LEASE_SECONDS,
)
from src.infrastructure.persistence.postgres.db import get_session_factory
from src.infrastructure.persistence.postgres.delivery_repository import (
    ProgramDeliveryRepository,
)
from src.infrastructure.persistence.postgres.generation_job_repository import (
    GenerationJobRepository,
)

logger = logging.getLogger(__name__)


def build_retry_coordinator() -> RetryCoordinator:
    from apps.backend.api.v1.dependencies import (
        build_generation_orchestrator,
        build_retry_policy,
    )

    session_factory = get_session_factory()
    policy = build_retry_policy()

    generation = GenerationRetryService(
        jobs=GenerationJobRepository(session_factory),
        orchestrator=build_generation_orchestrator(),
        policy=policy,
        owner=WORKER_COMPONENT_ID,
        lease_seconds=WORKER_LEASE_SECONDS,
        batch_size=WORKER_BATCH_SIZE,
    )
    delivery = DeliveryRecoveryService(
        deliveries=ProgramDeliveryRepository(session_factory),
        policy=policy,
        batch_size=WORKER_BATCH_SIZE,
    )
    return RetryCoordinator(generation=generation, delivery=delivery)
