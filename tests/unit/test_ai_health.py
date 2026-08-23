"""Unit-тесты AIInfrastructureHealthService.

Проверяется главное требование этапа: дашборд не хранит собственный список
провайдеров и моделей, а строится из фактической конфигурации, и три
измерения состояния не смешиваются:

- configuration state (enabled/disabled);
- infrastructure health провайдера/эндпоинта;
- availability конкретной модели.

Fake-репозитории, без PostgreSQL и без сетевых вызовов.
"""
from __future__ import annotations

from datetime import datetime, timezone

from src.application.ai.health import AIInfrastructureHealthService
from src.domain.ai.config import (
    AIEndpoint,
    AIModel,
    AIProvider,
    AITaskConfig,
    AITaskModelBinding,
)
from src.domain.ai.enums import AIProtocol, AITaskType
from src.infrastructure.ai.adapters import (
    AdapterRequest,
    AdapterResult,
    AIProviderAdapter,
    EndpointConnection,
    ProviderAdapterRegistry,
)

TASK = AITaskType.WORKOUT_GENERATION
TESTED_AT = datetime(2026, 8, 21, 10, 0, tzinfo=timezone.utc)


class FakeProviders:
    def __init__(self, items: list[AIProvider]) -> None:
        self.items = items

    async def list(self) -> list[AIProvider]:
        return self.items


class FakeEndpoints:
    def __init__(self, items: list[AIEndpoint]) -> None:
        self.items = items

    async def list_for_provider(self, provider_id: int):
        return [e for e in self.items if e.provider_id == provider_id]


class FakeModels:
    def __init__(self, items: list[AIModel]) -> None:
        self.items = items

    async def list_for_endpoint(self, endpoint_id: int):
        return [m for m in self.items if m.endpoint_id == endpoint_id]


class FakeTasks:
    def __init__(
        self,
        configs: list[AITaskConfig] | None = None,
        bindings: list[AITaskModelBinding] | None = None,
    ) -> None:
        self.configs = configs or []
        self.bindings = bindings or []

    async def list(self):
        return self.configs

    async def list_bindings(self, task_config_id: int):
        return [b for b in self.bindings if b.task_config_id == task_config_id]


class FakeUsage:
    def __init__(self, latest: dict[int, dict] | None = None) -> None:
        self.latest = latest or {}

    async def latest_by_endpoint(self) -> dict[int, dict]:
        return self.latest


class NoopAdapter(AIProviderAdapter):
    async def generate(
        self, request: AdapterRequest, connection: EndpointConnection, model_id: str
    ) -> AdapterResult:
        return AdapterResult(content="{}", model=model_id)


def _registry() -> ProviderAdapterRegistry:
    registry = ProviderAdapterRegistry()
    registry.register(AIProtocol.OPENAI_COMPATIBLE, NoopAdapter())
    return registry


def _provider(**overrides) -> AIProvider:
    data = {
        "id": 1,
        "name": "Router",
        "slug": "router",
        "protocol": AIProtocol.OPENAI_COMPATIBLE,
        "enabled": True,
    }
    data.update(overrides)
    return AIProvider(**data)


def _endpoint(**overrides) -> AIEndpoint:
    data = {
        "id": 10,
        "provider_id": 1,
        "name": "Main",
        "base_url": "https://ai.example/v1",
        "secret_reference": "ref-1",
        "enabled": True,
        "last_test_at": TESTED_AT,
        "last_test_status": "success",
    }
    data.update(overrides)
    return AIEndpoint(**data)


def _model(**overrides) -> AIModel:
    data = {
        "id": 100,
        "endpoint_id": 10,
        "model_id": "qwen/qwen3-max",
        "display_name": "Qwen 3 Max",
        "enabled": True,
    }
    data.update(overrides)
    return AIModel(**data)


def _service(
    *,
    providers: list[AIProvider] | None = None,
    endpoints: list[AIEndpoint] | None = None,
    models: list[AIModel] | None = None,
    configs: list[AITaskConfig] | None = None,
    bindings: list[AITaskModelBinding] | None = None,
    latest_usage: dict[int, dict] | None = None,
) -> AIInfrastructureHealthService:
    return AIInfrastructureHealthService(
        providers=FakeProviders(providers if providers is not None else [_provider()]),
        endpoints=FakeEndpoints(endpoints if endpoints is not None else [_endpoint()]),
        models=FakeModels(models if models is not None else [_model()]),
        tasks=FakeTasks(configs, bindings),
        usage=FakeUsage(latest_usage),
        adapter_registry=_registry(),
    )


def _first_model(report):
    return report.providers[0].endpoints[0].models[0]


def _first_endpoint(report):
    return report.providers[0].endpoints[0]


# --- Динамическое построение дерева ---------------------------------------------


async def test_tree_is_built_from_actual_configuration():
    report = await _service().report()

    assert len(report.providers) == 1
    provider = report.providers[0]
    assert provider.slug == "router"
    assert len(provider.endpoints) == 1
    assert len(provider.endpoints[0].models) == 1
    assert _first_model(report).model_id == "qwen/qwen3-max"


async def test_new_provider_appears_without_frontend_changes():
    """Сценарий 1 и 6: добавленный провайдер появляется автоматически."""
    report = await _service(
        providers=[_provider(), _provider(id=2, slug="second", name="Second")],
        endpoints=[_endpoint(), _endpoint(id=20, provider_id=2, name="Second main")],
        models=[_model(), _model(id=200, endpoint_id=20, model_id="m-2")],
    ).report()

    assert [p.slug for p in report.providers] == ["router", "second"]
    assert report.providers[1].endpoints[0].models[0].model_id == "m-2"


async def test_new_models_appear_under_their_endpoint():
    """Сценарий 2: новые модели появляются под своим provider/endpoint."""
    report = await _service(
        models=[_model(), _model(id=101, model_id="m-b", display_name="B")]
    ).report()

    assert [m.model_id for m in report.providers[0].endpoints[0].models] == [
        "qwen/qwen3-max",
        "m-b",
    ]


async def test_deleted_configuration_disappears_from_report():
    """Сценарий 5: удалённый провайдер в отчёте не остаётся."""
    report = await _service(providers=[], endpoints=[], models=[]).report()

    assert report.providers == []
    assert report.summary["providers_total"] == 0


async def test_empty_configuration_is_not_an_error():
    report = await _service(providers=[], endpoints=[], models=[]).report()

    assert report.generated_at
    assert report.summary["models_available"] == 0


# --- Health провайдера и эндпоинта ------------------------------------------------


async def test_successful_connection_test_is_healthy():
    report = await _service().report()

    assert report.providers[0].health == "healthy"
    assert _first_endpoint(report).health == "healthy"
    assert _first_endpoint(report).last_checked_at == TESTED_AT.isoformat()


async def test_failed_connection_test_is_unavailable():
    """Сценарий 4: провайдер потерял связь."""
    report = await _service(
        endpoints=[
            _endpoint(last_test_status="error", last_test_error_type="AIConnectionError")
        ]
    ).report()

    assert report.providers[0].health == "unavailable"
    assert _first_endpoint(report).health == "unavailable"
    assert "AIConnectionError" in (_first_endpoint(report).reason or "")


async def test_never_tested_connection_is_not_tested():
    report = await _service(
        endpoints=[_endpoint(last_test_at=None, last_test_status=None)]
    ).report()

    assert _first_endpoint(report).health == "not_tested"
    assert report.providers[0].health == "not_tested"


async def test_disabled_provider_is_disabled_not_unavailable():
    """Configuration state нельзя подменять инфраструктурным health."""
    report = await _service(providers=[_provider(enabled=False)]).report()

    assert report.providers[0].health == "disabled"
    assert _first_endpoint(report).health == "disabled"


async def test_disabled_endpoint_is_disabled():
    report = await _service(endpoints=[_endpoint(enabled=False)]).report()

    assert _first_endpoint(report).health == "disabled"


async def test_protocol_without_adapter_is_unsupported():
    report = await _service(providers=[_provider(protocol=AIProtocol.ANTHROPIC)]).report()

    assert report.providers[0].protocol_supported is False
    assert report.providers[0].health == "unsupported"
    assert _first_endpoint(report).health == "unsupported"


async def test_failed_last_call_degrades_healthy_endpoint():
    """Тест подключения прошёл, но реальные вызовы падают → degraded."""
    report = await _service(
        latest_usage={
            10: {
                "status": "error",
                "error_type": "AIProviderError",
                "created_at": TESTED_AT,
            }
        }
    ).report()

    endpoint = _first_endpoint(report)
    assert endpoint.health == "degraded"
    assert endpoint.last_call_status == "error"
    assert "AIProviderError" in (endpoint.reason or "")


async def test_successful_last_call_keeps_endpoint_healthy():
    report = await _service(
        latest_usage={
            10: {"status": "success", "error_type": None, "created_at": TESTED_AT}
        }
    ).report()

    assert _first_endpoint(report).health == "healthy"


async def test_provider_is_healthy_if_any_endpoint_works():
    report = await _service(
        endpoints=[
            _endpoint(id=10, last_test_status="error", last_test_error_type="Boom"),
            _endpoint(id=11, name="Backup"),
        ],
        models=[_model(endpoint_id=11)],
    ).report()

    assert report.providers[0].health == "healthy"


async def test_provider_without_endpoints_is_not_tested():
    report = await _service(endpoints=[], models=[]).report()

    assert report.providers[0].health == "not_tested"
    assert "нет адресов подключения" in (report.providers[0].reason or "")


# --- Availability модели ----------------------------------------------------------


async def test_model_on_healthy_endpoint_is_available():
    report = await _service().report()

    assert _first_model(report).availability == "available"
    assert _first_model(report).reason is None


async def test_disabled_model_is_disabled_not_unavailable():
    """Сценарий 3: отключённая модель остаётся в конфигурации как DISABLED."""
    report = await _service(models=[_model(enabled=False)]).report()

    model = _first_model(report)
    assert model.enabled is False
    assert model.availability == "disabled"
    # Провайдер при этом полностью работоспособен.
    assert report.providers[0].health == "healthy"


async def test_model_is_not_available_when_provider_is_unavailable():
    """При недоступном провайдере модель не может выглядеть доступной."""
    report = await _service(
        endpoints=[_endpoint(last_test_status="error", last_test_error_type="Boom")]
    ).report()

    assert _first_model(report).availability == "unavailable"


async def test_model_is_not_tested_when_endpoint_never_tested():
    report = await _service(
        endpoints=[_endpoint(last_test_at=None, last_test_status=None)]
    ).report()

    assert _first_model(report).availability == "not_tested"


async def test_model_is_unsupported_when_protocol_has_no_adapter():
    report = await _service(providers=[_provider(protocol=AIProtocol.CUSTOM)]).report()

    assert _first_model(report).availability == "unsupported"


async def test_model_is_disabled_when_provider_disabled():
    report = await _service(providers=[_provider(enabled=False)]).report()

    assert _first_model(report).availability == "disabled"


# --- Использование моделей задачами -----------------------------------------------


async def test_task_usage_is_reported_for_model():
    report = await _service(
        configs=[AITaskConfig(id=500, task_type=TASK, enabled=True)],
        bindings=[
            AITaskModelBinding(
                id=1, task_config_id=500, model_id=100, priority=1, is_primary=True
            )
        ],
    ).report()

    model = _first_model(report)
    assert len(model.tasks) == 1
    assert model.tasks[0].task_type == "workout_generation"
    assert model.tasks[0].is_primary is True
    assert model.in_active_use is True


async def test_binding_to_disabled_task_is_not_active_use():
    """enabled=false у задачи означает «AI не разрешён», а не «модель занята»."""
    report = await _service(
        configs=[AITaskConfig(id=500, task_type=TASK, enabled=False)],
        bindings=[
            AITaskModelBinding(
                id=1, task_config_id=500, model_id=100, priority=1, is_primary=True
            )
        ],
    ).report()

    model = _first_model(report)
    assert model.tasks[0].task_enabled is False
    assert model.in_active_use is False


async def test_unused_model_has_no_tasks():
    report = await _service().report()

    assert _first_model(report).tasks == []
    assert _first_model(report).in_active_use is False


# --- Сводка и безопасность --------------------------------------------------------


async def test_summary_counts_reflect_configuration():
    report = await _service(
        models=[_model(), _model(id=101, model_id="m-b", enabled=False)],
        configs=[AITaskConfig(id=500, task_type=TASK, enabled=True)],
        bindings=[
            AITaskModelBinding(
                id=1, task_config_id=500, model_id=100, priority=1, is_primary=True
            )
        ],
    ).report()

    assert report.summary["providers_total"] == 1
    assert report.summary["providers_healthy"] == 1
    assert report.summary["endpoints_total"] == 1
    assert report.summary["models_total"] == 2
    assert report.summary["models_available"] == 1
    assert report.summary["models_in_active_use"] == 1


async def test_report_never_exposes_secrets():
    """В health-ответе допустим только признак наличия ключа."""
    report = await _service().report()

    endpoint = _first_endpoint(report)
    assert endpoint.has_api_key is True
    assert not hasattr(endpoint, "secret_reference")
    assert "ref-1" not in str(endpoint.__dict__)


async def test_protocols_expose_adapter_support():
    report = await _service().report()

    assert report.providers[0].protocol_supported is True


# --- Активное обновление -----------------------------------------------------------


async def test_refresh_tests_only_usable_endpoints():
    """Отключённые и неподдержанные эндпоинты не пингуются напрасно."""
    tested: list[int] = []

    async def tester(endpoint_id: int) -> dict:
        tested.append(endpoint_id)
        return {"success": True}

    service = _service(
        providers=[
            _provider(),
            _provider(id=2, slug="off", name="Off", enabled=False),
            _provider(id=3, slug="anthropic", name="A", protocol=AIProtocol.ANTHROPIC),
        ],
        endpoints=[
            _endpoint(id=10),
            _endpoint(id=11, name="Disabled", enabled=False),
            _endpoint(id=20, provider_id=2, name="Off endpoint"),
            _endpoint(id=30, provider_id=3, name="Anthropic endpoint"),
        ],
        models=[_model()],
    )

    await service.refresh(tester)

    assert tested == [10]


async def test_refresh_survives_failing_endpoint_check():
    async def tester(endpoint_id: int) -> dict:
        raise RuntimeError("network down")

    report = await _service().refresh(tester)

    assert len(report.providers) == 1
