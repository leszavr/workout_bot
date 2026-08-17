"""Unit-тесты валидации ответов анкеты."""
from __future__ import annotations

import pytest

from src.application.questionnaire.questions import QUESTIONS_BY_ID
from src.application.questionnaire.service import QuestionnaireService
from src.domain.profile import FitnessProfile
from src.errors import QuestionnaireValidationError
from src.infrastructure.files.storage import LocalFileStorage


@pytest.fixture
def service(tmp_path) -> QuestionnaireService:
    return QuestionnaireService(LocalFileStorage(tmp_path, max_files=10, max_size_mb=20))


@pytest.fixture
def profile() -> FitnessProfile:
    return FitnessProfile()


class TestAgeValidation:
    def test_valid(self, service, profile):
        result = service.answer_text(profile, "q02_age", "35")
        assert result.profile.client.age_years == 35

    @pytest.mark.parametrize("bad", ["abc", "13", "101", "0", "-5"])
    def test_invalid(self, service, profile, bad):
        with pytest.raises(QuestionnaireValidationError):
            service.answer_text(profile, "q02_age", bad)


class TestHeightValidation:
    def test_valid(self, service, profile):
        result = service.answer_text(profile, "q04_height", "180")
        assert result.profile.client.height_cm == 180

    @pytest.mark.parametrize("bad", ["119", "251", "abc"])
    def test_invalid(self, service, profile, bad):
        with pytest.raises(QuestionnaireValidationError):
            service.answer_text(profile, "q04_height", bad)


class TestWeightValidation:
    def test_valid(self, service, profile):
        result = service.answer_text(profile, "q05_weight", "82.5")
        assert result.profile.client.weight_kg == 82.5

    @pytest.mark.parametrize("bad", ["29", "301", "abc"])
    def test_invalid(self, service, profile, bad):
        with pytest.raises(QuestionnaireValidationError):
            service.answer_text(profile, "q05_weight", bad)


class TestWaistValidation:
    def test_valid(self, service, profile):
        result = service.answer_text(profile, "q06_waist", "90")
        assert result.profile.client.waist_cm == 90

    @pytest.mark.parametrize("bad", ["39", "201", "abc"])
    def test_invalid(self, service, profile, bad):
        with pytest.raises(QuestionnaireValidationError):
            service.answer_text(profile, "q06_waist", bad)


class TestNameValidation:
    def test_valid(self, service, profile):
        result = service.answer_text(profile, "q01_name", "Иван")
        assert result.profile.client.name == "Иван"

    @pytest.mark.parametrize("bad", ["И", "x" * 51])
    def test_invalid(self, service, profile, bad):
        with pytest.raises(QuestionnaireValidationError):
            service.answer_text(profile, "q01_name", bad)


class TestMandatorySkip:
    def test_cannot_skip_mandatory(self, service, profile):
        with pytest.raises(QuestionnaireValidationError):
            service.skip(profile, "q01_name")

    def test_can_skip_optional(self, service, profile):
        result = service.skip(profile, "q06_waist")
        assert "q06_waist" in result.profile.questionnaire.skipped_questions

    def test_skip_word_in_text(self, service, profile):
        result = service.answer_text(profile, "q06_waist", "пропустить")
        assert result.profile.client.waist_cm is None


class TestChoiceAnswers:
    def test_choice(self, service, profile):
        result = service.answer_choice(profile, "q03_sex", "sex_female")
        assert result.profile.client.sex.value == "female"

    def test_unknown_option(self, service, profile):
        with pytest.raises(QuestionnaireValidationError):
            service.answer_choice(profile, "q03_sex", "sex_unknown")

    def test_text_rejected_for_choice_question(self, service, profile):
        with pytest.raises(QuestionnaireValidationError):
            service.answer_text(profile, "q03_sex", "мужской")


class TestListParsing:
    def test_comma_and_newline(self, service, profile):
        result = service.answer_text(profile, "q08_secondary_goals", "осанка, выносливость\nгибкость")
        assert result.profile.goals.secondary == ["осанка", "выносливость", "гибкость"]
