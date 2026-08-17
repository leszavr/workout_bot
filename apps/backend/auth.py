"""Минимальная авторизация администратора внутреннего интерфейса.

Admin login + JWT. Учётные данные — только из переменных окружения
(ADMIN_LOGIN, ADMIN_PASSWORD, JWT_SECRET), не хардкодятся в исходниках.

Ограничения этого подхода (осознанные для внутреннего инструмента):
- один администратор, без RBAC;
- пароль сравнивается напрямую (без rate limiting);
- JWT без refresh-механизма, срок жизни 12 часов.
"""
from __future__ import annotations

import hmac
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from src.infrastructure.config import ADMIN_LOGIN, ADMIN_PASSWORD, JWT_SECRET

TOKEN_TTL_HOURS = 12
ALGORITHM = "HS256"

_bearer = HTTPBearer(auto_error=False)


class LoginRequest(BaseModel):
    login: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


def issue_token(login: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": login,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=TOKEN_TTL_HOURS)).timestamp()),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=ALGORITHM)


def verify_credentials(login: str, password: str) -> bool:
    if not ADMIN_LOGIN or not ADMIN_PASSWORD:
        return False
    login_ok = hmac.compare_digest(login, ADMIN_LOGIN)
    password_ok = hmac.compare_digest(password, ADMIN_PASSWORD)
    return login_ok and password_ok


def require_admin(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> str:
    """Dependency: проверяет JWT и возвращает login администратора."""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated"
        )
    if not JWT_SECRET:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="JWT_SECRET is not configured",
        )
    try:
        payload = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token"
        )
    return payload.get("sub", "admin")
