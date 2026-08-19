"""Unit-тесты WorkoutProgramHtmlRenderer (Stage 5).

Проверяется мобильный HTML: структура, техника, количество изображений
(0/1/N), media URL-режим, отсутствие GitHub-ссылок и placeholder'ов,
экранирование пользовательского контента.
"""
from __future__ import annotations

import pytest

from src.application.programs.html_renderer import (
    ExerciseInfo,
    ExerciseMediaItem,
    render_program_html,
)
from src.domain.enums import GenerationSource
from src.domain.program import (
    GenerationInfo,
    ProgressionPlan,
    ProgramExercise,
    TrainingDay,
    WorkoutProgram,
)
from src.errors import HtmlRenderError

EX_ID = "Barbell_Full_Squat"
FAKE_WEBP_B64 = "UklGRiQAAABXRUJQVlA4IBgAAAAwAQCdASoBAAEAAQAcJaQAA3AA/v89WAA="


def _program(days: int = 1) -> WorkoutProgram:
    return WorkoutProgram(
        profile_id="p1",
        title="Тестовая программа",
        description="Программа для тестов <script>alert(1)</script>",
        duration_weeks=4,
        training_days_per_week=days,
        training_days=[
            TrainingDay(
                day_number=i + 1,
                title=f"День {i + 1}",
                focus="Full body",
                exercises=[
                    ProgramExercise(
                        exercise_external_id=EX_ID,
                        order=1,
                        sets=3,
                        repetitions_min=8,
                        repetitions_max=10,
                        rest_seconds=90,
                        notes="Совет: <b>не бросайте вес</b>",
                        technique_notes="1. Спина прижата\n2. Колени за носки\n3. Выдох при подъёме",
                    )
                ],
            )
            for i in range(days)
        ],
        progression=ProgressionPlan(description="Прибавляйте 2.5 кг", weekly_increase_percent=2.5),
        safety_notes=["Не работайте до отказа первые две недели"],
        generation=GenerationInfo(source=GenerationSource.DETERMINISTIC),
    )


def _info() -> list[ExerciseInfo]:
    return [
        ExerciseInfo(
            external_id=EX_ID,
            name="Barbell Full Squat",
            name_ru="Присед со штангой",
            technique="1. Исходное положение",
        )
    ]


class TestHtmlStructure:
    def test_html_generated_with_core_content(self):
        html = render_program_html(_program(), exercise_info=_info())

        assert html.startswith("<!DOCTYPE html>")
        assert 'lang="ru"' in html
        assert 'name="viewport"' in html
        assert "Тестовая программа" in html
        assert "Присед со штангой" in html
        assert "Техника" in html
        assert "отдых 90 сек" in html

    def test_days_count_matches_program(self):
        html = render_program_html(_program(days=3), exercise_info=_info())
        assert html.count('class="day-section"') == 3
        assert html.count('id="day-3"') == 1

    def test_exercise_count_rendered(self):
        program = _program()
        program.training_days[0].exercises.append(
            ProgramExercise(
                exercise_external_id=EX_ID,
                order=2,
                sets=2,
                repetitions_min=12,
                repetitions_max=12,
                rest_seconds=60,
            )
        )
        html = render_program_html(program, exercise_info=_info())
        assert html.count('class="exercise-card"') == 2

    def test_safety_notes_and_progression_rendered(self):
        html = render_program_html(_program(), exercise_info=_info())
        assert "Безопасность" in html
        assert "Не работайте до отказа первые две недели" in html
        assert "Прогрессия нагрузки" in html
        assert "2.5%" in html

    def test_empty_days_raises(self):
        program = _program()
        program.training_days = []
        program.training_days_per_week = 0
        with pytest.raises(HtmlRenderError):
            render_program_html(program)

    def test_user_content_escaped(self):
        html = render_program_html(_program(), exercise_info=_info())
        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;" in html
        assert "&lt;b&gt;не бросайте вес&lt;/b&gt;" in html


class TestHtmlImages:
    def test_no_images_no_placeholder(self):
        html = render_program_html(_program(), exercise_info=_info())
        assert '<div class="ex-images">' not in html
        assert "<img" not in html
        assert "placeholder" not in html.lower()
        assert "githubusercontent" not in html
        assert "github.com" not in html

    def test_single_image_rendered(self):
        media = [ExerciseMediaItem(EX_ID, 1, f"data:image/webp;base64,{FAKE_WEBP_B64}")]
        html = render_program_html(_program(), exercise_info=_info(), media=media)
        assert '<div class="ex-images">' in html
        assert html.count("<img") == 1
        assert FAKE_WEBP_B64 in html

    def test_multiple_images_rendered(self):
        media = [
            ExerciseMediaItem(EX_ID, seq, f"data:image/webp;base64,{FAKE_WEBP_B64}")
            for seq in range(1, 6)
        ]
        html = render_program_html(_program(), exercise_info=_info(), media=media)
        assert html.count("<img") == 5

    def test_empty_src_skipped(self):
        media = [ExerciseMediaItem(EX_ID, 1, "")]
        html = render_program_html(_program(), exercise_info=_info(), media=media)
        assert "<img" not in html

    def test_media_url_mode(self):
        media = [
            ExerciseMediaItem(
                EX_ID,
                1,
                f"https://workout.example.com/api/v1/media/exercises/{EX_ID}/1?source=leszavr/workout",
            )
        ]
        html = render_program_html(_program(), exercise_info=_info(), media=media)
        assert "/api/v1/media/exercises/" in html
        assert "github" not in html

    def test_images_not_rendered_for_unrelated_exercise(self):
        media = [ExerciseMediaItem("Other_Exercise", 1, "data:image/webp;base64,xxx")]
        html = render_program_html(_program(), exercise_info=_info(), media=media)
        assert "data:image/webp" not in html
