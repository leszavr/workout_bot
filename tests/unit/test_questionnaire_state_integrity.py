"""Тесты защиты состояния анкеты от неконсистентных ответов.

Регрессия, из-за которой писались эти тесты: на вопрос с фотографиями
(`q19_equipment_photos`) пользователь ответил текстом «пока нет». Строка
записалась в поле типа `list[str]` — присваивание Pydantic тогда не проверял —
и анкета застряла: сам ответ прошёл, а падало уже следующее чтение профиля из
FSM, на следующем вопросе. Пользователь видел молчание бота без объяснения.

Проверяется два уровня защиты:

1. вопрос с фотографиями не принимает текст и не трактует его как пропуск;
2. профиль отклоняет несовместимое присваивание в точке записи, а анкета
   превращает отказ в обычную ошибку вопроса.

Плюс общая проверка: ответ на любой вопрос оставляет профиль пригодным для
повторного чтения. Именно этого свойства и не хватало — ошибка проявлялась не
там, где возникала.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.application.questionnaire.questions import QUESTIONS, QuestionKind
from src.application.questionnaire.service import QuestionnaireService
from src.domain.enums import TrainingLocationType, Weekday
from src.domain.profile import FitnessProfile
from src.errors import QuestionnaireValidationError
from src.infrastructure.files.storage import LocalFileStorage

PHOTO_QUESTION = "q19_equipment_photos"


@pytest.fixture
def service(tmp_path) -> QuestionnaireService:
    return QuestionnaireService(
        LocalFileStorage(tmp_path / "photos", max_files=10, max_size_mb=20)
    )


@pytest.fixture
def gym_profile() -> FitnessProfile:
    profile = FitnessProfile()
    profile.training_location.primary_location = TrainingLocationType.GYM
    return profile


class TestPhotoQuestionRejectsText:
    def test_text_answer_is_rejected(self, service, gym_profile):
        with pytest.raises(QuestionnaireValidationError) as exc:
            service.answer_text(gym_profile, PHOTO_QUESTION, "пока нет")
        assert "фотограф" in exc.value.user_message.lower()

    def test_rejected_text_does_not_touch_profile(self, service, gym_profile):
        with pytest.raises(QuestionnaireValidationError):
            service.answer_text(gym_profile, PHOTO_QUESTION, "сейчас пришлю")
        assert gym_profile.training_location.equipment_photos == []

    def test_rejected_text_is_not_a_skip(self, service, gym_profile):
        """«Сейчас пришлю» означает обратное пропуску: вопрос должен остаться."""
        with pytest.raises(QuestionnaireValidationError):
            service.answer_text(gym_profile, PHOTO_QUESTION, "сейчас пришлю")
        assert PHOTO_QUESTION not in gym_profile.questionnaire.skipped_questions
        assert gym_profile.questionnaire.last_question_id != PHOTO_QUESTION

    def test_explicit_skip_word_still_works(self, service, gym_profile):
        result = service.answer_text(gym_profile, PHOTO_QUESTION, "пропустить")
        assert PHOTO_QUESTION in result.profile.questionnaire.skipped_questions
        assert result.next_question_id is not None

    def test_skip_button_still_works(self, service, gym_profile):
        result = service.skip(gym_profile, PHOTO_QUESTION)
        assert PHOTO_QUESTION in result.profile.questionnaire.skipped_questions

    def test_photo_answer_still_works(self, service, gym_profile):
        result = service.add_photo(gym_profile, "file-1", b"\x00\x01", ".jpg")
        assert len(result.profile.training_location.equipment_photos) == 1


class TestProfileRejectsBadAssignment:
    def test_string_into_list_field_is_rejected(self):
        """Второй уровень защиты: модель не даёт записать строку в список."""
        profile = FitnessProfile()
        with pytest.raises(ValidationError):
            profile.training_location.equipment_photos = "пока нет"

    def test_valid_assignment_and_enum_coercion_still_work(self):
        profile = FitnessProfile()
        profile.training_location.equipment_photos = ["photo.jpg"]
        profile.training_location.primary_location = "gym"
        assert profile.training_location.equipment_photos == ["photo.jpg"]
        assert profile.training_location.primary_location is TrainingLocationType.GYM

    def test_out_of_range_value_is_rejected(self):
        profile = FitnessProfile()
        with pytest.raises(ValidationError):
            profile.training_plan_preferences.sessions_per_week = 99


class TestEveryAnswerKeepsProfileReadable:
    """Ответ на любой вопрос должен оставлять профиль пригодным для чтения.

    Профиль между шагами анкеты сериализуется в FSM и читается заново. Если
    ответ оставил его в неконсистентном состоянии, анкета падает не на этом
    вопросе, а на следующем — как и произошло с `q19_equipment_photos`.
    """

    # Разные текстовые вопросы принимают разный ввод (возраст, рост, вес,
    # свободный текст). Перебираем кандидатов и берём первый принятый, чтобы
    # тест проверял реальный ответ, а не молча пропускал вопрос.
    TEXT_CANDIDATES = ("30", "170", "70", "тестовый ответ")

    def _answer_text(self, service, profile, question):
        last_error: QuestionnaireValidationError | None = None
        for candidate in self.TEXT_CANDIDATES:
            try:
                return service.answer_text(profile, question.id, candidate)
            except QuestionnaireValidationError as exc:
                last_error = exc
        raise AssertionError(
            f"{question.id} не принял ни один тестовый ввод: {last_error}"
        )

    @pytest.mark.parametrize("question", QUESTIONS, ids=lambda q: q.id)
    def test_profile_survives_serialization_roundtrip(self, service, question):
        profile = FitnessProfile()
        if question.kind is QuestionKind.CHOICE:
            result = service.answer_choice(
                profile, question.id, question.options[0].callback_data
            )
        elif question.kind is QuestionKind.MULTISELECT:
            service.toggle_day(profile, Weekday.MON.value)
            result = service.confirm_days(profile)
        elif question.kind is QuestionKind.PHOTOS:
            result = service.add_photo(profile, "file-1", b"\x00", ".jpg")
        else:
            result = self._answer_text(service, profile, question)

        # Ровно то, что делает gateway между шагами анкеты.
        FitnessProfile.model_validate(result.profile.model_dump(mode="json"))

    @pytest.mark.parametrize("question", QUESTIONS, ids=lambda q: q.id)
    def test_skipping_keeps_profile_readable(self, service, question):
        """Пропуск тоже меняет профиль и тоже должен оставлять его читаемым."""
        profile = FitnessProfile()
        if question.required:
            pytest.skip("обязательный вопрос пропустить нельзя")
        result = service.skip(profile, question.id)
        FitnessProfile.model_validate(result.profile.model_dump(mode="json"))
