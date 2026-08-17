"""Тесты PostgresProfileRepository против реальной PostgreSQL.

Требуют DATABASE_URL в окружении (docker compose postgres).
Если DATABASE_URL не задан — тесты пропускаются.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from src.application.profiles.finalization import ProfileFinalizationService
from src.application.questionnaire.service import QuestionnaireService
from src.domain.enums import CompletionStatus
from src.infrastructure.config import DATABASE_URL
from src.infrastructure.files.storage import LocalFileStorage

pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL is not set")


@pytest.fixture
async def repository():
    from src.infrastructure.persistence.postgres.db import get_session_factory
    from src.infrastructure.persistence.postgres.profile_repository import (
        PostgresProfileRepository,
    )

    return PostgresProfileRepository(get_session_factory())


@pytest.fixture(autouse=True)
async def cleanup_test_data():
    """Удаляет тестовые данные (telegram_user_id 9000xx) до и после тестов."""
    from src.infrastructure.persistence.postgres.db import get_session_factory
    from src.infrastructure.persistence.postgres.models import (
        ConsentRow,
        ProfileRow,
        UserRow,
    )

    async def _purge() -> None:
        async with get_session_factory()() as session:
            async with session.begin():
                user_ids = (
                    await session.execute(
                        select(UserRow.id).where(UserRow.telegram_user_id.like("9000%"))
                    )
                ).scalars().all()
                if user_ids:
                    await session.execute(
                        ConsentRow.__table__.delete().where(ConsentRow.user_id.in_(user_ids))
                    )
                    await session.execute(
                        ProfileRow.__table__.delete().where(ProfileRow.user_id.in_(user_ids))
                    )
                    await session.execute(
                        UserRow.__table__.delete().where(UserRow.id.in_(user_ids))
                    )

    await _purge()
    yield
    await _purge()
    # Engine привязан к event loop текущего теста — сбрасываем между тестами.
    from src.infrastructure.persistence.postgres.db import dispose_engine

    await dispose_engine()


@pytest.fixture
def service(tmp_path) -> QuestionnaireService:
    return QuestionnaireService(LocalFileStorage(tmp_path, max_files=10, max_size_mb=20))


async def test_save_and_get_profile(repository, service):
    profile = service.start_profile("900001", "pg_user")
    profile.client.name = "Пётр"
    profile.client.age_years = 40
    await repository.save(profile)

    loaded = await repository.get(profile.profile_id)
    assert loaded is not None
    assert loaded.client.name == "Пётр"
    assert loaded.client.age_years == 40
    assert loaded.source.bot_user_id == "900001"


async def test_user_created_once_no_duplicates(repository, service):
    profile = service.start_profile("900002", "dup_user")
    await repository.save(profile)
    await repository.save(profile)

    from sqlalchemy import select, func

    from src.infrastructure.persistence.postgres.db import get_session_factory
    from src.infrastructure.persistence.postgres.models import UserRow

    async with get_session_factory()() as session:
        count = (
            await session.execute(
                select(func.count()).select_from(UserRow).where(
                    UserRow.telegram_user_id == "900002"
                )
            )
        ).scalar_one()
    assert count == 1


async def test_update_profile_increments_version(repository, service):
    profile = service.start_profile("900003", "upd_user")
    profile.client.name = "Анна"
    await repository.save(profile)
    profile.client.name = "Анна Мария"
    await repository.save(profile)

    from sqlalchemy import select

    from src.infrastructure.persistence.postgres.db import get_session_factory
    from src.infrastructure.persistence.postgres.models import ProfileRow

    async with get_session_factory()() as session:
        row = (
            await session.execute(
                select(ProfileRow).where(ProfileRow.profile_id == profile.profile_id)
            )
        ).scalar_one()
    assert row.profile_version == 2
    loaded = await repository.get(profile.profile_id)
    assert loaded.client.name == "Анна Мария"


async def test_exists_and_delete(repository, service):
    profile = service.start_profile("900004", "del_user")
    await repository.save(profile)
    assert await repository.exists(profile.profile_id) is True
    await repository.delete(profile.profile_id)
    assert await repository.exists(profile.profile_id) is False
    assert await repository.get(profile.profile_id) is None


async def test_display_number_unique(repository):
    first = await repository.next_display_number()
    second = await repository.next_display_number()
    assert first != second
    assert first.startswith("REQ-")
    assert second.startswith("REQ-")


async def test_finalization_idempotent_in_postgres(repository, service):
    profile = service.start_profile("900005", "fin_user")
    finalization = ProfileFinalizationService(repository)
    first = await finalization.finalize(profile)
    second = await finalization.finalize(profile)
    assert first.already_finalized is False
    assert second.already_finalized is True
    assert profile.questionnaire.completion_status is CompletionStatus.CONFIRMED

    # Согласия записаны в отдельную таблицу.
    from sqlalchemy import select, func

    from src.infrastructure.persistence.postgres.db import get_session_factory
    from src.infrastructure.persistence.postgres.models import ConsentRow, UserRow

    async with get_session_factory()() as session:
        user_id = (
            await session.execute(
                select(UserRow.id).where(UserRow.telegram_user_id == "900005")
            )
        ).scalar_one()
        consent_count = (
            await session.execute(
                select(func.count()).select_from(ConsentRow).where(
                    ConsentRow.user_id == user_id
                )
            )
        ).scalar_one()
    assert consent_count == 3
