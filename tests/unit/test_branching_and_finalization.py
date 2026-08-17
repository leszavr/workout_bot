"""Unit-тесты ветвления вопросов, условий пропуска, идемпотентности и ID."""
from __future__ import annotations

import pytest

from src.application.profiles.finalization import ProfileFinalizationService
from src.application.questionnaire.questions import (
    QUESTIONS,
    active_questions,
    next_question_id,
)
from src.application.questionnaire.service import QuestionnaireService
from src.domain.enums import TrainingLocationType
from src.domain.profile import FitnessProfile
from src.infrastructure.files.storage import LocalFileStorage
from src.infrastructure.persistence.profile_repository import FileProfileRepository


@pytest.fixture
def repository(tmp_path) -> FileProfileRepository:
    return FileProfileRepository(tmp_path / "profiles", tmp_path / "counter.json")


@pytest.fixture
def service(tmp_path) -> QuestionnaireService:
    return QuestionnaireService(LocalFileStorage(tmp_path / "photos", max_files=10, max_size_mb=20))


class TestBranching:
    def test_no_current_training_skips_questions(self):
        profile = FitnessProfile()
        profile.training_background.current_frequency_per_week = 0
        ids = [q.id for q in active_questions(profile)]
        for skipped in ("q13_current_activity", "q14_current_exercises", "q15_working_weights"):
            assert skipped not in ids

    def test_training_now_keeps_questions(self):
        profile = FitnessProfile()
        profile.training_background.current_frequency_per_week = 3
        ids = [q.id for q in active_questions(profile)]
        assert "q13_current_activity" in ids

    def test_home_location_branch(self):
        profile = FitnessProfile()
        profile.training_location.primary_location = TrainingLocationType.HOME
        ids = [q.id for q in active_questions(profile)]
        assert "q17_gym_name" not in ids
        assert "q18_equipment" not in ids
        assert "q19_equipment_photos" not in ids
        assert "q18b_home_equipment" in ids

    def test_gym_location_branch(self):
        profile = FitnessProfile()
        profile.training_location.primary_location = TrainingLocationType.GYM
        ids = [q.id for q in active_questions(profile)]
        assert "q17_gym_name" in ids
        assert "q18b_home_equipment" not in ids

    def test_no_limitations_skips_categories(self):
        profile = FitnessProfile()
        profile.health_and_limitations.has_limitations = False
        ids = [q.id for q in active_questions(profile)]
        assert "q25_limitation_categories" not in ids

    def test_no_clearance_skips_doctor_recommendations(self):
        profile = FitnessProfile()
        profile.health_and_limitations.medical_clearance_required = False
        ids = [q.id for q in active_questions(profile)]
        assert "q28_doctor_recommendations" not in ids

    def test_next_question_skips_inactive(self):
        profile = FitnessProfile()
        profile.training_background.current_frequency_per_week = 0
        assert next_question_id(profile, "q12_current_frequency") == "q16_location"

    def test_next_question_after_location_home(self):
        profile = FitnessProfile()
        profile.training_location.primary_location = TrainingLocationType.HOME
        assert next_question_id(profile, "q16_location") == "q18b_home_equipment"

    def test_next_question_after_location_gym(self):
        profile = FitnessProfile()
        profile.training_location.primary_location = TrainingLocationType.GYM
        assert next_question_id(profile, "q16_location") == "q17_gym_name"

    def test_last_question_returns_none(self):
        profile = FitnessProfile()
        assert next_question_id(profile, "q36_free_text") is None


class TestIdGeneration:
    def test_profile_id_is_uuid_hex(self, service):
        profile = service.start_profile("123", "user")
        assert profile.profile_id is not None
        assert len(profile.profile_id) == 32
        int(profile.profile_id, 16)  # валидный hex

    def test_display_number_format(self, repository):
        number = repository.next_display_number()
        assert number.startswith("REQ-")
        assert number.endswith("-00001")

    def test_display_number_increments(self, repository):
        first = repository.next_display_number()
        second = repository.next_display_number()
        assert int(first.rsplit("-", 1)[1]) + 1 == int(second.rsplit("-", 1)[1])


class TestIdempotentFinalization:
    def _filled_profile(self, service) -> FitnessProfile:
        profile = service.start_profile("123", "user")
        return profile

    def test_first_finalize_saves(self, service, repository):
        profile = self._filled_profile(service)
        finalization = ProfileFinalizationService(repository)
        result = finalization.finalize(profile)
        assert result.already_finalized is False
        assert repository.exists(profile.profile_id)
        assert profile.display_number is not None

    def test_second_finalize_is_idempotent(self, service, repository):
        profile = self._filled_profile(service)
        finalization = ProfileFinalizationService(repository)
        first = finalization.finalize(profile)
        second = finalization.finalize(profile)
        assert second.already_finalized is True
        assert second.profile.profile_id == first.profile.profile_id
        assert second.profile.display_number == first.profile.display_number

    def test_consents_recorded_with_metadata(self, service, repository):
        profile = self._filled_profile(service)
        ProfileFinalizationService(repository).finalize(profile)
        assert len(profile.consents) == 3
        for consent in profile.consents:
            assert consent.granted_at is not None
            assert consent.document_version == "1.0"
            assert consent.source == "telegram_review_confirm"

    def test_no_duplicate_consents_on_repeat(self, service, repository):
        profile = self._filled_profile(service)
        finalization = ProfileFinalizationService(repository)
        finalization.finalize(profile)
        finalization.finalize(profile)
        assert len(profile.consents) == 3


class TestNormalization:
    def test_roundtrip_preserves_data(self, service, repository):
        profile = service.start_profile("123", "user")
        profile.client.name = "Иван"
        profile.client.age_years = 30
        ProfileFinalizationService(repository).finalize(profile)
        loaded = repository.get(profile.profile_id)
        assert loaded is not None
        assert loaded.client.name == "Иван"
        assert loaded.client.age_years == 30

    def test_legacy_consents_dict_migrated(self):
        legacy = {
            "schema_version": "1.0",
            "profile_id": "x",
            "consents": {
                "data_processing_confirmed": True,
                "health_information_confirmed": True,
                "accuracy_confirmed": False,
            },
        }
        profile = FitnessProfile.model_validate(legacy)
        assert len(profile.consents) == 2

    def test_question_count(self):
        # 36 вопросов анкеты + q18b_home_equipment (ветка «дома»)
        assert len(QUESTIONS) == 37
