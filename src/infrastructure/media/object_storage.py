"""Object storage для медиа упражнений (Stage 5).

Абстракция ``ObjectStorage`` позволяет заменить MinIO на S3 / Cloudflare R2 /
CDN без изменения domain-модели, importer'а и HTML-рендерера.
PostgreSQL хранит metadata, object storage — сами файлы.

Фабрика ``create_object_storage()`` выбирает реализацию из конфигурации:
MINIO_ENDPOINT/MINIO_ACCESS_KEY → MinIO; иначе dev/test in-memory/локальная.
"""
from __future__ import annotations

import abc
import io
import logging
import threading
from pathlib import Path

from src.errors import MediaStorageError

logger = logging.getLogger(__name__)

WEBP_MIME = "image/webp"


class ObjectStorage(abc.ABC):
    """Минимальный контракт S3-совместимого хранилища."""

    @abc.abstractmethod
    def put_object(self, key: str, data: bytes, content_type: str) -> None: ...

    @abc.abstractmethod
    def get_object(self, key: str) -> bytes: ...

    @abc.abstractmethod
    def object_exists(self, key: str) -> bool: ...

    @abc.abstractmethod
    def delete_object(self, key: str) -> None: ...


class MinioObjectStorage(ObjectStorage):
    """MinIO / S3-совместимое хранилище (официальный SDK minio)."""

    def __init__(self, endpoint: str, access_key: str, secret_key: str, bucket: str, secure: bool = False) -> None:
        from minio import Minio

        self._bucket = bucket
        self._client = Minio(
            endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=secure,
        )
        self._ensure_bucket()

    def _ensure_bucket(self) -> None:
        try:
            if not self._client.bucket_exists(self._bucket):
                self._client.make_bucket(self._bucket)
                logger.info("event=minio_bucket_created", extra={"bucket": self._bucket})
        except Exception as exc:  # noqa: BLE001 — нормализация в MediaStorageError
            raise MediaStorageError(f"Не удалось получить доступ к MinIO bucket '{self._bucket}': {exc}") from exc

    def put_object(self, key: str, data: bytes, content_type: str = WEBP_MIME) -> None:
        try:
            self._client.put_object(
                self._bucket,
                key,
                io.BytesIO(data),
                length=len(data),
                content_type=content_type,
            )
        except Exception as exc:  # noqa: BLE001
            raise MediaStorageError(f"Не удалось сохранить объект '{key}': {exc}") from exc

    def get_object(self, key: str) -> bytes:
        try:
            response = self._client.get_object(self._bucket, key)
            try:
                return response.read()
            finally:
                response.close()
                response.release_conn()
        except Exception as exc:  # noqa: BLE001
            raise MediaStorageError(f"Не удалось прочитать объект '{key}': {exc}") from exc

    def object_exists(self, key: str) -> bool:
        try:
            self._client.stat_object(self._bucket, key)
            return True
        except Exception:  # noqa: BLE001
            return False

    def delete_object(self, key: str) -> None:
        try:
            self._client.remove_object(self._bucket, key)
        except Exception as exc:  # noqa: BLE001
            raise MediaStorageError(f"Не удалось удалить объект '{key}': {exc}") from exc


class InMemoryObjectStorage(ObjectStorage):
    """In-memory реализация для unit-тестов и dev без MinIO."""

    def __init__(self) -> None:
        self._objects: dict[str, tuple[bytes, str]] = {}
        self._lock = threading.Lock()

    def put_object(self, key: str, data: bytes, content_type: str = WEBP_MIME) -> None:
        with self._lock:
            self._objects[key] = (data, content_type)

    def get_object(self, key: str) -> bytes:
        with self._lock:
            if key not in self._objects:
                raise MediaStorageError(f"Объект '{key}' не найден")
            return self._objects[key][0]

    def object_exists(self, key: str) -> bool:
        with self._lock:
            return key in self._objects

    def delete_object(self, key: str) -> None:
        with self._lock:
            self._objects.pop(key, None)


class LocalObjectStorage(ObjectStorage):
    """Файловая реализация (dev/test, когда MinIO не сконфигурирован)."""

    def __init__(self, base_dir: Path, bucket: str) -> None:
        self._base_dir = base_dir / bucket
        self._base_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self._base_dir / key).resolve()
        if not str(path).startswith(str(self._base_dir.resolve())):
            raise MediaStorageError(f"Недопустимый ключ объекта: {key}")
        return path

    def put_object(self, key: str, data: bytes, content_type: str = WEBP_MIME) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def get_object(self, key: str) -> bytes:
        path = self._path(key)
        if not path.exists():
            raise MediaStorageError(f"Объект '{key}' не найден")
        return path.read_bytes()

    def object_exists(self, key: str) -> bool:
        return self._path(key).exists()

    def delete_object(self, key: str) -> None:
        path = self._path(key)
        if path.exists():
            path.unlink()


def create_object_storage() -> ObjectStorage:
    """Фабрика по конфигурации окружения.

    MINIO_ENDPOINT + ключи → MinIO.
    Иначе — LocalObjectStorage в WORKOUT_DATA_DIR/object_storage (dev/test).
    """
    from src.infrastructure.config import (
        DATA_DIR,
        MEDIA_BUCKET,
        MINIO_ACCESS_KEY,
        MINIO_ENDPOINT,
        MINIO_SECRET_KEY,
        MINIO_SECURE,
    )

    if MINIO_ENDPOINT and MINIO_ACCESS_KEY and MINIO_SECRET_KEY:
        return MinioObjectStorage(
            endpoint=MINIO_ENDPOINT,
            access_key=MINIO_ACCESS_KEY,
            secret_key=MINIO_SECRET_KEY,
            bucket=MEDIA_BUCKET,
            secure=MINIO_SECURE,
        )
    return LocalObjectStorage(DATA_DIR / "object_storage", MEDIA_BUCKET)
