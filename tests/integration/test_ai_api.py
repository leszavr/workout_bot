"""API-тесты Admin AI endpoints: CRUD, секреты, fallback, connection test.

Ключевые проверки безопасности:
- API key никогда не возвращается ни в одном ответе;
- реальный секрет не присутствует в тексте ответов;
- audit metadata не содержит секретов.

Требуют DATABASE_URL; иначе пропускаются.
"""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from apps.backend.main import app
import apps.backend.auth as auth_module
from src.infrastructure.config import DATABASE_URL
from src.infrastructure.persistence.postgres.models import (
    AIAuditEventRow,
    AIEndpointRow,
    AIModelRow,
    AIProviderRow,
    AISecretRow,
    AITaskConfigRow,
    AITaskModelBindingRow,
    AIUsageRecordRow,
)

pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL is not set")

auth_module.ADMIN_LOGIN = "admin"
auth_module.ADMIN_PASSWORD = "test-admin-password"
auth_module.JWT_SECRET = "test-jwt-secret-with-sufficient-length-32b"

REAL_SECRET = "sk-api-test-secret-xyz987654321"


@pytest.fixture(scope="module")
def client() -> TestClient:
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module", autouse=True)
def reset_engine_after_module():
    yield
    from src.infrastructure.persistence.postgres.db import reset_engine_state

    reset_engine_state()


@pytest.fixture(scope="module")
def auth_headers(client: TestClient) -> dict:
    response = client.post(
        "/api/v1/auth/login",
        json={"login": "admin", "password": "test-admin-password"},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture(autouse=True)
async def cleanup_ai_api_data():
    """Отдельный локальный engine: не конфликтует с loop'ом TestClient."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def _purge() -> None:
        async with sessions() as session:
            async with session.begin():
                provider_ids = (
                    await session.execute(
                        select(AIProviderRow.id).where(AIProviderRow.slug.like("apitest-%"))
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
                    if endpoint_ids:
                        model_ids = (
                            await session.execute(
                                select(AIModelRow.id).where(
                                    AIModelRow.endpoint_id.in_(endpoint_ids)
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
                    delete(AIProviderRow).where(AIProviderRow.slug.like("apitest-%"))
                )
                await session.execute(
                    delete(AISecretRow).where(AISecretRow.reference.like("ai-endpoint-%"))
                )
                await session.execute(
                    delete(AIAuditEventRow).where(
                        AIAuditEventRow.entity_type.in_(
                            ["ai_provider", "ai_endpoint", "ai_model", "ai_task_config"]
                        )
                    )
                )

    await _purge()
    yield
    await _purge()
    await engine.dispose()


def _assert_no_secret_leak(response) -> None:
    """Реальный секрет не должен присутствовать ни в одном ответе API."""
    assert REAL_SECRET not in response.text
    data = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}

    # Явная проверка ключей ответа:
    def _walk(obj):
        if isinstance(obj, dict):
            for key, value in obj.items():
                assert key not in {"api_key", "secret", "encrypted_value"}, f"leaked field: {key}"
                _walk(value)
        elif isinstance(obj, list):
            for item in obj:
                _walk(item)
    _walk(data)


def test_ai_endpoints_require_auth(client: TestClient):
    response = client.get("/api/v1/admin/ai/providers")
    assert response.status_code == 401


def test_full_crud_and_secret_safety(client: TestClient, auth_headers: dict):
    # --- Provider CRUD ---
    response = client.post(
        "/api/v1/admin/ai/providers",
        headers=auth_headers,
        json={"name": "API Test Provider", "slug": "apitest-main", "protocol": "openai_compatible"},
    )
    assert response.status_code == 201, response.text
    provider = response.json()
    provider_id = provider["id"]
    assert provider["protocol"] == "openai_compatible"

    # Дубликат slug → 409.
    dup = client.post(
        "/api/v1/admin/ai/providers",
        headers=auth_headers,
        json={"name": "Dup", "slug": "apitest-main"},
    )
    assert dup.status_code == 409

    response = client.get(f"/api/v1/admin/ai/providers/{provider_id}", headers=auth_headers)
    assert response.status_code == 200

    response = client.patch(
        f"/api/v1/admin/ai/providers/{provider_id}",
        headers=auth_headers,
        json={"priority": 50},
    )
    assert response.status_code == 200
    assert response.json()["priority"] == 50

    # --- Endpoint с API key ---
    response = client.post(
        f"/api/v1/admin/ai/providers/{provider_id}/endpoints",
        headers=auth_headers,
        json={
            "name": "Main EP",
            "base_url": "https://ai.apitest.example/v1",
            "api_key": REAL_SECRET,
            "timeout_seconds": 30,
            "max_retries": 1,
        },
    )
    assert response.status_code == 201, response.text
    endpoint = response.json()
    endpoint_id = endpoint["id"]

    # API key НЕ возвращается; есть has_api_key и masked.
    assert "api_key" not in endpoint
    assert REAL_SECRET not in response.text
    assert endpoint["has_api_key"] is True
    assert endpoint["masked_api_key"].endswith("4321")
    assert REAL_SECRET not in (endpoint["masked_api_key"] or "")

    # GET списка эндпоинтов тоже без секрета.
    response = client.get(
        f"/api/v1/admin/ai/providers/{provider_id}/endpoints", headers=auth_headers
    )
    assert response.status_code == 200
    _assert_no_secret_leak(response)
    assert response.json()["items"][0]["has_api_key"] is True

    # --- Ротация ключа ---
    new_secret = "sk-rotated-key-abc111"
    response = client.put(
        f"/api/v1/admin/ai/endpoints/{endpoint_id}/secret",
        headers=auth_headers,
        json={"api_key": new_secret},
    )
    assert response.status_code == 200
    assert response.json()["masked_api_key"].endswith("c111")
    assert new_secret not in response.text

    # --- Models CRUD ---
    response = client.post(
        f"/api/v1/admin/ai/endpoints/{endpoint_id}/models",
        headers=auth_headers,
        json={
            "model_id": "apitest/model-a",
            "display_name": "Model A",
            "max_output_tokens": 8000,
            "supports_json_schema": True,
        },
    )
    assert response.status_code == 201, response.text
    model_a_id = response.json()["id"]

    response = client.post(
        f"/api/v1/admin/ai/endpoints/{endpoint_id}/models",
        headers=auth_headers,
        json={"model_id": "apitest/model-b", "display_name": "Model B"},
    )
    model_b_id = response.json()["id"]

    response = client.patch(
        f"/api/v1/admin/ai/models/{model_a_id}",
        headers=auth_headers,
        json={"context_window": 128000},
    )
    assert response.status_code == 200
    assert response.json()["context_window"] == 128000

    # --- Task config: primary + fallback ---
    response = client.put(
        "/api/v1/admin/ai/tasks/workout_generation",
        headers=auth_headers,
        json={
            "enabled": True,
            "temperature": 0.4,
            "max_tokens": 4000,
            "model_ids": [model_a_id, model_b_id],
        },
    )
    assert response.status_code == 200, response.text
    task = response.json()
    assert task["enabled"] is True
    assert [b["priority"] for b in task["bindings"]] == [1, 2]
    assert task["bindings"][0]["is_primary"] is True
    assert task["bindings"][0]["model_id"] == model_a_id

    # GET задачи.
    response = client.get(
        "/api/v1/admin/ai/tasks/workout_generation", headers=auth_headers
    )
    assert response.status_code == 200
    assert len(response.json()["bindings"]) == 2

    # Список задач содержит все типы.
    response = client.get("/api/v1/admin/ai/tasks", headers=auth_headers)
    assert response.status_code == 200
    task_types = {t["task_type"] for t in response.json()["items"]}
    assert "workout_generation" in task_types
    assert "user_chat" in task_types

    # --- Модель, привязанную к задаче, нельзя удалить ---
    response = client.delete(f"/api/v1/admin/ai/models/{model_a_id}", headers=auth_headers)
    assert response.status_code == 409

    # --- Validation errors ---
    bad = client.post(
        "/api/v1/admin/ai/providers",
        headers=auth_headers,
        json={"name": "X", "slug": "UPPERCASE_BAD"},
    )
    assert bad.status_code == 422

    # --- Audit: события есть, секрета нет ---
    response = client.get("/api/v1/admin/ai/audit", headers=auth_headers)
    assert response.status_code == 200
    _assert_no_secret_leak(response)
    events = {e["event_type"] for e in response.json()["items"]}
    assert "ai_provider_created" in events
    assert "ai_endpoint_created" in events
    assert "ai_endpoint_secret_rotated" in events
    assert "ai_task_updated" in events
    assert REAL_SECRET not in response.text

    # --- Удаление: обе модели привязаны к задаче → 409 (RESTRICT) ---
    response = client.delete(f"/api/v1/admin/ai/models/{model_b_id}", headers=auth_headers)
    assert response.status_code == 409

    # Отвязываем все модели (пустой список) — теперь удаление разрешено.
    response = client.put(
        "/api/v1/admin/ai/tasks/workout_generation",
        headers=auth_headers,
        json={"enabled": False, "model_ids": []},
    )
    assert response.status_code == 200
    response = client.delete(f"/api/v1/admin/ai/models/{model_a_id}", headers=auth_headers)
    assert response.status_code == 204
    response = client.delete(f"/api/v1/admin/ai/models/{model_b_id}", headers=auth_headers)
    assert response.status_code == 204

    response = client.delete(f"/api/v1/admin/ai/endpoints/{endpoint_id}", headers=auth_headers)
    assert response.status_code == 204
    response = client.delete(f"/api/v1/admin/ai/providers/{provider_id}", headers=auth_headers)
    assert response.status_code == 204

    # 404 после удаления.
    response = client.get(f"/api/v1/admin/ai/providers/{provider_id}", headers=auth_headers)
    assert response.status_code == 404


def test_connection_test_endpoint_not_found(client: TestClient, auth_headers: dict):
    response = client.post("/api/v1/admin/ai/endpoints/999999/test", headers=auth_headers)
    assert response.status_code == 404


def test_disabled_provider_not_leaked_in_responses(client: TestClient, auth_headers: dict):
    """Отключённый провайдер остаётся в списке, но помечен disabled."""
    response = client.post(
        "/api/v1/admin/ai/providers",
        headers=auth_headers,
        json={"name": "Disabled P", "slug": "apitest-disabled", "enabled": False},
    )
    assert response.status_code == 201
    assert response.json()["enabled"] is False
    provider_id = response.json()["id"]

    response = client.get("/api/v1/admin/ai/providers", headers=auth_headers)
    slugs = {p["slug"]: p["enabled"] for p in response.json()["items"]}
    assert slugs.get("apitest-disabled") is False

    client.delete(f"/api/v1/admin/ai/providers/{provider_id}", headers=auth_headers)
