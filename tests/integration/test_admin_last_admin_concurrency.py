"""Concurrency acceptance: два параллельных изменения последних admin."""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import apps.backend.auth as auth_module
from apps.backend.main import app
from src.errors import ProfilePersistenceError
from src.domain.auth import AdminRole
from src.infrastructure.config import DATABASE_URL
from src.infrastructure.persistence.postgres.admin_user_repository import AdminUserRepository
from src.infrastructure.persistence.postgres.models import AdminUserRow
from src.infrastructure.persistence.postgres.db import get_session_factory

pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL is not set")

PREFIX = "lastadminrace"
ENV_LOGIN = "envadmin"
ENV_PASSWORD = "env-admin-password-1"
PASSWORD = "strong-password-01"


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


@pytest.fixture(autouse=True)
async def cleanup():
    engine = create_async_engine(DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        async with session.begin():
            await session.execute(delete(AdminUserRow).where(AdminUserRow.login.like(f"{PREFIX}%")))
    yield
    async with sessions() as session:
        async with session.begin():
            await session.execute(delete(AdminUserRow).where(AdminUserRow.login.like(f"{PREFIX}%")))
    await engine.dispose()


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _login(client: TestClient, login: str, password: str) -> str:
    response = client.post("/api/v1/auth/login", json={"login": login, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def _create(client: TestClient, token: str, login: str) -> dict:
    response = client.post(
        "/api/v1/admin/users",
        headers=_headers(token),
        json={"login": login, "password": PASSWORD, "role": "admin", "must_change_password": False},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _demote(repo: AdminUserRepository, user_id: int):
    return await repo.update_guarded_last_admin(user_id, role=AdminRole.VIEWER.value)


def test_concurrent_demotions_leave_one_admin(client: TestClient):
    """При двух параллельных demote ровно один admin должен сохраниться."""
    env_token = _login(client, ENV_LOGIN, ENV_PASSWORD)
    first = _create(client, env_token, f"{PREFIX}-one")
    second = _create(client, env_token, f"{PREFIX}-two")
    repo = AdminUserRepository(get_session_factory())

    async def run():
        return await asyncio.gather(
            _demote(repo, first["id"]),
            _demote(repo, second["id"]),
            return_exceptions=True,
        )

    results = asyncio.run(run())
    failures = [r for r in results if isinstance(r, ProfilePersistenceError)]
    successes = [r for r in results if not isinstance(r, Exception)]

    assert len(failures) == 1
    assert len(successes) == 1

    async def count_admins():
        async with get_session_factory()() as session:
            return (
                await session.execute(
                    select(AdminUserRow.id).where(
                        AdminUserRow.login.like(f"{PREFIX}%"),
                        AdminUserRow.role == "admin",
                        AdminUserRow.is_active.is_(True),
                    )
                )
            ).scalars().all()

    remaining = asyncio.run(count_admins())
    assert len(remaining) == 1
