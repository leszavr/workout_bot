"""Разделяемые объекты процесса Gateway.

Клиент Backend один на процесс: у `httpx.AsyncClient` пул соединений, и создание
клиента на каждое обновление означало бы новое TCP- и TLS-соединение через
туннель RU↔EU на каждое нажатие кнопки.

Модуль существует, чтобы хендлерам не требовался доступ к точке входа: импорт
`main` из хендлера дал бы цикл (main регистрирует роутеры).
"""
from __future__ import annotations

from apps.telegram_gateway.backend_client import BackendClient
from src.infrastructure.config import (
    BACKEND_INTERNAL_URL,
    BACKEND_REQUEST_RETRIES,
    BACKEND_REQUEST_TIMEOUT_SECONDS,
    BACKEND_RETRY_DELAY_SECONDS,
    INTERNAL_SERVICE_TOKEN,
)

_client: BackendClient | None = None


def build_backend_client() -> BackendClient:
    """Создаёт клиент. Вызывается один раз при старте процесса."""
    if not BACKEND_INTERNAL_URL:
        raise RuntimeError(
            "BACKEND_INTERNAL_URL is empty. The gateway has no other way to reach "
            "domain data: PostgreSQL is not available to it."
        )
    if not INTERNAL_SERVICE_TOKEN:
        raise RuntimeError(
            "INTERNAL_SERVICE_TOKEN is empty. The internal API rejects "
            "unauthenticated requests."
        )
    return BackendClient(
        base_url=BACKEND_INTERNAL_URL,
        service_token=INTERNAL_SERVICE_TOKEN,
        timeout_seconds=BACKEND_REQUEST_TIMEOUT_SECONDS,
        retries=BACKEND_REQUEST_RETRIES,
        retry_delay_seconds=BACKEND_RETRY_DELAY_SECONDS,
    )


def set_backend_client(client: BackendClient | None) -> None:
    global _client
    _client = client


def get_backend_client() -> BackendClient:
    if _client is None:
        raise RuntimeError("Backend client is not initialised")
    return _client
