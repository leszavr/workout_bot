"""Подготовка и рендеринг HTML-программы (application layer, Stage 5).

ProgramHtmlService собирает всё необходимое для рендеринга:
данные каталога (имена, техника), медиа упражнения из object storage
и вызывает WorkoutProgramHtmlRenderer.

Режимы:
- html (default): фотографии встраиваются как data-URI → файл работает
  офлайн и не зависит от доступности backend;
- url: абсолютные HTTPS-ссылки на media endpoint → компактный файл.
"""
from __future__ import annotations

import base64
import logging
from typing import Iterable

from src.application.media.service import ExerciseMediaService
from src.application.programs.html_renderer import (
    ExerciseInfo,
    ExerciseMediaItem,
    render_program_html,
)
from src.domain.program import ProgramExercise, WorkoutProgram
from src.errors import HtmlRenderError, MediaStorageError

logger = logging.getLogger(__name__)

MEDIA_MODE_HTML = "html"
MEDIA_MODE_URL = "url"
VALID_MEDIA_MODES = {MEDIA_MODE_HTML, MEDIA_MODE_URL}


class ProgramHtmlService:
    def __init__(
        self,
        *,
        exercise_repository,
        media_service: ExerciseMediaService,
        media_mode: str = MEDIA_MODE_HTML,
        public_base_url: str = "",
        max_media_per_exercise: int = 5,
    ) -> None:
        if media_mode not in VALID_MEDIA_MODES:
            raise ValueError(f"Недопустимый media_mode: {media_mode}")
        if media_mode == MEDIA_MODE_URL and not public_base_url:
            raise ValueError("MEDIA_MODE=url требует public_base_url")
        self._exercises = exercise_repository
        self._media = media_service
        self._mode = media_mode
        self._base_url = public_base_url
        self._max_media = max_media_per_exercise

    @property
    def media_mode(self) -> str:
        return self._mode

    async def render(self, program: WorkoutProgram) -> bytes:
        """Генерирует HTML-документ программы. Бросает HtmlRenderError при сбое."""
        used = self._collect_used_exercises(program)
        if not used:
            raise HtmlRenderError("Программа не содержит упражнений")

        exercise_info = await self._load_exercise_info(used)
        media_items = await self._prepare_media(used)

        try:
            html = render_program_html(
                program,
                exercise_info=list(exercise_info.values()),
                media=media_items,
            )
        except Exception as exc:  # noqa: BLE001 — нормализуем в HtmlRenderError
            raise HtmlRenderError(f"Рендеринг HTML не удался: {exc}") from exc
        return html.encode("utf-8")

    def _collect_used_exercises(
        self, program: WorkoutProgram
    ) -> list[tuple[str, str]]:
        seen: dict[tuple[str, str], None] = {}
        for day in program.training_days:
            for ex in day.exercises:
                seen[(ex.exercise_external_id, ex.exercise_source)] = None
        return list(seen.keys())

    async def _load_exercise_info(
        self, used: Iterable[tuple[str, str]]
    ) -> dict[str, ExerciseInfo]:
        result: dict[str, ExerciseInfo] = {}
        for external_id, source in used:
            exercise = await self._exercises.get_by_external_id(external_id, source)
            if exercise is None:
                logger.warning(
                    "event=render_exercise_not_found",
                    extra={"exercise_external_id": external_id},
                )
                continue
            result[external_id] = ExerciseInfo(
                external_id=external_id,
                name=exercise.name,
                name_ru=exercise.name_ru,
                technique=exercise.technique_ru or exercise.technique,
            )
        return result

    async def _prepare_media(
        self, used: Iterable[tuple[str, str]]
    ) -> list[ExerciseMediaItem]:
        pairs = list(used)
        assets_by_exercise = await self._media.bulk_list(
            pairs, limit_per_exercise=self._max_media
        )

        items: list[ExerciseMediaItem] = []
        for external_id, assets in assets_by_exercise.items():
            for asset in assets:
                if self._mode == MEDIA_MODE_URL:
                    items.append(
                        ExerciseMediaItem(
                            exercise_external_id=external_id,
                            sequence=asset.sequence,
                            src=self._media.public_url(asset, self._base_url),
                        )
                    )
                    continue
                try:
                    data = await self._media.get_bytes(asset)
                except MediaStorageError:
                    # Отсутствие файла — не placeholder: изображение пропускается,
                    # ошибка уже зарегистрирована media-сервисом.
                    continue
                b64 = base64.b64encode(data).decode("ascii")
                items.append(
                    ExerciseMediaItem(
                        exercise_external_id=external_id,
                        sequence=asset.sequence,
                        src=f"data:{asset.mime_type};base64,{b64}",
                    )
                )
        return items
