"""Импортёр упражнений из репозитория leszavr/workout.

Читает exercises/<Name>/exercise.json, валидирует, преобразует во внутреннюю
модель Exercise и идемпотентно сохраняет в PostgreSQL (upsert по
external_id + source). Повторный импорт не создаёт дубликатов.

Запуск:
    python -m scripts.import_exercises /path/to/workout [--source-version REV]
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from pydantic import ValidationError

from src.domain.exercise import Exercise
from src.infrastructure.persistence.postgres.db import get_session_factory
from src.infrastructure.persistence.postgres.exercise_repository import ExerciseRepository

SOURCE = "leszavr/workout"


def load_exercise(exercise_dir: Path, source_version: str | None) -> Exercise | None:
    """Читает и валидирует одно упражнение. Возвращает None при ошибке."""
    json_path = exercise_dir / "exercise.json"
    if not json_path.exists():
        return None
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    instructions = data.get("instructions") or []
    instructions_ru = data.get("instructionsRu") or []
    equipment = data.get("equipment")
    images = [
        f"{exercise_dir.name}/images/{name}"
        for name in ("0.jpg", "1.jpg")
        if (exercise_dir / "images" / name).exists()
    ]

    try:
        return Exercise(
            external_id=exercise_dir.name,
            source=SOURCE,
            source_version=source_version,
            name=data["name"],
            name_ru=data.get("nameRu"),
            aliases=[data["nameRu"]] if data.get("nameRu") else [],
            technique="\n".join(f"{i + 1}. {step}" for i, step in enumerate(instructions)) or None,
            technique_ru="\n".join(f"{i + 1}. {step}" for i, step in enumerate(instructions_ru)) or None,
            primary_muscles=data.get("primaryMuscles") or [],
            secondary_muscles=data.get("secondaryMuscles") or [],
            equipment=[equipment] if equipment else [],
            exercise_type=data.get("category"),
            difficulty=data.get("level"),
            force=data.get("force"),
            mechanic=data.get("mechanic"),
            images=images,
        )
    except (ValidationError, KeyError):
        return None


async def import_exercises(repo_root: Path, source_version: str | None) -> dict:
    exercises_dir = repo_root / "exercises"
    if not exercises_dir.is_dir():
        raise FileNotFoundError(f"Каталог не найден: {exercises_dir}")

    repository = ExerciseRepository(get_session_factory())
    stats = {"total": 0, "imported": 0, "skipped_invalid": 0, "no_technique": 0}

    for exercise_dir in sorted(exercises_dir.iterdir()):
        if not exercise_dir.is_dir():
            continue
        stats["total"] += 1
        exercise = load_exercise(exercise_dir, source_version)
        if exercise is None:
            stats["skipped_invalid"] += 1
            continue
        if not exercise.technique:
            stats["no_technique"] += 1
        await repository.upsert(exercise)
        stats["imported"] += 1

    stats["in_database"] = await repository.count()
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Импорт упражнений из leszavr/workout")
    parser.add_argument("repo_path", help="Путь к клону репозитория leszavr/workout")
    parser.add_argument("--source-version", default=None, help="Версия источника (commit)")
    args = parser.parse_args()

    stats = asyncio.run(import_exercises(Path(args.repo_path), args.source_version))
    print("=== Отчёт импорта упражнений ===")
    print(f"Всего директорий:      {stats['total']}")
    print(f"Импортировано:         {stats['imported']}")
    print(f"Пропущено (невалидно): {stats['skipped_invalid']}")
    print(f"Без описания техники:  {stats['no_technique']}")
    print(f"Упражнений в БД:       {stats['in_database']}")


if __name__ == "__main__":
    main()
