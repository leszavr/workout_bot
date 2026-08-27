"""Сборка Telegram gateway с устойчивым FSM (Phase 1.2-A).

Проверяется то, что нельзя увидеть на уровне storage: точка входа обязана
требовать Redis, использовать его для FSM и изоляции обновлений, а сбой
хранилища должен превращаться в понятное сообщение пользователю, а не в
молчаливую потерю обновления.
"""
from __future__ import annotations

import datetime as dt

import pytest
from aiogram import Bot
from aiogram.fsm.storage.memory import MemoryStorage, SimpleEventIsolation
from aiogram.types import Chat, ErrorEvent, Message, Update, User

from apps.telegram_gateway.handlers.errors import (
    STORAGE_UNAVAILABLE_TEXT,
    handle_fsm_storage_error,
)
from apps.telegram_gateway.main import resolve_fsm_url
from src.errors import FSMStorageError
from src.infrastructure.telegram.fsm_storage import create_fsm_storage


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str) -> None:
        self.sent.append((chat_id, text))


def make_update() -> Update:
    chat = Chat(id=555, type="private")
    user = User(id=777, is_bot=False, first_name="Иван")
    message = Message(
        message_id=1,
        date=dt.datetime(2026, 8, 23, tzinfo=dt.timezone.utc),
        chat=chat,
        from_user=user,
        text="80",
    )
    return Update(update_id=1, message=message)


class TestFSMConfiguration:
    def test_missing_redis_url_is_rejected(self):
        with pytest.raises(RuntimeError) as failure:
            resolve_fsm_url("")
        assert "REDIS_URL" in str(failure.value)

    def test_configured_url_is_used(self):
        assert resolve_fsm_url("redis://localhost:6379/0") == "redis://localhost:6379/0"


class TestDispatcherWiring:
    def test_dispatcher_uses_provided_storage_and_isolation(self, dispatcher):
        assert isinstance(dispatcher.fsm.storage, MemoryStorage)
        assert isinstance(dispatcher.fsm.events_isolation, SimpleEventIsolation)

    def test_questionnaire_and_error_routers_are_registered(self, dispatcher):
        names = {router.name for router in dispatcher.sub_routers}
        assert "telegram_gateway.errors" in names
        # Анкета не должна потеряться при добавлении error router.
        assert any("questionnaire" in name for name in names)

    def test_error_router_is_first_to_receive_storage_failures(self, dispatcher):
        """Порядок важен: анкета отвечает на любой текст и перехватила бы событие."""
        assert dispatcher.sub_routers[0].name == "telegram_gateway.errors"


class TestStorageFailureIsUserVisible:
    async def test_user_gets_safe_message(self):
        bot = FakeBot()
        event = ErrorEvent(update=make_update(), exception=FSMStorageError("ping failed"))

        handled = await handle_fsm_storage_error(event, bot)

        assert handled is True
        assert len(bot.sent) == 1
        chat_id, text = bot.sent[0]
        assert chat_id == 555
        assert "позже" in text.lower()

    async def test_internal_details_are_not_leaked(self):
        bot = FakeBot()
        event = ErrorEvent(
            update=make_update(),
            exception=FSMStorageError("redis://user:secret@redis-host:6379 refused"),
        )

        await handle_fsm_storage_error(event, bot)

        _, text = bot.sent[0]
        assert "redis" not in text.lower()
        assert "secret" not in text

    async def test_send_failure_does_not_raise(self):
        """Если Telegram тоже недоступен, обработчик не должен ронять polling."""

        class BrokenBot(FakeBot):
            async def send_message(self, chat_id: int, text: str) -> None:
                raise RuntimeError("telegram unavailable")

        event = ErrorEvent(update=make_update(), exception=FSMStorageError("down"))
        assert await handle_fsm_storage_error(event, BrokenBot()) is True

    async def test_update_without_chat_is_ignored(self):
        bot = FakeBot()
        event = ErrorEvent(update=Update(update_id=2), exception=FSMStorageError("down"))

        assert await handle_fsm_storage_error(event, bot) is True
        assert bot.sent == []


class TestStorageFailureThroughDispatcher:
    """Сквозная проверка: сбой хранилища доходит до пользователя, а не в пустоту."""

    async def test_unreachable_storage_answers_the_user(self, dispatcher, monkeypatch):
        broken = create_fsm_storage("redis://127.0.0.1:6399/0")
        bot = Bot(token="42:TEST-TOKEN-NOT-A-SECRET")
        sent: list[str] = []

        async def capture(chat_id, text, **kwargs):
            sent.append(text)

        monkeypatch.setattr(bot, "send_message", capture)
        monkeypatch.setattr(dispatcher.fsm, "storage", broken.storage)
        monkeypatch.setattr(dispatcher.fsm, "events_isolation", broken.events_isolation)
        try:
            await dispatcher.feed_update(bot, make_update())
        finally:
            await broken.close()
            await bot.session.close()

        assert sent == [STORAGE_UNAVAILABLE_TEXT]
