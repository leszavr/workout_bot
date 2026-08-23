"""Интеграционные проверки актуальности DB-состояния поверх JWT."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import apps.backend.auth as auth_module
from apps.backend.main import app
from src.infrastructure.config import DATABASE_URL

pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL is not set")

PREFIX = "revtest"
ENV_LOGIN = "envadmin"
ENV_PASSWORD = "env-admin-password-1"
USER_PASSWORD = "strong-password-01"


@pytest.fixture(scope="module", autouse=True)
def credentials():
    previous = (auth_module.ADMIN_LOGIN, auth_module.ADMIN_PASSWORD, auth_module.JWT_SECRET)
    auth_module.ADMIN_LOGIN = ENV_LOGIN
    auth_module.ADMIN_PASSWORD = ENV_PASSWORD
    auth_module.JWT_SECRET = "test-jwt-secret-with-sufficient-length-32b"
    yield
    auth_module.ADMIN_LOGIN, auth_module.ADMIN_PASSWORD, auth_module.JWT_SECRET = previous


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module", autouse=True)
def reset_engine_after_module():
    """Сбрасывает глобальный engine после модуля.

    TestClient держит свой event loop и закрывает его при выходе, а engine —
    глобальный: соединения из его пула остаются привязанными к уже закрытому
    loop'у. Следующий модуль с TestClient берёт то же соединение и падает на
    «attached to a different loop». Сброс синхронный: закрывать соединения
    здесь уже нечем, поэтому состояние просто обнуляется.
    """
    yield
    from src.infrastructure.persistence.postgres.db import reset_engine_state

    reset_engine_state()


@pytest.fixture(autouse=True)
async def cleanup():
    from sqlalchemy import delete, select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from src.infrastructure.persistence.postgres.models import AdminUserRow

    engine = create_async_engine(DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async with sessions() as session:
        async with session.begin():
            await session.execute(delete(AdminUserRow).where(AdminUserRow.login.like(f"{PREFIX}%")))
    yield
    async with sessions() as session:
        async with session.begin():
            ids = (await session.execute(select(AdminUserRow.id).where(AdminUserRow.login.like(f"{PREFIX}%")))).scalars().all()
            if ids:
                await session.execute(delete(AdminUserRow).where(AdminUserRow.id.in_(ids)))
    await engine.dispose()


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _login(client: TestClient, login: str, password: str) -> str:
    response = client.post("/api/v1/auth/login", json={"login": login, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def _create(client: TestClient, admin_token: str, login: str, role: str = "admin") -> dict:
    response = client.post(
        "/api/v1/admin/users",
        headers=_headers(admin_token),
        json={"login": login, "password": USER_PASSWORD, "role": role, "must_change_password": False},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_deactivation_revokes_existing_token_immediately(client: TestClient):
    env_token = _login(client, ENV_LOGIN, ENV_PASSWORD)
    # Env-admin is an emergency account and has no row in admin_users. Create a
    # second DB admin so the target admin is not the last DB admin.
    _create(client, env_token, f"{PREFIX}-keeper")
    user = _create(client, env_token, f"{PREFIX}-disabled")
    user_token = _login(client, user["login"], USER_PASSWORD)

    response = client.get("/api/v1/dashboard", headers=_headers(user_token))
    assert response.status_code == 200

    changed = client.patch(
        f"/api/v1/admin/users/{user['id']}",
        headers=_headers(env_token),
        json={"is_active": False},
    )
    assert changed.status_code == 200

    revoked = client.get("/api/v1/dashboard", headers=_headers(user_token))
    assert revoked.status_code == 401


def test_role_demotion_revokes_existing_write_privilege_immediately(client: TestClient):
    env_token = _login(client, ENV_LOGIN, ENV_PASSWORD)
    _create(client, env_token, f"{PREFIX}-keeper")
    user = _create(client, env_token, f"{PREFIX}-demoted")
    user_token = _login(client, user["login"], USER_PASSWORD)

    before = client.get("/api/v1/admin/users", headers=_headers(user_token))
    assert before.status_code == 200

    changed = client.patch(
        f"/api/v1/admin/users/{user['id']}",
        headers=_headers(env_token),
        json={"role": "viewer"},
    )
    assert changed.status_code == 200

    after = client.get("/api/v1/admin/users", headers=_headers(user_token))
    assert after.status_code == 403

    # Read-only access остаётся доступным для viewer.
    assert client.get("/api/v1/dashboard", headers=_headers(user_token)).status_code == 200
