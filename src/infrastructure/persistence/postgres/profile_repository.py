"""PostgresProfileRepository — реализация ProfileRepository поверх PostgreSQL.

Pydantic Model → Validation → PostgreSQL JSONB.
БД не является хранилищем произвольного JSON: данные проходят строгую
валидацию при чтении и записи.
"""
from __future__ import annotations

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.domain.profile import FitnessProfile
from src.errors import ProfilePersistenceError
from src.infrastructure.persistence.postgres.models import ConsentRow, ProfileRow, UserRow
from src.infrastructure.persistence.profile_repository import ProfileRepository

DISPLAY_NUMBER_SEQUENCE = "profile_display_number_seq"


class PostgresProfileRepository(ProfileRepository):
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._sessions = session_factory

    async def _ensure_user(self, session, profile: FitnessProfile) -> int | None:
        bot_user_id = profile.source.bot_user_id
        if not bot_user_id:
            return None
        stmt = pg_insert(UserRow).values(
            telegram_user_id=bot_user_id,
            telegram_username=profile.source.telegram_username,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[UserRow.telegram_user_id],
            set_={"telegram_username": stmt.excluded.telegram_username},
        ).returning(UserRow.id)
        result = await session.execute(stmt)
        return result.scalar_one()

    async def save(self, profile: FitnessProfile) -> FitnessProfile:
        if not profile.profile_id:
            raise ProfilePersistenceError("profile_id is empty")
        profile.touch()
        payload = profile.model_dump(mode="json")
        try:
            async with self._sessions() as session:
                async with session.begin():
                    user_id = await self._ensure_user(session, profile)

                    stmt = pg_insert(ProfileRow).values(
                        profile_id=profile.profile_id,
                        display_number=profile.display_number,
                        user_id=user_id,
                        profile_version=1,
                        schema_version=profile.schema_version,
                        data=payload,
                        status=profile.questionnaire.completion_status.value,
                    )
                    stmt = stmt.on_conflict_do_update(
                        index_elements=[ProfileRow.profile_id],
                        set_={
                            "display_number": stmt.excluded.display_number,
                            "data": stmt.excluded.data,
                            "status": stmt.excluded.status,
                            "profile_version": ProfileRow.profile_version + 1,
                        },
                    )
                    await session.execute(stmt)

                    # Согласия — отдельная версионируемая сущность.
                    if user_id is not None and profile.consents:
                        await session.execute(
                            ConsentRow.__table__.delete().where(
                                ConsentRow.user_id == user_id
                            )
                        )
                        session.add_all(
                            ConsentRow(
                                user_id=user_id,
                                consent_type=c.scope.value,
                                consent_version=c.document_version,
                                granted=True,
                                granted_at=c.granted_at,
                                source=c.source,
                            )
                            for c in profile.consents
                        )
        except SQLAlchemyError as exc:
            raise ProfilePersistenceError(f"Не удалось сохранить профиль в БД: {exc}") from exc
        return profile

    async def get(self, profile_id: str) -> FitnessProfile | None:
        try:
            async with self._sessions() as session:
                result = await session.execute(
                    select(ProfileRow.data).where(ProfileRow.profile_id == profile_id)
                )
                data = result.scalar_one_or_none()
        except SQLAlchemyError as exc:
            raise ProfilePersistenceError(f"Не удалось прочитать профиль из БД: {exc}") from exc
        if data is None:
            return None
        return FitnessProfile.model_validate(data)

    async def exists(self, profile_id: str) -> bool:
        try:
            async with self._sessions() as session:
                result = await session.execute(
                    select(ProfileRow.id).where(ProfileRow.profile_id == profile_id)
                )
                return result.scalar_one_or_none() is not None
        except SQLAlchemyError as exc:
            raise ProfilePersistenceError(f"Ошибка проверки существования профиля: {exc}") from exc

    async def next_display_number(self) -> str:
        try:
            async with self._sessions() as session:
                result = await session.execute(
                    text(f"SELECT to_char(now() AT TIME ZONE 'UTC', 'YYYYMMDD') || '-' || "
                         f"lpad(nextval('{DISPLAY_NUMBER_SEQUENCE}')::text, 5, '0')")
                )
                suffix = result.scalar_one()
                await session.commit()
        except SQLAlchemyError as exc:
            raise ProfilePersistenceError(f"Не удалось получить номер заявки: {exc}") from exc
        return f"REQ-{suffix}"

    async def delete(self, profile_id: str) -> None:
        try:
            async with self._sessions() as session:
                async with session.begin():
                    await session.execute(
                        ProfileRow.__table__.delete().where(
                            ProfileRow.profile_id == profile_id
                        )
                    )
        except SQLAlchemyError as exc:
            raise ProfilePersistenceError(f"Не удалось удалить профиль из БД: {exc}") from exc
