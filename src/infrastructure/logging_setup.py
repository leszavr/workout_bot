"""Единый подход к логированию.

В логи пишутся только: user internal ID, profile ID, event type, status,
error class, correlation ID. Содержимое ответов и полные профили НЕ логируются.
"""
from __future__ import annotations

import logging
import sys

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def setup_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    # Сторонние библиотеки приглушаем до WARNING, чтобы не тащить лишнее в логи.
    logging.getLogger("aiogram").setLevel(logging.WARNING)


def log_event(
    logger: logging.Logger,
    event: str,
    *,
    user_id: str | int | None = None,
    profile_id: str | None = None,
    status: str | None = None,
    error_class: str | None = None,
) -> None:
    """Структурированное событие без персональных данных."""
    parts = [f"event={event}"]
    if user_id is not None:
        parts.append(f"user_id={user_id}")
    if profile_id is not None:
        parts.append(f"profile_id={profile_id}")
    if status is not None:
        parts.append(f"status={status}")
    if error_class is not None:
        parts.append(f"error_class={error_class}")
    logger.info(" ".join(parts))
