"""Unit-тесты чтения внешних источников и импорта медиа.

Источники читаются из локальной копии, поэтому тесты собирают минимальную копию
во временном каталоге. Проверяется то, что легко сделать неправильно:

- 605 тысяч строк датасета программ не превращаются в 605 тысяч упражнений;
- отрицательные повторения источника не смешиваются с повторениями;
- версия источника фиксируется, а не выдумывается;
- медиа не перезагружается повторно и не теряет атрибуцию.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from src.application.ingestion.candidates import (
    CandidateMedia,
    ExternalExerciseCandidate,
)
from src.application.ingestion.media_importer import (
    ExternalMediaImporter,
    storage_key_for,
)
from src.application.ingestion.sources import (
    GITHUB_SOURCE_KEY,
    KAGGLE_SOURCE_KEY,
    merge_aggregates,
    observation_metrics,
    read_github_dataset,
    read_kaggle_dataset,
)
from src.domain.ingestion import ExternalSourceKind
from src.domain.media import MEDIA_TYPE_ANIMATION, MEDIA_TYPE_IMAGE
from src.infrastructure.media.object_storage import InMemoryObjectStorage

# Однопиксельный GIF: минимальный валидный файл с известными размерами.
GIF_BYTES = bytes.fromhex(
    "47494638396101000100800000ffffff21f90401000000002c00000000010001000002024401003b"
)
# Валидный PNG 2x2 (минимальный, который PIL действительно открывает).
PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000002000000020802000000fdd49a73"
    "0000001649444154789c63e4129163606060626060606060000002e600405ca520"
    "5b0000000049454e44ae426082"
)


def write_github_copy(root: Path, records: list[dict]) -> None:
    (root / "data").mkdir(parents=True, exist_ok=True)
    (root / "images").mkdir(exist_ok=True)
    (root / "videos").mkdir(exist_ok=True)
    (root / "data" / "exercises.json").write_text(
        json.dumps(records, ensure_ascii=False), encoding="utf-8"
    )
    for record in records:
        if record.get("image"):
            (root / record["image"]).write_bytes(PNG_BYTES)
        if record.get("gif_url"):
            (root / record["gif_url"]).write_bytes(GIF_BYTES)


def github_record(record_id: str = "0001", **overrides) -> dict:
    record = {
        "id": record_id,
        "name": "barbell bench press",
        "category": "chest",
        "body_part": "chest",
        "equipment": "barbell",
        "instructions": {"en": "Full english text", "ru": "Полный русский текст"},
        "instruction_steps": {
            "en": ["Lie on the bench", "Press the bar"],
            "ru": ["Лягте на скамью", "Выжмите штангу"],
        },
        "muscle_group": "triceps",
        "secondary_muscles": ["triceps", "shoulders"],
        "target": "pectorals",
        "media_id": "abc123",
        "image": f"images/{record_id}-abc123.jpg",
        "gif_url": f"videos/{record_id}-abc123.gif",
        "attribution": "© Gym visual — https://gymvisual.com/",
        "created_at": "2026-01-01T00:00:00Z",
    }
    record.update(overrides)
    return record


KAGGLE_COLUMNS = [
    "title",
    "description",
    "level",
    "goal",
    "equipment",
    "program_length",
    "time_per_workout",
    "week",
    "day",
    "number_of_exercises",
    "exercise_name",
    "sets",
    "reps",
    "intensity",
    "created",
    "last_edit",
]


def kaggle_row(
    title: str, exercise_name: str, sets: str, reps: str, intensity: str = "7.0"
) -> dict:
    return {
        "title": title,
        "description": "desc",
        "level": "['Intermediate']",
        "goal": "['Bodybuilding']",
        "equipment": "Full Gym",
        "program_length": "8.0",
        "time_per_workout": "60.0",
        "week": "1.0",
        "day": "1.0",
        "number_of_exercises": "4.0",
        "exercise_name": exercise_name,
        "sets": sets,
        "reps": reps,
        "intensity": intensity,
        "created": "2025-01-01 00:00:00",
        "last_edit": "2025-06-01 00:00:00",
    }


def write_kaggle_copy(root: Path, rows: list[dict]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    with (root / "programs_detailed_boostcamp_kaggle.csv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=KAGGLE_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    with (root / "program_summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "title",
                "description",
                "level",
                "goal",
                "equipment",
                "program_length",
                "time_per_workout",
                "total_exercises",
                "created",
                "last_edit",
            ],
        )
        writer.writeheader()
        for title in sorted({row["title"] for row in rows}):
            writer.writerow(
                {
                    "title": title,
                    "description": "desc",
                    "level": "['Intermediate']",
                    "goal": "['Bodybuilding']",
                    "equipment": "Full Gym",
                    "program_length": "8.0",
                    "time_per_workout": "60.0",
                    "total_exercises": "4",
                    "created": "2025-01-01 00:00:00",
                    "last_edit": "2025-06-01 00:00:00",
                }
            )


# --- Источник A: каталог упражнений ---------------------------------------------


def test_github_reader_builds_candidates(tmp_path: Path):
    write_github_copy(tmp_path, [github_record()])
    read = read_github_dataset(tmp_path, version="abc")

    assert read.source.source_key == GITHUB_SOURCE_KEY
    assert read.source.kind is ExternalSourceKind.EXERCISE_CATALOG
    assert read.version.version == "abc"
    assert read.version.record_count == 1
    assert read.version.content_hash

    item = read.candidates[0]
    assert item.source_record_id == "0001"
    assert item.technique == "1. Lie on the bench\n2. Press the bar"
    assert item.technique_ru == "1. Лягте на скамью\n2. Выжмите штангу"
    assert item.equipment_values == ("barbell",)
    assert item.primary_muscle_values == ("pectorals",)
    # `muscle_group` источника — синергист, и он идёт в дополнительные мышцы:
    # подмена целевой мышцы синергистом изменила бы роль упражнения в программе.
    assert "triceps" in item.secondary_muscle_values
    assert {m.media_type for m in item.media} == {
        MEDIA_TYPE_IMAGE,
        MEDIA_TYPE_ANIMATION,
    }
    assert item.attribution == "© Gym visual — https://gymvisual.com/"


def test_github_reader_reports_missing_media_file(tmp_path: Path):
    record = github_record()
    write_github_copy(tmp_path, [record])
    (tmp_path / record["gif_url"]).unlink()
    read = read_github_dataset(tmp_path, version="abc")
    assert read.stats["missing_media_file"] == 1
    assert len(read.candidates[0].media) == 1


def test_github_reader_skips_record_without_name(tmp_path: Path):
    write_github_copy(tmp_path, [github_record(name="")])
    read = read_github_dataset(tmp_path, version="abc")
    assert read.candidates == []
    assert read.stats["skipped_invalid"] == 1


def test_github_reader_derives_version_from_content_without_git(tmp_path: Path):
    """Версия не выдумывается: без `.git` она выводится из содержимого файла."""
    write_github_copy(tmp_path, [github_record()])
    read = read_github_dataset(tmp_path)
    assert read.version.version.startswith("sha256:")


def test_github_reader_reads_commit_sha_from_clone(tmp_path: Path):
    write_github_copy(tmp_path, [github_record()])
    (tmp_path / ".git" / "refs" / "heads").mkdir(parents=True)
    (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (tmp_path / ".git" / "refs" / "heads" / "main").write_text(
        "0123456789abcdef0123456789abcdef01234567\n", encoding="utf-8"
    )
    read = read_github_dataset(tmp_path)
    assert read.version.version == "0123456789abcdef0123456789abcdef01234567"


def test_github_reader_fails_on_missing_file(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        read_github_dataset(tmp_path)


# --- Источник B: датасет программ ------------------------------------------------


def test_kaggle_rows_are_aggregated_by_exercise_name(tmp_path: Path):
    """Строки программ не являются упражнениями: 6 строк дают 2 упражнения."""
    rows = [
        kaggle_row("Program A", "Bench Press (Barbell)", "3.0", "10.0"),
        kaggle_row("Program A", "Bench Press (Barbell)", "4.0", "8.0"),
        kaggle_row("Program B", "Bench Press (Barbell)", "5.0", "5.0"),
        kaggle_row("Program A", "Squat (Barbell)", "3.0", "10.0"),
        kaggle_row("Program B", "Squat (Barbell)", "3.0", "12.0"),
        kaggle_row("Program C", "Squat (Barbell)", "3.0", "12.0"),
    ]
    write_kaggle_copy(tmp_path, rows)
    read, aggregates = read_kaggle_dataset(tmp_path)

    assert read.source.source_key == KAGGLE_SOURCE_KEY
    assert read.source.kind is ExternalSourceKind.PROGRAM_DATASET
    assert read.stats["rows_used"] == 6
    assert read.stats["unique_exercise_names"] == 2
    assert read.stats["programs_total"] == 3
    assert read.stats["summary_programs"] == 3
    assert len(read.candidates) == 2

    bench = aggregates["Bench Press (Barbell)"]
    assert bench.occurrence_count == 3
    assert len(bench.programs) == 2

    metrics = observation_metrics(bench)
    assert metrics["program_count"] == 2
    assert metrics["occurrence_count"] == 3
    assert metrics["typical_sets_median"] == 4.0
    assert metrics["typical_reps_min"] == 5
    assert metrics["typical_reps_max"] == 10
    assert metrics["source_goals"] == {"Bodybuilding": 3}


def test_program_candidates_carry_no_technique_or_media(tmp_path: Path):
    """Датасет программ не является каталогом: техники и медиа у него нет."""
    write_kaggle_copy(tmp_path, [kaggle_row("P", "Leg Press", "3.0", "10.0")])
    read, _ = read_kaggle_dataset(tmp_path)
    item = read.candidates[0]
    assert item.technique is None
    assert item.technique_ru is None
    assert item.media == ()
    assert item.equipment_values == ()


def test_negative_reps_are_treated_as_hold_seconds(tmp_path: Path):
    """Отрицательные повторения источника — секунды удержания, а не повторения."""
    rows = [
        kaggle_row("P", "Plank", "3.0", "-60.0"),
        kaggle_row("P", "Plank", "3.0", "-30.0"),
    ]
    write_kaggle_copy(tmp_path, rows)
    read, aggregates = read_kaggle_dataset(tmp_path)
    assert read.stats["reps_as_hold_seconds"] == 2
    metrics = observation_metrics(aggregates["Plank"])
    assert metrics["typical_reps_median"] is None
    assert metrics["typical_hold_seconds_median"] == 45.0


def test_row_limit_bounds_reading(tmp_path: Path):
    rows = [kaggle_row("P", f"Exercise {i}", "3.0", "10.0") for i in range(10)]
    write_kaggle_copy(tmp_path, rows)
    read, _ = read_kaggle_dataset(tmp_path, row_limit=4)
    assert read.stats["rows_used"] == 4


def test_missing_column_is_an_explicit_error(tmp_path: Path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "programs_detailed_boostcamp_kaggle.csv").write_text(
        "title,exercise_name\nP,Bench Press\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="отсутствуют столбцы"):
        read_kaggle_dataset(tmp_path)


def test_merge_aggregates_combines_raw_observations(tmp_path: Path):
    """Объединяются наблюдения, а не медианы: медиана от медиан бессмысленна."""
    rows = [
        kaggle_row("P1", "Bench Press (Barbell)", "3.0", "10.0"),
        kaggle_row("P1", "Barbell Bench Press", "5.0", "4.0"),
        kaggle_row("P2", "Barbell Bench Press", "5.0", "6.0"),
    ]
    write_kaggle_copy(tmp_path, rows)
    _, aggregates = read_kaggle_dataset(tmp_path)
    merged = merge_aggregates(
        [aggregates["Bench Press (Barbell)"], aggregates["Barbell Bench Press"]]
    )
    assert merged.occurrence_count == 3
    assert len(merged.programs) == 2
    metrics = observation_metrics(merged)
    assert metrics["typical_sets_median"] == 5.0
    assert metrics["typical_reps_min"] == 4
    assert metrics["typical_reps_max"] == 10


def test_merge_aggregates_rejects_empty_input():
    with pytest.raises(ValueError):
        merge_aggregates([])


# --- Импорт медиа ----------------------------------------------------------------


class FakeMediaRepository:
    """Минимальный репозиторий медиа: хранит ассеты в памяти."""

    def __init__(self) -> None:
        self.assets: dict[tuple[str, str, int], object] = {}

    async def list_all(self, limit: int = 5000):
        return list(self.assets.values())

    async def upsert(self, asset) -> None:
        key = (asset.exercise_external_id, asset.exercise_source, asset.sequence)
        self.assets[key] = asset


def media_job(root: Path, external_id: str = "New_Exercise"):
    candidate = ExternalExerciseCandidate(
        source_key=GITHUB_SOURCE_KEY,
        source_version="abc",
        source_record_id="0001",
        raw_name="name",
        name="name",
        media=(
            CandidateMedia(
                media_type=MEDIA_TYPE_IMAGE,
                relative_path="images/0001.jpg",
                attribution="© Gym visual — https://gymvisual.com/",
                source_url="https://example.invalid/images/0001.jpg",
            ),
            CandidateMedia(
                media_type=MEDIA_TYPE_ANIMATION,
                relative_path="videos/0001.gif",
                attribution="© Gym visual — https://gymvisual.com/",
            ),
        ),
    )
    (root / "images").mkdir(parents=True, exist_ok=True)
    (root / "videos").mkdir(parents=True, exist_ok=True)
    (root / "images" / "0001.jpg").write_bytes(PNG_BYTES)
    (root / "videos" / "0001.gif").write_bytes(GIF_BYTES)
    return [(external_id, "workout_bot/external", candidate)]


async def test_media_import_writes_both_types(tmp_path: Path):
    repository = FakeMediaRepository()
    storage = InMemoryObjectStorage()
    importer = ExternalMediaImporter(
        media_repository=repository, storage=storage, source_root=tmp_path
    )
    stats = await importer.import_for(media_job(tmp_path))

    assert stats.files_uploaded == 2
    assert stats.assets_written == 2
    assert stats.by_type == {MEDIA_TYPE_IMAGE: 1, MEDIA_TYPE_ANIMATION: 1}

    image = repository.assets[("New_Exercise", "workout_bot/external", 1)]
    animation = repository.assets[("New_Exercise", "workout_bot/external", 2)]
    # Статичный кадр конвертируется в WebP, как и весь остальной каталог.
    assert image.mime_type == "image/webp"
    # Анимация сохраняется как GIF: перекодирование меняет тайминги кадров и
    # рискует изменить размеры, ограниченные условиями использования источника.
    assert animation.mime_type == "image/gif"
    assert animation.width == 1 and animation.height == 1
    # Атрибуция привязана к файлу, а не к строке в документации.
    assert image.license == "© Gym visual — https://gymvisual.com/"
    assert animation.license == "© Gym visual — https://gymvisual.com/"
    assert storage.object_exists(image.storage_key)
    assert storage.object_exists(animation.storage_key)


async def test_media_import_is_idempotent(tmp_path: Path):
    """Повторный запуск не перезагружает неизменившиеся файлы."""
    repository = FakeMediaRepository()
    storage = InMemoryObjectStorage()
    importer = ExternalMediaImporter(
        media_repository=repository, storage=storage, source_root=tmp_path
    )
    jobs = media_job(tmp_path)
    await importer.import_for(jobs)
    second = await importer.import_for(jobs)

    assert second.files_uploaded == 0
    assert second.files_skipped_unchanged == 2
    assert len(repository.assets) == 2


async def test_media_import_reports_missing_file(tmp_path: Path):
    repository = FakeMediaRepository()
    importer = ExternalMediaImporter(
        media_repository=repository,
        storage=InMemoryObjectStorage(),
        source_root=tmp_path,
    )
    jobs = media_job(tmp_path)
    (tmp_path / "videos" / "0001.gif").unlink()
    stats = await importer.import_for(jobs)
    assert stats.files_missing == 1
    assert stats.assets_written == 1


async def test_media_dry_run_writes_nothing(tmp_path: Path):
    repository = FakeMediaRepository()
    storage = InMemoryObjectStorage()
    importer = ExternalMediaImporter(
        media_repository=repository, storage=storage, source_root=tmp_path
    )
    stats = await importer.import_for(media_job(tmp_path), dry_run=True)
    assert stats.files_uploaded == 2
    assert stats.assets_written == 0
    assert repository.assets == {}


def test_storage_key_is_deterministic_and_type_specific():
    assert storage_key_for("Bench_Press", MEDIA_TYPE_IMAGE) == (
        "exercises/Bench_Press/image/1.webp"
    )
    assert storage_key_for("Bench_Press", MEDIA_TYPE_ANIMATION) == (
        "exercises/Bench_Press/animation/2.gif"
    )
    # Небезопасные символы в идентификаторе не попадают в ключ хранения.
    assert "/" not in storage_key_for("a b", MEDIA_TYPE_IMAGE).split("/")[1]
