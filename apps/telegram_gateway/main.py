"""Точка входа Telegram gateway.

python -m apps.telegram_gateway.main
"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from apps.telegram_gateway.handlers import questionnaire, review, start
from src.infrastructure.config import BOT_TOKEN
from src.infrastructure.logging_setup import setup_logging

logger = logging.getLogger(__name__)


async def main() -> None:
    setup_logging()
    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN is empty. Set the environment variable BOT_TOKEN before running the bot."
        )

    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_routers(
        start.router,
        questionnaire.router,
        review.router,
    )

    logger.info("event=telegram_gateway_started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
