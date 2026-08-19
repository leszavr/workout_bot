"""Конвертация изображений упражнений в WebP (Stage 5).

Единственная функция конвертации: JPEG/PNG/GIF → WebP.
Проверяет результат: открытие, размеры, MIME-тип.
"""
from __future__ import annotations

import io

from src.errors import MediaStorageError

WEBP_MIME = "image/webp"
WEBP_QUALITY = 85
MAX_DIMENSION = 1600


def convert_to_webp(source_bytes: bytes, max_dimension: int = MAX_DIMENSION) -> tuple[bytes, int, int]:
    """Конвертирует исходное изображение в WebP.

    Возвращает ``(webp_bytes, width, height)``.
    Бросает MediaStorageError, если источник не является изображением
    или результат не является валидным WebP.
    """
    from PIL import Image, UnidentifiedImageError

    try:
        image = Image.open(io.BytesIO(source_bytes))
        image.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise MediaStorageError(f"Файл не является валидным изображением: {exc}") from exc

    image = image.convert("RGB")
    if image.width > max_dimension or image.height > max_dimension:
        image.thumbnail((max_dimension, max_dimension), Image.LANCZOS)

    buffer = io.BytesIO()
    try:
        image.save(buffer, format="WEBP", quality=WEBP_QUALITY, method=4)
    except (OSError, ValueError) as exc:
        raise MediaStorageError(f"Конвертация в WebP не удалась: {exc}") from exc

    result = buffer.getvalue()

    try:
        verification = Image.open(io.BytesIO(result))
        if verification.format != "WEBP":
            raise MediaStorageError(
                f"Результат конвертации не является WebP: {verification.format}"
            )
        width, height = verification.size
    except OSError as exc:
        raise MediaStorageError(f"Не удалось открыть результат WebP: {exc}") from exc

    return result, width, height
