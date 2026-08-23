"""Unit-тесты AIReadinessService: чек-лист готовности и защита включения задачи.

Проверяются:
- happy path: готовая конфигурация → ready=True;
- каждый недостающий шаг делает конфигурацию не готовой и объясняет причину;
- состояние «подключение не проверялось» отличается от «проверка провалилась»;
- протокол без адаптера не считается рабочим;
- validate_enable запрещает включение заведомо нерабочей задачи.

Fake-репозитории, без PostgreSQL и без сетевых вызовов.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.application.ai.readiness import (
    STATUS_FAILED,
    STATUS_MISSING,
    STATUS_OK,
    STATUS_WARNING,
    AIReadinessService,
)
from src.application.ai.selection import ModelSelector
from src.domain.ai.config import (
    AIEndpoint,
    AIModel,
    AIProvider,
    AITaskConfig,
    AITaskModelBinding,
    PromptTemplate,
)
from src.domain.ai.enums import AIFallbackReason, AIProtocol, AITaskType
from src.domain.ai.errors import AIConfigurationError
from src.infrastructure.ai.adapters import (
    AdapterRequest,
    AdapterResult,
    AIProviderAdapter,
    EndpointConnection,
    ProviderAdapterRegistry,
)
from src.infrastructure.ai.secrets import SecretStore

TASK = AITaskType.WORKOUT_GENERATION

# Отличает «параметр не передан» от осознанно переданного None.
_DEFAULT = object()


class _FakeSecrets(SecretStore):
    """Хранилище, которое умеет отвечать «ключа здесь нет»."""

    def __init__(self, *, present: bool) -> None:
        self._present = present

    async def put(self, reference: str, secret: str) -> None:  # pragma: no cover
        raise NotImplementedError

    async def get(self, reference: str) -> str | None:
        return "sk-test" if self._present else None

    async def delete(self, reference: str) -> None:  # pragma: no cover
        raise NotImplementedError

    async def exists(self, reference: str) -> bool:
        return self._present


class FakeProviders:
    def __init__(self, items: list[AIProvider]) -> None:
        self.items = items

    async def list(self) -> list[AIProvider]:
        return self.items

    async def get(self, provider_id: int):
        return next((p for p in self.items if p.id == provider_id), None)


class FakeEndpoints:
    def __init__(self, items: list[AIEndpoint]) -> None:
        self.items = items

    async def get(self, endpoint_id: int):
        return next((e for e in self.items if e.id == endpoint_id), None)

    async def list_for_provider(self, provider_id: int):
        return [e for e in self.items if e.provider_id == provider_id]


class FakeModels:
    def __init__(self, items: list[AIModel]) -> None:
        self.items = items

    async def get(self, model_pk: int):
        return next((m for m in self.items if m.id == model_pk), None)

    async def list_for_endpoint(self, endpoint_id: int):
        return [m for m in self.items if m.endpoint_id == endpoint_id]


class FakeTasks:
    def __init__(
        self, config: AITaskConfig | None, bindings: list[AITaskModelBinding]
    ) -> None:
        self.config = config
        self.bindings = bindings

    async def get(self, task_type: AITaskType):
        return self.config

    async def list_bindings(self, task_config_id: int):
        return self.bindings


class FakePrompts:
    def __init__(self, items: list[PromptTemplate] | None = None) -> None:
        self.items = items or []

    async def get(self, task_type: AITaskType, version: int | None):
        return next(
            (t for t in self.items if t.task_type == task_type and t.version == version),
            None,
        )

    async def list_for_task(self, task_type: AITaskType):
        return [t for t in self.items if t.task_type == task_type]


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
        "last_test_at": datetime(2026, 8, 21, 10, 0, tzinfo=timezone.utc),
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
    config: AITaskConfig | None = _DEFAULT,
    bindings: list[AITaskModelBinding] | None = None,
    prompts: list[PromptTemplate] | None = None,
    primary_generator: str = "ai",
    fallback_generator: str = "deterministic",
    secret_store: SecretStore | None = _DEFAULT,
) -> AIReadinessService:
    providers_repo = FakeProviders(providers if providers is not None else [_provider()])
    endpoints_repo = FakeEndpoints(endpoints if endpoints is not None else [_endpoint()])
    models_repo = FakeModels(models if models is not None else [_model()])
    if config is _DEFAULT:
        config = AITaskConfig(id=500, task_type=TASK, enabled=True)
    if bindings is None:
        bindings = [
            AITaskModelBinding(
                id=1, task_config_id=500, model_id=100, priority=1, is_primary=True
            )
        ]
    tasks_repo = FakeTasks(config, bindings)
    prompts_repo = FakePrompts(prompts)
    selector = ModelSelector(
        task_repository=tasks_repo,
        model_repository=models_repo,
        endpoint_repository=endpoints_repo,
        provider_repository=providers_repo,
    )
    if secret_store is _DEFAULT:
        # По умолчанию ключ на месте: проверка «ссылка есть, а ключа нет»
        # включается отдельным тестом.
        secret_store = _FakeSecrets(present=True)
    return AIReadinessService(
        providers=providers_repo,
        endpoints=endpoints_repo,
        models=models_repo,
        tasks=tasks_repo,
        prompts=prompts_repo,
        selector=selector,
        adapter_registry=_registry(),
        secret_store=secret_store,
        primary_generator=primary_generator,
        fallback_generator=fallback_generator,
    )


def _check(report, key: str):
    return next(c for c in report.checks if c.key == key)


# --- report: happy path ------------------------------------------------------------


async def test_fully_configured_task_is_ready():
    report = await _service().report(TASK)

    assert report.ready is True
    assert report.task_type == "workout_generation"
    assert [c.status for c in report.checks if c.blocking] == [STATUS_OK] * len(
        [c for c in report.checks if c.blocking]
    )
    assert len(report.chain) == 1
    assert report.chain[0].is_primary is True
    assert report.chain[0].model_id == "qwen/qwen3-max"
    # Промпт берётся из файлов проекта (prompts/program_generator/v1).
    assert "файлов" in _check(report, "prompt").detail


async def test_report_exposes_generation_strategy():
    report = await _service().report(TASK)
    assert report.generation["primary_generator"] == "ai"
    assert report.generation["fallback_generator"] == "deterministic"
    assert report.generation["ai_in_strategy"] is True


# --- report: недостающие шаги -------------------------------------------------------


async def test_empty_configuration_is_not_ready():
    report = await _service(
        providers=[], endpoints=[], models=[], config=None, bindings=[]
    ).report(TASK)

    assert report.ready is False
    assert _check(report, "provider").status == STATUS_MISSING
    assert _check(report, "endpoint").status == STATUS_MISSING
    assert _check(report, "model").status == STATUS_MISSING
    assert _check(report, "task_models").status == STATUS_MISSING
    assert _check(report, "task_enabled").status == STATUS_MISSING
    assert report.chain == []


async def test_connection_never_tested_blocks_readiness():
    report = await _service(
        endpoints=[_endpoint(last_test_at=None, last_test_status=None)]
    ).report(TASK)

    check = _check(report, "connection")
    assert check.status == STATUS_MISSING
    assert "ещё не проверяли" in check.detail
    assert report.ready is False


async def test_failed_connection_test_is_distinct_from_never_tested():
    report = await _service(
        endpoints=[
            _endpoint(last_test_status="error", last_test_error_type="AIProviderError")
        ]
    ).report(TASK)

    check = _check(report, "connection")
    assert check.status == STATUS_FAILED
    assert "AIProviderError" in check.detail
    assert report.ready is False


async def test_missing_api_key_warns_but_does_not_block():
    report = await _service(endpoints=[_endpoint(secret_reference=None)]).report(TASK)

    check = _check(report, "api_key")
    assert check.status == STATUS_WARNING
    assert check.blocking is False
    assert report.ready is True


async def test_dangling_secret_reference_is_reported_as_failure():
    """Ссылка на ключ есть, а ключа в хранилище нет.

    Так выглядит подключение после потери секрета (очистка хранилища, смена
    ключа шифрования): запросы получают 401, поэтому чек-лист не должен
    показывать «ключ сохранён».
    """
    report = await _service(secret_store=_FakeSecrets(present=False)).report(TASK)

    check = _check(report, "api_key")
    assert check.status == STATUS_FAILED
    assert "в хранилище его нет" in check.detail
    assert check.action


async def test_api_key_check_survives_without_secret_store():
    """Без хранилища проверка не падает: остаётся прежнее поведение."""
    report = await _service(secret_store=None).report(TASK)

    assert _check(report, "api_key").status == STATUS_OK


async def test_protocol_without_adapter_is_not_usable():
    report = await _service(
        providers=[_provider(protocol=AIProtocol.ANTHROPIC)]
    ).report(TASK)

    provider_check = _check(report, "provider")
    assert provider_check.status == STATUS_FAILED
    # В тексте — название сервиса, а не внутренний код протокола.
    assert "Router" in provider_check.detail
    assert report.ready is False
    assert report.chain == []


async def test_disabled_task_is_not_ready_but_chain_visible():
    report = await _service(
        config=AITaskConfig(id=500, task_type=TASK, enabled=False)
    ).report(TASK)

    assert report.ready is False
    assert _check(report, "task_enabled").status == STATUS_MISSING
    assert len(report.chain) == 1


async def test_bound_but_disabled_model_reports_failed_task_models():
    report = await _service(models=[_model(enabled=False)]).report(TASK)

    check = _check(report, "task_models")
    assert check.status == STATUS_FAILED
    assert "ни одна не доступна" in check.detail
    assert report.chain == []


async def test_unknown_prompt_version_fails_prompt_check():
    report = await _service(
        config=AITaskConfig(id=500, task_type=TASK, enabled=True, prompt_version=99)
    ).report(TASK)

    check = _check(report, "prompt")
    assert check.status == STATUS_FAILED
    assert "№99" in check.detail
    assert report.ready is False


async def test_prompt_version_from_database_is_accepted():
    template = PromptTemplate(
        id=1,
        task_type=TASK,
        version=7,
        name="v7",
        system_prompt="s",
        user_template="u",
        enabled=True,
    )
    report = await _service(
        config=AITaskConfig(id=500, task_type=TASK, enabled=True, prompt_version=7),
        prompts=[template],
    ).report(TASK)

    check = _check(report, "prompt")
    assert check.status == STATUS_OK
    assert "базы данных" in check.detail


async def test_deterministic_only_strategy_blocks_readiness():
    report = await _service(
        primary_generator="deterministic", fallback_generator="deterministic"
    ).report(TASK)

    check = _check(report, "generation_strategy")
    assert check.status == STATUS_FAILED
    assert report.ready is False
    assert report.generation["ai_in_strategy"] is False


async def test_ai_as_fallback_only_warns_without_blocking():
    report = await _service(
        primary_generator="deterministic", fallback_generator="ai"
    ).report(TASK)

    check = _check(report, "generation_strategy")
    assert check.status == STATUS_WARNING
    assert check.blocking is False
    assert report.ready is True


async def test_strategy_check_absent_for_other_tasks():
    report = await _service(
        config=AITaskConfig(id=500, task_type=AITaskType.USER_CHAT, enabled=True),
        bindings=[
            AITaskModelBinding(
                id=1, task_config_id=500, model_id=100, priority=1, is_primary=True
            )
        ],
    ).report(AITaskType.USER_CHAT)

    assert all(c.key != "generation_strategy" for c in report.checks)


# --- validate_enable ----------------------------------------------------------------


async def test_validate_enable_accepts_usable_model():
    service = _service()
    await service.validate_enable(AITaskConfig(task_type=TASK, enabled=True), [100])


async def test_validate_enable_rejects_empty_model_list():
    service = _service()
    with pytest.raises(AIConfigurationError, match="не выбрана ни одна модель"):
        await service.validate_enable(AITaskConfig(task_type=TASK, enabled=True), [])


async def test_validate_enable_rejects_disabled_model():
    service = _service(models=[_model(enabled=False)])
    with pytest.raises(AIConfigurationError, match="выключена"):
        await service.validate_enable(AITaskConfig(task_type=TASK, enabled=True), [100])


async def test_validate_enable_rejects_disabled_endpoint():
    service = _service(endpoints=[_endpoint(enabled=False)])
    with pytest.raises(AIConfigurationError, match="подключение"):
        await service.validate_enable(AITaskConfig(task_type=TASK, enabled=True), [100])


async def test_validate_enable_rejects_unsupported_protocol():
    service = _service(providers=[_provider(protocol=AIProtocol.CUSTOM)])
    with pytest.raises(AIConfigurationError, match="не поддерживает"):
        await service.validate_enable(AITaskConfig(task_type=TASK, enabled=True), [100])


async def test_validate_enable_accepts_when_only_fallback_is_usable():
    """Gateway использует первого доступного кандидата — этого достаточно."""
    service = _service(
        models=[_model(id=100, enabled=False), _model(id=101, model_id="m-b")]
    )
    await service.validate_enable(
        AITaskConfig(task_type=TASK, enabled=True), [100, 101]
    )


async def test_validate_enable_rejects_unknown_prompt_version():
    service = _service()
    with pytest.raises(AIConfigurationError, match="№42"):
        await service.validate_enable(
            AITaskConfig(task_type=TASK, enabled=True, prompt_version=42), [100]
        )


async def test_validate_enable_uses_existing_bindings_when_models_not_sent():
    """model_ids=None означает «не менять привязки» — проверяем сохранённые."""
    service = _service(models=[_model(enabled=False)])
    with pytest.raises(AIConfigurationError, match="выключена"):
        await service.validate_enable(
            AITaskConfig(task_type=TASK, enabled=True), None
        )


# --- runtime_gate: влияние readiness на генерацию ------------------------------------


async def test_runtime_gate_allows_ready_configuration():
    decision = await _service().runtime_gate(TASK)

    assert decision.allowed is True
    assert decision.reason is None


async def test_runtime_gate_blocks_when_ai_not_configured():
    decision = await _service(providers=[], endpoints=[], models=[]).runtime_gate(TASK)

    assert decision.allowed is False
    assert decision.reason is AIFallbackReason.AI_NOT_CONFIGURED


async def test_runtime_gate_blocks_when_provider_disabled():
    decision = await _service(providers=[_provider(enabled=False)]).runtime_gate(TASK)

    assert decision.allowed is False
    assert decision.reason is AIFallbackReason.PROVIDER_UNAVAILABLE


async def test_runtime_gate_blocks_when_endpoint_disabled():
    decision = await _service(endpoints=[_endpoint(enabled=False)]).runtime_gate(TASK)

    assert decision.allowed is False
    assert decision.reason is AIFallbackReason.ENDPOINT_UNAVAILABLE


async def test_runtime_gate_blocks_when_connection_never_tested():
    """«Не проверялось» — отдельная причина, не путается с провалом теста."""
    decision = await _service(
        endpoints=[_endpoint(last_test_at=None, last_test_status=None)]
    ).runtime_gate(TASK)

    assert decision.allowed is False
    assert decision.reason is AIFallbackReason.CONNECTION_NOT_TESTED


async def test_runtime_gate_blocks_when_connection_test_failed():
    decision = await _service(
        endpoints=[
            _endpoint(last_test_status="error", last_test_error_type="AIConnectionError")
        ]
    ).runtime_gate(TASK)

    assert decision.allowed is False
    assert decision.reason is AIFallbackReason.ENDPOINT_UNAVAILABLE
    assert "AIConnectionError" in (decision.detail or "")


async def test_runtime_gate_blocks_when_model_disabled():
    decision = await _service(models=[_model(enabled=False)]).runtime_gate(TASK)

    assert decision.allowed is False
    assert decision.reason is AIFallbackReason.MODEL_UNAVAILABLE


async def test_runtime_gate_blocks_on_unsupported_protocol():
    decision = await _service(
        providers=[_provider(protocol=AIProtocol.ANTHROPIC)]
    ).runtime_gate(TASK)

    assert decision.allowed is False
    assert decision.reason is AIFallbackReason.UNSUPPORTED_PROTOCOL


async def test_runtime_gate_blocks_when_task_disabled():
    """enabled=false — это запрет использовать AI, а не признак поломки."""
    decision = await _service(
        config=AITaskConfig(id=500, task_type=TASK, enabled=False)
    ).runtime_gate(TASK)

    assert decision.allowed is False
    assert decision.reason is AIFallbackReason.TASK_DISABLED


async def test_runtime_gate_blocks_when_prompt_version_missing():
    decision = await _service(
        config=AITaskConfig(id=500, task_type=TASK, enabled=True, prompt_version=42)
    ).runtime_gate(TASK)

    assert decision.allowed is False
    assert decision.reason is AIFallbackReason.TASK_NOT_READY


async def test_runtime_gate_reason_matches_report_reason_code():
    """Админка и runtime обязаны показывать одну и ту же причину."""
    service = _service(providers=[_provider(enabled=False)])
    report = await service.report(TASK)
    decision = await service.runtime_gate(TASK)

    blocking = [c for c in report.checks if c.blocking and c.status != STATUS_OK]
    assert decision.reason is not None
    assert blocking[0].reason_code == decision.reason.value
