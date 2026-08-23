"""Авторизация внутреннего интерфейса: пользователи из БД + аварийный вход.

Порядок проверки при входе:

1. пользователь из таблицы `admin_users` (пароль хранится как scrypt-хеш);
2. если совпадения нет — администратор из переменных окружения
   (`ADMIN_LOGIN`/`ADMIN_PASSWORD`).

Env-администратор оставлен намеренно как **аварийный вход**: если база пуста
или все пароли утрачены, доступ к системе не теряется. В интерфейсе такой
вход помечается отдельно, роль у него всегда `admin`.

Роли:
- `admin`  — полный доступ, включая управление пользователями;
- `viewer` — только чтение.

Ограничение viewer'а обеспечивается сервером: изменяющие endpoint'ы зависят
от `require_admin`, а не от того, скрыта ли кнопка в интерфейсе.

Три зависимости с разным смыслом:
- `current_user`   — любой аутентифицированный, БЕЗ проверки обязательной
  смены пароля (нужна самим endpoint'ам смены пароля);
- `require_viewer` — любой аутентифицированный, доступ к чтению;
- `require_admin`  — только роль `admin`, доступ к записи.

Для пользователей из БД JWT содержит только идентификатор пользователя как
ссылку на серверное состояние. На каждом защищённом запросе активность, роль
и `must_change_password` перечитываются из БД. Поэтому деактивация или смена
роли действует немедленно, не дожидаясь истечения JWT.

Осознанные ограничения: нет refresh-токенов (срок жизни 12 часов), нет
rate limiting на попытки входа (Phase 1.3). Аварийный env-администратор
валидируется только по JWT и изменяется через конфигурацию сервера.
"""
from __future__ import annotations

import hmac
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from src.domain.auth import AdminRole
from src.infrastructure.config import ADMIN_LOGIN, ADMIN_PASSWORD, JWT_SECRET
from src.infrastructure.persistence.postgres.admin_user_repository import AdminUserRepository
from src.infrastructure.persistence.postgres.db import get_session_factory

TOKEN_TTL_HOURS = 12
ALGORITHM = "HS256"
PASSWORD_CHANGE_REQUIRED = "password_change_required"

_bearer = HTTPBearer(auto_error=False)


class LoginRequest(BaseModel):
    login: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str = AdminRole.ADMIN.value
    must_change_password: bool = False


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


@dataclass(frozen=True)
class AuthenticatedUser:
    """Кто выполняет запрос.

    `user_id=None` — вход выполнен аварийным env-администратором: записи в
    таблице пользователей у него нет.
    """

    login: str
    role: AdminRole
    user_id: int | None = None
    must_change_password: bool = False

    @property
    def is_env_admin(self) -> bool:
        return self.user_id is None

    @property
    def can_write(self) -> bool:
        return self.role is AdminRole.ADMIN


def env_admin_configured() -> bool:
    return bool(ADMIN_LOGIN and ADMIN_PASSWORD)


def verify_env_admin(login: str, password: str) -> bool:
    """Аварийный вход из переменных окружения."""
    if not env_admin_configured():
        return False
    login_ok = hmac.compare_digest(login, ADMIN_LOGIN)
    password_ok = hmac.compare_digest(password, ADMIN_PASSWORD)
    return login_ok and password_ok


def issue_token(
    login: str,
    *,
    role: AdminRole = AdminRole.ADMIN,
    user_id: int | None = None,
    must_change_password: bool = False,
) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": login,
        "role": role.value,
        "uid": user_id,
        "pwd": must_change_password,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=TOKEN_TTL_HOURS)).timestamp()),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=ALGORITHM)


def _decode(credentials: HTTPAuthorizationCredentials | None) -> dict:
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
        return jwt.decode(credentials.credentials, JWT_SECRET, algorithms=[ALGORITHM])
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        ) from exc


async def current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> AuthenticatedUser:
    """Возвращает текущего пользователя из актуального server-side state.

    Для DB-пользователя JWT лишь идентифицирует запись. Роль, активность и
    требование смены пароля берутся из PostgreSQL на каждом запросе. Это
    немедленно отзывает доступ после disable/delete/demotion и не позволяет
    старой роли из JWT продолжать давать права.
    """
    payload = _decode(credentials)
    user_id = payload.get("uid")

    if user_id is not None:
        try:
            repository = AdminUserRepository(get_session_factory())
            stored = await repository.get(int(user_id))
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Authentication state is temporarily unavailable",
            ) from exc

        if stored is None or not stored.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired token",
            )

        return AuthenticatedUser(
            login=stored.login,
            role=stored.role,
            user_id=stored.id,
            must_change_password=stored.must_change_password,
        )

    raw_role = payload.get("role", AdminRole.ADMIN.value)
    try:
        role = AdminRole(raw_role)
    except ValueError:
        role = AdminRole.VIEWER
    return AuthenticatedUser(
        login=payload.get("sub", "admin"),
        role=role,
        user_id=None,
        must_change_password=bool(payload.get("pwd", False)),
    )


def _ensure_password_is_current(user: AuthenticatedUser) -> None:
    if user.must_change_password:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=PASSWORD_CHANGE_REQUIRED,
        )


def require_viewer(
    user: AuthenticatedUser = Depends(current_user),
) -> AuthenticatedUser:
    """Доступ на чтение: любая роль, но пароль должен быть актуальным."""
    _ensure_password_is_current(user)
    return user


def require_admin(
    user: AuthenticatedUser = Depends(current_user),
) -> AuthenticatedUser:
    """Доступ на запись: только роль admin."""
    _ensure_password_is_current(user)
    if not user.can_write:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Требуется роль администратора",
        )
    return user
