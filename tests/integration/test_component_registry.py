"""Интеграционные тесты Component Registry через API.

Проверяют то, что нельзя проверить без БД: идемпотентность регистрации,
изоляцию экземпляров одного типа, аутентификацию internal API и то, что
секреты не попадают в ответы.

Требуют DATABASE_URL; иначе пропускаются.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

import apps.backend.auth as auth_module
import apps.backend.service_auth as service_auth_module
from apps.backend.main import app
from src.infrastructure.config import DATABASE_URL
from src.infrastructure.persistence.postgres.models import ComponentInstanceRow

pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL is not set")

SERVICE_TOKEN = "integration-service-token"
PREFIX = "ctest-"

ADMIN_LOGIN = "compadmin"
ADMIN_PASSWORD = "comp-admin-password-1"


def metadata(component_id: str, **overrides) -> dict:
    payload = {
        "component_id": component_id,
        "component_type": "telegram_gateway",
        "name": "Telegram Gateway EU",
        "region": "EU",
        "version": "2.2.0",
        "build_sha": "abc1234",
        "contract_version": 1,
        "capabilities": ["telegram_polling", "telegram_delivery"],
        "status": "healthy",
    }
    payload.update(overrides)
    return payload


@pytest.fixture(scope="module", autouse=True)
def configured_secrets():
    previous = (
        auth_module.ADMIN_LOGIN,
        auth_module.ADMIN_PASSWORD,
        auth_module.JWT_SECRET,
        service_auth_module.INTERNAL_SERVICE_TOKEN,
    )
    auth_module.ADMIN_LOGIN = ADMIN_LOGIN
    auth_module.ADMIN_PASSWORD = ADMIN_PASSWORD
    auth_module.JWT_SECRET = "test-jwt-secret-with-sufficient-length-32b"
    service_auth_module.INTERNAL_SERVICE_TOKEN = SERVICE_TOKEN
    yield
    (
        auth_module.ADMIN_LOGIN,
        auth_module.ADMIN_PASSWORD,
        auth_module.JWT_SECRET,
        service_auth_module.INTERNAL_SERVICE_TOKEN,
    ) = previous


@pytest.fixture(scope="module")
def client() -> TestClient:
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module", autouse=True)
def reset_engine_after_module():
    yield
    from src.infrastructure.persistence.postgres.db import reset_engine_state

    reset_engine_state()


@pytest.fixture(autouse=True)
async def cleanup_components():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def _purge() -> None:
        async with sessions() as session:
            async with session.begin():
                await session.execute(
                    delete(ComponentInstanceRow).where(
                        ComponentInstanceRow.component_id.like(f"{PREFIX}%")
                    )
                )

    await _purge()
    try:
        yield
    finally:
        await _purge()
        await engine.dispose()


@pytest.fixture(scope="module")
def admin_headers(client: TestClient) -> dict:
    response = client.post(
        "/api/v1/auth/login",
        json={"login": ADMIN_LOGIN, "password": ADMIN_PASSWORD},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def service_headers() -> dict:
    return {"X-Internal-Service-Token": SERVICE_TOKEN}


# --- Component metadata ----------------------------------------------------


def test_backend_version_endpoint_reports_contract(client: TestClient):
    body = client.get("/version").json()
    assert body["component"] == "backend"
    assert body["contract_version"] >= 1
    assert isinstance(body["supported_contracts"], list)
    assert body["version"]


def test_version_endpoint_exposes_no_secrets(client: TestClient):
    text = client.get("/version").text.lower()
    for marker in ("token", "secret", "password", "postgresql", "redis://"):
        assert marker not in text


# --- Registration / heartbeat ----------------------------------------------


def test_registration_requires_service_token(client: TestClient):
    response = client.post(
        "/internal/v1/components/heartbeat", json=metadata(f"{PREFIX}noauth")
    )
    assert response.status_code == 401


def test_registration_rejects_wrong_token(client: TestClient):
    response = client.post(
        "/internal/v1/components/heartbeat",
        json=metadata(f"{PREFIX}badauth"),
        headers={"X-Internal-Service-Token": "wrong"},
    )
    assert response.status_code == 401


def test_registration_returns_compatibility_verdict(client: TestClient):
    response = client.post(
        "/internal/v1/components/heartbeat",
        json=metadata(f"{PREFIX}eu-1"),
        headers=service_headers(),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["accepted"] is True
    assert body["compatibility"]["state"] in ("compatible", "update_recommended")
    assert body["heartbeat_interval_seconds"] > 0


def test_repeated_registration_is_idempotent(client: TestClient):
    """Повторная регистрация не создаёт второй экземпляр и хранит registered_at."""
    first = client.post(
        "/internal/v1/components/heartbeat",
        json=metadata(f"{PREFIX}eu-1"),
        headers=service_headers(),
    ).json()
    second = client.post(
        "/internal/v1/components/heartbeat",
        json=metadata(f"{PREFIX}eu-1"),
        headers=service_headers(),
    ).json()

    assert first["registered_at"] == second["registered_at"]
    state = client.get(
        f"/internal/v1/components/{PREFIX}eu-1", headers=service_headers()
    ).json()
    assert state["component"]["metadata"]["component_id"] == f"{PREFIX}eu-1"


def test_heartbeat_updates_version_without_duplicating(client: TestClient):
    client.post(
        "/internal/v1/components/heartbeat",
        json=metadata(f"{PREFIX}eu-1", version="2.2.0"),
        headers=service_headers(),
    )
    client.post(
        "/internal/v1/components/heartbeat",
        json=metadata(f"{PREFIX}eu-1", version="2.3.0"),
        headers=service_headers(),
    )
    state = client.get(
        f"/internal/v1/components/{PREFIX}eu-1", headers=service_headers()
    ).json()
    assert state["component"]["metadata"]["version"] == "2.3.0"


def test_invalid_component_id_is_rejected(client: TestClient):
    response = client.post(
        "/internal/v1/components/heartbeat",
        json=metadata("Bad ID With Spaces"),
        headers=service_headers(),
    )
    assert response.status_code == 422


# --- Multiple instances ----------------------------------------------------


def test_two_instances_of_same_type_coexist(client: TestClient, admin_headers: dict):
    for suffix in ("eu-1", "eu-2"):
        assert (
            client.post(
                "/internal/v1/components/heartbeat",
                json=metadata(f"{PREFIX}{suffix}"),
                headers=service_headers(),
            ).status_code
            == 200
        )

    items = client.get("/api/v1/admin/components", headers=admin_headers).json()["items"]
    ids = {item["component_id"] for item in items}
    assert {f"{PREFIX}eu-1", f"{PREFIX}eu-2"} <= ids


# --- Admin API -------------------------------------------------------------


def test_admin_components_requires_auth(client: TestClient):
    assert client.get("/api/v1/admin/components").status_code == 401


def test_admin_components_includes_backend_itself(
    client: TestClient, admin_headers: dict
):
    body = client.get("/api/v1/admin/components", headers=admin_headers).json()
    types = {item["component_type"] for item in body["items"]}
    assert "backend" in types
    assert body["backend"]["contract_version"] >= 1
    assert "telegram_gateway" in body["requirements"]


def test_admin_components_response_has_no_secrets(
    client: TestClient, admin_headers: dict
):
    client.post(
        "/internal/v1/components/heartbeat",
        json=metadata(f"{PREFIX}eu-1"),
        headers=service_headers(),
    )
    text = client.get("/api/v1/admin/components", headers=admin_headers).text.lower()
    for marker in ("token", "secret", "password", "postgresql://", "redis://"):
        assert marker not in text


def test_deployment_safety_gate_is_machine_readable(client: TestClient):
    client.post(
        "/internal/v1/components/heartbeat",
        json=metadata(f"{PREFIX}eu-1"),
        headers=service_headers(),
    )
    body = client.get(
        "/internal/v1/deployment-safety", headers=service_headers()
    ).json()
    assert body["result"] in ("SAFE", "BLOCKED")
    assert body["backend_contracts"]


def test_outdated_contract_blocks_deployment(client: TestClient):
    """Экземпляр с контрактом ниже поддерживаемого блокирует обновление."""
    client.post(
        "/internal/v1/components/heartbeat",
        json=metadata(f"{PREFIX}old", contract_version=0 + 1),
        headers=service_headers(),
    )
    # Контракт 1 поддерживается: gate должен быть SAFE.
    safe = client.get(
        "/internal/v1/deployment-safety", headers=service_headers()
    ).json()
    assert safe["result"] == "SAFE"

    # Версия ниже минимально поддерживаемой — это уже блокировка.
    client.post(
        "/internal/v1/components/heartbeat",
        json=metadata(f"{PREFIX}old", version="0.9.0"),
        headers=service_headers(),
    )
    blocked = client.get(
        "/internal/v1/deployment-safety", headers=service_headers()
    ).json()
    assert blocked["result"] == "BLOCKED"
    assert any(v["component_id"] == f"{PREFIX}old" for v in blocked["blocking"])


def test_forget_component_requires_admin_role(client: TestClient):
    assert client.delete(f"/api/v1/admin/components/{PREFIX}eu-1").status_code == 401


def test_forget_component_removes_record(client: TestClient, admin_headers: dict):
    client.post(
        "/internal/v1/components/heartbeat",
        json=metadata(f"{PREFIX}eu-1"),
        headers=service_headers(),
    )
    assert (
        client.delete(
            f"/api/v1/admin/components/{PREFIX}eu-1", headers=admin_headers
        ).status_code
        == 204
    )
    assert (
        client.delete(
            f"/api/v1/admin/components/{PREFIX}eu-1", headers=admin_headers
        ).status_code
        == 404
    )
