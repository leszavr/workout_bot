"""Устойчивое FSM-хранилище анкеты (Phase 1.2-A).

`MemoryStorage` теряет состояние при перезапуске процесса и не работает при
нескольких экземплярах приложения: каждый процесс видит только свою память.
Здесь состояние анкеты хранится в Redis, поэтому оно переживает restart и
общее для всех экземпляров.

Redis используется только для runtime-состояния анкеты. Бизнес-данные
(профили, программы) остаются в PostgreSQL: сюда пишется черновик, который
после подтверждения сохраняется репозиторием.

Сбой Redis нормализуется в `FSMStorageError`, чтобы transport-слой мог
ответить пользователю понятным сообщением, а не молча потерять обновление.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

from aiogram.fsm.storage.base import (
    BaseEventIsolation,
    BaseStorage,
    DefaultKeyBuilder,
    StateType,
    StorageKey,
)
from aiogram.fsm.storage.redis import RedisEventIsolation, RedisStorage
from redis.asyncio import Redis
from redis.exceptions import RedisError

from src.errors import FSMStorageError

logger = logging.getLogger(__name__)

# Ключи включают bot_id: один Redis может обслуживать несколько ботов
# (например, рабочего и тестового), и их состояния не должны смешиваться.
KEY_BUILDER = DefaultKeyBuilder(with_bot_id=True)


class _GuardedStorage(BaseStorage):
    """Переводит ошибки Redis в `FSMStorageError`."""

    def __init__(self, inner: BaseStorage) -> None:
        self._inner = inner

    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        with _as_storage_error("set_state"):
            await self._inner.set_state(key=key, state=state)

    async def get_state(self, key: StorageKey) -> str | None:
        with _as_storage_error("get_state"):
            return await self._inner.get_state(key=key)

    async def set_data(self, key: StorageKey, data: dict[str, Any]) -> None:
        with _as_storage_error("set_data"):
            await self._inner.set_data(key=key, data=data)

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        with _as_storage_error("get_data"):
            return await self._inner.get_data(key=key)

    async def close(self) -> None:
        with _as_storage_error("close"):
            await self._inner.close()


class _GuardedIsolation(BaseEventIsolation):
    """Блокировка обновлений с той же нормализацией ошибок."""

    def __init__(self, inner: BaseEventIsolation) -> None:
        self._inner = inner

    @asynccontextmanager
    async def lock(self, key: StorageKey) -> AsyncGenerator[None, None]:
        with _as_storage_error("lock"):
            async with self._inner.lock(key=key):
                yield None

    async def close(self) -> None:
        with _as_storage_error("close"):
            await self._inner.close()


class FSMStorage:
    """Ресурсы FSM одного процесса: storage, isolation и их закрытие.

    После выноса Gateway за сетевую границу состояние анкеты здесь не хранится:
    позиция диалога и ответы живут в PostgreSQL (RU). Redis остаётся для
    служебных нужд aiogram — изоляции параллельных обновлений одного
    пользователя и технических ключей middleware.

    TTL обязателен: EU-сегмент не является системой хранения, и ключ без срока
    жизни превратил бы техническое состояние в постоянное. Срок задаётся
    конфигурацией (`GATEWAY_STATE_TTL_SECONDS`, по умолчанию сутки) — с запасом
    на прохождение анкеты в несколько заходов.
    """

    def __init__(self, client: Redis, *, ttl_seconds: int) -> None:
        self._client = client
        self._closed = False
        self.storage: BaseStorage = _GuardedStorage(
            RedisStorage(
                redis=client,
                key_builder=KEY_BUILDER,
                state_ttl=ttl_seconds,
                data_ttl=ttl_seconds,
            )
        )
        self.events_isolation: BaseEventIsolation = _GuardedIsolation(
            RedisEventIsolation(redis=client, key_builder=KEY_BUILDER)
        )

    async def verify(self) -> None:
        """Проверка доступности до старта: без неё анкета сломается на первом ответе."""
        with _as_storage_error("ping"):
            await self._client.ping()

    async def close(self) -> None:
        """Идемпотентно: aiogram закрывает storage сам, вызов дублируется при shutdown."""
        if self._closed:
            return
        self._closed = True
        with _as_storage_error("close"):
            await self._client.aclose(close_connection_pool=True)


def create_fsm_storage(url: str, *, ttl_seconds: int | None = None) -> FSMStorage:
    """Storage по строке подключения. Соединение открывается лениво."""
    from src.infrastructure.config import GATEWAY_STATE_TTL_SECONDS

    return FSMStorage(
        Redis.from_url(url),
        ttl_seconds=ttl_seconds or GATEWAY_STATE_TTL_SECONDS,
    )


class _as_storage_error:
    """Контекст, приводящий `RedisError` к `FSMStorageError` без утечки данных."""

    def __init__(self, operation: str) -> None:
        self._operation = operation

    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type, exc, traceback) -> bool:
        if exc_type is not None and issubclass(exc_type, RedisError):
            logger.error(
                "event=fsm_storage_unavailable operation=%s error_class=%s",
                self._operation,
                exc_type.__name__,
            )
            raise FSMStorageError(
                f"FSM storage is unavailable (operation={self._operation})"
            ) from exc
        return False
