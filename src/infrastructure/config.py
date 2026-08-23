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

# Redis — устойчивое runtime-состояние анкеты (FSM). Бизнес-данные остаются в
# PostgreSQL. Обязателен для Telegram-бота: на MemoryStorage анкета теряется
# при перезапуске и не работает при нескольких экземплярах приложения.
REDIS_URL = os.getenv("REDIS_URL", "")

# Внутренний веб-интерфейс: учётные данные администратора (только из env).
ADMIN_LOGIN = os.getenv("ADMIN_LOGIN", "")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
JWT_SECRET = os.getenv("JWT_SECRET", "")

# Ключ шифрования AI-секретов at rest (Fernet). Если не задан — выводится
# из JWT_SECRET (dev-режим); в production задайте отдельный AI_SECRETS_KEY.
AI_SECRETS_KEY = os.getenv("AI_SECRETS_KEY", "")

# MinIO (S3-compatible object storage) для медиа упражнений.
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "localhost:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "")
MINIO_SECURE = os.getenv("MINIO_SECURE", "false").lower() in ("1", "true", "yes")
MEDIA_BUCKET = os.getenv("MEDIA_BUCKET", "workout-media")
# Публичный базовый URL backend для абсолютных ссылок в HTML (url-режим).
# Пример: http://localhost:8000
MEDIA_PUBLIC_BASE_URL = os.getenv("MEDIA_PUBLIC_BASE_URL", "")

# Media pipeline.
# Максимум медиа-ассетов на одно упражнение (лимит импорта/вывода,
# не ограничение схемы БД).
EXERCISE_MEDIA_MAX_PER_EXERCISE = int(os.getenv("EXERCISE_MEDIA_MAX_PER_EXERCISE", "5"))
# html (base64 data-URI в HTML) | url (абсолютные URL media endpoint).
PROGRAM_HTML_MEDIA_MODE = os.getenv("PROGRAM_HTML_MEDIA_MODE", "html")

# Генерация программ (Stage 5).
# primary_generator / fallback_generator: ai | deterministic.
PROGRAM_PRIMARY_GENERATOR = os.getenv("PROGRAM_PRIMARY_GENERATOR", "ai")
PROGRAM_FALLBACK_GENERATOR = os.getenv("PROGRAM_FALLBACK_GENERATOR", "deterministic")
# Автоматическая генерация после финализации анкеты.
AUTO_GENERATE_PROGRAM_AFTER_FINALIZE = (
    os.getenv("AUTO_GENERATE_PROGRAM_AFTER_FINALIZE", "true").lower() in ("1", "true", "yes")
)

DEFAULT_TIMEZONE = "UTC"
MAX_TEXT_LENGTH = 2000
MAX_PHOTOS = 10
MAX_PHOTO_SIZE_MB = 20
