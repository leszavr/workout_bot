"""Хранилище файлов (фото оборудования).

Интерфейс ``FileStorage`` позволяет позже заменить ``LocalFileStorage``
на ``S3FileStorage`` без изменения Telegram handlers.
Ограничения: количество файлов, размер, тип.
"""
from __future__ import annotations

import abc
import re
from pathlib import Path

from src.errors import FileStorageError

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


class FileStorage(abc.ABC):
    @abc.abstractmethod
    def save_photo(self, profile_id: str, file_id: str, content: bytes, extension: str) -> str:
        """Сохраняет фото и возвращает относительный ключ файла."""

    @abc.abstractmethod
    def count_photos(self, profile_id: str) -> int: ...

    @abc.abstractmethod
    def delete_profile_files(self, profile_id: str) -> None:
        """Очистка файлов профиля (удаление данных / незавершённые анкеты)."""


class LocalFileStorage(FileStorage):
    def __init__(self, base_dir: Path, max_files: int, max_size_mb: int) -> None:
        self._base_dir = base_dir
        self._max_files = max_files
        self._max_size_bytes = max_size_mb * 1024 * 1024

    def _profile_dir(self, profile_id: str) -> Path:
        safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", profile_id)
        return self._base_dir / safe_id

    def save_photo(self, profile_id: str, file_id: str, content: bytes, extension: str) -> str:
        ext = extension.lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise FileStorageError("Недопустимый тип файла. Разрешены: JPG, PNG, WEBP.")
        if len(content) > self._max_size_bytes:
            raise FileStorageError(
                f"Файл слишком большой. Максимальный размер: {self._max_size_bytes // (1024 * 1024)} МБ."
            )
        profile_dir = self._profile_dir(profile_id)
        existing = list(profile_dir.glob("*")) if profile_dir.exists() else []
        if len(existing) >= self._max_files:
            raise FileStorageError(f"Достигнут лимит файлов: не более {self._max_files}.")
        profile_dir.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", file_id)[:100]
        path = profile_dir / f"{safe_name}{ext}"
        try:
            path.write_bytes(content)
        except OSError as exc:
            raise FileStorageError(f"Не удалось сохранить файл: {exc}") from exc
        return f"{profile_dir.name}/{path.name}"

    def count_photos(self, profile_id: str) -> int:
        profile_dir = self._profile_dir(profile_id)
        if not profile_dir.exists():
            return 0
        return len(list(profile_dir.glob("*")))

    def delete_profile_files(self, profile_id: str) -> None:
        profile_dir = self._profile_dir(profile_id)
        if not profile_dir.exists():
            return
        try:
            for file in profile_dir.iterdir():
                if file.is_file():
                    file.unlink()
            profile_dir.rmdir()
        except OSError as exc:
            raise FileStorageError(f"Не удалось удалить файлы профиля: {exc}") from exc
