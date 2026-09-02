"""Сценарии отказа на сетевой границе Gateway ↔ Backend.

Пять сценариев из требований задачи. Проверяется не «не упало», а конкретное
наблюдаемое поведение: пользователь получает сообщение, polling продолжается,
дубликаты не появляются.

Проверяются через реальный `Dispatcher` с настоящими хендлерами: имитируется
только транспорт (Telegram — фейковая сессия, Backend — подменённый клиент).
Так путь обновления проходит те же фильтры и middleware, что в продакшене.
"""
from __future__ import annotations

import datetime as dt

import pytest
from aiogram import Bot
from aiogram.fsm.storage.memory import MemoryStorage, SimpleEventIsolation
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from apps.telegram_gateway import runtime
from apps.telegram_gateway.backend_client import (
    BackendRejectedError,
    BackendUnavailableError,
)
from scripts.qa_harness.fake_telegram import FakeTelegramSession
from src.domain.telegram_contract import (
    TelegramMessage,
    TelegramUpdateResponse,
    TelegramView,
)

CHAT = 555
USER_ID = 777
FAKE_TOKEN = "42:TEST-TOKEN-NOT-A-SECRET"


class RecordingBackend:
    """Фейковый Backend с управляемым отказом."""

    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.updates: list[int] = []

    async def handle_update(self, request):
        self.updates.append(request.update_id)
        if self.error is not None:
            raise self.error
        return TelegramUpdateResponse(
            view=TelegramView(messages=[TelegramMessage(text="Вопрос 1")])
        )

    async def send_photo(self, **kwargs):
        if self.error is not None:
            raise self.error
        return TelegramUpdateResponse(
            view=TelegramView(messages=[TelegramMessage(text="Фото сохранено")])
        )

    async def close(self):
        return None


# Dispatcher берётся из session-scoped фикстуры `tests/unit/conftest.py`:
# роутеры aiogram — модульные singletons, и второй `include_router` того же
# роутера падает с `Router is already attached`.


@pytest.fixture
def session():
    return FakeTelegramSession()


@pytest.fixture
def bot(session):
    return Bot(token=FAKE_TOKEN, session=session)


def _text_update(update_id: int = 1, text: str = "Иван") -> Update:
    return Update(
        update_id=update_id,
        message=Message(
            message_id=100 + update_id,
            date=dt.datetime.now(dt.timezone.utc),
            chat=Chat(id=CHAT, type="private"),
            from_user=User(id=USER_ID, is_bot=False, first_name="Иван"),
            text=text,
        ),
    )


def _callback_update(update_id: int = 1, data: str = "start_qa") -> Update:
    return Update(
        update_id=update_id,
        callback_query=CallbackQuery(
            id=str(update_id),
            from_user=User(id=USER_ID, is_bot=False, first_name="Иван"),
            chat_instance="test",
            data=data,
            message=Message(
                message_id=200 + update_id,
                date=dt.datetime.now(dt.timezone.utc),
                chat=Chat(id=CHAT, type="private"),
                from_user=User(id=USER_ID, is_bot=False, first_name="Иван"),
                text="Предыдущий вопрос",
            ),
        ),
    )


@pytest.fixture
def with_backend(monkeypatch):
    """Подменяет клиент Backend на фейк на время теста."""

    def _install(backend: RecordingBackend) -> RecordingBackend:
        monkeypatch.setattr(runtime, "_client", backend)
        return backend

    yield _install
    monkeypatch.setattr(runtime, "_client", None)


class TestBackendUnavailable:
    """Сценарий 1: Backend недоступен."""

    async def test_user_gets_a_message(self, dispatcher, bot, session, with_backend):
        with_backend(RecordingBackend(error=BackendUnavailableError("down")))

        await dispatcher.feed_update(bot, _text_update())

        assert session.texts()
        assert "недоступен" in session.texts()[0].lower()

    async def test_message_mentions_answers_are_kept(
        self, dispatcher, bot, session, with_backend
    ):
        """Ответы в RU: сообщение не должно предлагать начать заново."""
        with_backend(RecordingBackend(error=BackendUnavailableError("down")))

        await dispatcher.feed_update(bot, _text_update())

        text = session.texts()[0].lower()
        assert "сохранены" in text
        assert "заново" not in text

    async def test_polling_survives(self, dispatcher, bot, session, with_backend):
        """Обновление обработано без исключения: polling не умирает."""
        with_backend(RecordingBackend(error=BackendUnavailableError("down")))

        for update_id in (1, 2, 3):
            await dispatcher.feed_update(bot, _text_update(update_id))

        assert len(session.texts()) == 3

    async def test_callback_gets_alert(self, dispatcher, bot, session, with_backend):
        """Нажатие должно быть отвечено, иначе кнопка зависает с часами."""
        with_backend(RecordingBackend(error=BackendUnavailableError("down")))

        await dispatcher.feed_update(bot, _callback_update())

        answers = [
            record for record in session.sent if record.method == "answer_callback"
        ]
        assert answers
        assert "недоступен" in answers[0].text.lower()

    async def test_rejected_request_does_not_crash(
        self, dispatcher, bot, session, with_backend
    ):
        """4xx тоже не должен ронять обработку: пользователю нужен ответ."""
        with_backend(RecordingBackend(error=BackendRejectedError(422, "bad")))

        await dispatcher.feed_update(bot, _text_update())

        assert session.texts()

    async def test_recovery_after_outage(self, dispatcher, bot, session, with_backend):
        """После восстановления диалог продолжается с того же места."""
        backend = with_backend(
            RecordingBackend(error=BackendUnavailableError("down"))
        )
        await dispatcher.feed_update(bot, _text_update(1))

        backend.error = None
        await dispatcher.feed_update(bot, _text_update(2))

        assert session.texts()[-1] == "Вопрос 1"


class TestNoDuplicateWork:
    """Сценарий 5: обрыв связи не создаёт дубликатов."""

    async def test_same_update_carries_same_key(
        self, dispatcher, bot, session, with_backend
    ):
        """Переотправка Telegram приходит с тем же update_id.

        Дубликат отсекает Backend по этому ключу; задача Gateway — не подменить
        его на что-то своё.
        """
        backend = with_backend(RecordingBackend())

        await dispatcher.feed_update(bot, _text_update(42))
        await dispatcher.feed_update(bot, _text_update(42))

        assert backend.updates == [42, 42]

    async def test_repeated_callback_on_same_message_is_distinct(
        self, dispatcher, bot, session, with_backend
    ):
        """Разные нажатия на одном сообщении — разные обновления.

        Переключение дней недели правит одно и то же сообщение. Если ключом было
        бы `message_id`, второе нажатие сочли бы дубликатом первого, и день не
        переключился бы.
        """
        backend = with_backend(RecordingBackend())

        await dispatcher.feed_update(bot, _callback_update(10, "day_mon"))
        await dispatcher.feed_update(bot, _callback_update(11, "day_tue"))

        assert backend.updates == [10, 11]


class TestGatewayHasNoDomainState:
    """Сценарий 3: рестарт Gateway не теряет анкету."""

    async def test_no_answers_in_gateway_state(
        self, dispatcher, bot, session, with_backend
    ):
        """Состояние Gateway не содержит ответов: они в RU.

        Проверяется после реальной обработки обновления: если бы хендлер писал
        профиль в FSM, он оказался бы в storage.
        """
        from aiogram.fsm.context import FSMContext
        from aiogram.fsm.storage.base import StorageKey

        with_backend(RecordingBackend())
        await dispatcher.feed_update(bot, _text_update())

        context = FSMContext(
            storage=dispatcher.fsm.storage,
            key=StorageKey(bot_id=bot.id, chat_id=CHAT, user_id=USER_ID),
        )
        data = await context.get_data()

        assert "profile" not in data
        assert data == {} or all(
            not isinstance(value, dict) for value in data.values()
        )

    async def test_restart_loses_nothing(
        self, dispatcher, bot, session, with_backend, monkeypatch
    ):
        """Очистка состояния Gateway не мешает диалогу.

        Имитация рестарта процесса: storage заменяется на пустой, как после
        перезапуска контейнера. Диалог продолжается — позиция и ответы читаются
        из RU, а не из состояния Gateway.
        """
        with_backend(RecordingBackend())
        await dispatcher.feed_update(bot, _text_update(1))

        monkeypatch.setattr(dispatcher.fsm, "storage", MemoryStorage())
        await dispatcher.feed_update(bot, _text_update(2))

        assert session.texts()[-1] == "Вопрос 1"
