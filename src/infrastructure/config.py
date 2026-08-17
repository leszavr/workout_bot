"""Конфигурация приложения.

Все пути — абсолютные, вычисляются от корня проекта.
Секреты читаются только из переменных окружения / .env.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BASE_DIR / ".env")

DATA_DIR = Path(os.getenv("WORKOUT_DATA_DIR", str(BASE_DIR / "data"))).resolve()
PROFILES_DIR = DATA_DIR / "profiles"
PHOTOS_DIR = DATA_DIR / "photos"
LOGS_DIR = DATA_DIR / "logs"

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", "")

# PostgreSQL. Если DATABASE_URL не задан — используется файловое хранилище (dev/test).
DATABASE_URL = os.getenv("DATABASE_URL", "")

# Внутренний веб-интерфейс: учётные данные администратора (только из env).
ADMIN_LOGIN = os.getenv("ADMIN_LOGIN", "")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
JWT_SECRET = os.getenv("JWT_SECRET", "")

DEFAULT_TIMEZONE = "UTC"
MAX_TEXT_LENGTH = 2000
MAX_PHOTOS = 10
MAX_PHOTO_SIZE_MB = 20
