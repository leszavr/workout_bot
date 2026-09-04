"""Точка входа Telegram gateway.

    python -m apps.telegram_gateway.main

Gateway — независимая единица развёртывания в EU-сегменте (там доступен Telegram
API). Данных у него нет: анкета, профили, программы и состояние диалога живут в
RU и доступны только через internal API Backend. Хранилищ у Gateway нет вовсе —
ни PostgreSQL, ни Redis, ни MinIO.

Что делает процесс:

- принимает обновления Telegram и передаёт их Backend, показывая полученный ответ;
- скачивает фотографии оборудования и передаёт байты в RU, не сохраняя их;
- опрашивает очередь доставки и отправляет готовые файлы программ;
- сообщает о себе в Component Registry.

Регистрация в реестре не является условием запуска: при недоступном Backend бот
продолжает работать, сообщая пользователю о временной недоступности, и heartbeat
возобновляется сам.
"""
from __future__ import annotations

import asyncio
import logging
import signal

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.base import BaseEventIsolation, BaseStorage
from aiogram.fsm.storage.memory import MemoryStorage, SimpleEventIsolation

from apps.telegram_gateway import delivery_poller
from apps.telegram_gateway.component import build_heartbeat_client, gateway_metadata
from apps.telegram_gateway.handlers import dialog
from apps.telegram_gateway.runtime import build_backend_client, set_backend_client
from src.infrastructure.config import (
    BOT_TOKEN,
    TELEGRAM_COMPONENT_ID,
    TELEGRAM_DELIVERY_BATCH_SIZE,
    TELEGRAM_DELIVERY_POLL_INTERVAL_SECONDS,
)
from src.infrastructure.logging_setup import setup_logging

logger = logging.getLogger(__name__)


def build_isolation() -> tuple[BaseStorage, BaseEventIsolation]:
    """Служебное состояние aiogram — только в памяти процесса.

    aiogram требует storage и isolation: FSM-middleware читает состояние на
    каждом обновлении, а isolation сериализует параллельные обновления одного
    пользователя, иначе второй ответ мог бы обогнать первый.

    Внешнее хранилище для этого не нужно. Хендлеры FSM не используют — позиция
    диалога и ответы лежат в RU, — поэтому терять при рестарте нечего. Общая
    блокировка между процессами тоже не нужна: `getUpdates` с одним токеном
    обслуживает ровно один процесс (второй получает от Telegram 409 Conflict),
    а очередь доставки защищена арендой в PostgreSQL, а не блокировкой здесь.

    Так у Gateway не остаётся ни адреса, ни клиента внешнего хранилища: в EU
    нечему хранить пользовательские данные и нечего случайно переподключить к
    хранилищам RU.
    """
    return MemoryStorage(), SimpleEventIsolation()


def build_dispatcher(
    *, storage: BaseStorage, events_isolation: BaseEventIsolation
) -> Dispatcher:
    """Один роутер: любое обновление обрабатывается одинаково.

    Прежнее деление на start/questionnaire/review отражало структуру анкеты.
    Этого знания здесь нет. Отдельного обработчика сбоя хранилища тоже нет:
    хранилище процесса локальное, а недоступность Backend обрабатывает диалог.
    """
    dispatcher = Dispatcher(storage=storage, events_isolation=events_isolation)
    dispatcher.include_router(dialog.router)
    return dispatcher


async def main() -> None:
    setup_logging()
    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN is empty. Set the environment variable BOT_TOKEN before running the bot."
        )

    fsm_storage, events_isolation = build_isolation()

    backend = build_backend_client()
    set_backend_client(backend)

    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = build_dispatcher(storage=fsm_storage, events_isolation=events_isolation)

    heartbeat = build_heartbeat_client()
    heartbeat_task = asyncio.create_task(heartbeat.run()) if heartbeat else None

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        # Поллер доставки нужно остановить вместе с polling: иначе он продолжал
        # бы забирать задания из очереди, пока процесс завершается.
        loop.add_signal_handler(sig, stop.set)

    poller_task = asyncio.create_task(
        delivery_poller.run_delivery_poller(
            bot=bot,
            client=backend,
            owner=TELEGRAM_COMPONENT_ID,
            interval_seconds=TELEGRAM_DELIVERY_POLL_INTERVAL_SECONDS,
            limit=TELEGRAM_DELIVERY_BATCH_SIZE,
            stop=stop,
        )
    )

    metadata = gateway_metadata()
    logger.info(
        "event=telegram_gateway_started component_id=%s version=%s contract=%s "
        "delivery_interval=%s heartbeat=%s",
        metadata.component_id,
        metadata.version,
        metadata.contract_version,
        TELEGRAM_DELIVERY_POLL_INTERVAL_SECONDS,
        "on" if heartbeat_task else "off",
    )
    try:
        await dp.start_polling(bot)
    finally:
        stop.set()
        await _cancel(poller_task)
        if heartbeat_task is not None:
            await _cancel(heartbeat_task)
            await heartbeat.close()
        await backend.close()
        set_backend_client(None)
        logger.info("event=telegram_gateway_stopped")


async def _cancel(task: asyncio.Task) -> None:
    """Останавливает задачу и ждёт завершения.

    Ждём фактического завершения: иначе httpx-клиент закрывается на
    незавершённом запросе и в лог попадает шум при остановке.
    """
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


if __name__ == "__main__":
    asyncio.run(main())
