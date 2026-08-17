"""Тесты Exercise Catalog: импорт, повторный импорт, защита от дублей, валидация.

Требуют DATABASE_URL (docker compose postgres). Иначе пропускаются.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.domain.exercise import Exercise
from src.infrastructure.config import DATABASE_URL

pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL is not set")


@pytest.fixture
async def repository():
    from src.infrastructure.persistence.postgres.db import get_session_factory
    from src.infrastructure.persistence.postgres.exercise_repository import (
        ExerciseRepository,
    )

    return ExerciseRepository(get_session_factory())


@pytest.fixture(autouse=True)
async def cleanup():
    from sqlalchemy import delete

    from src.infrastructure.persistence.postgres.db import get_session_factory
    from src.infrastructure.persistence.postgres.models import ExerciseRow

    async def _purge() -> None:
        async with get_session_factory()() as session:
            async with session.begin():
                await session.execute(
                    delete(ExerciseRow).where(ExerciseRow.source == "test-source")
                )

    await _purge()
    yield
    await _purge()
    from src.infrastructure.persistence.postgres.db import dispose_engine

    await dispose_engine()


def _exercise(external_id: str = "Test_Exercise", **kwargs) -> Exercise:
    base = dict(
        external_id=external_id,
        source="test-source",
        name="Test Exercise",
        primary_muscles=["chest"],
        equipment=["barbell"],
        exercise_type="strength",
        difficulty="beginner",
    )
    base.update(kwargs)
    return Exercise(**base)


async def test_import_exercise(repository):
    await repository.upsert(_exercise())
    loaded = await repository.get_by_external_id("Test_Exercise", "test-source")
    assert loaded is not None
    assert loaded.name == "Test Exercise"
    assert loaded.primary_muscles == ["chest"]


async def test_reimport_is_idempotent(repository):
    await repository.upsert(_exercise())
    await repository.upsert(_exercise(name="Test Exercise Updated"))
    loaded = await repository.get_by_external_id("Test_Exercise", "test-source")
    assert loaded is not None
    assert loaded.name == "Test Exercise Updated"
    assert await repository.count() >= 1
    # Дубликатов нет: по ключу (external_id, source) ровно одна запись.
    items = await repository.list(search="Test Exercise")
    assert len(items) == 1


async def test_duplicate_protection(repository):
    await repository.upsert(_exercise("Dup_A"))
    await repository.upsert(_exercise("Dup_A"))
    await repository.upsert(_exercise("Dup_B"))
    items = await repository.list(search="Test Exercise")
    external_ids = [e.external_id for e in items]
    assert external_ids.count("Dup_A") == 1
    assert external_ids.count("Dup_B") == 1


def test_required_fields_validation():
    with pytest.raises(ValidationError):
        Exercise(external_id="", source="s", name="x")  # пустой external_id
    with pytest.raises(ValidationError):
        Exercise(external_id="x", source="s", name="")  # пустой name


async def test_list_filters(repository):
    await repository.upsert(_exercise("F1", exercise_type="strength", difficulty="beginner"))
    await repository.upsert(_exercise("F2", exercise_type="cardio", difficulty="expert"))
    strength = await repository.list(exercise_type="strength", search="Test")
    assert all(e.exercise_type == "strength" for e in strength)
    cardio = await repository.list(exercise_type="cardio", search="Test")
    assert all(e.exercise_type == "cardio" for e in cardio)
