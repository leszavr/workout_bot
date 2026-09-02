"""Репозиторий серверных сессий Telegram-анкеты.

Сессия — состояние диалога: где стоит пользователь, что уже ответил, какой ответ
Gateway получил последним. Хранится в RU, потому что содержит ответы анкеты.

Одна операция чтения и одна записи: сессия целиком читается в начале обработки
события и целиком пишется в конце. Дробить её на частичные обновления смысла нет
— шаг анкеты меняет позицию, черновик и ключ идемпотентности одновременно, и
раздельная запись оставила бы их рассогласованными при сбое между запросами.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.errors import ProfilePersistenceError
from src.infrastructure.persistence.postgres.models import TelegramSessionRow


@dataclass
class TelegramSession:
    """Состояние диалога. `draft` — сырой JSON профиля, не доменный объект.

    Валидация в доменный `FitnessProfile` делается вызывающим сервисом: сессия
    может содержать черновик, собранный предыдущей версией схемы анкеты, и
    падать на чтении из-за этого репозиторий не должен.
    """

    telegram_user_id: str
    chat_id: str | None = None
    username: str | None = None
    position: str | None = None
    editing_question: str | None = None
    draft: dict | None = None
    profile_id: str | None = None
    last_update_id: int | None = None
    last_view: dict | None = None

    @property
    def started(self) -> bool:
        """Начат ли диалог. Пустая сессия равносильна отсутствию анкеты."""
        return self.draft is not None


class TelegramSessionRepository:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._sessions = session_factory

    async def get(self, telegram_user_id: str) -> TelegramSession | None:
        try:
            async with self._sessions() as session:
                row = (
                    await session.execute(
                        select(TelegramSessionRow).where(
                            TelegramSessionRow.telegram_user_id == telegram_user_id
                        )
                    )
                ).scalar_one_or_none()
        except SQLAlchemyError as exc:
            raise ProfilePersistenceError(
                f"Не удалось прочитать состояние диалога: {exc.__class__.__name__}"
            ) from exc
        if row is None:
            return None
        return TelegramSession(
            telegram_user_id=row.telegram_user_id,
            chat_id=row.chat_id,
            username=row.username,
            position=row.position,
            editing_question=row.editing_question,
            draft=row.draft,
            profile_id=row.profile_id,
            last_update_id=row.last_update_id,
            last_view=row.last_view,
        )

    async def save(self, session_state: TelegramSession) -> None:
        """Upsert по telegram_user_id.

        Upsert, а не «прочитать и решить»: между чтением и записью пользователь
        мог прислать второе обновление, и вставка упала бы на уникальном
        ограничении.
        """
        values = {
            "telegram_user_id": session_state.telegram_user_id,
            "chat_id": session_state.chat_id,
            "username": session_state.username,
            "position": session_state.position,
            "editing_question": session_state.editing_question,
            "draft": session_state.draft,
            "profile_id": session_state.profile_id,
            "last_update_id": session_state.last_update_id,
            "last_view": session_state.last_view,
        }
        try:
            async with self._sessions() as session:
                async with session.begin():
                    stmt = pg_insert(TelegramSessionRow).values(**values)
                    await session.execute(
                        stmt.on_conflict_do_update(
                            index_elements=[TelegramSessionRow.telegram_user_id],
                            set_={
                                key: getattr(stmt.excluded, key)
                                for key in values
                                if key != "telegram_user_id"
                            },
                        )
                    )
        except SQLAlchemyError as exc:
            raise ProfilePersistenceError(
                f"Не удалось сохранить состояние диалога: {exc.__class__.__name__}"
            ) from exc

    async def delete(self, telegram_user_id: str) -> None:
        """Сброс диалога (/cancel, «начать заново»).

        Профиль при этом не удаляется: подтверждённая анкета остаётся, новая
        сессия начинается с чистого состояния.
        """
        try:
            async with self._sessions() as session:
                async with session.begin():
                    await session.execute(
                        TelegramSessionRow.__table__.delete().where(
                            TelegramSessionRow.telegram_user_id == telegram_user_id
                        )
                    )
        except SQLAlchemyError as exc:
            raise ProfilePersistenceError(
                f"Не удалось сбросить состояние диалога: {exc.__class__.__name__}"
            ) from exc
