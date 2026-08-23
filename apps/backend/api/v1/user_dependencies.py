"""Фабрика зависимостей авторизации.

Собирает AdminUserService в стиле остальных фабрик проекта: без
singleton-глобалов, чтобы в тестах компоненты можно было собрать вручную
с подменёнными зависимостями.
"""
from __future__ import annotations

from src.application.auth.service import AdminUserService
from src.infrastructure.persistence.postgres.admin_user_repository import (
    AdminIdentityRepository,
    AdminUserRepository,
)
from src.infrastructure.persistence.postgres.ai_repository import AIAuditRepository
from src.infrastructure.persistence.postgres.db import get_session_factory


def build_admin_user_service() -> AdminUserService:
    session_factory = get_session_factory()
    return AdminUserService(
        users=AdminUserRepository(session_factory),
        identities=AdminIdentityRepository(session_factory),
        # Единый журнал административных событий проекта (легаси-имя таблицы).
        audit=AIAuditRepository(session_factory),
    )
