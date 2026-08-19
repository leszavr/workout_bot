"""API v1: media endpoint для фото упражнений (Stage 5).

GET /api/v1/media/exercises/{external_id}/{sequence} — отдаёт файл из
object storage (MinIO) через backend. Медиа — публичный контент каталога
(Unlicense), не персональные данные, поэтому endpoint не требует auth и
безопасен для кэширования (HTML-режим url ссылается именно сюда).
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query
from fastapi.responses import Response

from apps.backend.api.v1.dependencies import build_exercise_media_service
from src.errors import MediaStorageError

router = APIRouter(prefix="/api/v1/media")


@router.get(
    "/exercises/{external_id}/{sequence}",
    responses={404: {"description": "Media not found"}},
)
async def get_exercise_media(
    external_id: Annotated[str, Path(max_length=128)],
    sequence: Annotated[int, Path(ge=1, le=100)],
    source: Annotated[str | None, Query(max_length=64)] = None,
) -> Response:
    service = build_exercise_media_service()
    assets = await service.list_for_exercise(
        external_id, source=source or "leszavr/workout"
    )
    match = next((a for a in assets if a.sequence == sequence), None)
    if match is None:
        raise HTTPException(status_code=404, detail="Media not found")
    try:
        data = await service.get_bytes(match)
    except MediaStorageError as exc:
        raise HTTPException(status_code=404, detail="Media object missing") from exc
    return Response(
        content=data,
        media_type=match.mime_type,
        headers={"Cache-Control": "public, max-age=86400, immutable"},
    )
