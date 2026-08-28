"""Unit-тесты import_exercise_media (Stage 5): WebP-конверсия, идемпотентность, статистика."""
from __future__ import annotations

import io
from pathlib import Path

import pytest

import scripts.import_exercise_media as importer_module
from scripts.import_exercise_media import (
    SOURCE,
    collect_image_files,
    import_media,
    storage_key_for,
)
from src.infrastructure.config import DATABASE_URL

pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL is not set")

# Уникальный ID, отсутствующий в реальном каталоге: тест не должен
# затрагивать production-записи exercise_media.
TEST_EX_ID = "TestMediaImport_Exercise"


def _jpg(width: int = 60, height: int = 40) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color=(200, 100, 50)).save(buffer, format="JPEG")
    return buffer.getvalue()


@pytest.fixture
def repo_root(tmp_path: Path) -> Path:
    exercises_dir = tmp_path / "exercises" / TEST_EX_ID / "images"
    exercises_dir.mkdir(parents=True)
    (exercises_dir / "0.jpg").write_bytes(_jpg())
    (exercises_dir / "1.jpg").write_bytes(_jpg(70, 50))
    (tmp_path / "exercises" / TEST_EX_ID / "exercise.json").write_text("{}", encoding="utf-8")
    return tmp_path


@pytest.fixture
def local_storage(tmp_path: Path, monkeypatch):
    from src.infrastructure.media.object_storage import LocalObjectStorage

    storage = LocalObjectStorage(tmp_path / "object_storage", "workout-media-test")
    monkeypatch.setattr(importer_module, "create_object_storage", lambda: storage)
    return storage


@pytest.fixture
async def test_exercise_in_catalog():
    """Временное тестовое упражнение в каталоге (импортёр сверяется с ним)."""
    from src.infrastructure.persistence.postgres.db import get_session_factory
    from src.infrastructure.persistence.postgres.models import ExerciseRow

    async with get_session_factory()() as session:
        async with session.begin():
            session.add(
                ExerciseRow(
                    external_id=TEST_EX_ID,
                    source=SOURCE,
                    name="Test Media Import Exercise",
                )
            )
    yield


@pytest.fixture(autouse=True)
async def cleanup():
    """Удаляет медиа и тестовое упражнение после теста."""
    yield
    from sqlalchemy import delete

    from src.infrastructure.persistence.postgres.db import dispose_engine, get_session_factory
    from src.infrastructure.persistence.postgres.models import ExerciseMediaRow, ExerciseRow

    async with get_session_factory()() as session:
        async with session.begin():
            await session.execute(
                delete(ExerciseMediaRow).where(
                    ExerciseMediaRow.exercise_external_id == TEST_EX_ID
                )
            )
            await session.execute(
                delete(ExerciseRow).where(ExerciseRow.external_id == TEST_EX_ID)
            )
    await dispose_engine()


async def test_storage_key_deterministic():
    assert (
        storage_key_for("Barbell_Full_Squat", 1)
        == "exercises/Barbell_Full_Squat/images/1.webp"
    )
    assert storage_key_for("Weird ID!", 2) == "exercises/Weird_ID_/images/2.webp"


def test_collect_image_files_limits(tmp_path: Path):
    images = tmp_path / "images"
    images.mkdir()
    for name in ("0.jpg", "1.jpg", "2.jpg", "3.txt"):
        (images / name).write_bytes(b"x")
    files = collect_image_files(tmp_path, max_per_exercise=2)
    assert [f.name for f in files] == ["0.jpg", "1.jpg"]


async def test_import_creates_media_and_skips_unchanged(
    repo_root: Path, local_storage, test_exercise_in_catalog
):
    """Полный импорт: конверсия WebP → storage → БД; повторный запуск идемпотентен."""
    from src.infrastructure.persistence.postgres.db import get_session_factory
    from src.infrastructure.persistence.postgres.exercise_media_repository import (
        ExerciseMediaRepository,
    )

    stats = await import_media(
        repo_root,
        source_version="test",
        max_per_exercise=5,
        dry_run=False,
    )

    assert stats["total_exercises"] == 1
    assert stats["exercises_with_media"] == 1
    assert stats["files_imported"] == 2
    assert stats["files_skipped_unchanged"] == 0
    assert stats["conversion_failures"] == 0
    assert stats["media_records_in_db"] >= 2

    from PIL import Image

    for seq in (1, 2):
        key = storage_key_for(TEST_EX_ID, seq)
        data = local_storage.get_object(key)
        image = Image.open(io.BytesIO(data))
        assert image.format == "WEBP"

    stats2 = await import_media(
        repo_root,
        source_version="test",
        max_per_exercise=5,
        dry_run=False,
    )
    assert stats2["files_skipped_unchanged"] == 2
    assert stats2["files_imported"] == 0
    assert stats2["media_records_in_db"] == stats["media_records_in_db"]

    repo = ExerciseMediaRepository(get_session_factory())
    assets = await repo.list_for_exercise(TEST_EX_ID)
    assert len(assets) == 2
    for asset in assets:
        assert asset.source == SOURCE
        assert asset.license is not None and "Unlicense" in asset.license
        assert asset.source_url is not None
        assert asset.mime_type == "image/webp"
        assert len(asset.checksum) == 64
        assert asset.size_bytes > 0
        assert asset.width > 0 and asset.height > 0

async def test_bulk_list_filters_by_source(
    repo_root: Path, local_storage, test_exercise_in_catalog
):
    """Медиа ищется по паре external_id + source, как и каталог.

    Иначе программа с чужим source получала бы фотографии, но не находила
    описания упражнений, и расхождение оставалось бы незаметным.
    """
    from src.infrastructure.persistence.postgres.db import get_session_factory
    from src.infrastructure.persistence.postgres.exercise_media_repository import (
        ExerciseMediaRepository,
    )

    await import_media(repo_root, source_version="test", max_per_exercise=5)
    repo = ExerciseMediaRepository(get_session_factory())

    matching = await repo.bulk_list([(TEST_EX_ID, SOURCE)])
    assert len(matching.get(TEST_EX_ID, [])) == 2

    foreign = await repo.bulk_list([(TEST_EX_ID, "workout")])
    assert foreign == {}
