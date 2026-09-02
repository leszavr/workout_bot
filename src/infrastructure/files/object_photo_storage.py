"""Хранилище фотографий оборудования в object storage (MinIO).

Реализация `FileStorage` поверх того же MinIO, где лежат изображения упражнений.
Появилась вместе с выносом Gateway за сетевую границу: фото приходят от Gateway
байтами, и записывать их обязан Backend в RU — файловая система EU-контейнера
для пользовательского контента недопустима.

Отдельная реализация, а не переиспользование медиа-репозитория упражнений:
у того своя доменная модель (метаданные в PostgreSQL, конвертация в WebP,
последовательность кадров), и фотографии анкеты в неё не укладываются. Общего
здесь только object storage, и он уже абстрагирован.

Ключи (`equipment/{profile_id}/{file_id}{ext}`) остаются такими же по форме, как
у `LocalFileStorage`, чтобы существующие профили с сохранёнными фото читались без
преобразования.
"""
from __future__ import annotations

import logging
import re

from src.errors import FileStorageError
from src.infrastructure.files.storage import ALLOWED_EXTENSIONS, FileStorage
from src.infrastructure.media.object_storage import ObjectStorage

logger = logging.getLogger(__name__)

PREFIX = "equipment"

# Тип содержимого по расширению. Определяется по расширению, а не по байтам:
# расширение уже проверено белым списком, и повторный разбор содержимого не даёт
# дополнительной защиты для файла, который никуда не исполняется.
CONTENT_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


def _safe(value: str, limit: int = 100) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", value)[:limit]


class ObjectStoragePhotoStorage(FileStorage):
    """Фото анкеты в object storage.

    Число сохранённых фото отдельным состоянием не хранится: оно известно
    вызывающему из `equipment_photos` профиля. Второй счётчик разошёлся бы с
    профилем при повторной отправке.
    """

    def __init__(
        self, storage: ObjectStorage, *, max_files: int, max_size_mb: int
    ) -> None:
        self._storage = storage
        self._max_files = max_files
        self._max_size_bytes = max_size_mb * 1024 * 1024

    def save_photo(
        self, profile_id: str, file_id: str, content: bytes, extension: str
    ) -> str:
        ext = extension.lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise FileStorageError("Недопустимый тип файла. Разрешены: JPG, PNG, WEBP.")
        if not content:
            raise FileStorageError("Файл пустой.")
        if len(content) > self._max_size_bytes:
            raise FileStorageError(
                f"Файл слишком большой. Максимальный размер: "
                f"{self._max_size_bytes // (1024 * 1024)} МБ."
            )
        key = f"{PREFIX}/{_safe(profile_id, 64)}/{_safe(file_id)}{ext}"
        try:
            self._storage.put_object(
                key, content, CONTENT_TYPES.get(ext, "application/octet-stream")
            )
        except Exception as exc:  # noqa: BLE001 — MediaStorageError и ошибки клиента
            raise FileStorageError(f"Не удалось сохранить файл: {exc}") from exc
        return key

    def count_photos(self, profile_id: str) -> int:
        """Не поддерживается: перечисление ключей контракту не нужно.

        Лимит на число фото проверяет вызывающий по списку в профиле — там он и
        так есть. Реализовывать листинг ради метода, который никто не вызывает,
        значит добавить обход бакета без потребителя.
        """
        raise NotImplementedError(
            "count_photos не поддерживается object storage: "
            "лимит проверяется по equipment_photos профиля"
        )

    def delete_profile_files(self, profile_id: str) -> None:
        """Не поддерживается: удаление по префиксу требовало бы листинга бакета.

        Ключи сохранённых фото известны из `equipment_photos` профиля, поэтому
        удаление возможно точечно. Массовое удаление по префиксу затронуло бы и
        файлы, о которых профиль не знает.
        """
        raise NotImplementedError(
            "delete_profile_files не поддерживается: удаляйте по ключам профиля"
        )
