"""Тесты FastAPI API v1: health, profiles, users, exercises, auth.

Используют реальную PostgreSQL (DATABASE_URL) и TestClient.
"""
from __future__ import annotations

import os

import pytest

from fastapi.testclient import TestClient  # noqa: E402

from apps.backend.main import app  # noqa: E402
import apps.backend.auth as auth_module  # noqa: E402
from src.infrastructure.config import DATABASE_URL  # noqa: E402

pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL is not set")

# Учётные данные администратора для тестов (config читается при импорте,
# поэтому подменяем значения непосредственно в модуле auth).
auth_module.ADMIN_LOGIN = "admin"
auth_module.ADMIN_PASSWORD = "test-admin-password"
auth_module.JWT_SECRET = "test-jwt-secret-with-sufficient-length-32b"


@pytest.fixture(scope="module")
def client() -> TestClient:
    # Контекстный менеджер фиксирует один event loop на все запросы,
    # иначе глобальный async-engine попадает в чужой loop.
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module", autouse=True)
def reset_engine_after_module():
    yield
    # TestClient использует собственный event loop; сбрасываем глобальный engine,
    # чтобы другие тестовые модули создали новый в своём loop.
    from src.infrastructure.persistence.postgres.db import reset_engine_state

    reset_engine_state()


@pytest.fixture(scope="module")
def auth_headers(client: TestClient) -> dict:
    response = client.post(
        "/api/v1/auth/login",
        json={"login": "admin", "password": "test-admin-password"},
    )
    assert response.status_code == 200
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_health(client: TestClient):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_ready(client: TestClient):
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json()["storage"] is True


def test_login_invalid(client: TestClient):
    response = client.post(
        "/api/v1/auth/login", json={"login": "admin", "password": "wrong"}
    )
    assert response.status_code == 401


def test_profiles_requires_auth(client: TestClient):
    response = client.get("/api/v1/profiles")
    assert response.status_code == 401


def test_list_profiles(client: TestClient, auth_headers: dict):
    response = client.get("/api/v1/profiles", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert "total" in body
    assert isinstance(body["items"], list)


def test_get_profile_not_found(client: TestClient, auth_headers: dict):
    response = client.get("/api/v1/profiles/nonexistent-id", headers=auth_headers)
    assert response.status_code == 404


def test_list_users(client: TestClient, auth_headers: dict):
    response = client.get("/api/v1/users", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert "total" in body
    assert isinstance(body["items"], list)


def test_list_exercises(client: TestClient, auth_headers: dict):
    response = client.get("/api/v1/exercises?limit=5", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["total"] >= 873  # импортированный каталог
    assert len(body["items"]) <= 5
    item = body["items"][0]
    assert "name" in item
    assert "equipment" in item


def test_exercises_search_filter(client: TestClient, auth_headers: dict):
    response = client.get("/api/v1/exercises?search=sit-up", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["total"] >= 1


def test_get_exercise(client: TestClient, auth_headers: dict):
    listing = client.get("/api/v1/exercises?limit=1", headers=auth_headers).json()
    exercise_id = listing["items"][0]["id"]
    response = client.get(f"/api/v1/exercises/{exercise_id}", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == exercise_id
    assert body["source"] == "leszavr/workout"
    assert "technique" in body


def test_dashboard(client: TestClient, auth_headers: dict):
    response = client.get("/api/v1/dashboard", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["exercises_total"] >= 873
    assert body["programs_total"] is None  # программ пока нет — без фиктивных данных
