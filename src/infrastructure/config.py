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

# --- Распределённые компоненты (Component Registry) ---------------------------
# Компоненты разворачиваются независимо и в разных сегментах сети (RU/EU),
# поэтому каждый экземпляр должен уметь назвать себя. Значения по умолчанию
# рассчитаны на локальную разработку с одним экземпляром каждого типа.
BUILD_SHA = os.getenv("BUILD_SHA", "")
COMPONENT_REGION = os.getenv("COMPONENT_REGION", "RU")
# Идентификатор экземпляра Telegram Gateway. Разные экземпляры одного типа
# обязаны иметь разные значения, иначе они перезапишут друг друга в реестре.
TELEGRAM_COMPONENT_ID = os.getenv("TELEGRAM_COMPONENT_ID", "telegram-local-1")
TELEGRAM_COMPONENT_NAME = os.getenv("TELEGRAM_COMPONENT_NAME", "Telegram Gateway")
# Адрес Backend для регистрации и heartbeat. Пусто — компонент работает без
# регистрации (локальная разработка): реестр не должен быть условием запуска.
BACKEND_INTERNAL_URL = os.getenv("BACKEND_INTERNAL_URL", "")
# Общий секрет service-to-service вызовов внутреннего API. Пусто — internal
# API отвечает 503: принимать неаутентифицированные heartbeat нельзя.
INTERNAL_SERVICE_TOKEN = os.getenv("INTERNAL_SERVICE_TOKEN", "")

DEFAULT_TIMEZONE = "UTC"
MAX_TEXT_LENGTH = 2000
MAX_PHOTOS = 10
MAX_PHOTO_SIZE_MB = 20

# --- Worker: retry и recovery фоновых операций (Phase 1.2-D) -------------------
#
# Значения подобраны под фактические характеристики контура, а не «круглые»:
#
# WORKER_POLL_INTERVAL_SECONDS=15 — цикл опроса. Меньше не нужно: повтор
# transient-отказа не срочен, а холостой опрос — это запрос к PostgreSQL.
#
# WORKER_MAX_ATTEMPTS=3 — всего попыток на операцию, то есть исходная и два
# повтора. Внутри одной попытки AI-контур уже перебирает все подключённые
# модели (это его собственный механизм), поэтому третий внешний повтор лечил бы
# только длительную недоступность провайдера — а её решают планово, а не
# повторами.
#
# WORKER_RETRY_INITIAL_DELAY_SECONDS=60 при множителе 4 даёт паузы 60 с и 240 с.
# Первая пауза больше, чем типичный сетевой сбой и rate limit окно провайдера;
# верхняя граница 900 с не даёт повтору уехать за пределы разумного ожидания
# пользователя, который уже видел «формируем программу».
#
# WORKER_LEASE_SECONDS=1860 — аренда чуть больше максимального бюджета
# генерации (MAX_TOTAL_BUDGET_SECONDS = 1800 с в AI-контуре). Короткая аренда
# отобрала бы job у живого исполнителя и запустила бы вторую генерацию; аренда
# продлевается не автоматически, поэтому её длина должна покрывать легальную
# длительность работы.
WORKER_POLL_INTERVAL_SECONDS = float(os.getenv("WORKER_POLL_INTERVAL_SECONDS", "15"))
WORKER_MAX_ATTEMPTS = int(os.getenv("WORKER_MAX_ATTEMPTS", "3"))
WORKER_RETRY_INITIAL_DELAY_SECONDS = float(
    os.getenv("WORKER_RETRY_INITIAL_DELAY_SECONDS", "60")
)
WORKER_RETRY_MULTIPLIER = float(os.getenv("WORKER_RETRY_MULTIPLIER", "4"))
WORKER_RETRY_MAX_DELAY_SECONDS = float(
    os.getenv("WORKER_RETRY_MAX_DELAY_SECONDS", "900")
)
WORKER_LEASE_SECONDS = float(os.getenv("WORKER_LEASE_SECONDS", "1860"))
WORKER_BATCH_SIZE = int(os.getenv("WORKER_BATCH_SIZE", "5"))
# Идентификатор экземпляра worker'а: попадает в `lease_owner` и в Component
# Registry. Разные экземпляры обязаны иметь разные значения, иначе аренда одного
# будет продлеваться от имени другого.
WORKER_COMPONENT_ID = os.getenv("WORKER_COMPONENT_ID", "worker-local-1")
WORKER_COMPONENT_NAME = os.getenv("WORKER_COMPONENT_NAME", "Background Worker")
# WORKER_DELIVERY_ENABLED удалён вместе с выносом Gateway за сетевую границу:
# отправку выполняет Gateway (только у него есть доступ к Bot API), а worker
# восстанавливает застрявшие записи доставки — это нужно всегда и выключателя не
# требует.

# --- Telegram Gateway за сетевой границей ---------------------------------------
#
# Gateway размещается в EU (там доступен Telegram API), Backend с данными — в RU.
# Прямого доступа к PostgreSQL у Gateway нет; всё идёт через internal API.
#
# TELEGRAM_DELIVERY_POLL_INTERVAL_SECONDS=5 — интервал опроса очереди отправки.
# Инициатором может быть только Gateway: он за NAT, входящих подключений к нему
# нет. Пять секунд — это задержка между «программа готова» и «файл ушёл»;
# пользователь к этому моменту уже ждёт минуты, поэтому меньше не нужно, а
# холостой опрос — это запрос в RU через туннель.
#
# TELEGRAM_DELIVERY_LEASE_SECONDS=300 — аренда захваченного задания. Покрывает
# рендер HTML с изображениями и отправку документа в Telegram с повторами.
# Короткая аренда отдала бы задание второму экземпляру, и пользователь получил
# бы файл дважды.
TELEGRAM_DELIVERY_POLL_INTERVAL_SECONDS = float(
    os.getenv("TELEGRAM_DELIVERY_POLL_INTERVAL_SECONDS", "5")
)
TELEGRAM_DELIVERY_LEASE_SECONDS = float(
    os.getenv("TELEGRAM_DELIVERY_LEASE_SECONDS", "300")
)
TELEGRAM_DELIVERY_BATCH_SIZE = int(os.getenv("TELEGRAM_DELIVERY_BATCH_SIZE", "5"))
# Таймаут запроса к Backend. Больше него ждать нет смысла: Telegram всё равно
# переотправит обновление, а идемпотентность по update_id не даст обработать его
# дважды.
BACKEND_REQUEST_TIMEOUT_SECONDS = float(
    os.getenv("BACKEND_REQUEST_TIMEOUT_SECONDS", "15")
)
# Повторы запроса к Backend внутри обработки одного события. Три попытки с
# короткой паузой лечат мгновенную заминку туннеля RU↔EU, пока пользователь ещё
# ждёт ответа. Дольше держать его в тишине нельзя.
BACKEND_REQUEST_RETRIES = int(os.getenv("BACKEND_REQUEST_RETRIES", "3"))
BACKEND_RETRY_DELAY_SECONDS = float(os.getenv("BACKEND_RETRY_DELAY_SECONDS", "1"))
