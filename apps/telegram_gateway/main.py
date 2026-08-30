"""Точка входа Telegram gateway.

python -m apps.telegram_gateway.main

Состояние анкеты хранится в Redis (`REDIS_URL`): оно переживает перезапуск
процесса и одинаково доступно всем экземплярам приложения. Соединение
проверяется до старта polling и закрывается при остановке.

Gateway разворачивается отдельно от Backend (EU-сегмент), поэтому сообщает о
себе в Component Registry: версию, build, версию контракта и capabilities.
Регистрация не является условием запуска — при недоступном Backend бот
продолжает обслуживать анкеты, а heartbeat возобновится сам.
"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.base import BaseEventIsolation, BaseStorage

from apps.telegram_gateway.component import build_heartbeat_client, gateway_metadata
from apps.telegram_gateway.handlers import errors, questionnaire, review, start
from src.infrastructure.config import BOT_TOKEN, REDIS_URL
from src.infrastructure.logging_setup import setup_logging
from src.infrastructure.telegram.fsm_storage import create_fsm_storage

logger = logging.getLogger(__name__)


def resolve_fsm_url(url: str) -> str:
    """Без Redis бот не запускается: in-memory состояние теряет анкеты."""
    if not url:
        raise RuntimeError(
            "REDIS_URL is empty. Set the environment variable REDIS_URL before running "
            "the bot: questionnaire state must survive process restart."
        )
    return url


def build_dispatcher(
    *, storage: BaseStorage, events_isolation: BaseEventIsolation
) -> Dispatcher:
    dispatcher = Dispatcher(storage=storage, events_isolation=events_isolation)
    # Error router идёт первым: анкета реагирует на любой текст и иначе
    # перехватила бы обновление до обработчика ошибок хранилища.
    dispatcher.include_routers(
        errors.router,
        start.router,
        questionnaire.router,
        review.router,
    )
    return dispatcher


async def main() -> None:
    setup_logging()
    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN is empty. Set the environment variable BOT_TOKEN before running the bot."
        )

    fsm = create_fsm_storage(resolve_fsm_url(REDIS_URL))
    await fsm.verify()

    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = build_dispatcher(storage=fsm.storage, events_isolation=fsm.events_isolation)

    heartbeat = build_heartbeat_client()
    heartbeat_task = asyncio.create_task(heartbeat.run()) if heartbeat else None

    metadata = gateway_metadata()
    logger.info(
        "event=telegram_gateway_started fsm_storage=redis component_id=%s "
        "version=%s contract=%s heartbeat=%s",
        metadata.component_id,
        metadata.version,
        metadata.contract_version,
        "on" if heartbeat_task else "off",
    )
    try:
        await dp.start_polling(bot)
    finally:
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            # Ждём фактического завершения: иначе httpx-клиент закрывается на
            # незавершённом запросе и в лог попадает шум при остановке.
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
            await heartbeat.close()
        # aiogram закрывает storage сам; close идемпотентен и гарантирует
        # освобождение пула соединений при любом пути остановки.
        await fsm.close()
        logger.info("event=telegram_gateway_stopped")


if __name__ == "__main__":
    asyncio.run(main())
