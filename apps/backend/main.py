"""FastAPI backend.

GET /health  — жив ли процесс.
GET /ready   — готов ли backend (доступ к хранилищу).
GET /version — machine-readable metadata компонента (версия, build, контракт).
/api/v1/     — внутренний API: auth, dashboard, profiles, users, exercises.
/internal/v1 — service-to-service API: регистрация компонентов, safety gate.

Запуск: uvicorn apps.backend.main:app
"""
from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from apps.backend.api.v1.admin_user_routes import router as admin_user_router
from apps.backend.api.v1.ai_routes import router as ai_admin_router
from apps.backend.api.v1.component_routes import router as component_router
from apps.backend.api.v1.internal_routes import router as internal_router
from apps.backend.api.v1.media_routes import router as media_router
from apps.backend.api.v1.routes import router as api_v1_router
from src.domain.components import (
    BACKEND_CONTRACT_VERSION,
    BACKEND_SUPPORTED_CONTRACTS,
    ComponentType,
)
from src.infrastructure.config import (
    BUILD_SHA,
    COMPONENT_REGION,
    DATABASE_URL,
    PROFILES_DIR,
)
from src.version import APP_VERSION

# Разрешённые origin для внутреннего фронтенда (Next.js).
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",") if o.strip()]


def create_app() -> FastAPI:
    app = FastAPI(title="Workout Bot Backend", version=APP_VERSION)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.get("/version")
    async def version() -> dict:
        """Metadata компонента без обращения к БД и без аутентификации.

        Публичность осознана: здесь нет ни секретов, ни пользовательских
        данных, а deployment tooling должен читать версию до того, как
        появится рабочая база и валидный токен. Тот же формат отдаёт
        Telegram Gateway в heartbeat — сравнение версий не требует парсинга
        разных схем.
        """
        return {
            "component": ComponentType.BACKEND.value,
            "version": APP_VERSION,
            "build_sha": BUILD_SHA or None,
            "contract_version": BACKEND_CONTRACT_VERSION,
            "supported_contracts": list(BACKEND_SUPPORTED_CONTRACTS),
            "region": COMPONENT_REGION,
            "capabilities": [],
            "status": "ready",
        }

    @app.get("/ready")
    async def ready() -> dict:
        if DATABASE_URL:
            try:
                from sqlalchemy import text

                from src.infrastructure.persistence.postgres.db import get_session_factory

                async with get_session_factory()() as session:
                    await session.execute(text("SELECT 1"))
                storage_ok = True
            except Exception:  # noqa: BLE001
                storage_ok = False
        else:
            storage_ok = PROFILES_DIR.parent.exists()
        return {"status": "ok" if storage_ok else "degraded", "storage": storage_ok}

    app.include_router(api_v1_router)
    app.include_router(media_router)
    app.include_router(ai_admin_router)
    app.include_router(admin_user_router)
    app.include_router(component_router)
    app.include_router(internal_router)
    return app


app = create_app()
