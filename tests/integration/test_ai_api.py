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


# --- Readiness (Phase 1.1) ----------------------------------------------------------


def test_readiness_requires_auth(client: TestClient):
    assert client.get("/api/v1/admin/ai/readiness").status_code == 401


def test_readiness_reports_protocols_and_strategy(client: TestClient, auth_headers: dict):
    """Отчёт готовности сообщает поддерживаемые протоколы и стратегию генерации."""
    response = client.get("/api/v1/admin/ai/readiness", headers=auth_headers)
    assert response.status_code == 200, response.text
    report = response.json()

    assert report["task_type"] == "workout_generation"
    assert isinstance(report["ready"], bool)
    protocols = {p["value"]: p["supported"] for p in report["protocols"]}
    assert protocols["openai_compatible"] is True
    # Адаптеров для этих протоколов нет — UI обязан это видеть.
    assert protocols["anthropic"] is False
    assert protocols["custom"] is False
    assert "primary_generator" in report["generation"]
    keys = {c["key"] for c in report["checks"]}
    assert {
        "provider",
        "endpoint",
        "api_key",
        "connection",
        "model",
        "task_models",
        "task_enabled",
        "prompt",
        "generation_strategy",
    } <= keys


def test_readiness_without_task_models_is_not_ready(client: TestClient, auth_headers: dict):
    """Пустая конфигурация задачи → не готова, шаги объясняют причину."""
    report = client.get("/api/v1/admin/ai/readiness", headers=auth_headers).json()
    checks = {c["key"]: c for c in report["checks"]}

    assert report["ready"] is False
    assert checks["task_models"]["status"] == "missing"
    assert checks["task_enabled"]["status"] == "missing"
    assert checks["task_models"]["action"]
    assert report["chain"] == []


def test_connection_test_result_is_persisted(client: TestClient, auth_headers: dict):
    """Результат проверки подключения виден в состоянии эндпоинта и readiness."""
    provider_id = client.post(
        "/api/v1/admin/ai/providers",
        headers=auth_headers,
        json={"name": "Readiness P", "slug": "apitest-readiness"},
    ).json()["id"]
    endpoint = client.post(
        f"/api/v1/admin/ai/providers/{provider_id}/endpoints",
        headers=auth_headers,
        json={
            "name": "Unreachable",
            "base_url": "https://unreachable.apitest.invalid/v1",
            "timeout_seconds": 2,
        },
    ).json()
    assert endpoint["last_test_status"] is None

    result = client.post(
        f"/api/v1/admin/ai/endpoints/{endpoint['id']}/test", headers=auth_headers
    ).json()
    assert result["success"] is False

    endpoints = client.get(
        f"/api/v1/admin/ai/providers/{provider_id}/endpoints", headers=auth_headers
    ).json()["items"]
    stored = next(e for e in endpoints if e["id"] == endpoint["id"])
    assert stored["last_test_status"] == "error"
    assert stored["last_test_error_type"]
    assert stored["last_test_at"]

    client.delete(f"/api/v1/admin/ai/providers/{provider_id}", headers=auth_headers)


def test_task_cannot_be_enabled_without_models(client: TestClient, auth_headers: dict):
    response = client.put(
        "/api/v1/admin/ai/tasks/workout_generation",
        headers=auth_headers,
        json={"enabled": True, "model_ids": []},
    )
    assert response.status_code == 422
    assert "модель" in response.json()["detail"]


def test_task_cannot_be_enabled_with_disabled_model(client: TestClient, auth_headers: dict):
    """UI-ограничение дублируется серверной проверкой."""
    provider_id = client.post(
        "/api/v1/admin/ai/providers",
        headers=auth_headers,
        json={"name": "Guard P", "slug": "apitest-guard"},
    ).json()["id"]
    endpoint_id = client.post(
        f"/api/v1/admin/ai/providers/{provider_id}/endpoints",
        headers=auth_headers,
        json={"name": "EP", "base_url": "https://guard.apitest.example/v1"},
    ).json()["id"]
    model_id = client.post(
        f"/api/v1/admin/ai/endpoints/{endpoint_id}/models",
        headers=auth_headers,
        json={"model_id": "apitest/guard", "display_name": "Guard", "enabled": False},
    ).json()["id"]

    response = client.put(
        "/api/v1/admin/ai/tasks/workout_generation",
        headers=auth_headers,
        json={"enabled": True, "model_ids": [model_id]},
    )
    assert response.status_code == 422
    assert "отключена" in response.json()["detail"]

    # Выключенную задачу с той же моделью сохранить можно: это не ложное обещание.
    response = client.put(
        "/api/v1/admin/ai/tasks/workout_generation",
        headers=auth_headers,
        json={"enabled": False, "model_ids": [model_id]},
    )
    assert response.status_code == 200

    # После включения модели задача включается и попадает в цепочку.
    client.patch(
        f"/api/v1/admin/ai/models/{model_id}", headers=auth_headers, json={"enabled": True}
    )
    response = client.put(
        "/api/v1/admin/ai/tasks/workout_generation",
        headers=auth_headers,
        json={"enabled": True, "model_ids": [model_id]},
    )
    assert response.status_code == 200, response.text

    report = client.get("/api/v1/admin/ai/readiness", headers=auth_headers).json()
    checks = {c["key"]: c for c in report["checks"]}
    assert checks["task_enabled"]["status"] == "ok"
    assert checks["task_models"]["status"] == "ok"
    assert [entry["model_id"] for entry in report["chain"]] == ["apitest/guard"]
    # Подключение не проверялось — конфигурация не считается готовой.
    assert checks["connection"]["status"] == "missing"
    assert report["ready"] is False


def test_task_cannot_be_enabled_with_unknown_prompt_version(
    client: TestClient, auth_headers: dict
):
    provider_id = client.post(
        "/api/v1/admin/ai/providers",
        headers=auth_headers,
        json={"name": "Prompt P", "slug": "apitest-prompt"},
    ).json()["id"]
    endpoint_id = client.post(
        f"/api/v1/admin/ai/providers/{provider_id}/endpoints",
        headers=auth_headers,
        json={"name": "EP", "base_url": "https://prompt.apitest.example/v1"},
    ).json()["id"]
    model_id = client.post(
        f"/api/v1/admin/ai/endpoints/{endpoint_id}/models",
        headers=auth_headers,
        json={"model_id": "apitest/prompt", "display_name": "Prompt"},
    ).json()["id"]

    response = client.put(
        "/api/v1/admin/ai/tasks/workout_generation",
        headers=auth_headers,
        json={"enabled": True, "model_ids": [model_id], "prompt_version": 99},
    )
    assert response.status_code == 422
    assert "v99" in response.json()["detail"]


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


# --- Lifecycle: edit / enable / disable / safe delete ------------------------------


def _chain(client: TestClient, auth_headers: dict, slug: str) -> tuple[int, int, int]:
    """Провайдер → эндпоинт с ключом → модель."""
    provider_id = client.post(
        "/api/v1/admin/ai/providers",
        headers=auth_headers,
        json={"name": f"Chain {slug}", "slug": slug},
    ).json()["id"]
    endpoint_id = client.post(
        f"/api/v1/admin/ai/providers/{provider_id}/endpoints",
        headers=auth_headers,
        json={
            "name": "EP",
            "base_url": "https://chain.apitest.example/v1",
            "api_key": REAL_SECRET,
        },
    ).json()["id"]
    model_id = client.post(
        f"/api/v1/admin/ai/endpoints/{endpoint_id}/models",
        headers=auth_headers,
        json={"model_id": f"{slug}/m", "display_name": "M"},
    ).json()["id"]
    return provider_id, endpoint_id, model_id


def test_provider_endpoint_model_can_be_edited(client: TestClient, auth_headers: dict):
    """Редактирование должно быть доступно, а не только create/toggle."""
    provider_id, endpoint_id, model_id = _chain(client, auth_headers, "apitest-edit")

    provider = client.patch(
        f"/api/v1/admin/ai/providers/{provider_id}",
        headers=auth_headers,
        json={"name": "Renamed provider", "priority": 5},
    )
    assert provider.status_code == 200
    assert provider.json()["name"] == "Renamed provider"
    assert provider.json()["priority"] == 5

    endpoint = client.patch(
        f"/api/v1/admin/ai/endpoints/{endpoint_id}",
        headers=auth_headers,
        json={
            "name": "Renamed endpoint",
            "base_url": "https://edited.apitest.example/v1",
            "timeout_seconds": 30,
        },
    )
    assert endpoint.status_code == 200
    assert endpoint.json()["base_url"] == "https://edited.apitest.example/v1"
    assert endpoint.json()["timeout_seconds"] == 30
    # Правка эндпоинта не должна терять сохранённый ключ.
    assert endpoint.json()["has_api_key"] is True
    _assert_no_secret_leak(endpoint)

    model = client.patch(
        f"/api/v1/admin/ai/models/{model_id}",
        headers=auth_headers,
        json={"display_name": "Renamed model", "context_window": 128000},
    )
    assert model.status_code == 200
    assert model.json()["display_name"] == "Renamed model"
    assert model.json()["context_window"] == 128000

    client.delete(f"/api/v1/admin/ai/providers/{provider_id}", headers=auth_headers)


def test_entities_can_be_disabled_and_enabled(client: TestClient, auth_headers: dict):
    provider_id, endpoint_id, model_id = _chain(client, auth_headers, "apitest-toggle")

    for path in (
        f"/api/v1/admin/ai/providers/{provider_id}",
        f"/api/v1/admin/ai/endpoints/{endpoint_id}",
        f"/api/v1/admin/ai/models/{model_id}",
    ):
        disabled = client.patch(path, headers=auth_headers, json={"enabled": False})
        assert disabled.status_code == 200
        assert disabled.json()["enabled"] is False
        enabled = client.patch(path, headers=auth_headers, json={"enabled": True})
        assert enabled.status_code == 200
        assert enabled.json()["enabled"] is True

    client.delete(f"/api/v1/admin/ai/providers/{provider_id}", headers=auth_headers)


def test_safe_delete_without_dependencies(client: TestClient, auth_headers: dict):
    provider_id, endpoint_id, model_id = _chain(client, auth_headers, "apitest-safedel")

    assert (
        client.delete(
            f"/api/v1/admin/ai/models/{model_id}", headers=auth_headers
        ).status_code
        == 204
    )
    assert (
        client.delete(
            f"/api/v1/admin/ai/endpoints/{endpoint_id}", headers=auth_headers
        ).status_code
        == 204
    )
    assert (
        client.delete(
            f"/api/v1/admin/ai/providers/{provider_id}", headers=auth_headers
        ).status_code
        == 204
    )
    assert (
        client.get(
            f"/api/v1/admin/ai/providers/{provider_id}", headers=auth_headers
        ).status_code
        == 404
    )


def test_delete_with_dependencies_explains_blockers(
    client: TestClient, auth_headers: dict
):
    """409 должен перечислять зависимости, а не выдавать ошибку целостности."""
    provider_id, endpoint_id, model_id = _chain(client, auth_headers, "apitest-blocked")
    assert (
        client.put(
            "/api/v1/admin/ai/tasks/workout_generation",
            headers=auth_headers,
            json={"enabled": True, "model_ids": [model_id]},
        ).status_code
        == 200
    )

    for path in (
        f"/api/v1/admin/ai/models/{model_id}",
        f"/api/v1/admin/ai/endpoints/{endpoint_id}",
        f"/api/v1/admin/ai/providers/{provider_id}",
    ):
        response = client.delete(path, headers=auth_headers)
        assert response.status_code == 409
        detail = response.json()["detail"]
        assert "workout_generation" in detail["message"]
        assert detail["blockers"]
        assert detail["blockers"][0]["task_type"] == "workout_generation"

    # Broken references не появились: всё поддерево на месте.
    assert (
        client.get(
            f"/api/v1/admin/ai/providers/{provider_id}", headers=auth_headers
        ).status_code
        == 200
    )
    assert client.get(
        f"/api/v1/admin/ai/endpoints/{endpoint_id}/models", headers=auth_headers
    ).json()["total"] == 1

    client.put(
        "/api/v1/admin/ai/tasks/workout_generation",
        headers=auth_headers,
        json={"enabled": False, "model_ids": []},
    )
    client.delete(f"/api/v1/admin/ai/providers/{provider_id}", headers=auth_headers)


def test_delete_missing_entities_returns_404(client: TestClient, auth_headers: dict):
    assert (
        client.delete(
            "/api/v1/admin/ai/providers/99999999", headers=auth_headers
        ).status_code
        == 404
    )
    assert (
        client.delete(
            "/api/v1/admin/ai/endpoints/99999999", headers=auth_headers
        ).status_code
        == 404
    )
    assert (
        client.delete(
            "/api/v1/admin/ai/models/99999999", headers=auth_headers
        ).status_code
        == 404
    )


# --- Infrastructure health API -----------------------------------------------------


def test_infrastructure_health_requires_auth(client: TestClient):
    assert client.get("/api/v1/admin/ai/infrastructure-health").status_code == 401
    assert (
        client.post("/api/v1/admin/ai/infrastructure-health/refresh").status_code == 401
    )


def test_infrastructure_health_is_built_from_configuration(
    client: TestClient, auth_headers: dict
):
    """Новый провайдер и модель появляются в дашборде без правок frontend."""
    provider_id, endpoint_id, model_id = _chain(client, auth_headers, "apitest-health")

    response = client.get("/api/v1/admin/ai/infrastructure-health", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["generated_at"]

    provider = next(p for p in body["providers"] if p["id"] == provider_id)
    assert provider["slug"] == "apitest-health"
    assert provider["protocol_supported"] is True
    # Подключение не проверялось: это не healthy и не unavailable.
    assert provider["health"] == "not_tested"

    endpoint = next(e for e in provider["endpoints"] if e["id"] == endpoint_id)
    assert endpoint["has_api_key"] is True
    assert endpoint["last_checked_at"] is None

    model = next(m for m in endpoint["models"] if m["id"] == model_id)
    assert model["availability"] == "not_tested"
    assert model["in_active_use"] is False

    client.delete(f"/api/v1/admin/ai/providers/{provider_id}", headers=auth_headers)


def test_infrastructure_health_shows_disabled_states(
    client: TestClient, auth_headers: dict
):
    """Disabled-модель остаётся в конфигурации и не выглядит доступной."""
    provider_id, endpoint_id, model_id = _chain(
        client, auth_headers, "apitest-health-off"
    )
    client.patch(
        f"/api/v1/admin/ai/models/{model_id}",
        headers=auth_headers,
        json={"enabled": False},
    )

    body = client.get(
        "/api/v1/admin/ai/infrastructure-health", headers=auth_headers
    ).json()
    provider = next(p for p in body["providers"] if p["id"] == provider_id)
    model = provider["endpoints"][0]["models"][0]

    assert model["enabled"] is False
    assert model["availability"] == "disabled"

    client.patch(
        f"/api/v1/admin/ai/providers/{provider_id}",
        headers=auth_headers,
        json={"enabled": False},
    )
    body = client.get(
        "/api/v1/admin/ai/infrastructure-health", headers=auth_headers
    ).json()
    provider = next(p for p in body["providers"] if p["id"] == provider_id)
    assert provider["health"] == "disabled"

    client.delete(f"/api/v1/admin/ai/providers/{provider_id}", headers=auth_headers)


def test_infrastructure_health_disappears_after_delete(
    client: TestClient, auth_headers: dict
):
    """Сценарий 5: безопасно удалённый провайдер исчезает из дашборда."""
    provider_id, _, _ = _chain(client, auth_headers, "apitest-health-del")
    body = client.get(
        "/api/v1/admin/ai/infrastructure-health", headers=auth_headers
    ).json()
    assert any(p["id"] == provider_id for p in body["providers"])

    assert (
        client.delete(
            f"/api/v1/admin/ai/providers/{provider_id}", headers=auth_headers
        ).status_code
        == 204
    )

    body = client.get(
        "/api/v1/admin/ai/infrastructure-health", headers=auth_headers
    ).json()
    assert not any(p["id"] == provider_id for p in body["providers"])


def test_infrastructure_health_reports_task_usage(
    client: TestClient, auth_headers: dict
):
    provider_id, _, model_id = _chain(client, auth_headers, "apitest-health-task")
    client.put(
        "/api/v1/admin/ai/tasks/workout_generation",
        headers=auth_headers,
        json={"enabled": True, "model_ids": [model_id]},
    )

    body = client.get(
        "/api/v1/admin/ai/infrastructure-health", headers=auth_headers
    ).json()
    provider = next(p for p in body["providers"] if p["id"] == provider_id)
    model = provider["endpoints"][0]["models"][0]

    assert model["in_active_use"] is True
    assert model["tasks"][0]["task_type"] == "workout_generation"
    assert model["tasks"][0]["is_primary"] is True

    client.put(
        "/api/v1/admin/ai/tasks/workout_generation",
        headers=auth_headers,
        json={"enabled": False, "model_ids": []},
    )
    client.delete(f"/api/v1/admin/ai/providers/{provider_id}", headers=auth_headers)


def test_infrastructure_health_never_returns_secrets(
    client: TestClient, auth_headers: dict
):
    provider_id, _, _ = _chain(client, auth_headers, "apitest-health-secret")

    response = client.get(
        "/api/v1/admin/ai/infrastructure-health", headers=auth_headers
    )
    _assert_no_secret_leak(response)
    assert "secret_reference" not in response.text

    client.delete(f"/api/v1/admin/ai/providers/{provider_id}", headers=auth_headers)


def test_infrastructure_health_refresh_updates_last_checked(
    client: TestClient, auth_headers: dict
):
    """Manual refresh пингует эндпоинт и сохраняет время проверки."""
    provider_id, endpoint_id, _ = _chain(client, auth_headers, "apitest-health-refresh")

    response = client.post(
        "/api/v1/admin/ai/infrastructure-health/refresh", headers=auth_headers
    )
    assert response.status_code == 200
    provider = next(p for p in response.json()["providers"] if p["id"] == provider_id)
    endpoint = next(e for e in provider["endpoints"] if e["id"] == endpoint_id)

    # Хост недоступен, поэтому ожидаем зафиксированный провал, а не «не проверялось».
    assert endpoint["last_checked_at"] is not None
    assert endpoint["health"] == "unavailable"
    assert endpoint["last_check_error_type"]

    client.delete(f"/api/v1/admin/ai/providers/{provider_id}", headers=auth_headers)


# --- Fallback events API -----------------------------------------------------------


def test_fallback_events_require_auth(client: TestClient):
    assert client.get("/api/v1/admin/ai/fallback-events").status_code == 401


def test_fallback_events_endpoint_returns_list(client: TestClient, auth_headers: dict):
    response = client.get("/api/v1/admin/ai/fallback-events", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert "items" in body and "total" in body
    assert all(i["event_type"] == "ai_generation_fallback" for i in body["items"])
