"""Модель медиа-ассетов упражнений (Stage 5).

Упражнение связано с 1..N медиа-ассетами (фотографии). Количество не
ограничено схемой БД — лимит задаётся конфигурацией
(``EXERCISE_MEDIA_MAX_PER_EXERCISE``) и может меняться без миграций.

Каноническая привязка — ``(external_id, source)`` каталога упражнений:
программы и медиа ссылаются на один и тот же stable ID.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

MEDIA_TYPE_IMAGE = "image"
ALLOWED_MEDIA_TYPES = {MEDIA_TYPE_IMAGE}


class ExerciseMediaAsset(BaseModel):
    """Один медиа-ассет упражнения (изображение в object storage)."""

    model_config = ConfigDict(extra="forbid")

    id: int | None = None
    exercise_external_id: str = Field(min_length=1, max_length=128)
    exercise_source: str = Field(default="leszavr/workout", max_length=64)

    media_type: str = Field(default=MEDIA_TYPE_IMAGE, max_length=32)
    sequence: int = Field(ge=1, le=100, description="Порядковый номер в упражнении")

    storage_key: str = Field(min_length=1, max_length=300)
    mime_type: str = Field(default="image/webp", max_length=100)
    width: int = Field(ge=0, le=10000)
    height: int = Field(ge=0, le=10000)
    size_bytes: int = Field(ge=0)
    checksum: str = Field(min_length=1, max_length=64, description="sha256 хеш хранится hex")

    source: str | None = Field(default=None, max_length=64, description="Репозиторий источника")
    source_url: str | None = Field(default=None, max_length=500)
    license: str | None = Field(default=None, max_length=200)

    created_at: datetime | None = None
    updated_at: datetime | None = None
