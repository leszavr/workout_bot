"""Точка входа Telegram gateway.

    python -m apps.telegram_gateway.main

Gateway — независимая единица развёртывания в EU-сегменте (там доступен Telegram
API). Данных у него нет: анкета, профили, программы и состояние диалога живут в
RU и доступны только через internal API Backend. PostgreSQL и Redis Backend
Gateway недоступны.

Что делает процесс:

- принимает обновления Telegram и передаёт их Backend, показывая полученный ответ;
- скачивает фотографии оборудования и передаёт байты в RU, не сохраняя их;
- опрашивает очередь доставки и отправляет готовые файлы программ;
- сообщает о себе в Component Registry.

Redis остаётся, но только для служебных нужд aiogram (изоляция параллельных
обновлений одного пользователя). Ответов анкеты в нём больше нет — они в RU.

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

from apps.telegram_gateway import delivery_poller
from apps.telegram_gateway.component import build_heartbeat_client, gateway_metadata
from apps.telegram_gateway.handlers import dialog, errors
from apps.telegram_gateway.runtime import build_backend_client, set_backend_client
from src.infrastructure.config import (
    BOT_TOKEN,
    REDIS_URL,
    TELEGRAM_COMPONENT_ID,
    TELEGRAM_DELIVERY_BATCH_SIZE,
    TELEGRAM_DELIVERY_POLL_INTERVAL_SECONDS,
)
from src.infrastructure.logging_setup import setup_logging
from src.infrastructure.telegram.fsm_storage import create_fsm_storage

logger = logging.getLogger(__name__)


def resolve_fsm_url(url: str) -> str:
    """Redis нужен для изоляции параллельных обновлений одного пользователя.

    Без изоляции два быстрых сообщения обрабатывались бы одновременно, и второй
    ответ мог обогнать первый. Анкета при потере Redis не теряется: позиция и
    ответы лежат в RU.
    """
    if not url:
        raise RuntimeError(
            "REDIS_URL is empty. Set the environment variable REDIS_URL before "
            "running the bot: concurrent updates from one user must be serialised."
        )
    return url


def build_dispatcher(
    *, storage: BaseStorage, events_isolation: BaseEventIsolation
) -> Dispatcher:
    """Два роутера: обработчик ошибок хранилища и диалог.

    Прежнее деление на start/questionnaire/review отражало структуру анкеты.
    Этого знания здесь нет — любое обновление обрабатывается одинаково.

    Error router идёт первым: диалог реагирует на любой текст и иначе перехватил
    бы обновление до обработчика сбоя Redis.
    """
    dispatcher = Dispatcher(storage=storage, events_isolation=events_isolation)
    dispatcher.include_routers(errors.router, dialog.router)
    return dispatcher


async def main() -> None:
    setup_logging()
    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN is empty. Set the environment variable BOT_TOKEN before running the bot."
        )

    fsm = create_fsm_storage(resolve_fsm_url(REDIS_URL))
    await fsm.verify()

    backend = build_backend_client()
    set_backend_client(backend)

    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = build_dispatcher(storage=fsm.storage, events_isolation=fsm.events_isolation)

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
        # aiogram закрывает storage сам; close идемпотентен и гарантирует
        # освобождение пула соединений при любом пути остановки.
        await fsm.close()
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
