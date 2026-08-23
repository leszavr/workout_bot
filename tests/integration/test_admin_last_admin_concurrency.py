"""Concurrency acceptance: два параллельных изменения последних admin."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.domain.auth import AdminRole, AdminUser
from src.errors import ProfilePersistenceError
from src.infrastructure.config import DATABASE_URL
from src.infrastructure.auth.passwords import hash_password
from src.infrastructure.persistence.postgres.admin_user_repository import AdminUserRepository
from src.infrastructure.persistence.postgres.models import AdminUserRow

pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL is not set")

PREFIX = "lastadminrace"
PASSWORD_HASH = hash_password("strong-password-01")


@asynccontextmanager
async def only_test_admins_active(sessions):
    """Временно оставляет активными только администраторов этого теста.

    Защита считает всех активных администраторов в базе. В общей базе (машина
    разработчика, повторный прогон) есть посторонние записи, и тогда ни одно из
    двух изменений не затрагивает «последнего» админа — гонка не воспроизводится
    и тест падает не из-за кода. Посторонние записи не удаляются: они
    деактивируются и восстанавливаются, в том числе при падении теста.
    """
    async with sessions() as session:
        foreign_ids = (
            await session.execute(
                select(AdminUserRow.id).where(
                    AdminUserRow.role == AdminRole.ADMIN.value,
                    AdminUserRow.is_active.is_(True),
                    AdminUserRow.login.not_like(f"{PREFIX}%"),
                )
            )
        ).scalars().all()

    async def set_active(ids, active: bool) -> None:
        if not ids:
            return
        async with sessions() as session:
            async with session.begin():
                await session.execute(
                    update(AdminUserRow)
                    .where(AdminUserRow.id.in_(ids))
                    .values(is_active=active)
                )

    await set_active(list(foreign_ids), False)
    try:
        yield
    finally:
        await set_active(list(foreign_ids), True)


@pytest.mark.asyncio
async def test_concurrent_demotions_leave_one_admin():
    """При двух параллельных demote ровно один admin должен сохраниться.

    Тест намеренно работает непосредственно с двумя независимыми DB-сессиями
    на одном engine. Это проверяет PostgreSQL advisory lock, не смешивая
    asyncio.run() с TestClient/его event loop.
    """
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    repo = AdminUserRepository(sessions)

    try:
        async with sessions() as session:
            async with session.begin():
                await session.execute(
                    delete(AdminUserRow).where(AdminUserRow.login.like(f"{PREFIX}%"))
                )

        first = await repo.create(
            AdminUser(
                login=f"{PREFIX}-one",
                display_name=None,
                role=AdminRole.ADMIN,
                password_hash=PASSWORD_HASH,
                must_change_password=False,
                is_active=True,
            )
        )
        second = await repo.create(
            AdminUser(
                login=f"{PREFIX}-two",
                display_name=None,
                role=AdminRole.ADMIN,
                password_hash=PASSWORD_HASH,
                must_change_password=False,
                is_active=True,
            )
        )

        async with only_test_admins_active(sessions):
            results = await asyncio.gather(
                repo.update_guarded_last_admin(first.id, role=AdminRole.VIEWER.value),
                repo.update_guarded_last_admin(second.id, role=AdminRole.VIEWER.value),
                return_exceptions=True,
            )

            failures = [r for r in results if isinstance(r, ProfilePersistenceError)]
            successes = [r for r in results if not isinstance(r, Exception)]

            assert len(failures) == 1
            assert len(successes) == 1

            async with sessions() as session:
                remaining = (
                    await session.execute(
                        select(AdminUserRow.id).where(
                            AdminUserRow.login.like(f"{PREFIX}%"),
                            AdminUserRow.role == "admin",
                            AdminUserRow.is_active.is_(True),
                        )
                    )
                ).scalars().all()

            assert len(remaining) == 1
    finally:
        async with sessions() as session:
            async with session.begin():
                await session.execute(
                    delete(AdminUserRow).where(AdminUserRow.login.like(f"{PREFIX}%"))
                )
        await engine.dispose()
