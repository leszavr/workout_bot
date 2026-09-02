"""Integration-тесты FSM-хранилища Gateway (Phase 1.2-A + сетевая граница).

Требуют `REDIS_URL` в окружении. Без этой переменной тесты пропускаются,
а не «проходят»: проверять restart-safe поведение на MemoryStorage нельзя.

После выноса Gateway за сетевую границу назначение Redis изменилось: ответы
анкеты в нём не хранятся (они в PostgreSQL, RU), остаётся только служебное
состояние aiogram — изоляция параллельных обновлений и технические ключи
middleware. Поэтому здесь проверяется не сохранность профиля, а два свойства:
состояние переживает перезапуск процесса и у ключей есть TTL.

Тесты пишут только свои ключи (уникальные bot_id/chat_id/user_id) и удаляют
их после себя, поэтому прогон безопасен и на общем Redis разработчика.
"""
from __future__ import annotations

import asyncio
import random

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from redis.asyncio import Redis

from src.errors import FSMStorageError
from src.infrastructure.config import GATEWAY_STATE_TTL_SECONDS, REDIS_URL
from src.infrastructure.telegram.fsm_storage import KEY_BUILDER, create_fsm_storage

pytestmark = pytest.mark.skipif(not REDIS_URL, reason="REDIS_URL is not set")

# Порт, на котором заведомо никто не слушает: имитация недоступного Redis.
UNREACHABLE_REDIS_URL = "redis://127.0.0.1:6399/0"


def unique_key(*, user_id: int | None = None, bot_id: int | None = None) -> StorageKey:
    """Ключ, который не может пересечься с рабочими данными в общем Redis."""
    chat = random.randint(10**12, 10**13)
    return StorageKey(
        bot_id=bot_id if bot_id is not None else random.randint(10**12, 10**13),
        chat_id=chat,
        user_id=user_id if user_id is not None else chat,
    )


@pytest.fixture
async def redis_fsm():
    """Фабрика storage с гарантированной очисткой созданных ключей."""
    bundles = []
    keys: list[StorageKey] = []

    def factory():
        bundle = create_fsm_storage(REDIS_URL)
        bundles.append(bundle)
        return bundle

    def track(key: StorageKey) -> StorageKey:
        keys.append(key)
        return key

    yield factory, track

    cleanup = Redis.from_url(REDIS_URL)
    try:
        for key in keys:
            for part in ("state", "data", "lock"):
                await cleanup.delete(KEY_BUILDER.build(key, part))
    finally:
        await cleanup.aclose(close_connection_pool=True)
    for bundle in bundles:
        await bundle.close()


class TestRestartSafety:
    """A. Состояние анкеты сохраняется после перезапуска процесса."""

    async def test_state_and_data_survive_storage_restart(self, redis_fsm):
        factory, track = redis_fsm
        key = track(unique_key())

        first = factory()
        context = FSMContext(storage=first.storage, key=key)
        await context.set_state("QuestionnaireStates:q05_weight")
        await context.update_data(editing_question="q05_weight")
        # Процесс остановлен: соединения закрыты, объекты storage уничтожены.
        await first.close()

        second = factory()
        restarted = FSMContext(storage=second.storage, key=key)
        assert await restarted.get_state() == "QuestionnaireStates:q05_weight"
        assert (await restarted.get_data())["editing_question"] == "q05_weight"

    async def test_cleared_state_stays_cleared_after_restart(self, redis_fsm):
        factory, track = redis_fsm
        key = track(unique_key())

        first = factory()
        context = FSMContext(storage=first.storage, key=key)
        await context.set_state("QuestionnaireStates:q01_name")
        await context.clear()
        await first.close()

        second = factory()
        assert await FSMContext(storage=second.storage, key=key).get_state() is None


class TestUserIsolation:
    """B. Состояние одного пользователя не смешивается с другим."""

    async def test_states_do_not_leak_between_users(self, redis_fsm):
        factory, track = redis_fsm
        bundle = factory()
        first = track(unique_key())
        second = track(unique_key())

        await FSMContext(storage=bundle.storage, key=first).set_state("state:first")
        await FSMContext(storage=bundle.storage, key=first).update_data(profile={"n": 1})
        await FSMContext(storage=bundle.storage, key=second).set_state("state:second")
        await FSMContext(storage=bundle.storage, key=second).update_data(profile={"n": 2})

        assert await FSMContext(storage=bundle.storage, key=first).get_state() == "state:first"
        assert await FSMContext(storage=bundle.storage, key=second).get_state() == "state:second"
        assert (await FSMContext(storage=bundle.storage, key=first).get_data())["profile"] == {"n": 1}
        assert (await FSMContext(storage=bundle.storage, key=second).get_data())["profile"] == {"n": 2}

    async def test_same_user_id_in_different_bots_is_separate(self, redis_fsm):
        """Один Redis на несколько ботов не должен смешивать пользователей."""
        factory, track = redis_fsm
        bundle = factory()
        user_id = random.randint(10**12, 10**13)
        first = track(unique_key(user_id=user_id))
        second = track(StorageKey(bot_id=first.bot_id + 1, chat_id=first.chat_id, user_id=user_id))
        track(second)

        await FSMContext(storage=bundle.storage, key=first).set_state("state:bot-one")
        await FSMContext(storage=bundle.storage, key=second).set_state("state:bot-two")

        assert await FSMContext(storage=bundle.storage, key=first).get_state() == "state:bot-one"
        assert await FSMContext(storage=bundle.storage, key=second).get_state() == "state:bot-two"


class TestMultipleInstances:
    """C. Два экземпляра приложения работают с общим FSM storage."""

    async def test_second_instance_sees_state_written_by_first(self, redis_fsm):
        factory, track = redis_fsm
        key = track(unique_key())
        first, second = factory(), factory()

        await FSMContext(storage=first.storage, key=key).set_state("QuestionnaireStates:review")
        assert (
            await FSMContext(storage=second.storage, key=key).get_state()
            == "QuestionnaireStates:review"
        )

        await FSMContext(storage=second.storage, key=key).update_data(profile={"src": "second"})
        assert (await FSMContext(storage=first.storage, key=key).get_data())["profile"] == {
            "src": "second"
        }

    async def test_event_isolation_is_shared_between_instances(self, redis_fsm):
        """Блокировка одного экземпляра видна другому: обработка не дублируется."""
        factory, track = redis_fsm
        key = track(unique_key())
        first, second = factory(), factory()

        async with first.events_isolation.lock(key=key):
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(
                    second.events_isolation.lock(key=key).__aenter__(), timeout=0.5
                )

        # После освобождения блокировка снова доступна.
        async with second.events_isolation.lock(key=key):
            pass


class TestNoPersonalDataAndTTL:
    """D. EU не является системой хранения: только служебное состояние, с TTL."""

    async def test_state_keys_expire(self, redis_fsm):
        """Без TTL техническое состояние стало бы постоянным хранилищем."""
        factory, track = redis_fsm
        key = track(unique_key())
        bundle = factory()
        context = FSMContext(storage=bundle.storage, key=key)

        await context.set_state("QuestionnaireStates:q01_name")
        await context.update_data(last_message_id=42)

        client = Redis.from_url(REDIS_URL)
        try:
            state_ttl = await client.ttl(KEY_BUILDER.build(key, "state"))
            data_ttl = await client.ttl(KEY_BUILDER.build(key, "data"))
        finally:
            await client.aclose(close_connection_pool=True)

        # -1 означает «без срока жизни», -2 — «ключа нет». Оба недопустимы.
        assert 0 < state_ttl <= GATEWAY_STATE_TTL_SECONDS
        assert 0 < data_ttl <= GATEWAY_STATE_TTL_SECONDS

    async def test_questionnaire_answers_are_not_stored(self, redis_fsm):
        """Ответы анкеты в Redis Gateway не попадают.

        Проверяется фактом: диалог ведёт Backend, и у Gateway нет кода, который
        писал бы профиль в состояние. Тест фиксирует это на уровне хранилища —
        после установки состояния в данных лежит только то, что положил вызов.
        """
        factory, track = redis_fsm
        key = track(unique_key())
        context = FSMContext(storage=factory().storage, key=key)

        await context.set_state("QuestionnaireStates:q05_weight")
        data = await context.get_data()

        assert "profile" not in data


class TestRedisFailure:
    """E. Недоступность Redis обрабатывается предсказуемо."""

    async def test_startup_check_fails_fast_with_normalized_error(self):
        bundle = create_fsm_storage(UNREACHABLE_REDIS_URL)
        try:
            with pytest.raises(FSMStorageError):
                await bundle.verify()
        finally:
            await bundle.close()

    async def test_runtime_failure_is_not_silent_empty_state(self):
        """Сбой Redis не должен выглядеть как «состояния нет».

        Пустой словарь вместо ошибки означал бы для диалога «начни сначала»:
        Gateway переспросил бы у Backend текущий вопрос, но потерял бы
        идентификатор сообщения, которое правит, и в чате остался бы висеть
        экран с активными кнопками.
        """
        bundle = create_fsm_storage(UNREACHABLE_REDIS_URL)
        context = FSMContext(storage=bundle.storage, key=unique_key())
        try:
            with pytest.raises(FSMStorageError):
                await context.get_data()
        finally:
            await bundle.close()

    async def test_questionnaire_survives_redis_loss(self):
        """Потеря Redis не теряет анкету: она в RU.

        Проверяется свойство размещения данных, а не поведение хранилища:
        состояние диалога живёт в PostgreSQL, поэтому недоступность Redis EU
        отменяет только текущий шаг, а не собранные ответы.
        """
        bundle = create_fsm_storage(UNREACHABLE_REDIS_URL)
        try:
            with pytest.raises(FSMStorageError):
                await bundle.verify()
        finally:
            await bundle.close()

        # Ответы и позиция диалога недоступному Redis не принадлежат: они
        # читаются из PostgreSQL через TelegramSessionRepository, который к
        # Redis не обращается вовсе.
        from src.infrastructure.persistence.postgres.telegram_session_repository import (
            TelegramSessionRepository,
        )

        assert "redis" not in TelegramSessionRepository.__module__


class TestStorageLifecycle:
    """F. Startup/shutdown создают и закрывают ресурсы Redis."""

    async def test_verify_passes_on_available_redis(self, redis_fsm):
        factory, _ = redis_fsm
        await factory().verify()

    async def test_close_is_idempotent_and_releases_resources(self, redis_fsm):
        factory, track = redis_fsm
        key = track(unique_key())
        bundle = factory()
        await FSMContext(storage=bundle.storage, key=key).set_state("state:before-shutdown")

        await bundle.close()
        await bundle.close()

        fresh = factory()
        assert (
            await FSMContext(storage=fresh.storage, key=key).get_state()
            == "state:before-shutdown"
        )
