"""AIInfrastructureHealthService: состояние AI-инфраструктуры для админки.

Сервис НЕ хранит собственный список провайдеров и моделей. Дерево строится
динамически из существующей конфигурации:

    AIProvider → AIEndpoint → AIModel → task bindings

Поэтому любой новый провайдер или модель появляются в дашборде сами, а
удалённые — исчезают, без изменений во frontend.

Три независимых измерения состояния (их нельзя смешивать):

1. *configuration state* — `enabled` в конфигурации;
2. *infrastructure health* — доступен ли provider/endpoint фактически
   (`AIHealthState`);
3. *model availability* — можно ли вызвать конкретную модель
   (`AIModelAvailability`).

Provider может быть HEALTHY, а его модель — DISABLED. И наоборот: если
provider UNAVAILABLE, ни одна его модель не показывается как AVAILABLE.

Источники факта о доступности (дешёвые, без генерации):

- сохранённый результат connection test (`ai_endpoints.last_test_*`);
- последний реальный AI-вызов из журнала usage.

Второй источник закрывает сценарий «провайдер работал, потом отвалился»:
если тест был успешным, а последний вызов упал, эндпоинт помечается
DEGRADED без новых запросов к провайдеру.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Awaitable, Callable

from src.domain.ai.enums import (
    AIHealthState,
    AIModelAvailability,
    AIProtocol,
    AIUsageStatus,
)
from src.infrastructure.ai.adapters import ProviderAdapterRegistry
from src.infrastructure.persistence.postgres.ai_repository import (
    AIEndpointRepository,
    AIModelRepository,
    AIProviderRepository,
    AITaskConfigRepository,
    AIUsageRepository,
)

logger = logging.getLogger(__name__)


@dataclass
class TaskUsage:
    """Привязка модели к задаче: кто и как её использует."""

    task_type: str
    task_enabled: bool
    is_primary: bool
    priority: int


@dataclass
class ModelHealth:
    id: int | None
    model_id: str
    display_name: str
    enabled: bool
    availability: str
    reason: str | None
    # Модель используется включённой задачей: за такими следим отдельно.
    in_active_use: bool
    tasks: list[TaskUsage] = field(default_factory=list)


@dataclass
class EndpointHealth:
    id: int | None
    name: str
    base_url: str
    enabled: bool
    has_api_key: bool
    health: str
    reason: str | None
    last_checked_at: str | None
    last_check_status: str | None
    last_check_error_type: str | None
    last_call_at: str | None
    last_call_status: str | None
    last_call_error_type: str | None
    models: list[ModelHealth] = field(default_factory=list)


@dataclass
class ProviderHealth:
    id: int | None
    name: str
    slug: str
    protocol: str
    protocol_supported: bool
    enabled: bool
    health: str
    reason: str | None
    endpoints: list[EndpointHealth] = field(default_factory=list)


@dataclass
class InfrastructureHealthReport:
    generated_at: str
    providers: list[ProviderHealth] = field(default_factory=list)
    protocols: list[dict] = field(default_factory=list)
    summary: dict = field(default_factory=dict)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


class AIInfrastructureHealthService:
    def __init__(
        self,
        *,
        providers: AIProviderRepository,
        endpoints: AIEndpointRepository,
        models: AIModelRepository,
        tasks: AITaskConfigRepository,
        usage: AIUsageRepository,
        adapter_registry: ProviderAdapterRegistry,
    ) -> None:
        self._providers = providers
        self._endpoints = endpoints
        self._models = models
        self._tasks = tasks
        self._usage = usage
        self._registry = adapter_registry

    async def report(self) -> InfrastructureHealthReport:
        """Строит дерево состояния. Запросов к провайдерам не делает."""
        supported = self._supported_protocols()
        last_calls = await self._usage.latest_by_endpoint()
        bindings_by_model = await self._task_usage_by_model()

        providers: list[ProviderHealth] = []
        for provider in await self._providers.list():
            protocol_supported = provider.protocol in supported
            endpoints: list[EndpointHealth] = []
            for endpoint in await self._endpoints.list_for_provider(provider.id or 0):
                endpoint_health, endpoint_reason = self._endpoint_health(
                    provider_enabled=provider.enabled,
                    protocol_supported=protocol_supported,
                    endpoint_enabled=endpoint.enabled,
                    last_test_status=endpoint.last_test_status,
                    last_test_error_type=endpoint.last_test_error_type,
                    last_call=last_calls.get(endpoint.id or 0),
                )
                models: list[ModelHealth] = []
                for model in await self._models.list_for_endpoint(endpoint.id or 0):
                    tasks = bindings_by_model.get(model.id or 0, [])
                    availability, reason = self._model_availability(
                        model_enabled=model.enabled,
                        endpoint_health=endpoint_health,
                        endpoint_reason=endpoint_reason,
                    )
                    models.append(
                        ModelHealth(
                            id=model.id,
                            model_id=model.model_id,
                            display_name=model.display_name,
                            enabled=model.enabled,
                            availability=availability.value,
                            reason=reason,
                            in_active_use=any(t.task_enabled for t in tasks),
                            tasks=tasks,
                        )
                    )
                last_call = last_calls.get(endpoint.id or 0)
                endpoints.append(
                    EndpointHealth(
                        id=endpoint.id,
                        name=endpoint.name,
                        base_url=endpoint.base_url,
                        enabled=endpoint.enabled,
                        # Наличие ключа — да/нет. Ни ключ, ни маска не нужны.
                        has_api_key=bool(endpoint.secret_reference),
                        health=endpoint_health.value,
                        reason=endpoint_reason,
                        last_checked_at=_iso(endpoint.last_test_at),
                        last_check_status=endpoint.last_test_status,
                        last_check_error_type=endpoint.last_test_error_type,
                        last_call_at=_iso(last_call["created_at"]) if last_call else None,
                        last_call_status=last_call["status"] if last_call else None,
                        last_call_error_type=(
                            last_call["error_type"] if last_call else None
                        ),
                        models=models,
                    )
                )

            provider_health, provider_reason = self._provider_health(
                enabled=provider.enabled,
                protocol_supported=protocol_supported,
                endpoints=endpoints,
            )
            providers.append(
                ProviderHealth(
                    id=provider.id,
                    name=provider.name,
                    slug=provider.slug,
                    protocol=provider.protocol.value,
                    protocol_supported=protocol_supported,
                    enabled=provider.enabled,
                    health=provider_health.value,
                    reason=provider_reason,
                    endpoints=endpoints,
                )
            )

        return InfrastructureHealthReport(
            generated_at=datetime.now(timezone.utc).isoformat(),
            providers=providers,
            protocols=[
                {"value": p.value, "supported": p in supported} for p in AIProtocol
            ],
            summary=self._summary(providers),
        )

    # --- Состояния ---------------------------------------------------------------

    @staticmethod
    def _endpoint_health(
        *,
        provider_enabled: bool,
        protocol_supported: bool,
        endpoint_enabled: bool,
        last_test_status: str | None,
        last_test_error_type: str | None,
        last_call: dict | None,
    ) -> tuple[AIHealthState, str | None]:
        """Health эндпоинта. Порядок проверок = порядок приоритета причин."""
        if not endpoint_enabled:
            return AIHealthState.DISABLED, "Эндпоинт отключён в конфигурации"
        if not provider_enabled:
            return AIHealthState.DISABLED, "Провайдер отключён в конфигурации"
        if not protocol_supported:
            return (
                AIHealthState.UNSUPPORTED,
                "Для протокола провайдера не зарегистрирован адаптер",
            )
        if last_test_status == AIUsageStatus.ERROR.value:
            return (
                AIHealthState.UNAVAILABLE,
                f"Проверка подключения завершилась ошибкой: "
                f"{last_test_error_type or 'неизвестная ошибка'}",
            )
        if last_test_status != AIUsageStatus.SUCCESS.value:
            return AIHealthState.NOT_TESTED, "Подключение ни разу не проверялось"
        if last_call and last_call.get("status") == AIUsageStatus.ERROR.value:
            return (
                AIHealthState.DEGRADED,
                f"Проверка подключения успешна, но последний AI-вызов упал: "
                f"{last_call.get('error_type') or 'неизвестная ошибка'}",
            )
        return AIHealthState.HEALTHY, None

    @staticmethod
    def _model_availability(
        *,
        model_enabled: bool,
        endpoint_health: AIHealthState,
        endpoint_reason: str | None,
    ) -> tuple[AIModelAvailability, str | None]:
        """Доступность модели.

        Отключённая модель — это конфигурационное состояние, оно важнее
        состояния эндпоинта: администратор сам её выключил.
        """
        if not model_enabled:
            return AIModelAvailability.DISABLED, "Модель отключена в конфигурации"
        mapping = {
            AIHealthState.DISABLED: AIModelAvailability.DISABLED,
            AIHealthState.UNSUPPORTED: AIModelAvailability.UNSUPPORTED,
            AIHealthState.UNAVAILABLE: AIModelAvailability.UNAVAILABLE,
            AIHealthState.NOT_TESTED: AIModelAvailability.NOT_TESTED,
            AIHealthState.DEGRADED: AIModelAvailability.DEGRADED,
            AIHealthState.HEALTHY: AIModelAvailability.AVAILABLE,
        }
        availability = mapping[endpoint_health]
        if availability is AIModelAvailability.AVAILABLE:
            return availability, None
        return availability, endpoint_reason

    @staticmethod
    def _provider_health(
        *,
        enabled: bool,
        protocol_supported: bool,
        endpoints: list[EndpointHealth],
    ) -> tuple[AIHealthState, str | None]:
        """Агрегат по эндпоинтам: хотя бы один рабочий делает провайдера рабочим."""
        if not enabled:
            return AIHealthState.DISABLED, "Провайдер отключён в конфигурации"
        if not protocol_supported:
            return (
                AIHealthState.UNSUPPORTED,
                "Для протокола провайдера не зарегистрирован адаптер",
            )
        if not endpoints:
            return AIHealthState.NOT_TESTED, "У провайдера нет эндпоинтов"

        states = {e.health for e in endpoints}
        if AIHealthState.HEALTHY.value in states:
            return AIHealthState.HEALTHY, None
        if AIHealthState.DEGRADED.value in states:
            return AIHealthState.DEGRADED, "Ни один эндпоинт не отвечает стабильно"
        if AIHealthState.UNAVAILABLE.value in states:
            return AIHealthState.UNAVAILABLE, "Все проверки подключения провалились"
        if AIHealthState.NOT_TESTED.value in states:
            return AIHealthState.NOT_TESTED, "Подключение ни разу не проверялось"
        return AIHealthState.DISABLED, "Все эндпоинты отключены"

    # --- Вспомогательное ----------------------------------------------------------

    def _supported_protocols(self) -> set[AIProtocol]:
        registered = set(self._registry.protocols())
        return {p for p in AIProtocol if p.value in registered}

    async def _task_usage_by_model(self) -> dict[int, list[TaskUsage]]:
        """Кто использует каждую модель. Задача может быть выключенной."""
        result: dict[int, list[TaskUsage]] = {}
        for config in await self._tasks.list():
            if config.id is None:
                continue
            for binding in await self._tasks.list_bindings(config.id):
                result.setdefault(binding.model_id, []).append(
                    TaskUsage(
                        task_type=config.task_type.value,
                        task_enabled=config.enabled,
                        is_primary=binding.is_primary,
                        priority=binding.priority,
                    )
                )
        return result

    @staticmethod
    def _summary(providers: list[ProviderHealth]) -> dict:
        endpoints = [e for p in providers for e in p.endpoints]
        models = [m for e in endpoints for m in e.models]
        return {
            "providers_total": len(providers),
            "providers_healthy": sum(
                1 for p in providers if p.health == AIHealthState.HEALTHY.value
            ),
            "endpoints_total": len(endpoints),
            "models_total": len(models),
            "models_available": sum(
                1 for m in models if m.availability == AIModelAvailability.AVAILABLE.value
            ),
            "models_in_active_use": sum(1 for m in models if m.in_active_use),
        }

    # --- Активное обновление -------------------------------------------------------

    async def refresh(
        self, tester: Callable[[int], Awaitable[dict]]
    ) -> InfrastructureHealthReport:
        """Проверяет включённые эндпоинты и возвращает свежее дерево.

        `tester` — существующий connection test (`AIGateway.test_endpoint`):
        минимальный ping, а не генерация программы. Дорогих AI-запросов
        health-проверка не делает.

        Проверяются только эндпоинты, которые могут работать: отключённые и
        протоколы без адаптера пропускаются, чтобы не создавать заведомо
        бесполезную нагрузку. Ошибка проверки одного эндпоинта не прерывает
        остальные — её результат сохраняется в его же `last_test_*`.
        """
        supported = self._supported_protocols()
        for provider in await self._providers.list():
            if not provider.enabled or provider.protocol not in supported:
                continue
            for endpoint in await self._endpoints.list_for_provider(provider.id or 0):
                if not endpoint.enabled or endpoint.id is None:
                    continue
                try:
                    await tester(endpoint.id)
                except Exception:  # noqa: BLE001 — один эндпоинт не блокирует остальные
                    logger.exception(
                        "Не удалось проверить эндпоинт при обновлении health"
                    )
        return await self.report()

