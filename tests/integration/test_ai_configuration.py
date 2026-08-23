"""Интеграционные тесты AI-конфигурации против реальной PostgreSQL.

Полный сценарий этапа:
Create Provider → Create Endpoint → Set Secret → Create Model →
Configure workout_generation → Select Primary/Fallback → Mock AI Request →
Receive Response → Create Usage Record.

Требуют DATABASE_URL; иначе пропускаются.
"""
from __future__ import annotations

import pytest
from sqlalchemy import delete, select

from src.application.ai.admin_service import (
    AIConfigurationService,
    AIDependencyError,
)
from src.application.ai.gateway import AIGateway
from src.application.ai.health import AIInfrastructureHealthService
from src.application.ai.selection import ModelSelector
from src.domain.ai.config import (
    AIEndpoint,
    AIModel,
    AIProvider,
    AITaskConfig,
    AIUsageRecord,
    PromptTemplate,
)
from src.domain.ai.enums import AIProtocol, AITaskType
from src.domain.ai.gateway import AIMessage, AIRequest
from src.infrastructure.ai.adapters import (
    AdapterRequest,
    AdapterResult,
    AIProviderAdapter,
    EndpointConnection,
    ProviderAdapterRegistry,
)
from src.infrastructure.ai.secrets import EncryptedDbSecretStore
from src.infrastructure.config import DATABASE_URL
from src.infrastructure.persistence.postgres.ai_repository import (
    AIAuditRepository,
    AIEndpointRepository,
    AIModelRepository,
    AIProviderRepository,
    AITaskConfigRepository,
    AIUsageRepository,
    PromptTemplateRepository,
)
from src.infrastructure.persistence.postgres.models import (
    AIAuditEventRow,
    AIEndpointRow,
    AIModelRow,
    AIProviderRow,
    AISecretRow,
    AITaskConfigRow,
    AITaskModelBindingRow,
    AIUsageRecordRow,
    PromptTemplateRow,
)

pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL is not set")

REAL_SECRET = "sk-integration-test-secret-9999"


@pytest.fixture(autouse=True)
async def cleanup_ai_data():
    """Удаляет тестовые AI-данные (slug test-*) до и после тестов."""
    from src.infrastructure.persistence.postgres.db import get_session_factory

    async def _purge() -> None:
        async with get_session_factory()() as session:
            async with session.begin():
                provider_ids = (
                    await session.execute(
                        select(AIProviderRow.id).where(AIProviderRow.slug.like("test-%"))
                    )
                ).scalars().all()
                if provider_ids:
                    endpoint_ids = (
                        await session.execute(
                            select(AIEndpointRow.id).where(
                                AIEndpointRow.provider_id.in_(provider_ids)
                            )
                        )
                    ).scalars().all()
                    model_ids = (
                        await session.execute(
                            select(AIModelRow.id).where(
                                AIModelRow.endpoint_id.in_(endpoint_ids or [-1])
                            )
                        )
                    ).scalars().all()
                    if model_ids:
                        await session.execute(
                            delete(AITaskModelBindingRow).where(
                                AITaskModelBindingRow.model_id.in_(model_ids)
                            )
                        )
                        await session.execute(
                            delete(AIUsageRecordRow).where(
                                AIUsageRecordRow.model_id.in_(model_ids)
                            )
                        )
                await session.execute(
                    delete(AITaskModelBindingRow).where(
                        AITaskModelBindingRow.task_config_id.in_(
                            select(AITaskConfigRow.id).where(
                                AITaskConfigRow.task_type == "workout_generation"
                            )
                        )
                    )
                )
                await session.execute(
                    delete(AITaskConfigRow).where(
                        AITaskConfigRow.task_type == "workout_generation"
                    )
                )
                await session.execute(
                    delete(PromptTemplateRow).where(
                        PromptTemplateRow.task_type == "workout_generation"
                    )
                )
                await session.execute(
                    delete(AIProviderRow).where(AIProviderRow.slug.like("test-%"))
                )
                # FK ON DELETE SET NULL сохраняет записи usage после удаления
                # модели, поэтому их нужно чистить по собственной метке.
                await session.execute(
                    delete(AIUsageRecordRow).where(
                        AIUsageRecordRow.program_id.like("prog-test-%")
                    )
                )
                await session.execute(
                    delete(AISecretRow).where(AISecretRow.reference.like("test-ref%"))
                )
                await session.execute(
                    delete(AIAuditEventRow).where(
                        AIAuditEventRow.entity_type.in_(
                            [
                                "ai_provider",
                                "ai_endpoint",
                                "ai_model",
                                "ai_task_config",
                                "prompt_template",
                                "program_generation",
                            ]
                        )
                    )
                )

    await _purge()
    yield
    await _purge()
    from src.infrastructure.persistence.postgres.db import dispose_engine

    await dispose_engine()


@pytest.fixture
def components():
    from src.infrastructure.persistence.postgres.db import get_session_factory

    session_factory = get_session_factory()
    providers = AIProviderRepository(session_factory)
    endpoints = AIEndpointRepository(session_factory)
    models = AIModelRepository(session_factory)
    tasks = AITaskConfigRepository(session_factory)
    prompts = PromptTemplateRepository(session_factory)
    usage = AIUsageRepository(session_factory)
    audit = AIAuditRepository(session_factory)
    secret_store = EncryptedDbSecretStore(session_factory, AISecretRow.__table__)
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
    return {
        "providers": providers,
        "endpoints": endpoints,
        "models": models,
        "tasks": tasks,
        "prompts": prompts,
        "usage": usage,
        "audit": audit,
        "secret_store": secret_store,
        "admin": admin,
        "session_factory": session_factory,
    }


class MockAdapter(AIProviderAdapter):
    async def generate(self, request: AdapterRequest, connection: EndpointConnection, model_id: str) -> AdapterResult:
        return AdapterResult(
            content=f"generated-by-{model_id}",
            model=model_id,
            input_tokens=11,
            output_tokens=22,
            total_tokens=33,
            latency_ms=42,
        )


async def test_full_scenario_provider_to_usage_record(components):
    admin: AIConfigurationService = components["admin"]

    # 1. Create Provider
    provider = await admin.create_provider(
        AIProvider(name="Test Gateway", slug="test-gw", protocol=AIProtocol.OPENAI_COMPATIBLE),
        actor="test",
    )
    assert provider.id is not None

    # 2. Create Endpoint + 3. Set Secret
    endpoint = await admin.create_endpoint(
        AIEndpoint(
            provider_id=provider.id,
            name="Test EP",
            base_url="https://ai.test.example/v1",
        ),
        api_key=REAL_SECRET,
        actor="test",
    )
    assert endpoint.secret_reference is not None
    assert await components["secret_store"].get(endpoint.secret_reference) == REAL_SECRET

    # Секрет зашифрован at rest: в БД нет plaintext.
    async with components["session_factory"]() as session:
        encrypted = (
            await session.execute(
                select(AISecretRow.encrypted_value).where(
                    AISecretRow.reference == endpoint.secret_reference
                )
            )
        ).scalar_one()
    assert REAL_SECRET not in encrypted

    # 4. Create Models
    model_a = await admin.create_model(
        AIModel(endpoint_id=endpoint.id, model_id="test/model-a", display_name="Model A"),
        actor="test",
    )
    model_b = await admin.create_model(
        AIModel(endpoint_id=endpoint.id, model_id="test/model-b", display_name="Model B"),
        actor="test",
    )

    # 5. Configure workout_generation + 6. Primary/Fallback
    config, bindings = await admin.configure_task(
        AITaskConfig(task_type=AITaskType.WORKOUT_GENERATION, enabled=True),
        model_pks=[model_a.id, model_b.id],
        actor="test",
    )
    assert [b.priority for b in bindings] == [1, 2]
    assert bindings[0].is_primary is True

    # 7-8. Mock AI Request → Receive Response
    selector = ModelSelector(
        task_repository=components["tasks"],
        model_repository=components["models"],
        endpoint_repository=components["endpoints"],
        provider_repository=components["providers"],
    )
    registry = ProviderAdapterRegistry()
    registry.register(AIProtocol.OPENAI_COMPATIBLE, MockAdapter())
    gateway = AIGateway(
        selector=selector,
        adapter_registry=registry,
        secret_store=components["secret_store"],
        task_repository=components["tasks"],
        usage_repository=components["usage"],
        endpoint_repository=components["endpoints"],
        provider_repository=components["providers"],
        model_repository=components["models"],
    )
    response = await gateway.generate(
        AIRequest(
            task_type=AITaskType.WORKOUT_GENERATION,
            messages=[AIMessage(role="user", content="generate program")],
        )
    )
    assert response.content == "generated-by-test/model-a"
    assert response.provider == "test-gw"

    # 9. Usage record создан.
    usage_rows = await components["usage"].list_recent(limit=10)
    assert any(r["status"] == "success" and r["total_tokens"] == 33 for r in usage_rows)

    # Audit-события записаны и НЕ содержат секрет.
    audit_rows = await components["audit"].list_recent(limit=50)
    assert any(r["event_type"] == "ai_provider_created" for r in audit_rows)
    assert any(r["event_type"] == "ai_endpoint_created" for r in audit_rows)
    assert any(r["event_type"] == "ai_task_updated" for r in audit_rows)
    for row in audit_rows:
        assert REAL_SECRET not in str(row)


async def test_secret_rotation(components):
    admin: AIConfigurationService = components["admin"]
    provider = await admin.create_provider(
        AIProvider(name="Test Rotate", slug="test-rotate"), actor="test"
    )
    endpoint = await admin.create_endpoint(
        AIEndpoint(provider_id=provider.id, name="EP", base_url="https://x.example/v1"),
        api_key="old-key-value-1111",
        actor="test",
    )
    await admin.rotate_endpoint_secret(endpoint.id, "new-key-value-2222", actor="test")

    view = await admin.endpoint_secret_view(endpoint.id)
    assert view["has_api_key"] is True
    assert view["masked_api_key"].endswith("2222")
    assert "old-key-value-1111" not in str(view)
    assert await components["secret_store"].get(endpoint.secret_reference) == "new-key-value-2222"


async def test_prompt_versioning_immutable(components):
    admin: AIConfigurationService = components["admin"]
    v1 = await admin.create_prompt_version(
        PromptTemplate(
            task_type=AITaskType.WORKOUT_GENERATION,
            version=1,
            name="Base",
            system_prompt="You are a coach.",
            user_template="Generate for {profile}",
        ),
        actor="test",
    )
    # Повторное создание той же версии запрещено.
    from src.errors import WorkoutBotError

    duplicate = PromptTemplate(
        task_type=AITaskType.WORKOUT_GENERATION,
        version=1,
        name="Changed",
        system_prompt="Hacked!",
        user_template="x",
    )
    with pytest.raises(WorkoutBotError):
        await admin.create_prompt_version(duplicate, actor="test")
    # v1 остаётся неизменной.
    loaded = await components["prompts"].get(AITaskType.WORKOUT_GENERATION, 1)
    assert loaded is not None
    assert loaded.system_prompt == "You are a coach."
    assert loaded.id == v1.id

    # Новая версия v2 создаётся отдельно.
    assert await admin.next_prompt_version(AITaskType.WORKOUT_GENERATION) == 2
    await admin.create_prompt_version(
        PromptTemplate(
            task_type=AITaskType.WORKOUT_GENERATION,
            version=2,
            name="Improved",
            system_prompt="You are a better coach.",
            user_template="Generate v2 for {profile}",
        ),
        actor="test",
    )
    versions = await components["prompts"].list_for_task(AITaskType.WORKOUT_GENERATION)
    assert [v.version for v in versions] == [2, 1]


async def test_model_bound_to_task_cannot_be_deleted(components):
    admin: AIConfigurationService = components["admin"]
    provider = await admin.create_provider(
        AIProvider(name="Test Bound", slug="test-bound"), actor="test"
    )
    endpoint = await admin.create_endpoint(
        AIEndpoint(provider_id=provider.id, name="EP", base_url="https://x.example/v1"),
        actor="test",
    )
    model = await admin.create_model(
        AIModel(endpoint_id=endpoint.id, model_id="bound/model", display_name="Bound"),
        actor="test",
    )
    await admin.configure_task(
        AITaskConfig(task_type=AITaskType.WORKOUT_GENERATION, enabled=True),
        model_pks=[model.id],
        actor="test",
    )
    from src.errors import WorkoutBotError

    with pytest.raises(WorkoutBotError):
        await admin.delete_model(model.id, actor="test")

    # Soft disable разрешён.
    disabled = await admin.update_model(model.id, actor="test", enabled=False)
    assert disabled is not None and disabled.enabled is False


# --- Safe delete: зависимости проверяются заранее и объясняются ---------------------


async def _configured_chain(admin: AIConfigurationService, slug: str):
    """Провайдер → эндпоинт → модель. Привязки к задаче нет."""
    provider = await admin.create_provider(
        AIProvider(name=f"Chain {slug}", slug=slug), actor="test"
    )
    endpoint = await admin.create_endpoint(
        AIEndpoint(
            provider_id=provider.id, name="EP", base_url="https://chain.example/v1"
        ),
        api_key="sk-chain-secret-value",
        actor="test",
    )
    model = await admin.create_model(
        AIModel(endpoint_id=endpoint.id, model_id=f"{slug}/m", display_name="M"),
        actor="test",
    )
    return provider, endpoint, model


async def test_model_without_dependencies_is_deleted(components):
    admin: AIConfigurationService = components["admin"]
    _, _, model = await _configured_chain(admin, "test-del-model")

    assert (await admin.model_dependencies(model.id)).safe is True
    assert await admin.delete_model(model.id, actor="test") is True
    assert await components["models"].get(model.id) is None


async def test_model_dependencies_name_the_blocking_task(components):
    admin: AIConfigurationService = components["admin"]
    _, _, model = await _configured_chain(admin, "test-dep-model")
    await admin.configure_task(
        AITaskConfig(task_type=AITaskType.WORKOUT_GENERATION, enabled=True),
        model_pks=[model.id],
        actor="test",
    )

    dependencies = await admin.model_dependencies(model.id)

    assert dependencies.safe is False
    blocker = dependencies.blockers[0]
    assert blocker["type"] == "ai_task_config"
    assert blocker["task_type"] == "workout_generation"
    assert "workout_generation" in dependencies.describe()


async def test_endpoint_with_bound_model_cannot_be_deleted(components):
    admin: AIConfigurationService = components["admin"]
    _, endpoint, model = await _configured_chain(admin, "test-dep-endpoint")
    await admin.configure_task(
        AITaskConfig(task_type=AITaskType.WORKOUT_GENERATION, enabled=True),
        model_pks=[model.id],
        actor="test",
    )

    with pytest.raises(AIDependencyError) as exc_info:
        await admin.delete_endpoint(endpoint.id, actor="test")

    assert exc_info.value.blockers
    # Эндпоинт и его модель на месте: broken references не появились.
    assert await components["endpoints"].get(endpoint.id) is not None
    assert await components["models"].get(model.id) is not None


async def test_provider_with_bound_model_cannot_be_deleted(components):
    """Каскад провайдера не должен обходить привязку модели к задаче."""
    admin: AIConfigurationService = components["admin"]
    provider, endpoint, model = await _configured_chain(admin, "test-dep-provider")
    await admin.configure_task(
        AITaskConfig(task_type=AITaskType.WORKOUT_GENERATION, enabled=True),
        model_pks=[model.id],
        actor="test",
    )

    with pytest.raises(AIDependencyError) as exc_info:
        await admin.delete_provider(provider.id, actor="test")

    assert "workout_generation" in str(exc_info.value)
    assert await components["providers"].get(provider.id) is not None
    assert await components["endpoints"].get(endpoint.id) is not None
    assert await components["models"].get(model.id) is not None


async def test_provider_without_dependencies_is_deleted_with_secrets(components):
    """Hard delete допустим; секреты каскадных эндпоинтов не остаются в базе."""
    admin: AIConfigurationService = components["admin"]
    provider, endpoint, model = await _configured_chain(admin, "test-del-provider")
    stored = await components["endpoints"].get(endpoint.id)
    reference = stored.secret_reference
    assert reference is not None
    assert await components["secret_store"].get(reference) is not None

    assert await admin.delete_provider(provider.id, actor="test") is True

    assert await components["providers"].get(provider.id) is None
    assert await components["endpoints"].get(endpoint.id) is None
    assert await components["models"].get(model.id) is None
    # Осиротевший шифрованный секрет — это утечка, его быть не должно.
    assert await components["secret_store"].get(reference) is None


async def test_disabling_provider_keeps_configuration_intact(components):
    """Стратегия «сначала отключить»: объект остаётся, ссылки не рвутся."""
    admin: AIConfigurationService = components["admin"]
    provider, endpoint, model = await _configured_chain(admin, "test-disable-provider")

    updated = await admin.update_provider(provider.id, actor="test", enabled=False)

    assert updated is not None and updated.enabled is False
    assert await components["endpoints"].get(endpoint.id) is not None
    assert await components["models"].get(model.id) is not None


async def test_deleting_model_keeps_usage_history(components):
    """Историю вызовов нельзя удалять ради удаления конфигурации.

    Ссылка на удалённую модель обнуляется (FK ON DELETE SET NULL), поэтому
    запись остаётся, а битой ссылки не возникает.
    """
    admin: AIConfigurationService = components["admin"]
    provider, endpoint, model = await _configured_chain(admin, "test-hist-model")
    marker = "prog-test-hist-model"
    await components["usage"].save(
        AIUsageRecord(
            task_type=AITaskType.WORKOUT_GENERATION,
            provider_id=provider.id,
            endpoint_id=endpoint.id,
            model_id=model.id,
            program_id=marker,
            status="success",
        )
    )

    await admin.delete_model(model.id, actor="test")

    async with components["session_factory"]() as session:
        row = (
            await session.execute(
                select(AIUsageRecordRow).where(AIUsageRecordRow.program_id == marker)
            )
        ).scalar_one()
    assert row.status == "success"
    assert row.model_id is None


# --- Fallback observability --------------------------------------------------------


async def test_fallback_event_is_recorded_and_readable(components):
    admin: AIConfigurationService = components["admin"]

    await admin.record_generation_fallback(
        requested_generator="ai",
        actual_generator="deterministic",
        reason_code="provider_unavailable",
        detail="Провайдер: все подходящие провайдеры отключены",
        ai_attempted=False,
    )

    events = await admin.recent_fallback_events(limit=10)
    assert events
    event = events[0]
    assert event["event_type"] == "ai_generation_fallback"
    assert event["metadata"]["reason_code"] == "provider_unavailable"
    assert event["metadata"]["requested_generator"] == "ai"
    assert event["metadata"]["actual_generator"] == "deterministic"
    assert event["metadata"]["ai_attempted"] is False


async def test_fallback_events_exclude_other_audit_events(components):
    admin: AIConfigurationService = components["admin"]
    await admin.create_provider(
        AIProvider(name="Noise", slug="test-fallback-noise"), actor="test"
    )
    await admin.record_generation_fallback(
        requested_generator="ai",
        actual_generator="deterministic",
        reason_code="ai_timeout",
        detail="timeout",
        ai_attempted=True,
    )

    events = await admin.recent_fallback_events(limit=50)

    assert {e["event_type"] for e in events} == {"ai_generation_fallback"}


async def test_fallback_event_metadata_has_no_personal_data(components):
    admin: AIConfigurationService = components["admin"]

    await admin.record_generation_fallback(
        requested_generator="ai",
        actual_generator="deterministic",
        reason_code="ai_runtime_failure",
        detail="boom",
        ai_attempted=True,
    )

    event = (await admin.recent_fallback_events(limit=1))[0]
    assert set(event["metadata"]) == {
        "requested_generator",
        "actual_generator",
        "reason_code",
        "detail",
        "ai_attempted",
    }


# --- Infrastructure health на реальной конфигурации ---------------------------------


async def test_infrastructure_health_reflects_real_configuration(components):
    admin: AIConfigurationService = components["admin"]
    provider, endpoint, model = await _configured_chain(admin, "test-health-live")
    await admin.configure_task(
        AITaskConfig(task_type=AITaskType.WORKOUT_GENERATION, enabled=True),
        model_pks=[model.id],
        actor="test",
    )
    registry = ProviderAdapterRegistry()
    registry.register(AIProtocol.OPENAI_COMPATIBLE, MockAdapter())
    health = AIInfrastructureHealthService(
        providers=components["providers"],
        endpoints=components["endpoints"],
        models=components["models"],
        tasks=components["tasks"],
        usage=components["usage"],
        adapter_registry=registry,
    )

    report = await health.report()

    entry = next(p for p in report.providers if p.id == provider.id)
    assert entry.protocol_supported is True
    # Подключение ни разу не проверялось → это не «доступно».
    assert entry.health == "not_tested"
    endpoint_entry = next(e for e in entry.endpoints if e.id == endpoint.id)
    assert endpoint_entry.has_api_key is True
    model_entry = next(m for m in endpoint_entry.models if m.id == model.id)
    assert model_entry.availability == "not_tested"
    assert model_entry.in_active_use is True


async def test_infrastructure_health_marks_endpoint_available_after_test(components):
    admin: AIConfigurationService = components["admin"]
    provider, endpoint, model = await _configured_chain(admin, "test-health-tested")
    await components["endpoints"].record_test_result(endpoint.id, success=True)
    registry = ProviderAdapterRegistry()
    registry.register(AIProtocol.OPENAI_COMPATIBLE, MockAdapter())
    health = AIInfrastructureHealthService(
        providers=components["providers"],
        endpoints=components["endpoints"],
        models=components["models"],
        tasks=components["tasks"],
        usage=components["usage"],
        adapter_registry=registry,
    )

    report = await health.report()

    entry = next(p for p in report.providers if p.id == provider.id)
    assert entry.health == "healthy"
    endpoint_entry = next(e for e in entry.endpoints if e.id == endpoint.id)
    assert endpoint_entry.last_checked_at is not None
    model_entry = next(m for m in endpoint_entry.models if m.id == model.id)
    assert model_entry.availability == "available"
