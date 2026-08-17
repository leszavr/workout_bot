"""Минимальный FastAPI backend.

GET /health — жив ли процесс.
GET /ready  — готов ли backend обслуживать запросы (доступ к хранилищу).
/api/v1/    — заготовка для будущих endpoints (web, админка, Telegram Gateway, AI).

Запуск: uvicorn apps.backend.main:app
"""
from __future__ import annotations

from fastapi import APIRouter, FastAPI

from src.infrastructure.config import PROFILES_DIR

api_v1 = APIRouter(prefix="/api/v1")


@api_v1.get("/")
async def api_v1_root() -> dict:
    return {"status": "ok", "version": "v1"}


def create_app() -> FastAPI:
    app = FastAPI(title="Workout Bot Backend", version="2.0.0")

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.get("/ready")
    async def ready() -> dict:
        storage_ok = PROFILES_DIR.parent.exists()
        return {"status": "ok" if storage_ok else "degraded", "storage": storage_ok}

    app.include_router(api_v1)
    return app


app = create_app()
