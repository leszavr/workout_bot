"""Сервис доступа к медиа упражнений (application layer, Stage 5).

Единственная точка доступа к медиа для API, HTML-рендерера и importer'а.
Renderer и endpoints не знают про MinIO/S3 — только про этот сервис.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from src.domain.media import ExerciseMediaAsset
from src.errors import MediaStorageError
from src.infrastructure.media.object_storage import ObjectStorage, WEBP_MIME
from src.infrastructure.persistence.postgres.exercise_media_repository import (
    ExerciseMediaRepository,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MediaObject:
    """Данные медиа-ассета для рендеринга/отдачи."""

    asset: ExerciseMediaAsset
    data: bytes
    mime_type: str


class ExerciseMediaService:
    def __init__(self, repository: ExerciseMediaRepository, storage: ObjectStorage) -> None:
        self._repository = repository
        self._storage = storage

    async def list_for_exercise(
        self, external_id: str, source: str = "leszavr/workout", limit: int | None = None
    ) -> list[ExerciseMediaAsset]:
        return await self._repository.list_for_exercise(external_id, source, limit)

    async def bulk_list(
        self, pairs: list[tuple[str, str]], limit_per_exercise: int | None = None
    ) -> dict[str, list[ExerciseMediaAsset]]:
        return await self._repository.bulk_list(pairs, limit_per_exercise)

    async def get_bytes(self, asset: ExerciseMediaAsset) -> bytes:
        """Читает файл из object storage. Бросает MediaStorageError при отсутствии."""
        try:
            return self._storage.get_object(asset.storage_key)
        except MediaStorageError:
            logger.error(
                "event=media_object_missing",
                extra={"storage_key": asset.storage_key, "exercise": asset.exercise_external_id},
            )
            raise

    async def put_object(self, storage_key: str, data: bytes, content_type: str = WEBP_MIME) -> None:
        self._storage.put_object(storage_key, data, content_type)

    def object_exists(self, storage_key: str) -> bool:
        return self._storage.object_exists(storage_key)

    def public_url(self, asset: ExerciseMediaAsset, base_url: str) -> str:
        """Абсолютный URL media endpoint для asset (режим url в HTML)."""
        base = base_url.rstrip("/")
        return (
            f"{base}/api/v1/media/exercises/"
            f"{asset.exercise_external_id}/{asset.sequence}"
            f"?source={asset.exercise_source}"
        )

    async def count_exercises_with_media(self) -> int:
        return await self._repository.count_exercises_with_media()

    async def count(self) -> int:
        return await self._repository.count()
