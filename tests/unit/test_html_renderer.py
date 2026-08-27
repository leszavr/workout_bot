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


class TestHtmlTimer:
    def test_timer_block_rendered(self):
        html = render_program_html(_program(), exercise_info=_info())
        assert 'class="timer-wrap"' in html
        assert 'id="timerDisp"' in html
        assert "Таймер отдыха" in html

    def test_timer_presets(self):
        html = render_program_html(_program(), exercise_info=_info())
        for preset in ("setTimer(60,this)", "setTimer(90,this)", "setTimer(120,this)", "setTimer(180,this)"):
            assert preset in html
        assert 'class="btn-t act" onclick="setTimer(90,this)"' in html

    def test_timer_controls(self):
        html = render_program_html(_program(), exercise_info=_info())
        assert 'class="btn-go" onclick="startTimer()"' in html
        assert 'class="btn-rst" onclick="resetTimer()"' in html
        assert "Старт" in html
        assert "Сброс" in html

    def test_timer_js_auto_reset(self):
        html = render_program_html(_program(), exercise_info=_info())
        assert "function startTimer()" in html
        assert "function resetTimer()" in html
        # по истечении — автоматический возврат к исходному значению
        assert "tAutoReset = setTimeout" in html
        assert "tSec = tSet; updTimer();" in html

    def test_timer_hidden_in_print(self):
        html = render_program_html(_program(), exercise_info=_info())
        assert ".days-nav,.timer-wrap,.timer-spacer{display:none}" in html


class TestHtmlPinnedTimer:
    """Приклеивание таймера к верху экрана во время отдыха.

    Без него отсчёт уезжает за пределы экрана, как только пользователь
    прокручивает страницу к следующему упражнению.
    """

    def test_pinned_markup_present(self):
        html = render_program_html(_program(), exercise_info=_info())
        assert 'id="timerAnchor"' in html
        assert 'id="timerSpacer"' in html
        assert 'id="timerWrap"' in html

    def test_pinned_style_is_fixed_and_translucent(self):
        html = render_program_html(_program(), exercise_info=_info())
        assert ".timer-wrap.pinned{position:fixed;top:0" in html
        assert "opacity:.9" in html
        assert "backdrop-filter:blur(10px)" in html

    def test_pinned_state_hides_presets_and_label(self):
        """В сжатом виде остаются только отсчёт и Старт/Сброс."""
        html = render_program_html(_program(), exercise_info=_info())
        assert ".timer-wrap.pinned .timer-lbl{display:none}" in html
        assert ".timer-wrap.pinned .btn-t{display:none}" in html

    def test_pin_follows_scroll_only_while_running(self):
        html = render_program_html(_program(), exercise_info=_info())
        assert "function _syncPin()" in html
        assert "_pinTimer(tRunning && anchor.getBoundingClientRect().top < 0)" in html
        assert "window.addEventListener('scroll', _syncPin, { passive: true })" in html

    def test_spacer_compensates_height(self):
        """Переход в fixed вынимает элемент из потока: без распорки страница дёргается."""
        html = render_program_html(_program(), exercise_info=_info())
        assert "spacer.style.height = wrap.offsetHeight + 'px'" in html

    def test_state_changes_resync_pin(self):
        html = render_program_html(_program(), exercise_info=_info())
        # старт/пауза, сброс, смена пресета и автовозврат
        assert html.count("_syncPin();") >= 5
