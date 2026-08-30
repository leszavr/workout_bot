"""Реестр компонентов и connector-абстракция application-слоя.

Две отдельные задачи в одном модуле, потому что они работают с одними данными:

1. `ComponentRegistryService` — регистрация, heartbeat, вердикты совместимости,
   deployment safety gate;
2. `ConnectorDirectory` — ответ на вопрос «есть ли живой компонент, способный
   выполнить нужную функцию», без знания о том, Telegram это или MAX.

Connector-контракт описан через capability, а не через тип компонента: код,
которому нужна доставка сообщения, спрашивает `Capability.TELEGRAM_DELIVERY`
или будущую `MAX_DELIVERY` и не содержит Telegram-специфичных допущений.
Отдельного DI-контейнера для этого не вводится.
"""
from __future__ import annotations

from datetime import datetime, timezone

from src.domain.components import (
    BACKEND_SUPPORTED_CONTRACTS,
    COMPONENT_REQUIREMENTS,
    OPERATIONAL_STATES,
    Capability,
    CompatibilityVerdict,
    ComponentInstance,
    ComponentMetadata,
    ComponentType,
    DeploymentSafetyReport,
)
from src.application.components.compatibility import (
    deployment_safety,
    evaluate,
    evaluate_all,
)
from src.infrastructure.persistence.postgres.component_repository import (
    ComponentRegistryRepository,
)


class ComponentRegistryService:
    def __init__(
        self,
        repository: ComponentRegistryRepository,
        *,
        backend_version: str,
        backend_contracts: tuple[int, ...] = BACKEND_SUPPORTED_CONTRACTS,
    ) -> None:
        self._repository = repository
        self._backend_version = backend_version
        self._backend_contracts = backend_contracts

    async def register(
        self, metadata: ComponentMetadata, *, now: datetime | None = None
    ) -> tuple[ComponentInstance, CompatibilityVerdict]:
        """Принимает регистрацию/heartbeat и сразу возвращает вердикт.

        Вердикт отдаётся самому компоненту намеренно: узнав `UPDATE_REQUIRED`,
        Gateway может записать это в свой лог, и несовместимость будет видна
        по обе стороны, а не только в админке.
        """
        now = now or datetime.now(timezone.utc)
        instance = await self._repository.upsert(metadata, seen_at=now)
        return instance, evaluate(instance, now=now)

    async def list_with_verdicts(
        self, *, now: datetime | None = None
    ) -> list[tuple[ComponentInstance, CompatibilityVerdict]]:
        now = now or datetime.now(timezone.utc)
        instances = await self._repository.list()
        return list(zip(instances, evaluate_all(instances, now=now)))

    async def forget(self, component_id: str) -> bool:
        return await self._repository.delete(component_id)

    async def deployment_safety(
        self, *, now: datetime | None = None
    ) -> DeploymentSafetyReport:
        """Machine-readable ответ для CI: SAFE или BLOCKED."""
        now = now or datetime.now(timezone.utc)
        return deployment_safety(
            await self._repository.list(),
            backend_version=self._backend_version,
            backend_contracts=self._backend_contracts,
            now=now,
        )

    def requirements(self) -> dict[ComponentType, dict]:
        """Что Backend требует от компонентов. Нужно админке и CI."""
        return {
            component_type: {
                "supported_contracts": list(requirement.supported_contracts),
                "required_contract": requirement.required_contract,
                "min_version": requirement.min_version,
                "recommended_version": requirement.recommended_version,
            }
            for component_type, requirement in COMPONENT_REQUIREMENTS.items()
        }


class ConnectorDirectory:
    """Живые компоненты, пригодные для выполнения функции (capability).

    Существует, чтобы application-код не обращался к Telegram Gateway по имени
    типа. Когда появится MAX Gateway, добавится capability, а не ветвление по
    `component_type` в вызывающем коде.
    """

    def __init__(self, repository: ComponentRegistryRepository) -> None:
        self._repository = repository

    async def available(
        self, capability: Capability, *, now: datetime | None = None
    ) -> list[ComponentInstance]:
        now = now or datetime.now(timezone.utc)
        instances = await self._repository.list()
        verdicts = evaluate_all(instances, now=now)
        return [
            instance
            for instance, verdict in zip(instances, verdicts)
            if capability in instance.metadata.capabilities
            and verdict.state in OPERATIONAL_STATES
        ]

    async def is_available(
        self, capability: Capability, *, now: datetime | None = None
    ) -> bool:
        return bool(await self.available(capability, now=now))
