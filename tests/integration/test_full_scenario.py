"""Integration-тесты полного сценария анкеты.

Сценарий: start → прохождение анкеты → review → confirm → profile saved.
Покрываются все основные ветви:
не тренируется / тренируется, home / gym,
нет ограничений / есть ограничения, нет рекомендаций врача / есть рекомендации.
"""
from __future__ import annotations

import pytest

from src.application.profiles.finalization import ProfileFinalizationService
from src.application.questionnaire.questions import active_questions, next_question_id
from src.application.questionnaire.review import render_review_html
from src.application.questionnaire.service import QuestionnaireService
from src.domain.enums import CompletionStatus
from src.domain.profile import FitnessProfile
from src.infrastructure.files.storage import LocalFileStorage
from src.infrastructure.persistence.profile_repository import FileProfileRepository


@pytest.fixture
def repository(tmp_path) -> FileProfileRepository:
    return FileProfileRepository(tmp_path / "profiles", tmp_path / "counter.json")


@pytest.fixture
def service(tmp_path) -> QuestionnaireService:
    return QuestionnaireService(LocalFileStorage(tmp_path / "photos", max_files=10, max_size_mb=20))


# Ответы для текстовых вопросов по умолчанию.
TEXT_ANSWERS = {
    "q01_name": "Иван Петров",
    "q02_age": "30",
    "q04_height": "180",
    "q05_weight": "80",
    "q06_waist": "85",
    "q08_secondary_goals": "осанка, выносливость",
    "q09_desired_result": "Похудеть на 5 кг и стать сильнее",
    "q13_current_activity": "Тренажёры 40 минут",
    "q14_current_exercises": "приседания, жим",
    "q15_working_weights": "Жим ногами 120 кг",
    "q17_gym_name": "World Class",
    "q18_equipment": "гантели, скамья",
    "q18b_home_equipment": "гантели, коврик",
    "q25_limitation_categories": "боли в пояснице",
    "q27_movements_to_avoid": "бег, прыжки",
    "q28_doctor_recommendations": "избегать высоких нагрузок",
    "q29_preferred_exercises": "тренажёры",
    "q30_disliked_exercises": "бег",
    "q31_exercise_goals": "подтягивания",
    "q34_cardio_notes": "только эллипс",
    "q35_schedule_constraints": "понедельник 40 минут",
    "q36_free_text": "без прыжков",
}

# Ответы для вопросов с вариантами (callback_data).
CHOICE_ANSWERS = {
    "q03_sex": "sex_male",
    "q07_primary_goal": "goal_weight_loss",
    "q10_timeframe": "timeframe_3_6_months",
    "q11_experience": "exp_over_1_year",
    "q12_current_frequency": "freq_3",
    "q16_location": "loc_gym",
    "q20_sessions_per_week": "sessions_3",
    "q22_session_duration": "duration_60",
    "q23_preferred_time": "time_pref_evening",
    "q24_has_limitations": "limit_no",
    "q28_medical_clearance": "med_clear_no_recommendations",
    "q32_daily_activity": "activity_moderate",
    "q33_cardio_preference": "cardio_okay",
}


def run_questionnaire(service: QuestionnaireService, overrides: dict | None = None) -> FitnessProfile:
    """Проходит всю анкету через сервис, возвращает заполненный профиль."""
    choices = dict(CHOICE_ANSWERS)
    texts = dict(TEXT_ANSWERS)
    for key, value in (overrides or {}).items():
        if key in choices:
            choices[key] = value
        else:
            texts[key] = value

    profile = service.start_profile("12345", "testuser")
    question_id = service.first_question_id()

    while question_id is not None:
        question = service.get_question(question_id)
        if question.kind.value == "choice":
            result = service.answer_choice(profile, question_id, choices[question_id])
        elif question.kind.value == "multiselect":
            service.toggle_day(profile, "mon")
            service.toggle_day(profile, "wed")
            result = service.confirm_days(profile)
        elif question.kind.value == "photos":
            result = service.skip(profile, question_id)
        else:
            answer = texts.get(question_id)
            if answer is None and not question.required:
                result = service.skip(profile, question_id)
            else:
                result = service.answer_text(profile, question_id, answer or "—")
        profile = result.profile
        question_id = result.next_question_id

    return profile


async def finalize(service, repository, profile) -> FitnessProfile:
    result = await ProfileFinalizationService(repository).finalize(profile)
    return result.profile


class TestFullScenarioGymNoLimitations:
    async def test_scenario(self, service, repository):
        profile = run_questionnaire(service)
        # review
        html = render_review_html(profile)
        assert "Иван Петров" in html
        # confirm
        profile = await finalize(service, repository, profile)
        assert profile.questionnaire.completion_status is CompletionStatus.CONFIRMED
        assert await repository.exists(profile.profile_id)
        assert profile.display_number is not None
        assert len(profile.consents) == 3


class TestFullScenarioHomeWithLimitations:
    async def test_scenario(self, service, repository):
        overrides = {
            "q16_location": "loc_home",
            "q24_has_limitations": "limit_yes",
            "q28_medical_clearance": "med_clear_restricted",
        }
        profile = run_questionnaire(service, overrides)
        assert profile.training_location.primary_location.value == "home"
        assert profile.health_and_limitations.has_limitations is True
        assert profile.health_and_limitations.medical_clearance_required is True
        assert profile.health_and_limitations.doctor_recommendations is not None
        profile = await finalize(service, repository, profile)
        assert await repository.exists(profile.profile_id)


class TestBranchNotTraining:
    async def test_skips_current_training_questions(self, service, repository):
        overrides = {"q12_current_frequency": "freq_none"}
        profile = run_questionnaire(service, overrides)
        assert profile.training_background.current_frequency_per_week == 0
        assert profile.training_background.current_activity_description is None
        profile = await finalize(service, repository, profile)
        assert await repository.exists(profile.profile_id)


class TestBranchNoDoctorRecommendations:
    async def test_no_recommendations(self, service, repository):
        overrides = {"q28_medical_clearance": "med_clear_no_recommendations"}
        profile = run_questionnaire(service, overrides)
        assert profile.health_and_limitations.doctor_recommendations is None
        profile = await finalize(service, repository, profile)
        assert await repository.exists(profile.profile_id)


class TestReviewRendering:
    def test_review_contains_key_fields(self, service):
        profile = run_questionnaire(service)
        html = render_review_html(profile)
        assert "Ваша анкета" in html
        assert "Снижение веса" in html
        assert "В зале" in html
