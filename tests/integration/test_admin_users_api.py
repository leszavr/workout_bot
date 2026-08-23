"""API-тесты управления пользователями и авторизации.

Ключевые проверки безопасности:
- хеш пароля не возвращается ни в одном ответе;
- viewer не может выполнять изменяющие операции (запрет на сервере, а не в UI);
- пользователь с временным паролем не имеет доступа к API до его смены;
- нельзя удалить себя и нельзя остаться без активного администратора.

Требуют DATABASE_URL; иначе пропускаются.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, update

import apps.backend.auth as auth_module
from apps.backend.main import app
from src.infrastructure.config import DATABASE_URL
from src.infrastructure.persistence.postgres.models import (
    AdminIdentityRow,
    AdminUserRow,
    AIAuditEventRow,
)

pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL is not set")

# Аварийный env-администратор для этого модуля.
#
# Значения выставляются фикстурой, а НЕ на уровне модуля: другие тестовые
# модули задают свои ADMIN_LOGIN/ADMIN_PASSWORD теми же присваиваниями, и при
# импорте всего набора победил бы последний импортированный модуль. Фикстура
# применяет значения на время работы модуля и возвращает прежние.
ENV_ADMIN_LOGIN = "envadmin"
ENV_ADMIN_PASSWORD = "env-admin-password-1"

# Логины тестовых пользователей начинаются с этого префикса и вычищаются.
PREFIX = "ustest"
STRONG_PASSWORD = "strong-password-01"


@pytest.fixture(scope="module", autouse=True)
def env_admin_credentials():
    previous = (
        auth_module.ADMIN_LOGIN,
        auth_module.ADMIN_PASSWORD,
        auth_module.JWT_SECRET,
    )
    auth_module.ADMIN_LOGIN = ENV_ADMIN_LOGIN
    auth_module.ADMIN_PASSWORD = ENV_ADMIN_PASSWORD
    auth_module.JWT_SECRET = "test-jwt-secret-with-sufficient-length-32b"
    yield
    (
        auth_module.ADMIN_LOGIN,
        auth_module.ADMIN_PASSWORD,
        auth_module.JWT_SECRET,
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
async def cleanup_users():
    """Отдельный локальный engine: не конфликтует с loop'ом TestClient."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def _purge() -> None:
        async with sessions() as session:
            async with session.begin():
                user_ids = (
                    await session.execute(
                        select(AdminUserRow.id).where(
                            AdminUserRow.login.like(f"{PREFIX}%")
                        )
                    )
                ).scalars().all()
                if user_ids:
                    await session.execute(
                        delete(AdminIdentityRow).where(
                            AdminIdentityRow.user_id.in_(user_ids)
                        )
                    )
                await session.execute(
                    delete(AdminUserRow).where(AdminUserRow.login.like(f"{PREFIX}%"))
                )
                await session.execute(
                    delete(AIAuditEventRow).where(
                        AIAuditEventRow.entity_type == "admin_user"
                    )
                )

    await _purge()
    yield
    await _purge()
    await engine.dispose()


def _token(client: TestClient, login: str, password: str) -> str:
    response = client.post(
        "/api/v1/auth/login", json={"login": login, "password": password}
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


@pytest.fixture
async def sole_database_admin():
    """Гарантирует, что кроме тестовых в БД нет активных администраторов.

    Защита «нельзя понизить последнего администратора» считает всех активных
    админов в базе. В общей базе разработки живут посторонние учётные записи
    (их создаёт обычная работа с интерфейсом), поэтому тестовый админ
    последним не оказывается и проверка не срабатывает — тест падал не из-за
    кода, а из-за состояния базы.

    Посторонние записи не удаляются: они временно деактивируются и
    восстанавливаются после теста, даже если тест упал.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def _set_active(user_ids: list[int], active: bool) -> None:
        if not user_ids:
            return
        async with sessions() as session:
            async with session.begin():
                await session.execute(
                    update(AdminUserRow)
                    .where(AdminUserRow.id.in_(user_ids))
                    .values(is_active=active)
                )

    async with sessions() as session:
        foreign_ids = (
            await session.execute(
                select(AdminUserRow.id).where(
                    AdminUserRow.role == "admin",
                    AdminUserRow.is_active.is_(True),
                    AdminUserRow.login.not_like(f"{PREFIX}%"),
                )
            )
        ).scalars().all()

    await _set_active(list(foreign_ids), False)
    try:
        yield
    finally:
        await _set_active(list(foreign_ids), True)
        await engine.dispose()


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def admin_headers(client: TestClient) -> dict:
    """Аварийный env-администратор: роль admin, записи в БД нет."""
    return _headers(_token(client, ENV_ADMIN_LOGIN, ENV_ADMIN_PASSWORD))


def _create_user(
    client: TestClient,
    admin_headers: dict,
    *,
    login: str,
    role: str = "viewer",
    must_change_password: bool = False,
    password: str = STRONG_PASSWORD,
) -> dict:
    response = client.post(
        "/api/v1/admin/users",
        headers=admin_headers,
        json={
            "login": login,
            "password": password,
            "role": role,
            "must_change_password": must_change_password,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


# --- Аварийный вход и /auth/me ------------------------------------------------------


def test_env_admin_can_login_and_is_marked(client: TestClient, admin_headers: dict):
    """Env-администратор остаётся рабочим и помечен как аварийный."""
    response = client.get("/api/v1/auth/me", headers=admin_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["login"] == ENV_ADMIN_LOGIN
    assert body["role"] == "admin"
    assert body["is_env_admin"] is True
    assert body["can_write"] is True


def test_wrong_password_rejected(client: TestClient):
    response = client.post(
        "/api/v1/auth/login", json={"login": ENV_ADMIN_LOGIN, "password": "nope-nope-nope"}
    )

    assert response.status_code == 401


def test_unknown_login_rejected_without_hint(client: TestClient):
    """Ответ не должен подсказывать, существует ли логин."""
    unknown = client.post(
        "/api/v1/auth/login", json={"login": "no-such-user", "password": "whatever-x"}
    )
    wrong = client.post(
        "/api/v1/auth/login", json={"login": ENV_ADMIN_LOGIN, "password": "whatever-x"}
    )

    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json() == wrong.json()


def test_env_admin_cannot_change_password_via_api(
    client: TestClient, admin_headers: dict
):
    """Пароль env-администратора живёт в конфигурации сервера, не в БД."""
    response = client.post(
        "/api/v1/auth/change-password",
        headers=admin_headers,
        json={
            "current_password": ENV_ADMIN_PASSWORD,
            "new_password": "another-password-1",
        },
    )

    assert response.status_code == 409
    assert "ADMIN_PASSWORD" in response.json()["detail"]


def test_auth_endpoints_require_token(client: TestClient):
    assert client.get("/api/v1/auth/me").status_code == 401
    assert client.get("/api/v1/admin/users").status_code == 401



# --- CRUD -------------------------------------------------------------------------


def test_create_user_never_returns_password(client: TestClient, admin_headers: dict):
    created = _create_user(client, admin_headers, login=f"{PREFIX}-plain")

    assert "password" not in created
    assert "password_hash" not in created
    assert created["has_password"] is True
    assert STRONG_PASSWORD not in str(created)


def test_created_user_can_login(client: TestClient, admin_headers: dict):
    _create_user(client, admin_headers, login=f"{PREFIX}-login")

    token = _token(client, f"{PREFIX}-login", STRONG_PASSWORD)
    me = client.get("/api/v1/auth/me", headers=_headers(token)).json()

    assert me["login"] == f"{PREFIX}-login"
    assert me["role"] == "viewer"
    assert me["is_env_admin"] is False


def test_user_list_hides_hashes(client: TestClient, admin_headers: dict):
    _create_user(client, admin_headers, login=f"{PREFIX}-list")

    response = client.get("/api/v1/admin/users", headers=admin_headers)

    assert response.status_code == 200
    assert "password_hash" not in response.text
    assert STRONG_PASSWORD not in response.text


def test_weak_password_rejected(client: TestClient, admin_headers: dict):
    response = client.post(
        "/api/v1/admin/users",
        headers=admin_headers,
        json={"login": f"{PREFIX}-weak", "password": "short", "role": "viewer"},
    )

    assert response.status_code == 422


def test_duplicate_login_conflicts(client: TestClient, admin_headers: dict):
    _create_user(client, admin_headers, login=f"{PREFIX}-dup")

    response = client.post(
        "/api/v1/admin/users",
        headers=admin_headers,
        json={
            "login": f"{PREFIX}-dup",
            "password": STRONG_PASSWORD,
            "role": "viewer",
        },
    )

    assert response.status_code == 409


def test_update_user_role_and_activity(client: TestClient, admin_headers: dict):
    user = _create_user(client, admin_headers, login=f"{PREFIX}-patch")

    response = client.patch(
        f"/api/v1/admin/users/{user['id']}",
        headers=admin_headers,
        json={"role": "admin", "display_name": "Второй админ", "is_active": False},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["role"] == "admin"
    assert body["display_name"] == "Второй админ"
    assert body["is_active"] is False


def test_deactivated_user_cannot_login(client: TestClient, admin_headers: dict):
    user = _create_user(client, admin_headers, login=f"{PREFIX}-off")
    client.patch(
        f"/api/v1/admin/users/{user['id']}",
        headers=admin_headers,
        json={"is_active": False},
    )

    response = client.post(
        "/api/v1/auth/login",
        json={"login": f"{PREFIX}-off", "password": STRONG_PASSWORD},
    )

    assert response.status_code == 401


def test_delete_user(client: TestClient, admin_headers: dict):
    user = _create_user(client, admin_headers, login=f"{PREFIX}-del")

    assert (
        client.delete(
            f"/api/v1/admin/users/{user['id']}", headers=admin_headers
        ).status_code
        == 204
    )
    assert (
        client.get(
            f"/api/v1/admin/users/{user['id']}", headers=admin_headers
        ).status_code
        == 404
    )


def test_missing_user_returns_404(client: TestClient, admin_headers: dict):
    assert (
        client.get("/api/v1/admin/users/99999999", headers=admin_headers).status_code
        == 404
    )
    assert (
        client.delete("/api/v1/admin/users/99999999", headers=admin_headers).status_code
        == 404
    )


# --- Роли: viewer не может менять -----------------------------------------------------


def test_viewer_can_read(client: TestClient, admin_headers: dict):
    _create_user(client, admin_headers, login=f"{PREFIX}-reader")
    token = _token(client, f"{PREFIX}-reader", STRONG_PASSWORD)

    assert client.get("/api/v1/dashboard", headers=_headers(token)).status_code == 200
    assert (
        client.get("/api/v1/admin/ai/providers", headers=_headers(token)).status_code
        == 200
    )


def test_viewer_cannot_write_ai_configuration(client: TestClient, admin_headers: dict):
    """Ограничение роли обеспечивается сервером, а не скрытой кнопкой."""
    _create_user(client, admin_headers, login=f"{PREFIX}-nowrite")
    token = _token(client, f"{PREFIX}-nowrite", STRONG_PASSWORD)

    response = client.post(
        "/api/v1/admin/ai/providers",
        headers=_headers(token),
        json={"name": "Nope", "slug": "ustest-nope"},
    )

    assert response.status_code == 403


def test_viewer_cannot_manage_users(client: TestClient, admin_headers: dict):
    _create_user(client, admin_headers, login=f"{PREFIX}-nousers")
    token = _token(client, f"{PREFIX}-nousers", STRONG_PASSWORD)

    assert client.get("/api/v1/admin/users", headers=_headers(token)).status_code == 403
    assert (
        client.post(
            "/api/v1/admin/users",
            headers=_headers(token),
            json={
                "login": f"{PREFIX}-x",
                "password": STRONG_PASSWORD,
                "role": "admin",
            },
        ).status_code
        == 403
    )


def test_viewer_cannot_generate_program(client: TestClient, admin_headers: dict):
    _create_user(client, admin_headers, login=f"{PREFIX}-nogen")
    token = _token(client, f"{PREFIX}-nogen", STRONG_PASSWORD)

    response = client.post(
        "/api/v1/profiles/does-not-matter/programs/generate",
        headers=_headers(token),
        json={"generator": "deterministic"},
    )

    assert response.status_code == 403


def test_promoted_viewer_gains_write_access(client: TestClient, admin_headers: dict):
    """Роль берётся из токена, поэтому после повышения нужен новый вход."""
    user = _create_user(client, admin_headers, login=f"{PREFIX}-promote")
    client.patch(
        f"/api/v1/admin/users/{user['id']}",
        headers=admin_headers,
        json={"role": "admin"},
    )

    token = _token(client, f"{PREFIX}-promote", STRONG_PASSWORD)
    assert client.get("/api/v1/admin/users", headers=_headers(token)).status_code == 200


# --- Смена и сброс пароля -------------------------------------------------------------


def test_temporary_password_blocks_api_until_changed(
    client: TestClient, admin_headers: dict
):
    """Пока пароль не сменён, доступ к остальному API закрыт."""
    _create_user(
        client, admin_headers, login=f"{PREFIX}-temp", must_change_password=True
    )
    login = client.post(
        "/api/v1/auth/login",
        json={"login": f"{PREFIX}-temp", "password": STRONG_PASSWORD},
    ).json()
    assert login["must_change_password"] is True
    headers = _headers(login["access_token"])

    blocked = client.get("/api/v1/dashboard", headers=headers)
    assert blocked.status_code == 403
    assert blocked.json()["detail"] == "password_change_required"

    # Но /auth/me обязан работать: иначе UI не покажет нужный экран.
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 200


def test_changing_password_unlocks_api(client: TestClient, admin_headers: dict):
    _create_user(
        client, admin_headers, login=f"{PREFIX}-unlock", must_change_password=True
    )
    token = _token(client, f"{PREFIX}-unlock", STRONG_PASSWORD)

    changed = client.post(
        "/api/v1/auth/change-password",
        headers=_headers(token),
        json={
            "current_password": STRONG_PASSWORD,
            "new_password": "brand-new-password-2",
        },
    )
    assert changed.status_code == 200
    assert changed.json()["must_change_password"] is False

    new_headers = _headers(changed.json()["access_token"])
    assert client.get("/api/v1/dashboard", headers=new_headers).status_code == 200
    # Старый пароль больше не действует.
    assert (
        client.post(
            "/api/v1/auth/login",
            json={"login": f"{PREFIX}-unlock", "password": STRONG_PASSWORD},
        ).status_code
        == 401
    )


def test_change_password_requires_correct_current(
    client: TestClient, admin_headers: dict
):
    _create_user(client, admin_headers, login=f"{PREFIX}-wrongcur")
    token = _token(client, f"{PREFIX}-wrongcur", STRONG_PASSWORD)

    response = client.post(
        "/api/v1/auth/change-password",
        headers=_headers(token),
        json={
            "current_password": "not-the-password",
            "new_password": "brand-new-password-2",
        },
    )

    assert response.status_code == 400


def test_admin_reset_returns_temporary_password_once(
    client: TestClient, admin_headers: dict
):
    user = _create_user(client, admin_headers, login=f"{PREFIX}-reset")

    response = client.post(
        f"/api/v1/admin/users/{user['id']}/reset-password", headers=admin_headers
    )

    assert response.status_code == 200
    body = response.json()
    temporary = body["temporary_password"]
    assert body["must_change_password"] is True
    assert len(temporary) >= 10

    # Временным паролем можно войти, но API закрыт до смены.
    login = client.post(
        "/api/v1/auth/login",
        json={"login": f"{PREFIX}-reset", "password": temporary},
    ).json()
    assert login["must_change_password"] is True
    assert (
        client.get("/api/v1/dashboard", headers=_headers(login["access_token"])).status_code
        == 403
    )
    # Прежний пароль сброшен.
    assert (
        client.post(
            "/api/v1/auth/login",
            json={"login": f"{PREFIX}-reset", "password": STRONG_PASSWORD},
        ).status_code
        == 401
    )


def test_reset_password_is_not_visible_in_user_list(
    client: TestClient, admin_headers: dict
):
    user = _create_user(client, admin_headers, login=f"{PREFIX}-hidden")
    temporary = client.post(
        f"/api/v1/admin/users/{user['id']}/reset-password", headers=admin_headers
    ).json()["temporary_password"]

    listing = client.get("/api/v1/admin/users", headers=admin_headers)

    assert temporary not in listing.text


# --- Защита от потери доступа ----------------------------------------------------------


def test_cannot_delete_own_account(client: TestClient, admin_headers: dict):
    user = _create_user(
        client, admin_headers, login=f"{PREFIX}-self", role="admin"
    )
    token = _token(client, f"{PREFIX}-self", STRONG_PASSWORD)

    response = client.delete(
        f"/api/v1/admin/users/{user['id']}", headers=_headers(token)
    )

    assert response.status_code == 409
    assert "собственную" in response.json()["detail"]


def test_cannot_demote_last_database_admin(
    client: TestClient, admin_headers: dict, sole_database_admin
):
    """Единственный админ в БД не может понизить себя до viewer."""
    user = _create_user(client, admin_headers, login=f"{PREFIX}-only", role="admin")
    token = _token(client, f"{PREFIX}-only", STRONG_PASSWORD)

    response = client.patch(
        f"/api/v1/admin/users/{user['id']}",
        headers=_headers(token),
        json={"role": "viewer"},
    )

    assert response.status_code == 409
    assert "администратор" in response.json()["detail"]


def test_cannot_deactivate_last_database_admin(
    client: TestClient, admin_headers: dict, sole_database_admin
):
    """Отключение последнего администратора тоже заперло бы систему."""
    user = _create_user(client, admin_headers, login=f"{PREFIX}-onlyoff", role="admin")

    response = client.patch(
        f"/api/v1/admin/users/{user['id']}",
        headers=admin_headers,
        json={"is_active": False},
    )

    assert response.status_code == 409
    assert "администратор" in response.json()["detail"]


def test_cannot_delete_last_database_admin(
    client: TestClient, admin_headers: dict, sole_database_admin
):
    """Удаление последнего администратора запрещено на сервере."""
    user = _create_user(client, admin_headers, login=f"{PREFIX}-onlydel", role="admin")

    response = client.delete(
        f"/api/v1/admin/users/{user['id']}", headers=admin_headers
    )

    assert response.status_code == 409
    assert "администратор" in response.json()["detail"]


def test_adding_users_does_not_break_admin_protection(
    client: TestClient, admin_headers: dict, sole_database_admin
):
    """Состав пользователей меняется — защита продолжает работать по факту.

    Проверяется весь путь: последнего админа понизить нельзя, после появления
    второго — можно, а когда второй остаётся единственным, запрет включается
    снова уже для него.
    """
    first = _create_user(client, admin_headers, login=f"{PREFIX}-flow1", role="admin")
    blocked = client.patch(
        f"/api/v1/admin/users/{first['id']}",
        headers=admin_headers,
        json={"role": "viewer"},
    )
    assert blocked.status_code == 409

    second = _create_user(client, admin_headers, login=f"{PREFIX}-flow2", role="admin")
    allowed = client.patch(
        f"/api/v1/admin/users/{first['id']}",
        headers=admin_headers,
        json={"role": "viewer"},
    )
    assert allowed.status_code == 200
    assert allowed.json()["role"] == "viewer"

    # Теперь единственный админ — второй, и запрет распространяется на него.
    blocked_again = client.patch(
        f"/api/v1/admin/users/{second['id']}",
        headers=admin_headers,
        json={"role": "viewer"},
    )
    assert blocked_again.status_code == 409


def test_viewer_users_do_not_count_as_admin_protection(
    client: TestClient, admin_headers: dict, sole_database_admin
):
    """Наблюдатели не защищают от потери админского доступа."""
    admin = _create_user(client, admin_headers, login=f"{PREFIX}-cnt-a", role="admin")
    _create_user(client, admin_headers, login=f"{PREFIX}-cnt-v1", role="viewer")
    _create_user(client, admin_headers, login=f"{PREFIX}-cnt-v2", role="viewer")

    response = client.patch(
        f"/api/v1/admin/users/{admin['id']}",
        headers=admin_headers,
        json={"role": "viewer"},
    )

    assert response.status_code == 409


def test_disabled_admin_does_not_count_as_protection_via_api(
    client: TestClient, admin_headers: dict, sole_database_admin
):
    """Отключённый администратор не является доступом."""
    active = _create_user(client, admin_headers, login=f"{PREFIX}-act", role="admin")
    disabled = _create_user(client, admin_headers, login=f"{PREFIX}-dis", role="admin")
    assert (
        client.patch(
            f"/api/v1/admin/users/{disabled['id']}",
            headers=admin_headers,
            json={"is_active": False},
        ).status_code
        == 200
    )

    response = client.patch(
        f"/api/v1/admin/users/{active['id']}",
        headers=admin_headers,
        json={"role": "viewer"},
    )

    assert response.status_code == 409


def test_second_admin_allows_demotion(client: TestClient, admin_headers: dict):
    first = _create_user(client, admin_headers, login=f"{PREFIX}-a1", role="admin")
    _create_user(client, admin_headers, login=f"{PREFIX}-a2", role="admin")

    response = client.patch(
        f"/api/v1/admin/users/{first['id']}",
        headers=admin_headers,
        json={"role": "viewer"},
    )

    assert response.status_code == 200
    assert response.json()["role"] == "viewer"
