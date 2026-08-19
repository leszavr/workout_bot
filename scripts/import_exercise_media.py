"""Импортер медиа упражнений из репозитория leszavr/workout (Stage 5).

Забирает фотографии из локального клона репозитория (runtime больше НЕ
обращается к GitHub), конвертирует в WebP, загружает в object storage
(MinIO) и сохраняет metadata в PostgreSQL.

Идемпотентность:
- metadata upsert по (exercise_external_id, exercise_source, sequence);
- файл не перезагружается, если object с тем же checksum уже существует.
Повторный запуск не создаёт дубликатов.

Storage key детерминирован: exercises/{external_id}/images/{sequence}.webp

Лицензия источника: Unlicense (public domain) — https://github.com/leszavr/workout

Запуск:
    python -m scripts.import_exercise_media /path/to/workout \
        [--max-per-exercise 5] [--source-version REV] [--dry-run]
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
from pathlib import Path

from src.domain.media import ExerciseMediaAsset, MEDIA_TYPE_IMAGE
from src.errors import MediaStorageError
from src.infrastructure.config import EXERCISE_MEDIA_MAX_PER_EXERCISE
from src.infrastructure.media.object_storage import WEBP_MIME, create_object_storage
from src.infrastructure.media.webp import convert_to_webp
from src.infrastructure.persistence.postgres.db import get_session_factory
from src.infrastructure.persistence.postgres.exercise_media_repository import (
    ExerciseMediaRepository,
)
from src.infrastructure.persistence.postgres.exercise_repository import ExerciseRepository

SOURCE = "leszavr/workout"
LICENSE = "Unlicense (public domain, https://unlicense.org)"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9_.-]")


def storage_key_for(external_id: str, sequence: int) -> str:
    safe_id = _SAFE_ID_RE.sub("_", external_id)
    return f"exercises/{safe_id}/images/{sequence}.webp"


def collect_image_files(exercise_dir: Path, max_per_exercise: int) -> list[Path]:
    images_dir = exercise_dir / "images"
    if not images_dir.is_dir():
        return []
    files = sorted(
        p for p in images_dir.iterdir()
        if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
    )
    return files[:max_per_exercise]


async def import_media(
    repo_root: Path,
    *,
    source_version: str | None,
    max_per_exercise: int,
    dry_run: bool = False,
) -> dict:
    exercises_dir = repo_root / "exercises"
    if not exercises_dir.is_dir():
        raise FileNotFoundError(f"Каталог не найден: {exercises_dir}")

    session_factory = get_session_factory()
    media_repo = ExerciseMediaRepository(session_factory)
    exercise_repo = ExerciseRepository(session_factory)
    storage = None if dry_run else create_object_storage()

    catalog_ids = {
        e.external_id for e in await exercise_repo.list(limit=10000)
    }

    stats = {
        "total_exercises": 0,
        "exercises_with_media": 0,
        "exercises_missing_in_catalog": 0,
        "source_files_found": 0,
        "files_imported": 0,
        "files_skipped_unchanged": 0,
        "files_reread_after_change": 0,
        "conversion_failures": 0,
        "missing_source_files": 0,
        "media_records_in_db": 0,
    }

    existing_assets = {}
    if not dry_run:
        for asset in await media_repo.list_all():
            key = (asset.exercise_external_id, asset.sequence)
            existing_assets[key] = (asset.checksum, asset.storage_key)

    for exercise_dir in sorted(exercises_dir.iterdir()):
        if not exercise_dir.is_dir():
            continue
        stats["total_exercises"] += 1
        external_id = exercise_dir.name

        if external_id not in catalog_ids:
            stats["exercises_missing_in_catalog"] += 1
            continue

        files = collect_image_files(exercise_dir, max_per_exercise)
        if not files:
            stats["missing_source_files"] += 1
            continue
        stats["exercises_with_media"] += 1
        stats["source_files_found"] += len(files)

        for sequence, source_path in enumerate(files, start=1):
            try:
                source_bytes = source_path.read_bytes()
            except OSError:
                stats["missing_source_files"] += 1
                continue

            checksum = hashlib.sha256(source_bytes).hexdigest()
            key = storage_key_for(external_id, sequence)

            try:
                webp_bytes, width, height = convert_to_webp(source_bytes)
            except MediaStorageError:
                stats["conversion_failures"] += 1
                continue

            asset = ExerciseMediaAsset(
                exercise_external_id=external_id,
                exercise_source=SOURCE,
                media_type=MEDIA_TYPE_IMAGE,
                sequence=sequence,
                storage_key=key,
                mime_type=WEBP_MIME,
                width=width,
                height=height,
                size_bytes=len(webp_bytes),
                checksum=checksum,
                source=SOURCE,
                source_url=(
                    f"https://github.com/leszavr/workout/tree/master/exercises/"
                    f"{external_id}/images"
                ),
                license=LICENSE,
            )

            if dry_run:
                stats["files_imported"] += 1
                continue

            previous = existing_assets.get((external_id, sequence))
            previous_unchanged = previous is not None and storage.object_exists(previous[1])

            if previous_unchanged and previous[0] == checksum:
                stats["files_skipped_unchanged"] += 1
            else:
                storage.put_object(key, webp_bytes, WEBP_MIME)
                stats["files_imported"] += 1
                if previous is not None:
                    stats["files_reread_after_change"] += 1
            await media_repo.upsert(asset)

    stats["media_records_in_db"] = await media_repo.count()
    stats["exercises_with_media_in_db"] = await media_repo.count_exercises_with_media()
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Импорт фото упражнений в object storage")
    parser.add_argument("repo_path", help="Путь к клону репозитория leszavr/workout")
    parser.add_argument("--source-version", default=None, help="Версия источника (commit)")
    parser.add_argument(
        "--max-per-exercise",
        type=int,
        default=EXERCISE_MEDIA_MAX_PER_EXERCISE,
        help="Максимум фото на упражнение (конфигурация, не схема БД)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Без записи в storage/БД")
    args = parser.parse_args()

    stats = asyncio.run(
        import_media(
            Path(args.repo_path),
            source_version=args.source_version,
            max_per_exercise=args.max_per_exercise,
            dry_run=args.dry_run,
        )
    )
    print("=== Отчёт импорта медиа упражнений ===")
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
