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

from src.application.ai.admin_service import AIConfigurationService
from src.application.ai.gateway import AIGateway
from src.application.ai.selection import ModelSelector
from src.domain.ai.config import (
    AIEndpoint,
    AIModel,
    AIProvider,
    AITaskConfig,
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
                await session.execute(
                    delete(AISecretRow).where(AISecretRow.reference.like("test-ref%"))
                )
                await session.execute(
                    delete(AIAuditEventRow).where(
                        AIAuditEventRow.entity_type.in_(
                            ["ai_provider", "ai_endpoint", "ai_model", "ai_task_config", "prompt_template"]
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
