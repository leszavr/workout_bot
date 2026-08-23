"""Общие защиты для интеграционных тестов.

Интеграционные тесты работают против реальной PostgreSQL из `DATABASE_URL`, и
на машине разработчика это обычно та же база, где живёт рабочая конфигурация.
Тестам нужна чистая настройка задачи `workout_generation`, поэтому они её
удаляют — а вместе с ней исчезала и рабочая настройка администратора: после
прогона тестов ИИ оказывался выключенным, и интерфейс показывал «задача не
настроена».

Здесь снимается снимок такой конфигурации на весь прогон и восстанавливается
в конце. Фикстура синхронная и заводит собственный engine: так она не зависит
от event loop, в котором работают тесты.
"""
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import delete, select

from src.infrastructure.config import DATABASE_URL
from src.infrastructure.persistence.postgres.models import (
    AIModelRow,
    AITaskConfigRow,
    AITaskModelBindingRow,
)

WORKOUT_GENERATION = "workout_generation"


async def _snapshot(sessions) -> dict | None:
    async with sessions() as session:
        config = (
            await session.execute(
                select(AITaskConfigRow).where(
                    AITaskConfigRow.task_type == WORKOUT_GENERATION
                )
            )
        ).scalar_one_or_none()
        if config is None:
            return None
        bindings = (
            await session.execute(
                select(
                    AITaskModelBindingRow.model_id,
                    AITaskModelBindingRow.priority,
                    AITaskModelBindingRow.is_primary,
                ).where(AITaskModelBindingRow.task_config_id == config.id)
            )
        ).all()
        return {
            "config": {
                "task_type": config.task_type,
                "enabled": config.enabled,
                "temperature": config.temperature,
                "max_tokens": config.max_tokens,
                "timeout_seconds": config.timeout_seconds,
                "prompt_version": config.prompt_version,
            },
            "bindings": [
                {"model_id": b[0], "priority": b[1], "is_primary": b[2]}
                for b in bindings
            ],
        }


async def _restore(sessions, snapshot: dict) -> None:
    async with sessions() as session:
        async with session.begin():
            # Тесты могли оставить свою конфигурацию: заменяем её снимком,
            # иначе уникальность task_type не даст восстановить исходную.
            await session.execute(
                delete(AITaskModelBindingRow).where(
                    AITaskModelBindingRow.task_config_id.in_(
                        select(AITaskConfigRow.id).where(
                            AITaskConfigRow.task_type == WORKOUT_GENERATION
                        )
                    )
                )
            )
            await session.execute(
                delete(AITaskConfigRow).where(
                    AITaskConfigRow.task_type == WORKOUT_GENERATION
                )
            )
            restored = AITaskConfigRow(**snapshot["config"])
            session.add(restored)
            await session.flush()

            wanted = [b["model_id"] for b in snapshot["bindings"]]
            alive = set()
            if wanted:
                alive = set(
                    (
                        await session.execute(
                            select(AIModelRow.id).where(AIModelRow.id.in_(wanted))
                        )
                    ).scalars().all()
                )
            # Модель могла быть удалена за время прогона: восстанавливать
            # привязку в никуда нельзя.
            for binding in snapshot["bindings"]:
                if binding["model_id"] in alive:
                    session.add(
                        AITaskModelBindingRow(task_config_id=restored.id, **binding)
                    )


@pytest.fixture(scope="session", autouse=True)
def preserve_workout_generation_task():
    if not DATABASE_URL:
        yield
        return

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    async def _with_engine(action):
        engine = create_async_engine(DATABASE_URL)
        try:
            return await action(async_sessionmaker(engine, expire_on_commit=False))
        finally:
            await engine.dispose()

    snapshot = asyncio.run(_with_engine(_snapshot))
    try:
        yield
    finally:
        if snapshot is not None:
            asyncio.run(_with_engine(lambda s: _restore(s, snapshot)))
