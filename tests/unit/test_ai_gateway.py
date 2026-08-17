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
)
from src.domain.ai.gateway import AIMessage, AIRequest
from src.infrastructure.ai.adapters import (
    AdapterRequest,
    AdapterResult,
    AIProviderAdapter,
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


class ScriptedAdapter(AIProviderAdapter):
    """Адаптер со сценарием: список результатов/ошибок по попыткам."""

    def __init__(self, outcomes: list) -> None:
        self._outcomes = list(outcomes)
        self.calls: list[tuple[str, str | None]] = []

    async def generate(self, request: AdapterRequest, connection: EndpointConnection, model_id: str) -> AdapterResult:
        self.calls.append((model_id, connection.api_key))
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
