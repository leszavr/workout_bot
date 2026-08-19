"""Unit-тесты media-инфраструктуры (Stage 5): WebP, object storage, доменная модель."""
from __future__ import annotations

import io

import pytest

from src.domain.media import ExerciseMediaAsset, MEDIA_TYPE_IMAGE
from src.errors import MediaStorageError
from src.infrastructure.media.object_storage import (
    InMemoryObjectStorage,
    LocalObjectStorage,
)
from src.infrastructure.media.webp import WEBP_MIME, convert_to_webp


def _jpg_bytes() -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (40, 30), color=(120, 200, 80)).save(buffer, format="JPEG")
    return buffer.getvalue()


class TestConvertToWebp:
    def test_converts_jpeg_to_valid_webp(self):
        data, width, height = convert_to_webp(_jpg_bytes())

        from PIL import Image

        image = Image.open(io.BytesIO(data))
        assert image.format == "WEBP"
        assert width == 40
        assert height == 30
        assert len(data) > 0

    def test_downscales_large_image(self):
        from PIL import Image

        buffer = io.BytesIO()
        Image.new("RGB", (3000, 2000), color=(10, 10, 10)).save(buffer, format="JPEG")

        data, width, height = convert_to_webp(buffer.getvalue(), max_dimension=1600)
        assert width <= 1600 and height <= 1600

    def test_rejects_non_image(self):
        with pytest.raises(MediaStorageError):
            convert_to_webp(b"\x00\x01 not an image")

    def test_rejects_corrupt_image(self):
        corrupted = _jpg_bytes()[:100] + b"\xff\xff\xff"
        with pytest.raises(MediaStorageError):
            convert_to_webp(corrupted)


class TestObjectStorageImplementations:
    def test_inmemory_put_get_exists_delete(self):
        storage = InMemoryObjectStorage()
        storage.put_object("a/b.webp", b"data", WEBP_MIME)

        assert storage.object_exists("a/b.webp")
        assert storage.get_object("a/b.webp") == b"data"

        storage.delete_object("a/b.webp")
        assert not storage.object_exists("a/b.webp")
        with pytest.raises(MediaStorageError):
            storage.get_object("a/b.webp")

    def test_local_storage_roundtrip(self, tmp_path):
        storage = LocalObjectStorage(tmp_path, "workout-media")
        storage.put_object("exercises/x/images/1.webp", b"\x00\x01", WEBP_MIME)

        assert storage.object_exists("exercises/x/images/1.webp")
        assert storage.get_object("exercises/x/images/1.webp") == b"\x00\x01"

    def test_local_storage_rejects_path_escape(self, tmp_path):
        storage = LocalObjectStorage(tmp_path, "workout-media")
        with pytest.raises(MediaStorageError):
            storage.get_object("../../etc/passwd")


class TestExerciseMediaAssetModel:
    def test_valid_asset(self):
        asset = ExerciseMediaAsset(
            exercise_external_id="Barbell_Full_Squat",
            sequence=1,
            storage_key="exercises/Barbell_Full_Squat/images/1.webp",
            width=800,
            height=600,
            size_bytes=12345,
            checksum="abc123",
            source="leszavr/workout",
            source_url="https://github.com/leszavr/workout",
            license="Unlicense",
        )
        assert asset.media_type == MEDIA_TYPE_IMAGE
        assert asset.mime_type == WEBP_MIME

    def test_extra_fields_forbidden(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ExerciseMediaAsset(
                exercise_external_id="X",
                sequence=1,
                storage_key="y",
                width=1,
                height=1,
                size_bytes=1,
                checksum="c",
                unknown_field="boom",
            )

    def test_sequence_limits(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ExerciseMediaAsset(
                exercise_external_id="X",
                storage_key="y",
                width=1,
                height=1,
                size_bytes=1,
                checksum="c",
                sequence=0,
            )
