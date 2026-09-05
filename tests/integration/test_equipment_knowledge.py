"""Интеграционные тесты Gym Knowledge Base: словарь, требования, health, Explorer.

Требуют DATABASE_URL с применёнными миграциями. Проверяется то, что нельзя
проверить unit-тестами: миграции создали словарь, импорт значений каталога
ничего не потерял, серверные фильтры Explorer действительно серверные, а Admin
CRUD не позволяет ломать ссылочную целостность.

Тесты создают собственные записи с префиксом `test_` и удаляют их за собой:
рабочий словарь и каталог упражнений не изменяются.
"""
from __future__ import annotations

import pytest

from fastapi.testclient import TestClient  # noqa: E402

import apps.backend.auth as auth_module  # noqa: E402
from apps.backend.main import app  # noqa: E402
from src.infrastructure.config import DATABASE_URL  # noqa: E402

pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL is not set")

auth_module.ADMIN_LOGIN = "admin"
auth_module.ADMIN_PASSWORD = "test-admin-password"
auth_module.JWT_SECRET = "test-jwt-secret-with-sufficient-length-32b"

KB = "/api/v1/admin/knowledge"
TEST_EQUIPMENT_ID = "test_kb_machine"
TEST_PROFILE_KEY = "test-kb-profile"

# Записи начального словаря, на которые опираются тесты. Они поставляются
# миграцией 0015 и являются частью контракта схемы, а не случайными данными.
SEED_BARBELL = "barbell"
SEED_DUMBBELL = "dumbbell"
SEED_BODYWEIGHT = "bodyweight"


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


@pytest.fixture(scope="module")
def viewer_headers(client: TestClient, auth_headers: dict) -> dict:
    """Токен наблюдателя: запрет на изменение словаря обязан быть серверным."""
    login = "test-kb-viewer"
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

        async with get_session_factory()() as session:
            async with session.begin():
                await session.execute(
                    delete(AdminUserRow).where(AdminUserRow.login == login)
                )

    client.portal.call(_purge)


@pytest.fixture(autouse=True)
def cleanup_test_equipment(client: TestClient):
    """Удаляет записи, созданные тестами, до и после каждого теста."""

    async def _purge() -> None:
        from sqlalchemy import delete

        from src.infrastructure.persistence.postgres.db import get_session_factory
        from src.infrastructure.persistence.postgres.models import (
            EquipmentAliasRow,
            EquipmentCapabilityLinkRow,
            EquipmentItemRow,
            EquipmentProfileRow,
            ExerciseEquipmentRequirementRow,
        )

        async with get_session_factory()() as session:
            async with session.begin():
                await session.execute(
                    delete(ExerciseEquipmentRequirementRow).where(
                        ExerciseEquipmentRequirementRow.equipment_id
                        == TEST_EQUIPMENT_ID
                    )
                )
                await session.execute(
                    delete(EquipmentAliasRow).where(
                        EquipmentAliasRow.equipment_id == TEST_EQUIPMENT_ID
                    )
                )
                await session.execute(
                    delete(EquipmentCapabilityLinkRow).where(
                        EquipmentCapabilityLinkRow.equipment_id == TEST_EQUIPMENT_ID
                    )
                )
                await session.execute(
                    delete(EquipmentItemRow).where(
                        EquipmentItemRow.equipment_id == TEST_EQUIPMENT_ID
                    )
                )
                await session.execute(
                    delete(EquipmentProfileRow).where(
                        EquipmentProfileRow.profile_key == TEST_PROFILE_KEY
                    )
                )

    client.portal.call(_purge)
    yield
    client.portal.call(_purge)


def _first_exercise(client: TestClient, headers: dict) -> dict:
    response = client.get("/api/v1/exercises?limit=1", headers=headers)
    assert response.status_code == 200
    items = response.json()["items"]
    if not items:
        pytest.skip("Каталог упражнений пуст")
    return items[0]


# --- Миграции и seed ------------------------------------------------------------


def test_seed_vocabulary_is_present(client: TestClient, auth_headers: dict):
    """Словарь поставляется миграцией: compatibility engine без него не работает."""
    response = client.get(f"{KB}/equipment?limit=200", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    ids = {item["equipment_id"] for item in body["items"]}
    assert {SEED_BARBELL, SEED_DUMBBELL, SEED_BODYWEIGHT} <= ids
    assert body["total"] >= 50


def test_capabilities_are_present(client: TestClient, auth_headers: dict):
    response = client.get(f"{KB}/capabilities", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["total"] >= 20
    ids = {item["capability_id"] for item in body["items"]}
    assert "adjustable_resistance" in ids
    assert "incline_support" in ids


def test_seed_equipment_has_aliases_and_capabilities(
    client: TestClient, auth_headers: dict
):
    """Синонимы — данные, а не код: без них сопоставление невозможно."""
    response = client.get(f"{KB}/equipment/{SEED_BARBELL}", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    aliases = {item["alias"] for item in body["aliases"]}
    assert "barbell" in aliases
    assert any(item["match_mode"] == "stem" for item in body["aliases"])
    assert "free_weight" in body["capabilities"]


def test_categories_endpoint_returns_counts(client: TestClient, auth_headers: dict):
    response = client.get(f"{KB}/equipment/categories", headers=auth_headers)
    assert response.status_code == 200
    items = response.json()["items"]
    assert items
    assert all(item["count"] > 0 for item in items)


# --- Импорт данных каталога -----------------------------------------------------


def test_catalog_import_produced_requirements(client: TestClient, auth_headers: dict):
    """Значения `exercises.equipment` переведены в нормализованные требования."""
    health = client.get(f"{KB}/health", headers=auth_headers).json()
    assert health["exercises_total"] > 0
    assert health["equipment_known"] > 0
    assert health["requirements_total"] >= health["equipment_known"]


def test_existing_exercise_data_survived_migration(
    client: TestClient, auth_headers: dict
):
    """Старое поле `equipment` не удалено: оно вход для повторного импорта."""
    exercise = _first_exercise(client, auth_headers)
    detail = client.get(
        f"/api/v1/exercises/{exercise['id']}", headers=auth_headers
    ).json()
    assert "equipment" in detail
    assert isinstance(detail["equipment"], list)


def test_unmapped_values_are_recorded_not_lost(client: TestClient, auth_headers: dict):
    """`other` не превращается в «оборудование не нужно», а остаётся видимым."""
    response = client.get(f"{KB}/unmapped?limit=5", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    if body["total"] == 0:
        pytest.skip("Незакрытых значений нет: каталог полностью сопоставлен")
    assert all(
        item["reason"] in {"ambiguous", "unmapped"} for item in body["items"]
    )


def test_health_counters_are_consistent(client: TestClient, auth_headers: dict):
    health = client.get(f"{KB}/health", headers=auth_headers).json()
    assert (
        health["equipment_known"] + health["equipment_unknown"]
        == health["exercises_total"]
    )
    assert (
        health["equipment_confirmed"] + health["equipment_inferred"]
        == health["equipment_known"]
    )
    assert health["orphan_equipment_references"] == 0
    assert health["invalid_capability_references"] == 0
    assert health["impossible_requirement_combinations"] == 0


# --- Admin CRUD -----------------------------------------------------------------


def _create_payload(**overrides) -> dict:
    payload = {
        "equipment_id": TEST_EQUIPMENT_ID,
        "name": "Test KB machine",
        "name_ru": "Тестовый тренажёр",
        "category": "machine",
        "capabilities": ["fixed_path", "adjustable_resistance"],
        "aliases": [
            {"alias": "test kb machine", "match_mode": "exact"},
            {"alias": "тестовый тренаж", "match_mode": "stem"},
        ],
        "is_active": True,
    }
    payload.update(overrides)
    return payload


def test_create_and_read_equipment(client: TestClient, auth_headers: dict):
    """Добавление тренажёра — вставка данных, а не изменение кода."""
    response = client.post(f"{KB}/equipment", headers=auth_headers, json=_create_payload())
    assert response.status_code == 201
    body = response.json()
    assert body["equipment_id"] == TEST_EQUIPMENT_ID
    assert set(body["capabilities"]) == {"fixed_path", "adjustable_resistance"}
    assert len(body["aliases"]) == 2

    read = client.get(f"{KB}/equipment/{TEST_EQUIPMENT_ID}", headers=auth_headers)
    assert read.status_code == 200
    assert read.json()["exercise_count"] == 0


def test_create_duplicate_is_rejected(client: TestClient, auth_headers: dict):
    client.post(f"{KB}/equipment", headers=auth_headers, json=_create_payload())
    again = client.post(f"{KB}/equipment", headers=auth_headers, json=_create_payload())
    assert again.status_code == 409


def test_create_with_unknown_capability_is_rejected(
    client: TestClient, auth_headers: dict
):
    """Ссылка на несуществующую возможность делает требование невыполнимым."""
    response = client.post(
        f"{KB}/equipment",
        headers=auth_headers,
        json=_create_payload(capabilities=["no_such_capability"]),
    )
    assert response.status_code == 422


def test_invalid_equipment_id_is_rejected(client: TestClient, auth_headers: dict):
    response = client.post(
        f"{KB}/equipment",
        headers=auth_headers,
        json=_create_payload(equipment_id="Test Machine"),
    )
    assert response.status_code == 422


def test_patch_equipment_replaces_capabilities_and_aliases(
    client: TestClient, auth_headers: dict
):
    client.post(f"{KB}/equipment", headers=auth_headers, json=_create_payload())
    response = client.patch(
        f"{KB}/equipment/{TEST_EQUIPMENT_ID}",
        headers=auth_headers,
        json={
            "name_ru": "Обновлённый тренажёр",
            "capabilities": ["fixed_path"],
            "aliases": [{"alias": "updated alias", "match_mode": "exact"}],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["name_ru"] == "Обновлённый тренажёр"
    assert body["capabilities"] == ["fixed_path"]
    assert [a["alias"] for a in body["aliases"]] == ["updated alias"]


def test_deactivate_keeps_record(client: TestClient, auth_headers: dict):
    client.post(f"{KB}/equipment", headers=auth_headers, json=_create_payload())
    response = client.post(
        f"{KB}/equipment/{TEST_EQUIPMENT_ID}/deactivate", headers=auth_headers
    )
    assert response.status_code == 200
    assert response.json()["is_active"] is False
    # Запись остаётся доступной для чтения: ссылки на неё не разорваны.
    assert (
        client.get(f"{KB}/equipment/{TEST_EQUIPMENT_ID}", headers=auth_headers).status_code
        == 200
    )


def test_delete_unused_equipment(client: TestClient, auth_headers: dict):
    client.post(f"{KB}/equipment", headers=auth_headers, json=_create_payload())
    response = client.delete(
        f"{KB}/equipment/{TEST_EQUIPMENT_ID}", headers=auth_headers
    )
    assert response.status_code == 204
    assert (
        client.get(f"{KB}/equipment/{TEST_EQUIPMENT_ID}", headers=auth_headers).status_code
        == 404
    )


def test_delete_used_equipment_is_blocked(client: TestClient, auth_headers: dict):
    """Удаление используемого оборудования запрещено: есть деактивация."""
    client.post(f"{KB}/equipment", headers=auth_headers, json=_create_payload())
    exercise = _first_exercise(client, auth_headers)
    put = client.put(
        f"{KB}/exercises/{exercise['external_id']}/requirements"
        f"?source={exercise['source']}",
        headers=auth_headers,
        json={
            "requirements": [
                {"equipment_id": TEST_EQUIPMENT_ID, "requirement": "required"}
            ]
        },
    )
    assert put.status_code == 200
    try:
        response = client.delete(
            f"{KB}/equipment/{TEST_EQUIPMENT_ID}", headers=auth_headers
        )
        assert response.status_code == 409
    finally:
        # Восстанавливаем исходные требования упражнения: тест не должен менять
        # рабочие данные каталога.
        client.put(
            f"{KB}/exercises/{exercise['external_id']}/requirements"
            f"?source={exercise['source']}",
            headers=auth_headers,
            json={"requirements": []},
        )


def test_viewer_cannot_modify_vocabulary(client: TestClient, viewer_headers: dict):
    """Словарь определяет результат подбора: наблюдатель менять его не может."""
    assert (
        client.post(
            f"{KB}/equipment", headers=viewer_headers, json=_create_payload()
        ).status_code
        == 403
    )
    assert (
        client.delete(
            f"{KB}/equipment/{SEED_BARBELL}", headers=viewer_headers
        ).status_code
        == 403
    )


def test_viewer_can_read_vocabulary(client: TestClient, viewer_headers: dict):
    assert client.get(f"{KB}/equipment", headers=viewer_headers).status_code == 200
    assert client.get(f"{KB}/health", headers=viewer_headers).status_code == 200


def test_knowledge_requires_auth(client: TestClient):
    assert client.get(f"{KB}/equipment").status_code == 401
    assert client.get(f"{KB}/health").status_code == 401


# --- Требования упражнения ------------------------------------------------------


def test_requirements_roundtrip(client: TestClient, auth_headers: dict):
    exercise = _first_exercise(client, auth_headers)
    url = (
        f"{KB}/exercises/{exercise['external_id']}/requirements"
        f"?source={exercise['source']}"
    )
    original = client.get(url, headers=auth_headers).json()["items"]
    try:
        response = client.put(
            url,
            headers=auth_headers,
            json={
                "requirements": [
                    {"equipment_id": SEED_BARBELL, "requirement": "required"},
                    {
                        "equipment_id": SEED_DUMBBELL,
                        "requirement": "alternative",
                        "alternative_group": 1,
                    },
                    {"capability_id": "flat_support", "requirement": "optional"},
                ]
            },
        )
        assert response.status_code == 200
        items = response.json()["items"]
        assert len(items) == 3
        assert all(item["source"] == "admin" for item in items)
    finally:
        client.put(
            url,
            headers=auth_headers,
            json={
                "requirements": [
                    {
                        "equipment_id": item["equipment_id"],
                        "capability_id": item["capability_id"],
                        "requirement": item["requirement"],
                        "alternative_group": item["alternative_group"],
                        "confidence": item["confidence"],
                        "notes": item["notes"],
                    }
                    for item in original
                ]
            },
        )


def test_requirement_with_both_targets_is_rejected(
    client: TestClient, auth_headers: dict
):
    exercise = _first_exercise(client, auth_headers)
    response = client.put(
        f"{KB}/exercises/{exercise['external_id']}/requirements"
        f"?source={exercise['source']}",
        headers=auth_headers,
        json={
            "requirements": [
                {
                    "equipment_id": SEED_BARBELL,
                    "capability_id": "free_weight",
                    "requirement": "required",
                }
            ]
        },
    )
    assert response.status_code == 422


def test_alternative_without_group_is_rejected(client: TestClient, auth_headers: dict):
    exercise = _first_exercise(client, auth_headers)
    response = client.put(
        f"{KB}/exercises/{exercise['external_id']}/requirements"
        f"?source={exercise['source']}",
        headers=auth_headers,
        json={
            "requirements": [
                {"equipment_id": SEED_BARBELL, "requirement": "alternative"}
            ]
        },
    )
    assert response.status_code == 422


def test_impossible_combination_is_rejected(client: TestClient, auth_headers: dict):
    """Снаряд и его отсутствие не могут быть обязательны одновременно."""
    exercise = _first_exercise(client, auth_headers)
    response = client.put(
        f"{KB}/exercises/{exercise['external_id']}/requirements"
        f"?source={exercise['source']}",
        headers=auth_headers,
        json={
            "requirements": [
                {"equipment_id": SEED_BODYWEIGHT, "requirement": "required"},
                {"equipment_id": SEED_BARBELL, "requirement": "required"},
            ]
        },
    )
    assert response.status_code == 422


def test_requirements_for_unknown_exercise_are_rejected(
    client: TestClient, auth_headers: dict
):
    response = client.put(
        f"{KB}/exercises/No_Such_Exercise/requirements",
        headers=auth_headers,
        json={
            "requirements": [
                {"equipment_id": SEED_BARBELL, "requirement": "required"}
            ]
        },
    )
    assert response.status_code == 422


def test_unknown_equipment_reference_is_rejected(
    client: TestClient, auth_headers: dict
):
    exercise = _first_exercise(client, auth_headers)
    response = client.put(
        f"{KB}/exercises/{exercise['external_id']}/requirements"
        f"?source={exercise['source']}",
        headers=auth_headers,
        json={
            "requirements": [
                {"equipment_id": "no_such_equipment", "requirement": "required"}
            ]
        },
    )
    assert response.status_code == 422


# --- Совместимость --------------------------------------------------------------


def test_compatibility_endpoint_returns_status_per_exercise(
    client: TestClient, auth_headers: dict
):
    response = client.get("/api/v1/exercises?limit=5", headers=auth_headers)
    external_ids = [item["external_id"] for item in response.json()["items"]]
    if not external_ids:
        pytest.skip("Каталог упражнений пуст")
    result = client.post(
        f"{KB}/compatibility",
        headers=auth_headers,
        json={
            "exercise_external_ids": external_ids,
            "available_equipment": [SEED_BARBELL, SEED_DUMBBELL],
        },
    )
    assert result.status_code == 200
    items = result.json()["items"]
    assert len(items) == len(external_ids)
    assert all(
        item["status"] in {"compatible", "incompatible", "unknown"} for item in items
    )


def test_compatibility_against_profile(client: TestClient, auth_headers: dict):
    """Профиль — источник доступности, включая различение «нет» и «неизвестно»."""
    created = client.put(
        f"{KB}/profiles/{TEST_PROFILE_KEY}",
        headers=auth_headers,
        json={
            "profile_key": TEST_PROFILE_KEY,
            "owner_type": "gym",
            "name": "Тестовый зал",
            "items": [
                {"equipment_id": SEED_BARBELL, "availability": "available"},
                {"equipment_id": SEED_DUMBBELL, "availability": "unavailable"},
            ],
            "assume_unlisted_unavailable": False,
        },
    )
    assert created.status_code == 200
    assert len(created.json()["items"]) == 2

    exercise = _first_exercise(client, auth_headers)
    result = client.post(
        f"{KB}/compatibility",
        headers=auth_headers,
        json={
            "exercise_external_ids": [exercise["external_id"]],
            "profile_key": TEST_PROFILE_KEY,
        },
    )
    assert result.status_code == 200
    assert result.json()["items"][0]["status"] in {
        "compatible",
        "incompatible",
        "unknown",
    }


def test_compatibility_with_unknown_profile_is_404(
    client: TestClient, auth_headers: dict
):
    exercise = _first_exercise(client, auth_headers)
    response = client.post(
        f"{KB}/compatibility",
        headers=auth_headers,
        json={
            "exercise_external_ids": [exercise["external_id"]],
            "profile_key": "no-such-profile",
        },
    )
    assert response.status_code == 404


def test_profile_with_unknown_equipment_is_rejected(
    client: TestClient, auth_headers: dict
):
    response = client.put(
        f"{KB}/profiles/{TEST_PROFILE_KEY}",
        headers=auth_headers,
        json={
            "profile_key": TEST_PROFILE_KEY,
            "name": "Тестовый зал",
            "items": [{"equipment_id": "no_such_equipment"}],
        },
    )
    assert response.status_code == 422


# --- Equipment-aware Exercise Explorer ------------------------------------------


def test_explorer_filters_by_knowledge_equipment(
    client: TestClient, auth_headers: dict
):
    """Фильтр по canonical ID словаря выполняется в базе, а не на клиенте."""
    response = client.get(
        f"/api/v1/exercises?equipment_id={SEED_BARBELL}&limit=5",
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    if body["total"] == 0:
        pytest.skip("Требования по штанге не заполнены")
    assert body["total"] < 873
    assert len(body["items"]) <= 5


def test_explorer_filters_by_capability(client: TestClient, auth_headers: dict):
    response = client.get(
        "/api/v1/exercises?capability=free_weight&limit=5", headers=auth_headers
    )
    assert response.status_code == 200
    assert response.json()["total"] >= 0


def test_explorer_unknown_and_known_partition_catalog(
    client: TestClient, auth_headers: dict
):
    """Заполненные и незаполненные требования вместе дают весь каталог."""
    total = client.get(
        "/api/v1/exercises?is_active=all&limit=1", headers=auth_headers
    ).json()["total"]
    known = client.get(
        "/api/v1/exercises?is_active=all&equipment_knowledge=known&limit=1",
        headers=auth_headers,
    ).json()["total"]
    unknown = client.get(
        "/api/v1/exercises?is_active=all&equipment_knowledge=unknown&limit=1",
        headers=auth_headers,
    ).json()["total"]
    assert known + unknown == total


def test_explorer_filter_with_no_matches_returns_empty(
    client: TestClient, auth_headers: dict
):
    """Пустой результат фильтра — это ноль строк, а не весь каталог."""
    client.post(f"{KB}/equipment", headers=auth_headers, json=_create_payload())
    response = client.get(
        f"/api/v1/exercises?equipment_id={TEST_EQUIPMENT_ID}&limit=5",
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 0
    assert body["items"] == []


def test_explorer_returns_compatibility_when_equipment_given(
    client: TestClient, auth_headers: dict
):
    response = client.get(
        f"/api/v1/exercises?available_equipment={SEED_BARBELL}&limit=5",
        headers=auth_headers,
    )
    assert response.status_code == 200
    items = response.json()["items"]
    if not items:
        pytest.skip("Каталог упражнений пуст")
    assert all(item["compatibility"] is not None for item in items)
    assert all(
        item["compatibility"]["status"] in {"compatible", "incompatible", "unknown"}
        for item in items
    )


def test_explorer_omits_compatibility_without_equipment(
    client: TestClient, auth_headers: dict
):
    """Без перечня оборудования статус не вычисляется и не выдумывается."""
    response = client.get("/api/v1/exercises?limit=3", headers=auth_headers)
    items = response.json()["items"]
    if not items:
        pytest.skip("Каталог упражнений пуст")
    assert all(item["compatibility"] is None for item in items)


def test_explorer_server_side_pagination_is_preserved(
    client: TestClient, auth_headers: dict
):
    """Каталог не выгружается целиком: страница ограничена сервером."""
    first = client.get("/api/v1/exercises?limit=2&offset=0", headers=auth_headers).json()
    second = client.get("/api/v1/exercises?limit=2&offset=2", headers=auth_headers).json()
    if first["total"] < 4:
        pytest.skip("В каталоге меньше четырёх упражнений")
    assert len(first["items"]) == 2
    assert len(second["items"]) == 2
    assert {i["id"] for i in first["items"]} & {i["id"] for i in second["items"]} == set()
    assert first["total"] == second["total"]


def test_explorer_facets_still_work(client: TestClient, auth_headers: dict):
    response = client.get(
        "/api/v1/exercises?with_facets=true&limit=1", headers=auth_headers
    )
    assert response.status_code == 200
    facets = response.json()["facets"]
    assert "equipment" in facets
    assert "primary_muscles" in facets


def test_explorer_compatibility_filter_reports_page_count(
    client: TestClient, auth_headers: dict
):
    response = client.get(
        f"/api/v1/exercises?available_equipment={SEED_BARBELL}"
        "&compatibility=compatible&limit=10",
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert "filtered_page_count" in body
    assert body["filtered_page_count"] == len(body["items"])


# --- Альтернативы ---------------------------------------------------------------


def test_alternatives_endpoint(client: TestClient, auth_headers: dict):
    exercise = _first_exercise(client, auth_headers)
    response = client.get(
        f"{KB}/exercises/{exercise['external_id']}/alternatives"
        f"?source={exercise['source']}",
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["exercise_external_id"] == exercise["external_id"]
    for item in body["items"]:
        assert item["substitution"] in {"exact", "similar", "partial"}
        assert 0.0 <= item["score"] <= 1.0
        assert item["alternative_external_id"] != exercise["external_id"]


# --- Специализация оборудования -------------------------------------------------


def test_seed_declares_specializations(client: TestClient, auth_headers: dict):
    """Родовые значения каталога закрываются частными тренажёрами.

    У 67 упражнений каталога оборудование указано родовым словом `machine`, и без
    этой связи человек с жимом ногами получал бы «не подходит» на упражнение
    «жим ногами».
    """
    response = client.get(f"{KB}/equipment/leg_press", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["specializes"] == "resistance_machine"


def test_specialization_closes_generic_requirement(
    client: TestClient, auth_headers: dict
):
    exercise = client.get(
        "/api/v1/exercises?equipment=machine&limit=1", headers=auth_headers
    ).json()["items"]
    if not exercise:
        pytest.skip("В каталоге нет упражнений с родовым значением machine")
    external_id = exercise[0]["external_id"]
    result = client.post(
        f"{KB}/compatibility",
        headers=auth_headers,
        json={
            "exercise_external_ids": [external_id],
            "available_equipment": ["leg_press"],
            "assume_unlisted_unavailable": True,
        },
    )
    assert result.status_code == 200
    item = result.json()["items"][0]
    assert item["status"] == "compatible"
    assert item["reason"] == "specialized_equipment_available"


def test_self_specialization_is_rejected(client: TestClient, auth_headers: dict):
    response = client.post(
        f"{KB}/equipment",
        headers=auth_headers,
        json=_create_payload(specializes=TEST_EQUIPMENT_ID),
    )
    assert response.status_code == 422


def test_unknown_specialization_target_is_rejected(
    client: TestClient, auth_headers: dict
):
    response = client.post(
        f"{KB}/equipment",
        headers=auth_headers,
        json=_create_payload(specializes="no_such_equipment"),
    )
    assert response.status_code == 422


def test_specialization_cycle_is_rejected(client: TestClient, auth_headers: dict):
    """Цикл сделал бы «частный случай» бессмысленным: записи закрывали бы друг друга."""
    created = client.post(
        f"{KB}/equipment",
        headers=auth_headers,
        json=_create_payload(specializes="resistance_machine"),
    )
    assert created.status_code == 201
    response = client.patch(
        f"{KB}/equipment/resistance_machine",
        headers=auth_headers,
        json={"specializes": TEST_EQUIPMENT_ID},
    )
    assert response.status_code == 422
