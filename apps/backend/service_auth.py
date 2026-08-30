"""Аутентификация service-to-service вызовов внутреннего API.

Отдельный механизм от JWT админки намеренно: у Telegram Gateway нет
пользователя, роли и срока сессии — это процесс, а не человек. Выдавать ему
admin-JWT значило бы дать доступ ко всему Admin API вместо одного endpoint'а
регистрации.

Общий секрет (`INTERNAL_SERVICE_TOKEN`) выбран как минимально достаточный
механизм: канал между RU и EU уже защищён TLS/туннелем, а mTLS или подписанные
запросы потребовали бы отдельной инфраструктуры ключей без выигрыша на этом
этапе.

Если секрет не задан, internal API отвечает 503, а не пропускает запрос:
принимать неаутентифицированные heartbeat нельзя, даже в dev-окружении.
Сравнение — `compare_digest`, чтобы время ответа не зависело от совпавшего
префикса.
"""
from __future__ import annotations

import hmac

from fastapi import Header, HTTPException, status

from src.infrastructure.config import INTERNAL_SERVICE_TOKEN

SERVICE_TOKEN_HEADER = "X-Internal-Service-Token"


async def require_service_token(
    token: str | None = Header(default=None, alias=SERVICE_TOKEN_HEADER),
) -> None:
    if not INTERNAL_SERVICE_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="INTERNAL_SERVICE_TOKEN is not configured",
        )
    if not token or not hmac.compare_digest(token, INTERNAL_SERVICE_TOKEN):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid service token",
        )
