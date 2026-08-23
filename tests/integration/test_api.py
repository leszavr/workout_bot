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
    assert isinstance(body["programs_total"], int)


# --- Programs -----------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def cleanup_program_test_data():
    """Удаляет тестовые профили/программы (test-api-prog-*, user 900088) после модуля."""
    yield

    async def _purge() -> None:
        from sqlalchemy import delete, select

        from src.infrastructure.persistence.postgres.db import get_session_factory
        from src.infrastructure.persistence.postgres.models import (
            ConsentRow,
            GenerationJobRow,
            ProfileRow,
            UserRow,
            WorkoutProgramRow,
        )

        async with get_session_factory()() as session:
            async with session.begin():
                profile_ids = (
                    await session.execute(
                        select(ProfileRow.profile_id).where(
                            ProfileRow.profile_id.like("test-api-prog-%")
                        )
                    )
                ).scalars().all()
                if profile_ids:
                    # Operational-записи генерации ссылаются на программы,
                    # поэтому удаляются первыми.
                    await session.execute(
                        delete(GenerationJobRow).where(
                            GenerationJobRow.profile_id.in_(profile_ids)
                        )
                    )
                    await session.execute(
                        delete(WorkoutProgramRow).where(
                            WorkoutProgramRow.profile_id.in_(profile_ids)
                        )
                    )
                user_ids = (
                    await session.execute(
                        select(UserRow.id).where(UserRow.telegram_user_id == "900088")
                    )
                ).scalars().all()
                if user_ids:
                    await session.execute(
                        delete(ConsentRow).where(ConsentRow.user_id.in_(user_ids))
                    )
                    await session.execute(
                        delete(ProfileRow).where(ProfileRow.user_id.in_(user_ids))
                    )
                    await session.execute(
                        delete(UserRow).where(UserRow.id.in_(user_ids))
                    )

    # Глобальный engine привязан к loop'у модульного TestClient — сбрасываем,
    # чтобы purge создал новый engine в своём loop.
    from src.infrastructure.persistence.postgres.db import reset_engine_state

    reset_engine_state()
    _client = TestClient(app)
    with _client:
        _client.portal.call(_purge)
    reset_engine_state()


def _create_test_profile(client: TestClient, profile_id: str) -> None:
    """Создаёт тестовый профиль напрямую в БД (в event loop'е TestClient)."""
    from src.domain.enums import ExperienceLevel, PrimaryGoal, TrainingLocationType
    from src.domain.profile import FitnessProfile
    from src.infrastructure.persistence.postgres.db import get_session_factory
    from src.infrastructure.persistence.postgres.profile_repository import (
        PostgresProfileRepository,
    )

    profile = FitnessProfile(profile_id=profile_id)
    profile.source.bot_user_id = "900088"
    profile.source.telegram_username = "test_api_programs"
    profile.client.name = "Тест API"
    profile.goals.primary = PrimaryGoal.MUSCLE_GAIN
    profile.training_background.experience_level = ExperienceLevel.THREE_TWELVE_MONTHS
    profile.training_location.primary_location = TrainingLocationType.GYM
    profile.training_plan_preferences.sessions_per_week = 3

    async def _save() -> None:
        repo = PostgresProfileRepository(get_session_factory())
        await repo.save(profile)

    client.portal.call(_save)


def test_generate_program(client: TestClient, auth_headers: dict):
    profile_id = "test-api-prog-1"
    _create_test_profile(client, profile_id)
    response = client.post(
        f"/api/v1/profiles/{profile_id}/programs/generate", headers=auth_headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["program"]["status"] == "validated"
    assert body["program"]["profile_id"] == profile_id
    assert body["pool_stats"]["safe_allowed"] > 0
    assert body["pool_stats"]["total_exercises"] >= 873
    # Operational-состояние генерации доступно вызывающему (Phase 1.2-B).
    assert body["generation"]["status"] == "succeeded"
    assert body["generation"]["attempts"] == 1
    assert body["generation"]["reused_existing"] is False
    assert body["generation"]["last_error_code"] is None


def test_generate_program_with_same_idempotency_key_reuses_program(
    client: TestClient, auth_headers: dict
):
    """Повтор того же логического запроса не создаёт вторую программу."""
    profile_id = "test-api-prog-idem"
    _create_test_profile(client, profile_id)
    payload = {"generator": "deterministic", "idempotency_key": "api-test-key-1"}

    first = client.post(
        f"/api/v1/profiles/{profile_id}/programs/generate",
        headers=auth_headers,
        json=payload,
    )
    second = client.post(
        f"/api/v1/profiles/{profile_id}/programs/generate",
        headers=auth_headers,
        json=payload,
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["generation"]["reused_existing"] is True
    assert (
        second.json()["program"]["program_id"] == first.json()["program"]["program_id"]
    )
    assert second.json()["program"]["version"] == first.json()["program"]["version"]

    programs = client.get(
        f"/api/v1/profiles/{profile_id}/programs", headers=auth_headers
    ).json()
    assert programs["total"] == 1


def test_generate_program_without_key_creates_new_version(
    client: TestClient, auth_headers: dict
):
    """Явный повторный запрос администратора — законная новая генерация."""
    profile_id = "test-api-prog-explicit"
    _create_test_profile(client, profile_id)

    first = client.post(
        f"/api/v1/profiles/{profile_id}/programs/generate", headers=auth_headers
    ).json()
    second = client.post(
        f"/api/v1/profiles/{profile_id}/programs/generate", headers=auth_headers
    ).json()

    assert second["generation"]["reused_existing"] is False
    assert second["program"]["version"] == first["program"]["version"] + 1


def test_generate_program_missing_profile(client: TestClient, auth_headers: dict):
    response = client.post(
        "/api/v1/profiles/nonexistent-id/programs/generate", headers=auth_headers
    )
    assert response.status_code == 422


def test_list_programs(client: TestClient, auth_headers: dict):
    response = client.get("/api/v1/programs", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert "total" in body
    assert isinstance(body["items"], list)


def test_get_program(client: TestClient, auth_headers: dict):
    profile_id = "test-api-prog-2"
    _create_test_profile(client, profile_id)
    generated = client.post(
        f"/api/v1/profiles/{profile_id}/programs/generate", headers=auth_headers
    ).json()
    program_id = generated["program"]["program_id"]

    response = client.get(f"/api/v1/programs/{program_id}", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["program"]["program_id"] == program_id
    assert len(body["program"]["training_days"]) == 3
    assert isinstance(body["versions"], list)


def test_get_program_not_found(client: TestClient, auth_headers: dict):
    response = client.get("/api/v1/programs/nonexistent-id", headers=auth_headers)
    assert response.status_code == 404


def test_profile_programs(client: TestClient, auth_headers: dict):
    profile_id = "test-api-prog-3"
    _create_test_profile(client, profile_id)
    client.post(f"/api/v1/profiles/{profile_id}/programs/generate", headers=auth_headers)

    response = client.get(
        f"/api/v1/profiles/{profile_id}/programs", headers=auth_headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] >= 1
    assert body["items"][0]["profile_id"] == profile_id


def test_get_exercise_by_external_id(client: TestClient, auth_headers: dict):
    listing = client.get("/api/v1/exercises?limit=1", headers=auth_headers).json()
    external_id = listing["items"][0]["external_id"]
    response = client.get(
        f"/api/v1/exercises/external/{external_id}", headers=auth_headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["external_id"] == external_id
