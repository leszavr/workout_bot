"""FastAPI backend.

GET /health — жив ли процесс.
GET /ready  — готов ли backend (доступ к хранилищу).
/api/v1/    — внутренний API: auth, dashboard, profiles, users, exercises.

Запуск: uvicorn apps.backend.main:app
"""
from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from apps.backend.api.v1.routes import router as api_v1_router
from src.infrastructure.config import DATABASE_URL, PROFILES_DIR

# Разрешённые origin для внутреннего фронтенда (Next.js).
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",") if o.strip()]


def create_app() -> FastAPI:
    app = FastAPI(title="Workout Bot Backend", version="2.1.0")

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
    return app


app = create_app()
