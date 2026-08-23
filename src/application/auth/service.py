"""AdminUserService: управление пользователями админ-панели.

Единственное место, где принимаются решения о доступе и целостности состава
пользователей. Гарантии:

- пароль наружу не возвращается никогда, только факт его наличия;
- нельзя остаться без активных администраторов (иначе система запирается);
- нельзя понизить или отключить самого себя, если это заберёт последний
  админский доступ;
- временный пароль после сброса требует обязательной смены при входе;
- все изменения состава пользователей попадают в журнал событий.

Журнал — существующая таблица `ai_audit_events` (см. комментарий в ORM-модели):
плодить параллельный журнал ради второй сущности хуже, чем использовать один.

Вход через внешних провайдеров (Яндекс, VK, MAX) готов на уровне данных:
`authenticate_external` находит пользователя по аккаунту провайдера. Сам
OAuth-флоу не реализован — его добавление не потребует менять этот сервис.
"""
from __future__ import annotations

import logging

from src.domain.auth import (
    AdminIdentity,
    AdminRole,
    AdminUser,
    AuthProvider,
)
from src.errors import ProfilePersistenceError, WorkoutBotError
from src.infrastructure.auth.passwords import (
    PasswordPolicyError,
    generate_temporary_password,
    hash_password,
    verify_password,
)
from src.infrastructure.persistence.postgres.admin_user_repository import (
    AdminIdentityRepository,
    AdminUserRepository,
)
from src.infrastructure.persistence.postgres.ai_repository import AIAuditRepository

logger = logging.getLogger(__name__)


class AdminUserError(WorkoutBotError):
    """Операция над пользователями недопустима."""


class LastAdminError(AdminUserError):
    """Операция оставила бы систему без активных администраторов."""


class AdminUserService:
    def __init__(
        self,
        *,
        users: AdminUserRepository,
        identities: AdminIdentityRepository,
        audit: AIAuditRepository,
    ) -> None:
        self._users = users
        self._identities = identities
        self._audit = audit

    # --- Аутентификация ---------------------------------------------------------

    async def authenticate(self, login: str, password: str) -> AdminUser | None:
        """Проверяет логин и пароль. None — доступ не предоставлен.

        Причина отказа наружу не детализируется: форма входа не должна
        различать «нет такого пользователя», «нет пароля» и «пароль неверный».
        """
        user = await self._users.get_by_login(login)
        if user is None or not user.is_active:
            # Хеш всё равно считаем, чтобы время ответа не выдавало наличие
            # пользователя. Результат игнорируется.
            verify_password(password, None)
            return None
        if not verify_password(password, user.password_hash):
            return None
        if user.id is not None:
            await self._users.touch_login(user.id)
        return user

    async def authenticate_external(
        self, provider: AuthProvider, provider_user_id: str
    ) -> AdminUser | None:
        """Вход по аккаунту внешнего провайдера.

        Готовая точка подключения для Яндекс/VK/MAX: OAuth-флоу должен лишь
        получить `provider_user_id` и вызвать этот метод. Пользователи
        автоматически не создаются: аккаунт должен быть привязан заранее,
        иначе любой владелец аккаунта у провайдера получил бы доступ.
        """
        user_id = await self._identities.find_user_id(provider, provider_user_id)
        if user_id is None:
            return None
        user = await self._users.get(user_id)
        if user is None or not user.is_active:
            return None
        await self._users.touch_login(user_id)
        return user

    # --- CRUD -------------------------------------------------------------------

    async def list_users(self) -> list[AdminUser]:
        return await self._users.list()

    async def get_user(self, user_id: int) -> AdminUser | None:
        return await self._users.get(user_id)

    async def create_user(
        self,
        *,
        login: str,
        password: str,
        role: AdminRole,
        display_name: str | None = None,
        must_change_password: bool = True,
        actor: str | None = None,
    ) -> AdminUser:
        """Создаёт пользователя. По умолчанию требует смены пароля при входе."""
        try:
            password_hash = hash_password(password)
        except PasswordPolicyError as exc:
            raise AdminUserError(str(exc)) from exc

        created = await self._users.create(
            AdminUser(
                login=login,
                display_name=display_name,
                role=role,
                password_hash=password_hash,
                must_change_password=must_change_password,
                is_active=True,
            )
        )
        await self._record(
            "admin_user_created",
            actor=actor,
            user_id=created.id,
            metadata={"login": created.login, "role": created.role.value},
        )
        return created

    async def update_user(
        self,
        user_id: int,
        *,
        actor_user_id: int | None = None,
        actor: str | None = None,
        display_name: str | None = None,
        role: AdminRole | None = None,
        is_active: bool | None = None,
    ) -> AdminUser | None:
        """Меняет профиль, роль и активность с защитой от потери доступа."""
        user = await self._users.get(user_id)
        if user is None:
            return None

        losing_admin = user.role is AdminRole.ADMIN and user.is_active and (
            (role is not None and role is not AdminRole.ADMIN)
            or is_active is False
        )

        fields: dict = {}
        if display_name is not None:
            fields["display_name"] = display_name
        if role is not None:
            fields["role"] = role.value
        if is_active is not None:
            fields["is_active"] = is_active
        if not fields:
            return user

        try:
            if losing_admin:
                # Критическая проверка выполняется внутри одной DB-транзакции
                # с изменением и под PostgreSQL advisory lock. Предварительная
                # проверка через отдельный запрос здесь намеренно отсутствует:
                # она была бы подвержена race condition между инстансами.
                updated = await self._users.update_guarded_last_admin(
                    user_id, **fields
                )
            else:
                updated = await self._users.update(user_id, **fields)
        except ProfilePersistenceError as exc:
            if "последнего активного администратора" in str(exc):
                raise LastAdminError(
                    "Это последний активный администратор. Сначала назначьте "
                    "администратором другого пользователя."
                ) from exc
            raise

        if updated is not None:
            await self._record(
                "admin_user_updated",
                actor=actor,
                user_id=user_id,
                metadata={"fields": sorted(fields.keys()), "login": updated.login},
            )
        return updated

    async def delete_user(
        self, user_id: int, *, actor_user_id: int | None = None, actor: str | None = None
    ) -> bool:
        """Удаляет пользователя. Себя удалить нельзя, последнего админа тоже."""
        user = await self._users.get(user_id)
        if user is None:
            return False
        if actor_user_id is not None and actor_user_id == user_id:
            raise AdminUserError(
                "Нельзя удалить собственную учётную запись. "
                "Попросите другого администратора."
            )

        try:
            if user.role is AdminRole.ADMIN and user.is_active:
                deleted = await self._users.delete_guarded_last_admin(user_id)
            else:
                deleted = await self._users.delete(user_id)
        except ProfilePersistenceError as exc:
            if "последнего активного администратора" in str(exc):
                raise LastAdminError(
                    "Это последний активный администратор. Сначала назначьте "
                    "администратором другого пользователя."
                ) from exc
            raise

        if deleted:
            await self._record(
                "admin_user_deleted",
                actor=actor,
                user_id=user_id,
                metadata={"login": user.login},
            )
        return deleted

    # --- Пароли -----------------------------------------------------------------

    async def change_own_password(
        self, user_id: int, *, current_password: str, new_password: str
    ) -> None:
        """Смена пароля самим пользователем: требует текущий пароль."""
        user = await self._users.get(user_id)
        if user is None:
            raise AdminUserError("Пользователь не найден")
        if not verify_password(current_password, user.password_hash):
            raise AdminUserError("Текущий пароль указан неверно")
        if current_password == new_password:
            raise AdminUserError("Новый пароль должен отличаться от текущего")
        try:
            password_hash = hash_password(new_password)
        except PasswordPolicyError as exc:
            raise AdminUserError(str(exc)) from exc

        await self._users.update(
            user_id, password_hash=password_hash, must_change_password=False
        )
        await self._record(
            "admin_user_password_changed",
            actor=user.login,
            user_id=user_id,
            metadata={},
        )

    async def reset_password(
        self, user_id: int, *, actor: str | None = None
    ) -> str:
        """Сброс пароля администратором: возвращает временный пароль."""
        user = await self._users.get(user_id)
        if user is None:
            raise AdminUserError("Пользователь не найден")
        temporary = generate_temporary_password()
        await self._users.update(
            user_id,
            password_hash=hash_password(temporary),
            must_change_password=True,
        )
        await self._record(
            "admin_user_password_reset",
            actor=actor,
            user_id=user_id,
            metadata={"login": user.login},
        )
        return temporary

    # --- Внешние идентичности ------------------------------------------------------

    async def link_identity(
        self,
        user_id: int,
        *,
        provider: AuthProvider,
        provider_user_id: str,
        actor: str | None = None,
    ) -> AdminIdentity:
        """Привязывает аккаунт внешнего провайдера к пользователю."""
        if provider is AuthProvider.PASSWORD:
            raise AdminUserError(
                "Пароль не является внешним провайдером: используйте смену пароля"
            )
        if await self._users.get(user_id) is None:
            raise AdminUserError("Пользователь не найден")
        identity = await self._identities.link(
            AdminIdentity(
                user_id=user_id,
                provider=provider,
                provider_user_id=provider_user_id,
            )
        )
        await self._record(
            "admin_identity_linked",
            actor=actor,
            user_id=user_id,
            metadata={"provider": provider.value},
        )
        return identity

    async def list_identities(self, user_id: int) -> list[AdminIdentity]:
        return await self._identities.list_for_user(user_id)

    async def unlink_identity(
        self, identity_id: int, *, actor: str | None = None
    ) -> bool:
        unlinked = await self._identities.unlink(identity_id)
        if unlinked:
            await self._record(
                "admin_identity_unlinked",
                actor=actor,
                user_id=None,
                metadata={"identity_id": identity_id},
            )
        return unlinked

    # --- Вспомогательное ------------------------------------------------------------

    async def _assert_other_admin_remains(self, user_id: int) -> None:
        remaining = await self._users.count_active_admins(exclude_user_id=user_id)
        if remaining == 0:
            raise LastAdminError(
                "Это последний активный администратор. Сначала назначьте "
                "администратором другого пользователя."
            )

    async def _record(
        self,
        event_type: str,
        *,
        actor: str | None,
        user_id: int | None,
        metadata: dict,
    ) -> None:
        """Журналирование не должно ломать основную операцию."""
        try:
            await self._audit.record(
                event_type,
                actor=actor,
                entity_type="admin_user",
                entity_id=str(user_id) if user_id is not None else None,
                metadata=metadata,
            )
        except Exception:  # noqa: BLE001
            logger.exception("Не удалось записать событие управления пользователями")
