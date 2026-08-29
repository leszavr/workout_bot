"""Тесты FastAPI API v1: health, profiles, users, exercises, auth.

Используют реальную PostgreSQL (DATABASE_URL) и TestClient.
"""
from __future__ import annotations

import os

import pytest
from sqlalchemy import select

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


@pytest.fixture(scope="module")
def viewer_headers(client: TestClient, auth_headers: dict) -> dict:
    """Токен наблюдателя: проверяет, что запрет на удаление серверный."""
    login = "test-api-viewer"
    password = "viewer-password-0123"
    client.post(
        "/api/v1/admin/users",
        headers=auth_headers,
        json={
            "login": login,
            "password": password,
            "role": "viewer",
            "must_change_password": False,
        },
    )
    token = client.post(
        "/api/v1/auth/login", json={"login": login, "password": password}
    ).json()["access_token"]
    yield {"Authorization": f"Bearer {token}"}

    async def _purge() -> None:
        from sqlalchemy import delete

        from src.infrastructure.persistence.postgres.db import get_session_factory
        from src.infrastructure.persistence.postgres.models import AdminUserRow

        # Привязки внешних аккаунтов удалит каскад (ON DELETE CASCADE).
        async with get_session_factory()() as session:
            async with session.begin():
                await session.execute(
                    delete(AdminUserRow).where(AdminUserRow.login == login)
                )

    client.portal.call(_purge)


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
            ProgramDeliveryRow,
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
                    # Доставки внешнего ключа не имеют: чистим сами, иначе
                    # маркер «отправлено» протёк бы в следующие прогоны.
                    await session.execute(
                        delete(ProgramDeliveryRow).where(
                            ProgramDeliveryRow.profile_id.in_(profile_ids)
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
    # Фактическая стратегия — часть контракта результата (Phase 1.2-C).
    assert body["generation"]["requested_generator"] == "deterministic"
    assert body["generation"]["actual_generator"] == "deterministic"
    assert body["generation"]["fallback_used"] is False
    assert body["generation"]["fallback_reason_code"] is None


def test_generate_program_ai_is_never_substituted(
    client: TestClient, auth_headers: dict
):
    """Явно выбранный ИИ не подменяется алгоритмом подбора.

    Главное следствие Phase 1.2-C: генерация идёт через оркестратор, но выбор
    администратора не переопределяется fallback'ом. Тест не зависит от того,
    настроен ли ИИ в окружении: проверяется инвариант, а не конкретный исход —
    либо программа собрана именно ИИ, либо администратор видит отказ и
    программы нет.
    """
    profile_id = "test-api-prog-ai"
    _create_test_profile(client, profile_id)

    response = client.post(
        f"/api/v1/profiles/{profile_id}/programs/generate",
        headers=auth_headers,
        json={"generator": "ai"},
    )

    if response.status_code == 200:
        generation = response.json()["generation"]
        assert generation["requested_generator"] == "ai"
        assert generation["actual_generator"] == "ai"
        assert generation["fallback_used"] is False
        return

    # Отказ ИИ — не ошибка запроса администратора: это 502.
    assert response.status_code == 502
    assert "ИИ" in response.json()["detail"]
    programs = client.get(
        f"/api/v1/profiles/{profile_id}/programs", headers=auth_headers
    ).json()
    assert programs["total"] == 0


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


def test_generate_program_key_reuse_with_other_generator_is_409(
    client: TestClient, auth_headers: dict
):
    """Тот же ключ с другим генератором — конфликт параметров, а не подмена.

    Ключ означает «это тот же запрос». Отдать программу, собранную другим
    генератором, значило бы отменить явный выбор администратора, а запустить
    новую генерацию под тем же ключом — разрушить идемпотентность.
    """
    profile_id = "test-api-prog-key-conflict"
    _create_test_profile(client, profile_id)
    key = "api-test-conflict-1"

    first = client.post(
        f"/api/v1/profiles/{profile_id}/programs/generate",
        headers=auth_headers,
        json={"generator": "deterministic", "idempotency_key": key},
    )
    assert first.status_code == 200

    conflict = client.post(
        f"/api/v1/profiles/{profile_id}/programs/generate",
        headers=auth_headers,
        json={"generator": "ai", "idempotency_key": key},
    )

    assert conflict.status_code == 409
    assert "deterministic" in conflict.json()["detail"]

    # Вторая программа не создана, подмены генератора не произошло.
    programs = client.get(
        f"/api/v1/profiles/{profile_id}/programs", headers=auth_headers
    ).json()
    assert programs["total"] == 1

    # Новый ключ — законный новый запрос, конфликт не «залипает».
    retry = client.post(
        f"/api/v1/profiles/{profile_id}/programs/generate",
        headers=auth_headers,
        json={"generator": "deterministic", "idempotency_key": "api-test-conflict-2"},
    )
    assert retry.status_code == 200


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


def test_generate_program_invalid_generator_is_422(
    client: TestClient, auth_headers: dict
):
    """Недопустимый генератор — ошибка запроса, а не 500."""
    profile_id = "test-api-prog-badgen"
    _create_test_profile(client, profile_id)

    response = client.post(
        f"/api/v1/profiles/{profile_id}/programs/generate",
        headers=auth_headers,
        json={"generator": "magic"},
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


def test_program_html_is_served(client: TestClient, auth_headers: dict):
    """Админка получает тот же документ, что уходит пользователю в Telegram."""
    profile_id = "test-api-prog-html"
    _create_test_profile(client, profile_id)
    generated = client.post(
        f"/api/v1/profiles/{profile_id}/programs/generate", headers=auth_headers
    ).json()
    program_id = generated["program"]["program_id"]

    response = client.get(f"/api/v1/programs/{program_id}/html", headers=auth_headers)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["content-disposition"].startswith("inline")
    assert "<!DOCTYPE html>" in response.text
    assert 'id="timerWrap"' in response.text


def test_program_html_download_disposition(client: TestClient, auth_headers: dict):
    profile_id = "test-api-prog-html-dl"
    _create_test_profile(client, profile_id)
    generated = client.post(
        f"/api/v1/profiles/{profile_id}/programs/generate", headers=auth_headers
    ).json()
    program_id = generated["program"]["program_id"]

    response = client.get(
        f"/api/v1/programs/{program_id}/html?download=true", headers=auth_headers
    )

    assert response.status_code == 200
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment")
    assert f"workout_program_{profile_id}_v1.html" in disposition


def test_program_html_not_found(client: TestClient, auth_headers: dict):
    response = client.get("/api/v1/programs/nonexistent-id/html", headers=auth_headers)
    assert response.status_code == 404


def test_program_html_requires_auth(client: TestClient):
    response = client.get("/api/v1/programs/any-id/html")
    assert response.status_code in (401, 403)


def test_get_exercise_by_external_id(client: TestClient, auth_headers: dict):
    listing = client.get("/api/v1/exercises?limit=1", headers=auth_headers).json()
    external_id = listing["items"][0]["external_id"]
    response = client.get(
        f"/api/v1/exercises/external/{external_id}", headers=auth_headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["external_id"] == external_id


# --- Маркеры исполнения анкеты, сортировка и удаление ------------------------------
#
# Раздел анкет накапливался: удаления не было вовсе, а по списку нельзя было
# понять, исполнена ли анкета. Маркеры вычисляются подзапросами по фактическому
# составу программ и доставок, а не хранятся в анкете отдельными флагами.


def _mark_delivered(client: TestClient, profile_id: str, program_id: str) -> None:
    """Отмечает программу отправленной пользователю в Telegram.

    Пишется через штатный репозиторий доставки, а не прямым INSERT: маркер
    обязан читать ровно то, что создаёт доставка.
    """
    from src.domain.enums import ProgramDeliveryStatus
    from src.infrastructure.persistence.postgres.db import get_session_factory
    from src.infrastructure.persistence.postgres.delivery_repository import (
        ProgramDeliveryRecord,
        ProgramDeliveryRepository,
    )

    async def _write() -> None:
        repo = ProgramDeliveryRepository(get_session_factory())
        record = await repo.create(
            ProgramDeliveryRecord(
                program_id=program_id,
                profile_id=profile_id,
                chat_id="900088",
                filename="program.html",
            )
        )
        record.status = ProgramDeliveryStatus.SENT
        record.sent_message_id = 4242
        await repo.update(record)

    client.portal.call(_write)


def _profile_item(client: TestClient, auth_headers: dict, profile_id: str) -> dict:
    body = client.get(
        f"/api/v1/profiles?search={profile_id}", headers=auth_headers
    ).json()
    return next(i for i in body["items"] if i["profile_id"] == profile_id)


def test_profile_markers_reflect_program_and_delivery(
    client: TestClient, auth_headers: dict
):
    """Маркеры показывают, исполнена ли анкета."""
    profile_id = "test-api-prog-markers"
    _create_test_profile(client, profile_id)

    fresh = _profile_item(client, auth_headers, profile_id)
    assert fresh["has_program"] is False
    assert fresh["delivered"] is False
    assert fresh["delivered_at"] is None
    assert fresh["delivery_status"] is None

    generated = client.post(
        f"/api/v1/profiles/{profile_id}/programs/generate", headers=auth_headers
    ).json()
    program_id = generated["program"]["program_id"]

    with_program = _profile_item(client, auth_headers, profile_id)
    assert with_program["has_program"] is True
    # Программа собрана, но пользователю ещё не уходила.
    assert with_program["delivered"] is False

    _mark_delivered(client, profile_id, program_id)

    delivered = _profile_item(client, auth_headers, profile_id)
    assert delivered["has_program"] is True
    assert delivered["delivered"] is True
    assert delivered["delivered_at"] is not None
    assert delivered["delivery_status"] == "sent"


def test_profile_filters_by_markers(client: TestClient, auth_headers: dict):
    profile_id = "test-api-prog-filter"
    _create_test_profile(client, profile_id)

    def ids(query: str) -> set[str]:
        body = client.get(f"/api/v1/profiles?{query}", headers=auth_headers).json()
        return {i["profile_id"] for i in body["items"]}

    # Анкета без программы: попадает в «без программы», не попадает в обратный.
    assert profile_id in ids(f"search={profile_id}&generated=false")
    assert profile_id not in ids(f"search={profile_id}&generated=true")

    client.post(
        f"/api/v1/profiles/{profile_id}/programs/generate", headers=auth_headers
    )

    assert profile_id in ids(f"search={profile_id}&generated=true")
    assert profile_id not in ids(f"search={profile_id}&generated=false")
    # Программа не отправлена, поэтому фильтр по доставке её не покажет.
    assert profile_id in ids(f"search={profile_id}&delivered=false")
    assert profile_id not in ids(f"search={profile_id}&delivered=true")


def test_profile_sorting_groups_by_markers(client: TestClient, auth_headers: dict):
    """Сортировка ставит нужную группу первой, внутри группы — новые сверху."""
    plain = "test-api-prog-sort-plain"
    with_program = "test-api-prog-sort-ready"
    _create_test_profile(client, plain)
    _create_test_profile(client, with_program)
    client.post(
        f"/api/v1/profiles/{with_program}/programs/generate", headers=auth_headers
    )

    def order(sort: str) -> list[str]:
        body = client.get(
            f"/api/v1/profiles?search=test-api-prog-sort&sort={sort}",
            headers=auth_headers,
        ).json()
        assert body["sort"] == sort
        return [i["profile_id"] for i in body["items"]]

    assert order("generated_first")[0] == with_program
    assert order("not_generated_first")[0] == plain

    by_date = order("created_asc")
    assert by_date.index(plain) < by_date.index(with_program)
    newest_first = order("created_desc")
    assert newest_first.index(with_program) < newest_first.index(plain)


def test_unknown_sort_is_rejected(client: TestClient, auth_headers: dict):
    """Порядок сортировки — белый список, а не имя колонки из запроса."""
    response = client.get("/api/v1/profiles?sort=data", headers=auth_headers)
    assert response.status_code == 422


def test_program_list_reports_delivery(client: TestClient, auth_headers: dict):
    profile_id = "test-api-prog-listdelivery"
    _create_test_profile(client, profile_id)
    generated = client.post(
        f"/api/v1/profiles/{profile_id}/programs/generate", headers=auth_headers
    ).json()
    program_id = generated["program"]["program_id"]

    before = client.get(
        f"/api/v1/profiles/{profile_id}/programs", headers=auth_headers
    ).json()
    assert before["items"][0]["delivered"] is False

    _mark_delivered(client, profile_id, program_id)

    after = client.get(
        f"/api/v1/profiles/{profile_id}/programs", headers=auth_headers
    ).json()
    assert after["items"][0]["delivered"] is True


def test_delete_endpoints_require_auth(client: TestClient):
    assert client.delete("/api/v1/profiles/any").status_code in (401, 403)
    assert client.delete("/api/v1/programs/any").status_code in (401, 403)


def test_delete_endpoints_require_admin_role(
    client: TestClient, viewer_headers: dict
):
    """Наблюдатель не удаляет данные: ограничение серверное, не только в UI."""
    assert client.delete("/api/v1/profiles/any", headers=viewer_headers).status_code == 403
    assert client.delete("/api/v1/programs/any", headers=viewer_headers).status_code == 403


def test_profile_with_programs_cannot_be_deleted(
    client: TestClient, auth_headers: dict
):
    """Анкету заполнял человек: её нельзя потерять из-за одного клика."""
    profile_id = "test-api-prog-delblocked"
    _create_test_profile(client, profile_id)
    client.post(
        f"/api/v1/profiles/{profile_id}/programs/generate", headers=auth_headers
    )

    response = client.delete(f"/api/v1/profiles/{profile_id}", headers=auth_headers)

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["blockers"][0]["type"] == "workout_program"
    assert detail["blockers"][0]["count"] >= 1
    # Анкета на месте: отказ произошёл до удаления.
    assert client.get(f"/api/v1/profiles/{profile_id}", headers=auth_headers).status_code == 200


def test_program_delete_removes_all_versions(client: TestClient, auth_headers: dict):
    profile_id = "test-api-prog-delprog"
    _create_test_profile(client, profile_id)
    first = client.post(
        f"/api/v1/profiles/{profile_id}/programs/generate", headers=auth_headers
    ).json()
    program_id = first["program"]["program_id"]
    # Вторая сборка добавляет версию той же программы.
    client.post(
        f"/api/v1/profiles/{profile_id}/programs/generate", headers=auth_headers
    )

    assert (
        client.delete(f"/api/v1/programs/{program_id}", headers=auth_headers).status_code
        == 204
    )
    assert (
        client.get(f"/api/v1/programs/{program_id}", headers=auth_headers).status_code
        == 404
    )
    remaining = client.get(
        f"/api/v1/profiles/{profile_id}/programs", headers=auth_headers
    ).json()
    assert program_id not in {i["program_id"] for i in remaining["items"]}


def test_profile_becomes_deletable_after_programs_removed(
    client: TestClient, auth_headers: dict
):
    """Заявленный порядок: сначала программы, потом анкета."""
    profile_id = "test-api-prog-delorder"
    _create_test_profile(client, profile_id)
    generated = client.post(
        f"/api/v1/profiles/{profile_id}/programs/generate", headers=auth_headers
    ).json()
    program_id = generated["program"]["program_id"]
    _mark_delivered(client, profile_id, program_id)

    assert (
        client.delete(f"/api/v1/profiles/{profile_id}", headers=auth_headers).status_code
        == 409
    )
    assert (
        client.delete(f"/api/v1/programs/{program_id}", headers=auth_headers).status_code
        == 204
    )
    assert (
        client.delete(f"/api/v1/profiles/{profile_id}", headers=auth_headers).status_code
        == 204
    )
    assert (
        client.get(f"/api/v1/profiles/{profile_id}", headers=auth_headers).status_code
        == 404
    )
    # Анкета исчезла и из списка, а не только из детального ответа.
    listed = client.get(
        f"/api/v1/profiles?search={profile_id}", headers=auth_headers
    ).json()
    assert profile_id not in {i["profile_id"] for i in listed["items"]}


def test_deleting_missing_entities_returns_404(client: TestClient, auth_headers: dict):
    assert (
        client.delete("/api/v1/profiles/absent-profile", headers=auth_headers).status_code
        == 404
    )
    assert (
        client.delete("/api/v1/programs/absent-program", headers=auth_headers).status_code
        == 404
    )
