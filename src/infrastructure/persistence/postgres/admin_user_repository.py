"""Репозитории пользователей админ-панели (PostgreSQL, async SQLAlchemy 2.0).

Маппинг ORM ↔ Pydantic без бизнес-логики. Проверки «нельзя удалить последнего
администратора» и политика паролей живут в сервисном слое.

Хеш пароля репозиторий читает и пишет как обычную строку: он не знает, каким
алгоритмом посчитан хеш.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.domain.auth import AdminIdentity, AdminRole, AdminUser, AuthProvider
from src.errors import ProfilePersistenceError
from src.infrastructure.persistence.postgres.models import (
    AdminIdentityRow,
    AdminUserRow,
)


def _persistence_error(exc: SQLAlchemyError, what: str) -> ProfilePersistenceError:
    return ProfilePersistenceError(f"{what}: {exc.__class__.__name__}")


class AdminUserRepository:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._sessions = session_factory

    @staticmethod
    def _to_domain(row: AdminUserRow) -> AdminUser:
        return AdminUser(
            id=row.id,
            login=row.login,
            display_name=row.display_name,
            role=row.role,  # type: ignore[arg-type]
            password_hash=row.password_hash,
            must_change_password=row.must_change_password,
            is_active=row.is_active,
            last_login_at=row.last_login_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    async def create(self, user: AdminUser) -> AdminUser:
        try:
            async with self._sessions() as session:
                async with session.begin():
                    row = AdminUserRow(
                        login=user.login,
                        display_name=user.display_name,
                        role=user.role.value,
                        password_hash=user.password_hash,
                        must_change_password=user.must_change_password,
                        is_active=user.is_active,
                    )
                    session.add(row)
                    await session.flush()
                    await session.refresh(row)
                    return self._to_domain(row)
        except IntegrityError as exc:
            raise ProfilePersistenceError(
                f"Пользователь с логином «{user.login}» уже существует"
            ) from exc
        except SQLAlchemyError as exc:
            raise _persistence_error(exc, "Не удалось создать пользователя") from exc

    async def get(self, user_id: int) -> AdminUser | None:
        async with self._sessions() as session:
            row = (
                await session.execute(
                    select(AdminUserRow).where(AdminUserRow.id == user_id)
                )
            ).scalar_one_or_none()
        return self._to_domain(row) if row else None

    async def get_by_login(self, login: str) -> AdminUser | None:
        async with self._sessions() as session:
            row = (
                await session.execute(
                    select(AdminUserRow).where(AdminUserRow.login == login)
                )
            ).scalar_one_or_none()
        return self._to_domain(row) if row else None

    async def list(self) -> list[AdminUser]:
        async with self._sessions() as session:
            rows = (
                await session.execute(
                    select(AdminUserRow).order_by(AdminUserRow.login)
                )
            ).scalars().all()
        return [self._to_domain(r) for r in rows]

    async def update(self, user_id: int, **fields) -> AdminUser | None:
        """PATCH-семантика: обновляет только переданные поля."""
        if not fields:
            return await self.get(user_id)
        try:
            async with self._sessions() as session:
                async with session.begin():
                    row = (
                        await session.execute(
                            select(AdminUserRow)
                            .where(AdminUserRow.id == user_id)
                            .with_for_update()
                        )
                    ).scalar_one_or_none()
                    if row is None:
                        return None
                    for key, value in fields.items():
                        setattr(row, key, value)
                    await session.flush()
                    await session.refresh(row)
                    return self._to_domain(row)
        except IntegrityError as exc:
            raise ProfilePersistenceError(
                "Нарушение уникальности логина при обновлении пользователя"
            ) from exc
        except SQLAlchemyError as exc:
            raise _persistence_error(exc, "Не удалось обновить пользователя") from exc

    async def delete(self, user_id: int) -> bool:
        """Удаляет пользователя вместе с его внешними идентичностями (CASCADE)."""
        try:
            async with self._sessions() as session:
                async with session.begin():
                    result = await session.execute(
                        delete(AdminUserRow).where(AdminUserRow.id == user_id)
                    )
                    return bool(result.rowcount)
        except SQLAlchemyError as exc:
            raise _persistence_error(exc, "Не удалось удалить пользователя") from exc

    async def count_active_admins(self, exclude_user_id: int | None = None) -> int:
        """Сколько активных администраторов останется без указанного пользователя.

        Используется, чтобы не допустить состояния «в системе не осталось
        ни одного администратора».
        """
        query = (
            select(func.count())
            .select_from(AdminUserRow)
            .where(
                AdminUserRow.role == AdminRole.ADMIN.value,
                AdminUserRow.is_active.is_(True),
            )
        )
        if exclude_user_id is not None:
            query = query.where(AdminUserRow.id != exclude_user_id)
        async with self._sessions() as session:
            return (await session.execute(query)).scalar_one()

    async def touch_login(self, user_id: int) -> None:
        """Отмечает факт успешного входа. Сбой не должен ломать вход."""
        await self.update(user_id, last_login_at=datetime.now(timezone.utc))


class AdminIdentityRepository:
    """Идентичности внешних провайдеров (Яндекс, VK, MAX).

    Существует для того, чтобы подключение внешнего входа не требовало
    менять схему пользователей и логику проверки прав.
    """

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._sessions = session_factory

    @staticmethod
    def _to_domain(row: AdminIdentityRow) -> AdminIdentity:
        return AdminIdentity(
            id=row.id,
            user_id=row.user_id,
            provider=row.provider,  # type: ignore[arg-type]
            provider_user_id=row.provider_user_id,
            created_at=row.created_at,
        )

    async def link(self, identity: AdminIdentity) -> AdminIdentity:
        try:
            async with self._sessions() as session:
                async with session.begin():
                    row = AdminIdentityRow(
                        user_id=identity.user_id,
                        provider=identity.provider.value,
                        provider_user_id=identity.provider_user_id,
                    )
                    session.add(row)
                    await session.flush()
                    await session.refresh(row)
                    return self._to_domain(row)
        except IntegrityError as exc:
            raise ProfilePersistenceError(
                f"Аккаунт провайдера «{identity.provider.value}» уже привязан "
                "к пользователю"
            ) from exc
        except SQLAlchemyError as exc:
            raise _persistence_error(exc, "Не удалось привязать аккаунт") from exc

    async def find_user_id(
        self, provider: AuthProvider, provider_user_id: str
    ) -> int | None:
        """Точка входа для будущего OAuth-callback: аккаунт → пользователь."""
        async with self._sessions() as session:
            row = (
                await session.execute(
                    select(AdminIdentityRow).where(
                        AdminIdentityRow.provider == provider.value,
                        AdminIdentityRow.provider_user_id == provider_user_id,
                    )
                )
            ).scalar_one_or_none()
        return row.user_id if row else None

    async def list_for_user(self, user_id: int) -> list[AdminIdentity]:
        async with self._sessions() as session:
            rows = (
                await session.execute(
                    select(AdminIdentityRow)
                    .where(AdminIdentityRow.user_id == user_id)
                    .order_by(AdminIdentityRow.provider)
                )
            ).scalars().all()
        return [self._to_domain(r) for r in rows]

    async def unlink(self, identity_id: int) -> bool:
        try:
            async with self._sessions() as session:
                async with session.begin():
                    result = await session.execute(
                        delete(AdminIdentityRow).where(
                            AdminIdentityRow.id == identity_id
                        )
                    )
                    return bool(result.rowcount)
        except SQLAlchemyError as exc:
            raise _persistence_error(exc, "Не удалось отвязать аккаунт") from exc
