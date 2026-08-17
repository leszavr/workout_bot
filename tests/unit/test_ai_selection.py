"""Unit-тесты ModelSelector: primary/fallback порядок, disabled, capabilities.

Используются in-memory fake-репозитории — без PostgreSQL.
"""
from __future__ import annotations

from src.application.ai.selection import ModelSelector, model_satisfies
from src.domain.ai.config import (
    AIEndpoint,
    AIModel,
    AIProvider,
    AITaskConfig,
    AITaskModelBinding,
)
from src.domain.ai.enums import AIProtocol, AITaskType
from src.domain.ai.gateway import ModelRequirements


class FakeProviders:
    def __init__(self, items: list[AIProvider]) -> None:
        self._items = {p.id: p for p in items}

    async def get(self, provider_id: int):
        return self._items.get(provider_id)


class FakeEndpoints:
    def __init__(self, items: list[AIEndpoint]) -> None:
        self._items = {e.id: e for e in items}

    async def get(self, endpoint_id: int):
        return self._items.get(endpoint_id)


class FakeModels:
    def __init__(self, items: list[AIModel]) -> None:
        self._items = {m.id: m for m in items}

    async def get(self, model_pk: int):
        return self._items.get(model_pk)


class FakeTasks:
    def __init__(self, config: AITaskConfig | None, bindings: list[AITaskModelBinding]) -> None:
        self._config = config
        self._bindings = bindings

    async def get(self, task_type: AITaskType):
        return self._config

    async def list_bindings(self, task_config_id: int):
        return self._bindings


def _provider(pk: int = 1, enabled: bool = True) -> AIProvider:
    return AIProvider(
        id=pk, name="P", slug=f"p{pk}", protocol=AIProtocol.OPENAI_COMPATIBLE, enabled=enabled
    )


def _endpoint(pk: int = 10, provider_id: int = 1, enabled: bool = True) -> AIEndpoint:
    return AIEndpoint(
        id=pk, provider_id=provider_id, name=f"E{pk}", base_url="https://x.example/v1", enabled=enabled
    )


def _model(
    pk: int,
    endpoint_id: int = 10,
    enabled: bool = True,
    max_output_tokens: int | None = 8000,
    json_schema: bool = False,
) -> AIModel:
    return AIModel(
        id=pk,
        endpoint_id=endpoint_id,
        model_id=f"model-{pk}",
        display_name=f"Model {pk}",
        enabled=enabled,
        max_output_tokens=max_output_tokens,
        supports_json_schema=json_schema,
    )


def _selector(models, bindings, config=None):
    config = config or AITaskConfig(id=100, task_type=AITaskType.WORKOUT_GENERATION, enabled=True)
    return ModelSelector(
        task_repository=FakeTasks(config, bindings),
        model_repository=FakeModels(models),
        endpoint_repository=FakeEndpoints([_endpoint()]),
        provider_repository=FakeProviders([_provider()]),
    )


async def test_primary_selected_first_then_fallbacks():
    models = [_model(1), _model(2), _model(3)]
    bindings = [
        AITaskModelBinding(id=1, task_config_id=100, model_id=2, priority=2, is_primary=False),
        AITaskModelBinding(id=2, task_config_id=100, model_id=1, priority=1, is_primary=True),
        AITaskModelBinding(id=3, task_config_id=100, model_id=3, priority=3, is_primary=False),
    ]
    selector = _selector(models, bindings)
    candidates = await selector.select_candidates(AITaskType.WORKOUT_GENERATION)
    assert [c.model.id for c in candidates] == [1, 2, 3]
    assert candidates[0].is_primary is True
    assert candidates[1].is_primary is False


async def test_disabled_model_skipped():
    models = [_model(1, enabled=False), _model(2)]
    bindings = [
        AITaskModelBinding(id=1, task_config_id=100, model_id=1, priority=1, is_primary=True),
        AITaskModelBinding(id=2, task_config_id=100, model_id=2, priority=2, is_primary=False),
    ]
    selector = _selector(models, bindings)
    candidates = await selector.select_candidates(AITaskType.WORKOUT_GENERATION)
    assert [c.model.id for c in candidates] == [2]


async def test_disabled_endpoint_skipped():
    endpoint = _endpoint(pk=10, enabled=False)
    config = AITaskConfig(id=100, task_type=AITaskType.WORKOUT_GENERATION, enabled=True)
    bindings = [
        AITaskModelBinding(id=1, task_config_id=100, model_id=1, priority=1, is_primary=True),
    ]
    selector = ModelSelector(
        task_repository=FakeTasks(config, bindings),
        model_repository=FakeModels([_model(1)]),
        endpoint_repository=FakeEndpoints([endpoint]),
        provider_repository=FakeProviders([_provider()]),
    )
    candidates = await selector.select_candidates(AITaskType.WORKOUT_GENERATION)
    assert candidates == []


async def test_disabled_provider_skipped():
    provider = _provider(enabled=False)
    config = AITaskConfig(id=100, task_type=AITaskType.WORKOUT_GENERATION, enabled=True)
    bindings = [
        AITaskModelBinding(id=1, task_config_id=100, model_id=1, priority=1, is_primary=True),
    ]
    selector = ModelSelector(
        task_repository=FakeTasks(config, bindings),
        model_repository=FakeModels([_model(1)]),
        endpoint_repository=FakeEndpoints([_endpoint()]),
        provider_repository=FakeProviders([provider]),
    )
    candidates = await selector.select_candidates(AITaskType.WORKOUT_GENERATION)
    assert candidates == []


async def test_insufficient_capabilities_filtered():
    models = [_model(1, max_output_tokens=1000), _model(2, max_output_tokens=8000)]
    bindings = [
        AITaskModelBinding(id=1, task_config_id=100, model_id=1, priority=1, is_primary=True),
        AITaskModelBinding(id=2, task_config_id=100, model_id=2, priority=2, is_primary=False),
    ]
    selector = _selector(models, bindings)
    requirements = ModelRequirements(min_max_output_tokens=4000)
    candidates = await selector.select_candidates(AITaskType.WORKOUT_GENERATION, requirements)
    assert [c.model.id for c in candidates] == [2]


async def test_json_schema_requirement_filters():
    models = [_model(1, json_schema=False), _model(2, json_schema=True)]
    bindings = [
        AITaskModelBinding(id=1, task_config_id=100, model_id=1, priority=1, is_primary=True),
        AITaskModelBinding(id=2, task_config_id=100, model_id=2, priority=2, is_primary=False),
    ]
    selector = _selector(models, bindings)
    requirements = ModelRequirements(requires_json_schema=True)
    candidates = await selector.select_candidates(AITaskType.WORKOUT_GENERATION, requirements)
    assert [c.model.id for c in candidates] == [2]


async def test_no_config_gives_no_candidates():
    selector = ModelSelector(
        task_repository=FakeTasks(None, []),
        model_repository=FakeModels([]),
        endpoint_repository=FakeEndpoints([]),
        provider_repository=FakeProviders([]),
    )
    candidates = await selector.select_candidates(AITaskType.WORKOUT_GENERATION)
    assert candidates == []


def test_model_satisfies_unknown_capability_defaults():
    """Неизвестный context_window (None) не проходит минимальное требование."""
    model = _model(1, max_output_tokens=None)
    assert model_satisfies(model, ModelRequirements(min_max_output_tokens=100)) is False
    assert model_satisfies(model, None) is True
