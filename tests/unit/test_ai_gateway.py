"""Unit-тесты маскирования секретов и AIGateway (мок-транспорт).

Gateway проверяется с fake-репозиториями, in-memory SecretStore и
подменённым адаптером: success, fallback при ошибке, запись usage.
"""
from __future__ import annotations

import pytest

from src.application.ai.gateway import AIGateway
from src.application.ai.selection import ModelSelector
from src.domain.ai.config import (
    AIEndpoint,
    AIModel,
    AIProvider,
    AITaskConfig,
    AITaskModelBinding,
)
from src.domain.ai.enums import AIProtocol, AITaskType
from src.domain.ai.errors import (
    AIConfigurationError,
    AIProviderError,
    AIUnsupportedProtocolError,
)
from src.domain.ai.gateway import AIMessage, AIRequest
from src.infrastructure.ai.adapters import (
    AdapterRequest,
    AdapterResult,
    AIProviderAdapter,
    DiscoveredModel,
    EndpointConnection,
    ProviderAdapterRegistry,
)
from src.infrastructure.ai.secrets import SecretStore, mask_secret

REAL_SECRET = "sk-super-secret-key-abcd1234"


# --- mask_secret ------------------------------------------------------------------


def test_mask_secret_hides_value():
    masked = mask_secret(REAL_SECRET)
    assert REAL_SECRET not in masked
    assert masked.endswith("1234")
    assert masked.startswith("****")


def test_mask_secret_short_value():
    assert mask_secret("abc") == "*****"
    assert mask_secret("") == ""


# --- In-memory SecretStore ----------------------------------------------------------


class InMemorySecretStore(SecretStore):
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def put(self, reference: str, secret: str) -> None:
        self.values[reference] = secret

    async def get(self, reference: str) -> str | None:
        return self.values.get(reference)

    async def delete(self, reference: str) -> None:
        self.values.pop(reference, None)

    async def exists(self, reference: str) -> bool:
        return reference in self.values


async def test_secret_rotation_replaces_old_value():
    store = InMemorySecretStore()
    await store.put("ref-1", "old-key")
    await store.put("ref-1", "new-key")
    assert await store.get("ref-1") == "new-key"
    assert "old-key" not in store.values.values()


# --- Gateway fakes -------------------------------------------------------------------


class FakeUsageRepo:
    def __init__(self) -> None:
        self.records: list = []

    async def save(self, record) -> None:
        self.records.append(record)


class FakeTasksRepo:
    def __init__(self, config: AITaskConfig | None, bindings: list[AITaskModelBinding]) -> None:
        self._config = config
        self._bindings = bindings

    async def get(self, task_type):
        return self._config

    async def list_bindings(self, task_config_id: int):
        return self._bindings


class FakeRepo:
    def __init__(self, items: dict[int, object]) -> None:
        self._items = items

    async def get(self, pk: int):
        return self._items.get(pk)

    async def list_for_endpoint(self, endpoint_id: int):
        return [m for m in self._items.values() if getattr(m, "endpoint_id", None) == endpoint_id]


class FakeEndpointsRepo(FakeRepo):
    """Репозиторий эндпоинтов, запоминающий результаты connection test."""

    def __init__(self, items: dict[int, object]) -> None:
        super().__init__(items)
        self.test_results: list[tuple[int, bool, str | None]] = []

    async def record_test_result(
        self, endpoint_id: int, *, success: bool, error_type: str | None = None
    ):
        self.test_results.append((endpoint_id, success, error_type))
        return self._items.get(endpoint_id)


class ScriptedAdapter(AIProviderAdapter):
    """Адаптер со сценарием: список результатов/ошибок по попыткам."""

    def __init__(self, outcomes: list) -> None:
        self._outcomes = list(outcomes)
        self.calls: list[tuple[str, str | None]] = []
        self.requests: list[AdapterRequest] = []

    async def generate(self, request: AdapterRequest, connection: EndpointConnection, model_id: str) -> AdapterResult:
        self.calls.append((model_id, connection.api_key))
        self.requests.append(request)
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _gateway(adapter, config=None, bindings=None, models=None, usage=None, secret_store=None):
    config = config or AITaskConfig(
        id=100, task_type=AITaskType.WORKOUT_GENERATION, enabled=True, temperature=0.3
    )
    if bindings is None:
        bindings = [
            AITaskModelBinding(id=1, task_config_id=100, model_id=1, priority=1, is_primary=True)
        ]
    models = models or {
        1: AIModel(id=1, endpoint_id=10, model_id="model-a", display_name="A")
    }
    usage = usage or FakeUsageRepo()
    secret_store = secret_store or InMemorySecretStore()
    registry = ProviderAdapterRegistry()
    registry.register(AIProtocol.OPENAI_COMPATIBLE, adapter)
    selector = ModelSelector(
        task_repository=FakeTasksRepo(config, bindings),
        model_repository=FakeRepo(models),
        endpoint_repository=FakeRepo(
            {10: AIEndpoint(id=10, provider_id=1, name="E", base_url="https://x.example/v1", secret_reference="ref-1")}
        ),
        provider_repository=FakeRepo(
            {1: AIProvider(id=1, name="P", slug="p1", protocol=AIProtocol.OPENAI_COMPATIBLE)}
        ),
    )
    return AIGateway(
        selector=selector,
        adapter_registry=registry,
        secret_store=secret_store,
        task_repository=FakeTasksRepo(config, bindings),
        usage_repository=usage,
        endpoint_repository=FakeRepo({}),
        provider_repository=FakeRepo({}),
        model_repository=FakeRepo(models),
    ), usage, secret_store


def _request() -> AIRequest:
    return AIRequest(
        task_type=AITaskType.WORKOUT_GENERATION,
        messages=[AIMessage(role="user", content="generate")],
    )


async def test_gateway_success_records_usage():
    adapter = ScriptedAdapter(
        [AdapterResult(content="program-json", model="model-a", input_tokens=10, output_tokens=20, total_tokens=30, latency_ms=100)]
    )
    store = InMemorySecretStore()
    await store.put("ref-1", REAL_SECRET)
    gateway, usage, _ = _gateway(adapter, secret_store=store)

    response = await gateway.generate(_request())

    assert response.content == "program-json"
    assert response.provider == "p1"
    assert response.total_tokens == 30
    # Адаптер получил реальный секрет, но он не в ответе.
    assert adapter.calls[0][1] == REAL_SECRET
    assert REAL_SECRET not in response.model_dump_json()
    assert len(usage.records) == 1
    assert usage.records[0].status == "success"
    assert usage.records[0].total_tokens == 30


async def test_gateway_fallback_to_second_candidate():
    adapter = ScriptedAdapter(
        [
            AIProviderError("primary failed", status_code=500),
            AdapterResult(content="from-fallback", model="model-b"),
        ]
    )
    config = AITaskConfig(id=100, task_type=AITaskType.WORKOUT_GENERATION, enabled=True)
    bindings = [
        AITaskModelBinding(id=1, task_config_id=100, model_id=1, priority=1, is_primary=True),
        AITaskModelBinding(id=2, task_config_id=100, model_id=2, priority=2, is_primary=False),
    ]
    models = {
        1: AIModel(id=1, endpoint_id=10, model_id="model-a", display_name="A"),
        2: AIModel(id=2, endpoint_id=10, model_id="model-b", display_name="B"),
    }
    gateway, usage, _ = _gateway(adapter, config=config, bindings=bindings, models=models)

    response = await gateway.generate(_request())

    assert response.content == "from-fallback"
    assert [c[0] for c in adapter.calls] == ["model-a", "model-b"]
    # usage: ошибка primary + успех fallback.
    assert [r.status for r in usage.records] == ["error", "success"]
    assert usage.records[0].error_type == "AIProviderError"


async def test_gateway_all_candidates_fail_raises_last_error():
    adapter = ScriptedAdapter(
        [
            AIProviderError("first failed", status_code=500),
            AIProviderError("second failed", status_code=503),
        ]
    )
    config = AITaskConfig(id=100, task_type=AITaskType.WORKOUT_GENERATION, enabled=True)
    bindings = [
        AITaskModelBinding(id=1, task_config_id=100, model_id=1, priority=1, is_primary=True),
        AITaskModelBinding(id=2, task_config_id=100, model_id=2, priority=2, is_primary=False),
    ]
    models = {
        1: AIModel(id=1, endpoint_id=10, model_id="model-a", display_name="A"),
        2: AIModel(id=2, endpoint_id=10, model_id="model-b", display_name="B"),
    }
    gateway, usage, _ = _gateway(adapter, config=config, bindings=bindings, models=models)

    request = _request()
    with pytest.raises(AIProviderError):
        await gateway.generate(request)
    assert len(usage.records) == 2
    assert all(r.status == "error" for r in usage.records)


async def test_gateway_disabled_task_raises_configuration_error():
    adapter = ScriptedAdapter([])
    config = AITaskConfig(id=100, task_type=AITaskType.WORKOUT_GENERATION, enabled=False)
    gateway, _, _ = _gateway(adapter, config=config)
    request = _request()
    with pytest.raises(AIConfigurationError):
        await gateway.generate(request)


async def test_gateway_no_candidates_raises_configuration_error():
    adapter = ScriptedAdapter([])
    config = AITaskConfig(id=100, task_type=AITaskType.WORKOUT_GENERATION, enabled=True)
    gateway, _, _ = _gateway(adapter, config=config, bindings=[])
    request = _request()
    with pytest.raises(AIConfigurationError):
        await gateway.generate(request)


# --- Двухшаговый контракт: prepare + generate_once ------------------------------------
#
# Транспортный успех не равен пригодному ответу: генератор программ проверяет
# вывод модели схемой, safe pool и каталогом, и обязан сменить модель после
# неудачных исправлений. Для этого перебор цепочки должен быть доступен
# вызывающей стороне, а не быть заперт внутри `generate`.


async def test_prepare_returns_ordered_chain():
    adapter = ScriptedAdapter([])
    config = AITaskConfig(id=100, task_type=AITaskType.WORKOUT_GENERATION, enabled=True)
    bindings = [
        AITaskModelBinding(id=2, task_config_id=100, model_id=2, priority=2, is_primary=False),
        AITaskModelBinding(id=1, task_config_id=100, model_id=1, priority=1, is_primary=True),
    ]
    models = {
        1: AIModel(id=1, endpoint_id=10, model_id="model-a", display_name="A"),
        2: AIModel(id=2, endpoint_id=10, model_id="model-b", display_name="B"),
    }
    gateway, _, _ = _gateway(adapter, config=config, bindings=bindings, models=models)

    chain = await gateway.prepare(_request())

    assert [c.model.model_id for c in chain.candidates] == ["model-a", "model-b"]
    assert chain.candidates[0].is_primary is True
    # Параметры вызова берутся из настроек задачи.
    assert chain.config.id == 100


async def test_prepare_rejects_disabled_task():
    adapter = ScriptedAdapter([])
    config = AITaskConfig(id=100, task_type=AITaskType.WORKOUT_GENERATION, enabled=False)
    gateway, _, _ = _gateway(adapter, config=config)
    with pytest.raises(AIConfigurationError):
        await gateway.prepare(_request())


async def test_generate_once_calls_only_the_given_candidate():
    """Перебор ведёт вызывающая сторона: сам вызов ходит ровно в одну модель."""
    adapter = ScriptedAdapter([AIProviderError("primary failed", status_code=500)])
    config = AITaskConfig(id=100, task_type=AITaskType.WORKOUT_GENERATION, enabled=True)
    bindings = [
        AITaskModelBinding(id=1, task_config_id=100, model_id=1, priority=1, is_primary=True),
        AITaskModelBinding(id=2, task_config_id=100, model_id=2, priority=2, is_primary=False),
    ]
    models = {
        1: AIModel(id=1, endpoint_id=10, model_id="model-a", display_name="A"),
        2: AIModel(id=2, endpoint_id=10, model_id="model-b", display_name="B"),
    }
    gateway, usage, _ = _gateway(adapter, config=config, bindings=bindings, models=models)

    request = _request()
    chain = await gateway.prepare(request)
    with pytest.raises(AIProviderError):
        await gateway.generate_once(chain.candidates[0], request, chain)

    # К резервной модели gateway сам не переходит.
    assert [c[0] for c in adapter.calls] == ["model-a"]
    # Учёт вызова ведётся так же, как в `generate`.
    assert [r.status for r in usage.records] == ["error"]


async def test_generate_once_uses_messages_of_the_passed_request():
    """Repair-запрос идёт той же модели с другими сообщениями."""
    adapter = ScriptedAdapter([AdapterResult(content="corrected", model="model-a")])
    gateway, _, _ = _gateway(adapter)

    request = _request()
    chain = await gateway.prepare(request)
    repair = AIRequest(
        task_type=AITaskType.WORKOUT_GENERATION,
        messages=[AIMessage(role="user", content="fix your previous answer")],
    )
    response = await gateway.generate_once(chain.candidates[0], repair, chain)

    assert response.content == "corrected"
    assert adapter.requests[-1].messages[0].content == "fix your previous answer"


# --- Connection test -----------------------------------------------------------------


def _test_gateway(
    adapter, endpoint: AIEndpoint, models: dict[int, AIModel] | None = None
) -> tuple[AIGateway, FakeEndpointsRepo]:
    """Gateway для проверки подключения (endpoint/provider доступны репозиториям)."""
    endpoints = FakeEndpointsRepo({10: endpoint})
    providers = FakeRepo(
        {1: AIProvider(id=1, name="P", slug="p1", protocol=AIProtocol.OPENAI_COMPATIBLE)}
    )
    models = FakeRepo(
        models
        if models is not None
        else {1: AIModel(id=1, endpoint_id=10, model_id="model-a", display_name="A")}
    )
    registry = ProviderAdapterRegistry()
    registry.register(AIProtocol.OPENAI_COMPATIBLE, adapter)
    config = AITaskConfig(id=100, task_type=AITaskType.WORKOUT_GENERATION, enabled=True)
    tasks = FakeTasksRepo(config, [])
    gateway = AIGateway(
        selector=ModelSelector(
            task_repository=tasks,
            model_repository=models,
            endpoint_repository=endpoints,
            provider_repository=providers,
        ),
        adapter_registry=registry,
        secret_store=InMemorySecretStore(),
        task_repository=tasks,
        usage_repository=FakeUsageRepo(),
        endpoint_repository=endpoints,
        provider_repository=providers,
        model_repository=models,
    )
    return gateway, endpoints


async def test_connection_test_success_is_persisted():
    endpoint = AIEndpoint(id=10, provider_id=1, name="E", base_url="https://x.example/v1")
    adapter = ScriptedAdapter([AdapterResult(content="pong", model="model-a")])
    gateway, endpoints = _test_gateway(adapter, endpoint)

    result = await gateway.test_endpoint(10)

    assert result["success"] is True
    assert result["model"] == "model-a"
    assert endpoints.test_results == [(10, True, None)]


async def test_connection_test_failure_persists_error_type():
    endpoint = AIEndpoint(id=10, provider_id=1, name="E", base_url="https://x.example/v1")
    adapter = ScriptedAdapter([AIProviderError("unauthorized", status_code=401)])
    gateway, endpoints = _test_gateway(adapter, endpoint)

    result = await gateway.test_endpoint(10)

    assert result["success"] is False
    assert result["error_type"] == "AIProviderError"
    assert endpoints.test_results == [(10, False, "AIProviderError")]


async def test_connection_test_unsupported_protocol_is_persisted_as_failure():
    """Протокол без адаптера — тоже неудачная проверка, а не исключение наружу."""
    endpoint = AIEndpoint(id=10, provider_id=1, name="E", base_url="https://x.example/v1")
    endpoints = FakeEndpointsRepo({10: endpoint})
    providers = FakeRepo(
        {1: AIProvider(id=1, name="P", slug="p1", protocol=AIProtocol.ANTHROPIC)}
    )
    models = FakeRepo({})
    config = AITaskConfig(id=100, task_type=AITaskType.WORKOUT_GENERATION, enabled=True)
    tasks = FakeTasksRepo(config, [])
    gateway = AIGateway(
        selector=ModelSelector(
            task_repository=tasks,
            model_repository=models,
            endpoint_repository=endpoints,
            provider_repository=providers,
        ),
        adapter_registry=ProviderAdapterRegistry(),
        secret_store=InMemorySecretStore(),
        task_repository=tasks,
        usage_repository=FakeUsageRepo(),
        endpoint_repository=endpoints,
        provider_repository=providers,
        model_repository=models,
    )

    result = await gateway.test_endpoint(10)

    assert result["success"] is False
    assert result["error_type"] == "AIUnsupportedProtocolError"
    assert endpoints.test_results == [(10, False, "AIUnsupportedProtocolError")]


async def test_connection_test_unknown_endpoint_raises():
    adapter = ScriptedAdapter([])
    gateway, _, _ = _gateway(adapter)
    with pytest.raises(AIConfigurationError):
        await gateway.test_endpoint(999)


async def test_connection_test_pings_enabled_model_not_the_first_one():
    """Проверять надо ту модель, которую система реально может вызвать.

    Отключённая модель может быть снята провайдером с обслуживания: её отказ
    пометил бы рабочее подключение как недоступное и закрыл readiness gate.
    """
    endpoint = AIEndpoint(id=10, provider_id=1, name="E", base_url="https://x.example/v1")
    adapter = ScriptedAdapter([AdapterResult(content="pong", model="live")])
    gateway, endpoints = _test_gateway(
        adapter,
        endpoint,
        models={
            1: AIModel(
                id=1, endpoint_id=10, model_id="retired", display_name="R", enabled=False
            ),
            2: AIModel(id=2, endpoint_id=10, model_id="live", display_name="L"),
        },
    )

    result = await gateway.test_endpoint(10)

    assert result["model"] == "live"
    assert adapter.calls == [("live", None)]
    assert result["success"] is True
    assert endpoints.test_results == [(10, True, None)]


async def test_connection_test_without_enabled_models_reports_configuration():
    """Все модели выключены — это настройка, а не потеря связи.

    Результат теста не перезаписывается: иначе рабочее подключение осталось бы
    помеченным как недоступное, а readiness gate — закрытым.
    """
    endpoint = AIEndpoint(id=10, provider_id=1, name="E", base_url="https://x.example/v1")
    adapter = ScriptedAdapter([])
    gateway, endpoints = _test_gateway(
        adapter,
        endpoint,
        models={
            1: AIModel(
                id=1, endpoint_id=10, model_id="retired", display_name="R", enabled=False
            )
        },
    )

    result = await gateway.test_endpoint(10)

    assert result["success"] is False
    assert result["error_type"] == "NoEnabledModel"
    assert result["model"] is None
    assert adapter.calls == []
    assert endpoints.test_results == []


async def test_connection_test_without_any_model_still_pings():
    """Эндпоинт без моделей: проверяем сам адрес нейтральным запросом."""
    endpoint = AIEndpoint(id=10, provider_id=1, name="E", base_url="https://x.example/v1")
    adapter = ScriptedAdapter([AdapterResult(content="pong", model="ping")])
    gateway, endpoints = _test_gateway(adapter, endpoint, models={})

    result = await gateway.test_endpoint(10)

    assert result["model"] == "ping"
    assert result["success"] is True
    assert endpoints.test_results == [(10, True, None)]


# --- Список доступных моделей ---------------------------------------------------------
#
# Идентификаторы моделей администратор больше не переписывает из документации:
# gateway спрашивает их у самого сервиса.


class ListingAdapter(ScriptedAdapter):
    """Адаптер, умеющий перечислять модели."""

    def __init__(self, models: list[DiscoveredModel] | Exception) -> None:
        super().__init__([])
        self._models = models
        self.connections: list[EndpointConnection] = []

    async def list_models(self, connection: EndpointConnection):
        self.connections.append(connection)
        if isinstance(self._models, Exception):
            raise self._models
        return self._models


async def test_discover_models_uses_endpoint_secret():
    endpoint = AIEndpoint(
        id=10,
        provider_id=1,
        name="E",
        base_url="https://x.example/v1",
        secret_reference="ref-1",
    )
    adapter = ListingAdapter([DiscoveredModel(model_id="m-1", display_name="m-1")])
    gateway, _ = _test_gateway(adapter, endpoint)
    # Секрет подставляет gateway: адаптер не знает, где он хранится.
    await gateway._secrets.put("ref-1", REAL_SECRET)  # noqa: SLF001

    models = await gateway.discover_models(10)

    assert [m.model_id for m in models] == ["m-1"]
    assert adapter.connections[0].api_key == REAL_SECRET
    # Случайный отказ края повторяется внутри адаптера: администратор не должен
    # видеть «список получить не удалось» там, где сервис доступен.
    assert adapter.connections[0].max_retries >= 1


async def test_discover_models_unknown_endpoint_raises():
    adapter = ListingAdapter([])
    gateway, _ = _test_gateway(
        adapter, AIEndpoint(id=10, provider_id=1, name="E", base_url="https://x.example/v1")
    )
    with pytest.raises(AIConfigurationError):
        await gateway.discover_models(999)


async def test_probe_models_works_without_saved_endpoint():
    """Первичная настройка: подключения ещё нет, а выбрать модель уже нужно."""
    adapter = ListingAdapter(
        [
            DiscoveredModel(model_id="vendor/m-1:free", display_name="m-1"),
            DiscoveredModel(model_id="m-2", display_name="m-2"),
        ]
    )
    gateway, _ = _test_gateway(
        adapter, AIEndpoint(id=10, provider_id=1, name="E", base_url="https://x.example/v1")
    )

    models = await gateway.probe_models(
        protocol=AIProtocol.OPENAI_COMPATIBLE,
        base_url="https://new.example/v1",
        api_key=REAL_SECRET,
    )

    assert [m.model_id for m in models] == ["vendor/m-1:free", "m-2"]
    assert adapter.connections[0].base_url == "https://new.example/v1"


async def test_probe_models_unsupported_protocol_raises():
    adapter = ListingAdapter([])
    gateway, _ = _test_gateway(
        adapter, AIEndpoint(id=10, provider_id=1, name="E", base_url="https://x.example/v1")
    )
    with pytest.raises(AIUnsupportedProtocolError):
        await gateway.probe_models(
            protocol=AIProtocol.ANTHROPIC,
            base_url="https://new.example/v1",
            api_key=None,
        )
