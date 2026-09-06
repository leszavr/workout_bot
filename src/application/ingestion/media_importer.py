"""Загрузка медиа внешних источников в объектное хранилище проекта.

Модуль решает узкую задачу: перенести файлы из локальной копии внешнего каталога
в MinIO проекта и записать метаданные, не создавая второго слоя хранения и не
меняя существующую архитектуру MinIO.

Три решения, каждое с причиной.

**Статичное изображение конвертируется в WebP, анимация — нет.** Конвертация
статичного кадра идёт через существующий `convert_to_webp`, тот же, что у
импорта каталога: два формата для одного вида медиа означали бы две ветки во
всех потребителях. Анимация сохраняется как GIF без перекодирования: во-первых,
условия использования media источника ограничивают разрешение 180×180, и
перекодирование — лишний риск изменить размеры; во-вторых, анимированный WebP из
PIL меняет тайминги кадров, и «то же изображение» перестало бы быть тем же.

**Дедупликация по контрольной сумме, а не по имени файла.** Один и тот же ассет
может прийти из двух источников под разными именами. Ключ хранения строится из
canonical идентификатора упражнения и порядкового номера, а повторная загрузка
одинакового содержимого пропускается по сравнению `checksum`: файл не
перезаписывается, метаданные обновляются.

**Атрибуция сохраняется в метаданных каждого ассета.** Права на использование
media согласованы владельцем проекта, но правообладатель у media остаётся, и его
указание обязано быть привязано к файлу, а не к строке в документации.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path

from src.application.ingestion.candidates import ExternalExerciseCandidate
from src.domain.media import (
    ALLOWED_MEDIA_TYPES,
    MEDIA_TYPE_ANIMATION,
    MEDIA_TYPE_IMAGE,
    ExerciseMediaAsset,
)
from src.errors import MediaStorageError
from src.infrastructure.media.object_storage import WEBP_MIME
from src.infrastructure.media.webp import convert_to_webp

logger = logging.getLogger(__name__)

GIF_MIME = "image/gif"

# Расширение ключа хранения по типу медиа. Ключ детерминирован: повторный импорт
# обращается к тому же объекту, а не создаёт второй.
_EXTENSION = {MEDIA_TYPE_IMAGE: "webp", MEDIA_TYPE_ANIMATION: "gif"}

# Порядковые номера по типу медиа. Фиксированы, потому что уникальность в схеме
# задана тройкой (external_id, source, sequence): без фиксации повторный импорт
# мог бы поменять номер и создать вторую строку для того же файла.
_SEQUENCE = {MEDIA_TYPE_IMAGE: 1, MEDIA_TYPE_ANIMATION: 2}

_SAFE_ID = str.maketrans({c: "_" for c in ' /\\:*?"<>|'})


def storage_key_for(external_id: str, media_type: str) -> str:
    safe_id = external_id.translate(_SAFE_ID)
    extension = _EXTENSION.get(media_type, "bin")
    return f"exercises/{safe_id}/{media_type}/{_SEQUENCE.get(media_type, 1)}.{extension}"


@dataclass
class MediaImportStats:
    """Что произошло с медиа при импорте."""

    files_seen: int = 0
    files_uploaded: int = 0
    files_skipped_unchanged: int = 0
    files_missing: int = 0
    conversion_failures: int = 0
    unsupported_type: int = 0
    assets_written: int = 0
    by_type: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "files_seen": self.files_seen,
            "files_uploaded": self.files_uploaded,
            "files_skipped_unchanged": self.files_skipped_unchanged,
            "files_missing": self.files_missing,
            "conversion_failures": self.conversion_failures,
            "unsupported_type": self.unsupported_type,
            "assets_written": self.assets_written,
            "by_type": dict(sorted(self.by_type.items())),
        }


class ExternalMediaImporter:
    """Переносит медиа внешних записей в объектное хранилище проекта."""

    def __init__(self, *, media_repository, storage, source_root: Path) -> None:
        self._repository = media_repository
        self._storage = storage
        self._root = source_root

    async def import_for(
        self,
        jobs: list[tuple[str, str, ExternalExerciseCandidate]],
        *,
        dry_run: bool = False,
    ) -> MediaImportStats:
        """Загружает медиа для списка «упражнение ← внешняя запись»."""
        stats = MediaImportStats()
        existing: dict[tuple[str, str, int], str] = {}
        if not dry_run:
            for asset in await self._repository.list_all():
                existing[
                    (asset.exercise_external_id, asset.exercise_source, asset.sequence)
                ] = asset.checksum

        for external_id, exercise_source, candidate in jobs:
            for media in candidate.media:
                stats.files_seen += 1
                if media.media_type not in ALLOWED_MEDIA_TYPES:
                    stats.unsupported_type += 1
                    continue

                path = self._root / media.relative_path
                if not path.is_file():
                    stats.files_missing += 1
                    continue
                try:
                    source_bytes = path.read_bytes()
                except OSError:
                    stats.files_missing += 1
                    continue

                if media.media_type == MEDIA_TYPE_IMAGE:
                    try:
                        payload, width, height = convert_to_webp(source_bytes)
                    except MediaStorageError:
                        stats.conversion_failures += 1
                        continue
                    mime = WEBP_MIME
                else:
                    # Анимация сохраняется как есть: перекодирование меняет
                    # тайминги кадров и рискует изменить размеры, ограниченные
                    # условиями использования источника.
                    payload = source_bytes
                    width, height = _gif_size(source_bytes)
                    mime = GIF_MIME

                checksum = hashlib.sha256(payload).hexdigest()
                sequence = _SEQUENCE[media.media_type]
                key = storage_key_for(external_id, media.media_type)

                asset = ExerciseMediaAsset(
                    exercise_external_id=external_id,
                    exercise_source=exercise_source,
                    media_type=media.media_type,
                    sequence=sequence,
                    storage_key=key,
                    mime_type=mime,
                    width=width,
                    height=height,
                    size_bytes=len(payload),
                    checksum=checksum,
                    source=candidate.source_key[:64],
                    source_url=media.source_url,
                    license=(media.attribution or candidate.attribution or None),
                )

                if dry_run:
                    stats.files_uploaded += 1
                    stats.by_type[media.media_type] = (
                        stats.by_type.get(media.media_type, 0) + 1
                    )
                    continue

                previous = existing.get((external_id, exercise_source, sequence))
                if previous == checksum and self._storage.object_exists(key):
                    stats.files_skipped_unchanged += 1
                else:
                    self._storage.put_object(key, payload, mime)
                    stats.files_uploaded += 1
                await self._repository.upsert(asset)
                stats.assets_written += 1
                stats.by_type[media.media_type] = (
                    stats.by_type.get(media.media_type, 0) + 1
                )
        return stats


def _gif_size(data: bytes) -> tuple[int, int]:
    """Размеры GIF из заголовка.

    Читается напрямую, а не через PIL: открытие анимации ради двух чисел
    декодирует первый кадр, и на 1324 файлах это заметная работа без выигрыша.
    Заголовок GIF содержит ширину и высоту в байтах 6..9 little-endian.
    """
    if len(data) < 10 or not data.startswith((b"GIF87a", b"GIF89a")):
        return 0, 0
    width = int.from_bytes(data[6:8], "little")
    height = int.from_bytes(data[8:10], "little")
    return width, height
