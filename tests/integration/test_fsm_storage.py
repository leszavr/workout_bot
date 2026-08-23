"""Integration-тесты устойчивого FSM-хранилища анкеты (Phase 1.2-A).

Требуют `REDIS_URL` в окружении. Без этой переменной тесты пропускаются,
а не «проходят»: проверять restart-safe поведение на MemoryStorage нельзя.

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

from apps.telegram_gateway.handlers.common import load_profile, store_profile
from src.application.profiles.finalization import ProfileFinalizationService
from src.application.questionnaire.service import QuestionnaireService
from src.domain.enums import CompletionStatus
from src.errors import FSMStorageError
from src.infrastructure.config import REDIS_URL
from src.infrastructure.files.storage import LocalFileStorage
from src.infrastructure.persistence.profile_repository import FileProfileRepository
from src.infrastructure.telegram.fsm_storage import KEY_BUILDER, create_fsm_storage
from tests.integration.test_full_scenario import run_questionnaire

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


class TestQuestionnaireStillWorks:
    """D. Завершение анкеты продолжает работать через persistent storage."""

    async def test_full_questionnaire_round_trip_and_finalize(self, redis_fsm, tmp_path):
        factory, track = redis_fsm
        key = track(unique_key())
        bundle = factory()
        context = FSMContext(storage=bundle.storage, key=key)

        service = QuestionnaireService(
            LocalFileStorage(tmp_path / "photos", max_files=10, max_size_mb=20)
        )
        profile = run_questionnaire(service)
        await store_profile(context, profile)

        # Профиль пережил сериализацию в Redis без потерь.
        restored = await load_profile(context)
        assert restored is not None
        assert restored.model_dump(mode="json") == profile.model_dump(mode="json")

        repository = FileProfileRepository(tmp_path / "profiles", tmp_path / "counter.json")
        result = await ProfileFinalizationService(repository).finalize(restored)
        assert result.profile.questionnaire.completion_status is CompletionStatus.CONFIRMED
        assert await repository.exists(result.profile.profile_id)


class TestRedisFailure:
    """E. Недоступность Redis обрабатывается предсказуемо."""

    async def test_startup_check_fails_fast_with_normalized_error(self):
        bundle = create_fsm_storage(UNREACHABLE_REDIS_URL)
        try:
            with pytest.raises(FSMStorageError):
                await bundle.verify()
        finally:
            await bundle.close()

    async def test_runtime_failure_is_not_silent_empty_state(self, tmp_path):
        """Сбой Redis не должен выглядеть как «анкеты нет»."""
        bundle = create_fsm_storage(UNREACHABLE_REDIS_URL)
        context = FSMContext(storage=bundle.storage, key=unique_key())
        try:
            with pytest.raises(FSMStorageError):
                await load_profile(context)
        finally:
            await bundle.close()

    async def test_business_data_is_untouched_when_fsm_is_broken(self, tmp_path):
        """PostgreSQL/бизнес-хранилище не задето сбоем runtime state."""
        repository = FileProfileRepository(tmp_path / "profiles", tmp_path / "counter.json")
        bundle = create_fsm_storage(UNREACHABLE_REDIS_URL)
        context = FSMContext(storage=bundle.storage, key=unique_key())
        service = QuestionnaireService(
            LocalFileStorage(tmp_path / "photos", max_files=10, max_size_mb=20)
        )
        profile = service.start_profile("999", "broken-fsm")
        try:
            with pytest.raises(FSMStorageError):
                await store_profile(context, profile)
        finally:
            await bundle.close()
        assert not await repository.exists(profile.profile_id)


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
