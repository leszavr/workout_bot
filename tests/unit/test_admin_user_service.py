"""Unit-тесты AdminUserService: доступ, роли и защита от потери доступа.

Ключевые инварианты, которые проверяются здесь:
- аутентификация не пускает отключённого пользователя и пользователя без пароля;
- систему нельзя оставить без активных администраторов;
- нельзя удалить собственную учётную запись;
- сброс пароля выдаёт временный пароль и требует его смены;
- вход через внешнего провайдера работает только по заранее привязанному аккаунту.

Fake-репозитории in-memory, без PostgreSQL.
"""
from __future__ import annotations

import pytest

from src.application.auth.service import (
    AdminUserError,
    AdminUserService,
    LastAdminError,
)
from src.domain.auth import AdminIdentity, AdminRole, AdminUser, AuthProvider
from src.infrastructure.auth.passwords import hash_password, verify_password

PASSWORD = "initial-password-01"


class FakeUsers:
    def __init__(self, users: list[AdminUser] | None = None) -> None:
        self.users = users or []
        self._next_id = max((u.id or 0) for u in self.users) + 1 if self.users else 1
        self.touched: list[int] = []

    async def create(self, user: AdminUser) -> AdminUser:
        if any(u.login == user.login for u in self.users):
            from src.errors import ProfilePersistenceError

            raise ProfilePersistenceError("Пользователь уже существует")
        stored = user.model_copy(update={"id": self._next_id})
        self._next_id += 1
        self.users.append(stored)
        return stored

    async def get(self, user_id: int) -> AdminUser | None:
        return next((u for u in self.users if u.id == user_id), None)

    async def get_by_login(self, login: str) -> AdminUser | None:
        return next((u for u in self.users if u.login == login), None)

    async def list(self) -> list[AdminUser]:
        return list(self.users)

    async def update(self, user_id: int, **fields) -> AdminUser | None:
        for index, user in enumerate(self.users):
            if user.id != user_id:
                continue
            if "role" in fields and isinstance(fields["role"], str):
                fields["role"] = AdminRole(fields["role"])
            self.users[index] = user.model_copy(update=fields)
            return self.users[index]
        return None

    async def update_guarded_last_admin(self, user_id: int, **fields) -> AdminUser | None:
        """Fake эквивалент guarded-операции: те же инварианты в unit-тестах."""
        user = await self.get(user_id)
        if user is None:
            return None
        if user.role is AdminRole.ADMIN and user.is_active:
            remaining = await self.count_active_admins(exclude_user_id=user_id)
            becoming_non_admin = fields.get("role") not in (None, AdminRole.ADMIN.value)
            becoming_inactive = fields.get("is_active") is False
            if (becoming_non_admin or becoming_inactive) and remaining == 0:
                from src.errors import ProfilePersistenceError

                raise ProfilePersistenceError(
                    "Нельзя изменить последнего активного администратора"
                )
        return await self.update(user_id, **fields)

    async def delete(self, user_id: int) -> bool:
        before = len(self.users)
        self.users = [u for u in self.users if u.id != user_id]
        return len(self.users) != before

    async def delete_guarded_last_admin(self, user_id: int) -> bool:
        user = await self.get(user_id)
        if user is None:
            return False
        if user.role is AdminRole.ADMIN and user.is_active:
            if await self.count_active_admins(exclude_user_id=user_id) == 0:
                from src.errors import ProfilePersistenceError

                raise ProfilePersistenceError(
                    "Нельзя удалить последнего активного администратора"
                )
        return await self.delete(user_id)

    async def count_active_admins(self, exclude_user_id: int | None = None) -> int:
        return sum(
            1
            for u in self.users
            if u.role is AdminRole.ADMIN
            and u.is_active
            and u.id != exclude_user_id
        )

    async def touch_login(self, user_id: int) -> None:
        self.touched.append(user_id)


class FakeIdentities:
    def __init__(self, items: list[AdminIdentity] | None = None) -> None:
        self.items = items or []
        self._next_id = 1

    async def link(self, identity: AdminIdentity) -> AdminIdentity:
        stored = identity.model_copy(update={"id": self._next_id})
        self._next_id += 1
        self.items.append(stored)
        return stored

    async def find_user_id(self, provider, provider_user_id):
        return next(
            (
                i.user_id
                for i in self.items
                if i.provider is provider and i.provider_user_id == provider_user_id
            ),
            None,
        )

    async def list_for_user(self, user_id: int):
        return [i for i in self.items if i.user_id == user_id]

    async def unlink(self, identity_id: int) -> bool:
        before = len(self.items)
        self.items = [i for i in self.items if i.id != identity_id]
        return len(self.items) != before


class FakeAudit:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    async def record(self, event_type, *, actor=None, entity_type=None, entity_id=None, metadata=None):
        self.events.append((event_type, metadata or {}))


def _user(**overrides) -> AdminUser:
    data = {
        "id": 1,
        "login": "owner",
        "role": AdminRole.ADMIN,
        "password_hash": hash_password(PASSWORD),
        "is_active": True,
    }
    data.update(overrides)
    return AdminUser(**data)


def _service(users: list[AdminUser] | None = None, identities=None):
    users_repo = FakeUsers(users if users is not None else [_user()])
    identities_repo = FakeIdentities(identities)
    audit = FakeAudit()
    service = AdminUserService(
        users=users_repo, identities=identities_repo, audit=audit
    )
    return service, users_repo, identities_repo, audit


# --- Аутентификация ---------------------------------------------------------------


async def test_authenticate_accepts_correct_password():
    service, users, _, _ = _service()
    user = await service.authenticate("owner", PASSWORD)
    assert user is not None
    assert user.role is AdminRole.ADMIN
    assert users.touched == [1]


async def test_authenticate_rejects_wrong_password():
    service, users, _, _ = _service()
    assert await service.authenticate("owner", "wrong-password-x") is None
    assert users.touched == []


async def test_authenticate_rejects_unknown_login():
    service, _, _, _ = _service()
    assert await service.authenticate("nobody", PASSWORD) is None


async def test_authenticate_rejects_disabled_user():
    service, _, _, _ = _service([_user(is_active=False)])
    assert await service.authenticate("owner", PASSWORD) is None


async def test_authenticate_rejects_user_without_password():
    service, _, _, _ = _service([_user(password_hash=None)])
    assert await service.authenticate("owner", PASSWORD) is None


# --- Внешние провайдеры -----------------------------------------------------------


async def test_external_login_requires_linked_account():
    service, _, _, _ = _service()
    assert await service.authenticate_external(AuthProvider.YANDEX, "ya-1") is None


async def test_external_login_works_for_linked_account():
    service, _, _, _ = _service(
        identities=[AdminIdentity(id=1, user_id=1, provider=AuthProvider.YANDEX, provider_user_id="ya-1")]
    )
    user = await service.authenticate_external(AuthProvider.YANDEX, "ya-1")
    assert user is not None and user.login == "owner"


async def test_external_login_rejects_disabled_user():
    service, _, _, _ = _service(
        [_user(is_active=False)],
        identities=[AdminIdentity(id=1, user_id=1, provider=AuthProvider.VK, provider_user_id="vk-9")],
    )
    assert await service.authenticate_external(AuthProvider.VK, "vk-9") is None


async def test_password_is_not_an_external_provider():
    service, _, _, _ = _service()
    with pytest.raises(AdminUserError, match="не является внешним"):
        await service.link_identity(1, provider=AuthProvider.PASSWORD, provider_user_id="x")


async def test_link_identity_records_provider_without_account_id():
    service, _, _, audit = _service()
    await service.link_identity(1, provider=AuthProvider.MAX, provider_user_id="secret-account-id")
    event = next(e for e in audit.events if e[0] == "admin_identity_linked")
    assert event[1] == {"provider": "max"}


# --- Создание и роли --------------------------------------------------------------


async def test_created_user_must_change_password_by_default():
    service, _, _, _ = _service()
    created = await service.create_user(login="newbie", password="temporary-pass-1", role=AdminRole.VIEWER)
    assert created.must_change_password is True
    assert created.role is AdminRole.VIEWER
    assert created.password_hash is not None


async def test_created_user_password_is_hashed_not_stored_raw():
    service, _, _, _ = _service()
    created = await service.create_user(login="newbie", password="temporary-pass-1", role=AdminRole.VIEWER)
    assert "temporary-pass-1" not in (created.password_hash or "")
    assert verify_password("temporary-pass-1", created.password_hash) is True


async def test_create_user_rejects_weak_password():
    service, _, _, _ = _service()
    with pytest.raises(AdminUserError, match="не короче"):
        await service.create_user(login="newbie", password="short", role=AdminRole.VIEWER)


async def test_duplicate_login_rejected():
    service, _, _, _ = _service()
    from src.errors import ProfilePersistenceError
    with pytest.raises(ProfilePersistenceError):
        await service.create_user(login="owner", password="another-pass-01", role=AdminRole.VIEWER)


# --- Защита от потери доступа -----------------------------------------------------


async def test_cannot_demote_last_admin():
    service, _, _, _ = _service()
    with pytest.raises(LastAdminError):
        await service.update_user(1, role=AdminRole.VIEWER)


async def test_cannot_deactivate_last_admin():
    service, _, _, _ = _service()
    with pytest.raises(LastAdminError):
        await service.update_user(1, is_active=False)


async def test_cannot_delete_last_admin():
    service, _, _, _ = _service()
    with pytest.raises(LastAdminError):
        await service.delete_user(1, actor_user_id=999)


async def test_can_demote_admin_when_another_admin_exists():
    service, users, _, _ = _service([_user(id=1, login="first"), _user(id=2, login="second")])
    updated = await service.update_user(1, role=AdminRole.VIEWER)
    assert updated is not None and updated.role is AdminRole.VIEWER
    assert await users.count_active_admins() == 1


async def test_cannot_delete_own_account():
    service, _, _, _ = _service([_user(id=1, login="first"), _user(id=2, login="second")])
    with pytest.raises(AdminUserError, match="собственную"):
        await service.delete_user(1, actor_user_id=1)


async def test_deleting_viewer_is_allowed():
    service, users, _, _ = _service([_user(id=1, login="boss"), _user(id=2, login="guest", role=AdminRole.VIEWER)])
    assert await service.delete_user(2, actor_user_id=1) is True
    assert await users.get(2) is None


async def test_disabled_admin_does_not_count_as_protection():
    service, _, _, _ = _service([_user(id=1, login="active"), _user(id=2, login="off", is_active=False)])
    with pytest.raises(LastAdminError):
        await service.update_user(1, is_active=False)


# --- Пароли ----------------------------------------------------------------------


async def test_change_own_password_requires_current():
    service, _, _, _ = _service()
    with pytest.raises(AdminUserError, match="неверно"):
        await service.change_own_password(1, current_password="wrong-one-x", new_password="brand-new-pass-1")


async def test_change_own_password_clears_must_change_flag():
    service, users, _, _ = _service([_user(must_change_password=True)])
    await service.change_own_password(1, current_password=PASSWORD, new_password="brand-new-pass-1")
    stored = await users.get(1)
    assert stored is not None
    assert stored.must_change_password is False
    assert verify_password("brand-new-pass-1", stored.password_hash) is True


async def test_change_own_password_rejects_same_password():
    service, _, _, _ = _service()
    with pytest.raises(AdminUserError, match="отличаться"):
        await service.change_own_password(1, current_password=PASSWORD, new_password=PASSWORD)


async def test_change_own_password_enforces_policy():
    service, _, _, _ = _service()
    with pytest.raises(AdminUserError, match="не короче"):
        await service.change_own_password(1, current_password=PASSWORD, new_password="short")


async def test_reset_password_returns_temporary_and_forces_change():
    service, users, _, _ = _service()
    temporary = await service.reset_password(1, actor="boss")
    stored = await users.get(1)
    assert stored is not None
    assert stored.must_change_password is True
    assert verify_password(temporary, stored.password_hash) is True
    assert verify_password(PASSWORD, stored.password_hash) is False


async def test_reset_password_invalidates_old_credentials_and_is_audited_without_secret():
    service, users, _, audit = _service()
    temporary = await service.reset_password(1, actor="boss")
    assert await service.authenticate("owner", PASSWORD) is None
    assert await service.authenticate("owner", temporary) is not None
    event = next(e for e in audit.events if e[0] == "admin_user_password_reset")
    assert temporary not in str(event[1])
    stored = await users.get(1)
    assert stored is not None and stored.must_change_password is True


async def test_password_change_event_has_no_password_data():
    service, _, _, audit = _service()
    await service.change_own_password(1, current_password=PASSWORD, new_password="brand-new-pass-1")
    event = next(e for e in audit.events if e[0] == "admin_user_password_changed")
    assert event[1] == {}


async def test_audit_failure_does_not_break_operation():
    class BrokenAudit:
        async def record(self, *args, **kwargs):
            raise RuntimeError("audit down")
    users = FakeUsers([_user()])
    service = AdminUserService(users=users, identities=FakeIdentities(), audit=BrokenAudit())
    created = await service.create_user(login="newbie", password="temporary-pass-1", role=AdminRole.VIEWER)
    assert created.id is not None
