"""QuestionnaireService — вся бизнес-логика анкеты.

Telegram handler делает только: получить сообщение → вызвать сервис →
отправить результат. Здесь нет ничего Telegram-специфичного.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4

from pydantic import ValidationError

from src.application.questionnaire.questions import (
    QUESTIONS_BY_ID,
    QuestionDefinition,
    QuestionKind,
    active_questions,
    next_question_id,
    question_progress,
)
from src.domain.enums import CompletionStatus, ConsentScope, Weekday
from src.domain.profile import FitnessProfile
from src.errors import QuestionnaireValidationError
from src.infrastructure.files.storage import FileStorage

MAX_TEXT_LENGTH = 2000
SKIP_WORDS = {"пропустить", "skip"}


@dataclass
class QuestionPrompt:
    """Что показать пользователю для вопроса."""

    question_id: str
    text: str
    kind: QuestionKind
    options: tuple = field(default_factory=tuple)
    skippable: bool = False
    selected_days: list[str] = field(default_factory=list)


@dataclass
class AnswerResult:
    """Результат обработки ответа."""

    profile: FitnessProfile
    next_question_id: str | None  # None → анкета завершена, показать review
    confirmation: str = "✅ Записал: ответ сохранён"


class QuestionnaireService:
    def __init__(self, file_storage: FileStorage) -> None:
        self._file_storage = file_storage

    # --- Навигация -----------------------------------------------------------

    def start_profile(self, bot_user_id: str, telegram_username: str | None) -> FitnessProfile:
        profile = FitnessProfile()
        profile.profile_id = uuid4().hex
        profile.source.bot_user_id = bot_user_id
        profile.source.telegram_username = telegram_username
        profile.questionnaire.completion_status = CompletionStatus.IN_PROGRESS
        profile.touch()
        return profile

    def first_question_id(self) -> str:
        return "q01_name"

    def get_question(self, question_id: str) -> QuestionDefinition:
        question = QUESTIONS_BY_ID.get(question_id)
        if question is None:
            raise QuestionnaireValidationError("Этот вопрос больше не используется.")
        return question

    def build_prompt(self, profile: FitnessProfile, question_id: str) -> QuestionPrompt:
        question = self.get_question(question_id)
        selected_days = (
            [d.value for d in profile.training_plan_preferences.preferred_days]
            if question.kind is QuestionKind.MULTISELECT
            else []
        )
        return QuestionPrompt(
            question_id=question_id,
            text=f"{question_progress(profile, question_id)}{question.text}\n\n{question.hint}".rstrip(),
            kind=question.kind,
            options=question.options,
            skippable=not question.required,
            selected_days=selected_days,
        )

    def next_question(self, profile: FitnessProfile, current_id: str) -> str | None:
        return next_question_id(profile, current_id)

    # --- Обработка ответов ----------------------------------------------------

    def answer_text(self, profile: FitnessProfile, question_id: str, text: str) -> AnswerResult:
        question = self.get_question(question_id)
        if question.kind not in (QuestionKind.TEXT, QuestionKind.PHOTOS):
            raise QuestionnaireValidationError(
                "Неожиданный тип данных. Введите корректный ответ или выберите кнопку из диалога."
            )
        text = text.strip()
        if text.lower() in SKIP_WORDS:
            if question.required:
                raise QuestionnaireValidationError("Этот вопрос обязателен — его нельзя пропустить.")
            return self._skip(profile, question)

        if question.kind is QuestionKind.PHOTOS:
            # Текст на вопрос с фотографиями принимать нельзя: у поля тип
            # «список файлов», и строка вида «пока нет» записалась бы в него как
            # есть. Присваивание Pydantic не валидирует, поэтому запись прошла
            # бы молча, а падало бы уже следующее чтение профиля — анкета
            # застревала без объяснения.
            #
            # Такой ответ НЕ трактуется как пропуск: «сейчас пришлю» означает
            # обратное, и автоматический переход к следующему вопросу потерял бы
            # присланное потом фото.
            raise QuestionnaireValidationError(
                "Здесь нужна фотография. Отправьте снимок или нажмите «Пропустить», "
                "если фотографий нет."
            )

        if question.validate is not None:
            error = question.validate(text)
            if error:
                raise QuestionnaireValidationError(error)

        value = question.parse(text) if question.parse else text
        self._set_value(question, profile, value)
        return self._advance(profile, question)

    def answer_choice(self, profile: FitnessProfile, question_id: str, callback_data: str) -> AnswerResult:
        question = self.get_question(question_id)
        option = question.option_by_data(callback_data)
        if option is None:
            raise QuestionnaireValidationError("Неизвестный вариант ответа.")
        self._set_value(question, profile, option.value)
        return self._advance(profile, question)

    def toggle_day(self, profile: FitnessProfile, day: str) -> tuple[list[str], str]:
        """Возвращает (обновлённый список дней, текст подтверждения)."""
        selected = [d.value for d in profile.training_plan_preferences.preferred_days]
        if day in selected:
            selected.remove(day)
            action = "удалён"
        else:
            selected.append(day)
            action = "добавлен"
        profile.training_plan_preferences.preferred_days = [Weekday(d) for d in selected]
        return selected, action

    def confirm_days(self, profile: FitnessProfile) -> AnswerResult:
        if not profile.training_plan_preferences.preferred_days:
            raise QuestionnaireValidationError("Выберите хотя бы один день недели.")
        question = self.get_question("q21_preferred_days")
        return self._advance(profile, question, confirmation="✅ Выбраны удобные дни недели")

    def add_photo(self, profile: FitnessProfile, file_id: str, content: bytes, extension: str) -> AnswerResult:
        """Сохраняет фото оборудования через FileStorage и продолжает анкету."""
        question = self.get_question("q19_equipment_photos")
        key = self._file_storage.save_photo(
            profile_id=profile.profile_id or "draft",
            file_id=file_id,
            content=content,
            extension=extension,
        )
        profile.training_location.equipment_photos.append(key)
        return self._advance(
            profile, question, confirmation="✅ Фото сохранено. Можно отправить ещё одно или продолжить анкету."
        )

    def skip(self, profile: FitnessProfile, question_id: str) -> AnswerResult:
        question = self.get_question(question_id)
        if question.required:
            raise QuestionnaireValidationError("Этот вопрос обязателен — его нельзя пропустить.")
        return self._skip(profile, question)

    # --- Review / редактирование ----------------------------------------------

    def active_question_ids(self, profile: FitnessProfile) -> list[str]:
        return [q.id for q in active_questions(profile)]

    def begin_edit(self, profile: FitnessProfile, question_id: str) -> QuestionPrompt:
        question = self.get_question(question_id)
        if not question.is_active(profile):
            raise QuestionnaireValidationError("Этот вопрос не относится к вашей анкете.")
        return self.build_prompt(profile, question_id)

    # --- Внутреннее -------------------------------------------------------------

    @staticmethod
    def _set_value(
        question: QuestionDefinition, profile: FitnessProfile, value: object
    ) -> None:
        """Записывает ответ в профиль, превращая отказ модели в ошибку анкеты.

        Профиль валидирует присваивание (`validate_assignment`), поэтому
        несовместимое значение не попадёт в состояние и не сломает следующее
        чтение анкеты. Пользователю нужен ответ на его сообщение, а не молчание,
        поэтому `ValidationError` переводится в обычную ошибку вопроса: он
        останется на том же шаге и сможет ответить заново.
        """
        try:
            question.set_value(profile, value)
        except ValidationError as exc:
            raise QuestionnaireValidationError(
                "Не удалось сохранить такой ответ. Проверьте формат и попробуйте снова."
            ) from exc

    def _skip(self, profile: FitnessProfile, question: QuestionDefinition) -> AnswerResult:
        if question.id not in profile.questionnaire.skipped_questions:
            profile.questionnaire.skipped_questions.append(question.id)
        return self._advance(profile, question, confirmation="⏭️ Вопрос пропущен")

    def _advance(
        self,
        profile: FitnessProfile,
        question: QuestionDefinition,
        confirmation: str = "✅ Записал: ответ сохранён",
    ) -> AnswerResult:
        profile.questionnaire.last_question_id = question.id
        if question.id in profile.questionnaire.skipped_questions and confirmation != "⏭️ Вопрос пропущен":
            profile.questionnaire.skipped_questions.remove(question.id)
        return AnswerResult(
            profile=profile,
            next_question_id=self.next_question(profile, question.id),
            confirmation=confirmation,
        )
