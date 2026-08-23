"""Admin API: управление пользователями внутреннего интерфейса.

Все endpoint'ы требуют роль `admin` (`require_admin`): viewer не может ни
создавать пользователей, ни менять роли, ни сбрасывать пароли.

Гарантии безопасности:
- хеш пароля не возвращается ни в одном ответе (только `has_password`);
- временный пароль после сброса отдаётся ровно один раз, в ответе на сам
  сброс, и нигде не хранится в открытом виде;
- нельзя удалить себя и нельзя оставить систему без активного администратора;
- изменения состава пользователей попадают в журнал событий.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from apps.backend.api.v1.user_dependencies import build_admin_user_service
from apps.backend.auth import AuthenticatedUser, require_admin
from src.application.auth.service import (
    AdminUserError,
    LastAdminError,
)
from src.domain.auth import MIN_PASSWORD_LENGTH, AdminRole, AdminUser
from src.errors import ProfilePersistenceError

router = APIRouter(prefix="/api/v1/admin/users")

_NOT_FOUND = {404: {"description": "User not found"}}
_CONFLICT = {409: {"description": "Conflict or last-admin protection"}}
_USER_NOT_FOUND = "User not found"


# --- DTO ------------------------------------------------------------------------


class UserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    login: str = Field(min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9._-]+$")
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=256)
    role: AdminRole = AdminRole.VIEWER
    display_name: str | None = Field(default=None, max_length=120)
    # По умолчанию новый пользователь обязан сменить выданный пароль.
    must_change_password: bool = True


class UserPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str | None = Field(default=None, max_length=120)
    role: AdminRole | None = None
    is_active: bool | None = None


class UserOut(BaseModel):
    """Response DTO. Поля password_hash здесь НЕТ намеренно."""

    id: int
    login: str
    display_name: str | None = None
    role: str
    is_active: bool
    must_change_password: bool
    has_password: bool
    last_login_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class PasswordResetOut(BaseModel):
    """Временный пароль показывается один раз и больше не восстанавливается."""

    login: str
    temporary_password: str
    must_change_password: bool = True


# --- Helpers --------------------------------------------------------------------


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def _user_out(user: AdminUser) -> UserOut:
    return UserOut(
        id=user.id or 0,
        login=user.login,
        display_name=user.display_name,
        role=user.role.value,
        is_active=user.is_active,
        must_change_password=user.must_change_password,
        has_password=bool(user.password_hash),
        last_login_at=_iso(user.last_login_at),
        created_at=_iso(user.created_at),
        updated_at=_iso(user.updated_at),
    )


# --- Users ----------------------------------------------------------------------


@router.get("")
async def list_users(_: Annotated[AuthenticatedUser, Depends(require_admin)]) -> dict:
    users = await build_admin_user_service().list_users()
    return {"total": len(users), "items": [_user_out(u) for u in users]}


@router.post("", status_code=201, responses=_CONFLICT)
async def create_user(
    body: UserCreate, admin: Annotated[AuthenticatedUser, Depends(require_admin)]
) -> UserOut:
    try:
        created = await build_admin_user_service().create_user(
            login=body.login,
            password=body.password,
            role=body.role,
            display_name=body.display_name,
            must_change_password=body.must_change_password,
            actor=admin.login,
        )
    except AdminUserError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ProfilePersistenceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _user_out(created)


@router.get("/{user_id}", responses=_NOT_FOUND)
async def get_user(
    user_id: int, _: Annotated[AuthenticatedUser, Depends(require_admin)]
) -> UserOut:
    user = await build_admin_user_service().get_user(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail=_USER_NOT_FOUND)
    return _user_out(user)


@router.patch("/{user_id}", responses={**_NOT_FOUND, **_CONFLICT})
async def patch_user(
    user_id: int,
    body: UserPatch,
    admin: Annotated[AuthenticatedUser, Depends(require_admin)],
) -> UserOut:
    try:
        updated = await build_admin_user_service().update_user(
            user_id,
            actor_user_id=admin.user_id,
            actor=admin.login,
            display_name=body.display_name,
            role=body.role,
            is_active=body.is_active,
        )
    except LastAdminError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except AdminUserError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ProfilePersistenceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if updated is None:
        raise HTTPException(status_code=404, detail=_USER_NOT_FOUND)
    return _user_out(updated)


@router.delete("/{user_id}", status_code=204, responses={**_NOT_FOUND, **_CONFLICT})
async def delete_user(
    user_id: int, admin: Annotated[AuthenticatedUser, Depends(require_admin)]
) -> None:
    try:
        deleted = await build_admin_user_service().delete_user(
            user_id, actor_user_id=admin.user_id, actor=admin.login
        )
    except LastAdminError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except AdminUserError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail=_USER_NOT_FOUND)


@router.post("/{user_id}/reset-password", responses=_NOT_FOUND)
async def reset_password(
    user_id: int, admin: Annotated[AuthenticatedUser, Depends(require_admin)]
) -> PasswordResetOut:
    """Выдаёт временный пароль. Значение показывается только сейчас."""
    service = build_admin_user_service()
    user = await service.get_user(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail=_USER_NOT_FOUND)
    try:
        temporary = await service.reset_password(user_id, actor=admin.login)
    except AdminUserError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return PasswordResetOut(login=user.login, temporary_password=temporary)
