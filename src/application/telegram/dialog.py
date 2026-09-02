"""Диалог Telegram-анкеты на стороне Backend.

Здесь живёт вся логика, которую раньше выполнял Gateway: где стоит пользователь,
что показать дальше, как отреагировать на кнопку. Gateway после выноса за
сетевую границу присылает «пользователь сделал X» и получает готовое описание
того, что отобразить.

Почему логика переехала целиком, а не разделилась. Состояние диалога неотделимо
от накопленных ответов: чтобы решить, какой вопрос следующий, нужен профиль
(часть вопросов пропускается по предыдущим ответам). Ответы — персональные
данные, включая данные о здоровье, и хранить их в EU нельзя. Значит и решение
«что дальше» принимается там, где лежат ответы.

Сервис не знает про Telegram API: он возвращает `TelegramView` — текст, кнопки и
тип операции. Bot API вызывает Gateway.

Бизнес-правила анкеты не дублируются: используется существующий
`QuestionnaireService` (валидация, переходы, пропуски) и
`ProfileFinalizationService` (идемпотентное сохранение). Этот модуль отвечает
только за состояние диалога и за перевод результата в отображаемый вид.
"""
from __future__ import annotations

import json
import logging

from src.application.profiles.finalization import ProfileFinalizationService
from src.application.questionnaire.questions import (
    QUESTIONS,
    QUESTIONS_BY_ID,
    QuestionKind,
)
from src.application.questionnaire.review import render_review_html
from src.application.questionnaire.service import QuestionnaireService
from src.domain.enums import CompletionStatus
from src.domain.profile import FitnessProfile
from src.domain.telegram_contract import (
    TelegramButton,
    TelegramDocument,
    TelegramMessage,
    TelegramMessageKind,
    TelegramUpdateKind,
    TelegramUpdateRequest,
    TelegramUpdateResponse,
    TelegramView,
)
from src.errors import ProfilePersistenceError, QuestionnaireValidationError
from src.infrastructure.persistence.profile_repository import ProfileRepository
from src.infrastructure.telegram.admin_summary import build_admin_summary
from src.infrastructure.persistence.postgres.telegram_session_repository import (
    TelegramSession,
    TelegramSessionRepository,
)

logger = logging.getLogger(__name__)

# Служебные экраны. Не вопросы анкеты, поэтому хранятся теми же значениями
# `position`, но в `QUESTIONS_BY_ID` их нет.
POSITION_REVIEW = "review"
POSITION_CONFIRM = "confirm"

# Действия кнопок, не относящиеся к вариантам ответа. Совпадают с прежними
# `callback_data`: у пользователя в чате остаются старые сообщения с кнопками, и
# переименование сделало бы их мёртвыми.
ACTION_START = "start_qa"
ACTION_RESUME = "resume_qa"
ACTION_RESTART = "restart_qa"
ACTION_SERVICE_INFO = "show_service_info"
ACTION_SKIP = "skip_question"
ACTION_DAYS_DONE = "days_done"
ACTION_REVIEW_CONFIRM = "review_confirm"
ACTION_REVIEW_EDIT = "review_edit"
ACTION_FINAL_CONFIRM = "final_confirm"
ACTION_RETURN_TO_QUESTIONNAIRE = "return_to_questionnaire"
PREFIX_DAY = "day_"
PREFIX_EDIT_SECTION = "edit_section_"
PREFIX_EDIT_QUESTION = "edit_question_"

DAY_LABELS = {
    "mon": "Пн",
    "tue": "Вт",
    "wed": "Ср",
    "thu": "Чт",
    "fri": "Пт",
    "sat": "Сб",
    "sun": "Вс",
}

SECTION_LABELS = {
    "client": "👤 О себе",
    "goals": "🎯 Цели",
    "training_background": "🏋️ Опыт тренировок",
    "training_location": "📍 Место",
    "training_plan_preferences": "📅 График",
    "health_and_limitations": "⚕️ Здоровье",
    "exercise_preferences": "💪 Предпочтения",
    "lifestyle": "🏃 Образ жизни",
    "additional_information": "📝 Дополнительно",
}

SERVICE_INFO_TEXT = (
    "ℹ️ Услуга включает сбор анкеты, подбор целей, ограничений и режима тренировок, "
    "а затем формирование структурированного профиля клиента для дальнейшей генерации программы.\n\n"
    "Если готовы — нажмите кнопку ниже."
)

START_TEXT = (
    "🏋️ Индивидуальная программа тренировок\n\n"
    "Я помогу собрать информацию для составления персональной программы.\n"
    "Анкета займёт 5–10 минут.\n\n"
    "На основе ваших ответов будет подготовлена программа тренировок в виде удобного файла, "
    "который открывается на смартфоне и компьютере.\n\n"
    "⚠️ Не все вопросы анкеты обязательны для заполнения, но чем полнее будет заполнена анкета, "
    "тем точнее будет подобрана программа.\n\n"
    "Вопросы, отмеченные звёздочкой (*), обязательны. Необязательные можно пропустить.\n\n"
    "Готовы начать?"
)

RESUME_TEXT = "У вас есть незавершённая анкета. Продолжить заполнение или начать заново?"
CANCEL_TEXT = "❌ Анкета сброшена. Чтобы начать заново, отправьте /start."
CONFIRM_TEXT = (
    "Подтвердите отправку анкеты. После подтверждения данные будут переданы "
    "тренеру для составления программы."
)
GENERATION_STARTED_TEXT = "⏳ Формируем вашу персональную программу..."
UNKNOWN_ACTION_TEXT = "Это действие больше не доступно. Отправьте /start."
PHOTO_WRONG_STEP_TEXT = "Сейчас фотография не требуется. Ответьте на текущий вопрос."


# --- Клавиатуры ----------------------------------------------------------------
#
# Кнопки строятся здесь, а не в Gateway: их состав зависит от анкеты
# (варианты ответа, обязательность вопроса, выбранные дни). Оставить их в EU
# значило бы держать там знание о структуре анкеты.


def _rows(*buttons: TelegramButton) -> list[list[TelegramButton]]:
    """По одной кнопке в строке — так же, как было в Gateway."""
    return [[button] for button in buttons]


def _start_buttons() -> list[list[TelegramButton]]:
    return _rows(
        TelegramButton(label="▶ Начать анкету", action=ACTION_START),
        TelegramButton(label="ℹ️ Подробнее об услуге", action=ACTION_SERVICE_INFO),
    )


def _resume_buttons() -> list[list[TelegramButton]]:
    return _rows(
        TelegramButton(label="▶ Продолжить анкету", action=ACTION_RESUME),
        TelegramButton(label="🆕 Начать заново", action=ACTION_RESTART),
    )


def _review_buttons() -> list[list[TelegramButton]]:
    return _rows(
        TelegramButton(label="✅ Всё верно", action=ACTION_REVIEW_CONFIRM),
        TelegramButton(label="✏️ Исправить", action=ACTION_REVIEW_EDIT),
    )


def _confirm_buttons() -> list[list[TelegramButton]]:
    return _rows(
        TelegramButton(label="✅ Подтвердить", action=ACTION_FINAL_CONFIRM),
        TelegramButton(
            label="↩️ Вернуться к анкете", action=ACTION_RETURN_TO_QUESTIONNAIRE
        ),
    )


def _question_buttons(question_id: str, skippable: bool) -> list[list[TelegramButton]]:
    question = QUESTIONS_BY_ID[question_id]
    buttons = [
        TelegramButton(label=option.label, action=option.callback_data)
        for option in question.options
    ]
    if skippable:
        buttons.append(TelegramButton(label="⏭️ Пропустить", action=ACTION_SKIP))
    return _rows(*buttons)


def _days_buttons(selected: list[str]) -> list[list[TelegramButton]]:
    chosen = set(selected)
    buttons = [
        TelegramButton(
            label=f"✅ {label}" if value in chosen else label,
            action=f"{PREFIX_DAY}{value}",
        )
        for value, label in DAY_LABELS.items()
    ]
    buttons.append(TelegramButton(label="Готово", action=ACTION_DAYS_DONE))
    return _rows(*buttons)


def _edit_sections_buttons() -> list[list[TelegramButton]]:
    sections: list[str] = []
    for question in QUESTIONS:
        if question.section not in sections:
            sections.append(question.section)
    return _rows(
        *(
            TelegramButton(
                label=SECTION_LABELS.get(section, section),
                action=f"{PREFIX_EDIT_SECTION}{section}",
            )
            for section in sections
        )
    )


def _edit_questions_buttons(section: str) -> list[list[TelegramButton]]:
    buttons = [
        TelegramButton(
            label=question.text.replace(" *", "")[:64],
            action=f"{PREFIX_EDIT_QUESTION}{question.id}",
        )
        for question in QUESTIONS
        if question.section == section
    ]
    buttons.append(TelegramButton(label="↩️ К разделам", action=ACTION_REVIEW_EDIT))
    return _rows(*buttons)


class TelegramDialogService:
    """Обработка одного события Telegram: от FSM до отображаемого вида.

    Сервис не создаёт генерацию и не отправляет сообщения — только меняет
    состояние диалога и описывает результат. Автогенерация после финализации
    запускается вызывающим слоем через оркестратор, потому что она не относится
    к диалогу и не должна задерживать ответ пользователю.
    """

    def __init__(
        self,
        *,
        sessions: TelegramSessionRepository,
        questionnaire: QuestionnaireService,
        finalization: ProfileFinalizationService,
        profiles: ProfileRepository | None = None,
        admin_chat_id: str | None = None,
    ) -> None:
        self._sessions = sessions
        self._questionnaire = questionnaire
        self._finalization = finalization
        self._profiles = profiles
        # Уведомление администратору о новой анкете уходит тем же путём, что и
        # ответ пользователю: Backend в RU не имеет доступа к Bot API, а
        # отправка сообщения в другой чат для Gateway — та же операция.
        self._admin_chat_id = admin_chat_id

    async def handle(self, request: TelegramUpdateRequest) -> TelegramUpdateResponse:
        """Единственная точка входа. Идемпотентна по `update_id`.

        Повтор возвращает сохранённый ответ, а не пересчитывает шаг: Telegram
        переотправляет неподтверждённое обновление, и второй расчёт продвинул бы
        анкету на два вопроса от одного ответа пользователя.
        """
        session = await self._sessions.get(request.telegram_user_id) or TelegramSession(
            telegram_user_id=request.telegram_user_id
        )

        if (
            session.last_update_id is not None
            and request.update_id == session.last_update_id
            and session.last_view is not None
        ):
            logger.info(
                "event=telegram_update_duplicate",
                extra={"update_id": request.update_id},
            )
            return TelegramUpdateResponse(
                view=TelegramView.model_validate(session.last_view),
                profile_id=session.profile_id,
                duplicate=True,
            )

        session.chat_id = request.chat_id
        session.username = request.username or session.username

        response = await self._dispatch(request, session)

        session.last_update_id = request.update_id
        session.last_view = response.view.model_dump(mode="json")
        await self._sessions.save(session)
        return response

    async def handle_photo(
        self,
        *,
        update_id: int,
        telegram_user_id: str,
        chat_id: str,
        file_id: str,
        content: bytes,
        extension: str,
    ) -> TelegramUpdateResponse:
        """Приём фотографии оборудования.

        Байты приходят от Gateway, а не скачиваются здесь: `api.telegram.org`
        доступен только из EU-сегмента. Записывает их Backend, поэтому на диске
        EU файл не появляется.
        """
        session = await self._sessions.get(telegram_user_id) or TelegramSession(
            telegram_user_id=telegram_user_id
        )
        if (
            session.last_update_id is not None
            and update_id == session.last_update_id
            and session.last_view is not None
        ):
            return TelegramUpdateResponse(
                view=TelegramView.model_validate(session.last_view),
                profile_id=session.profile_id,
                duplicate=True,
            )

        session.chat_id = chat_id
        profile = self._load_draft(session)
        if profile is None or session.position != "q19_equipment_photos":
            # Фото не на своём шаге: молча проглатывать нельзя — пользователь
            # ждёт реакции на отправленный файл.
            view = TelegramView(messages=[TelegramMessage(text=PHOTO_WRONG_STEP_TEXT)])
            return TelegramUpdateResponse(view=view, profile_id=session.profile_id)

        try:
            result = self._questionnaire.add_photo(profile, file_id, content, extension)
        except QuestionnaireValidationError as exc:
            view = TelegramView(messages=[TelegramMessage(text=exc.user_message)])
            return TelegramUpdateResponse(view=view, profile_id=session.profile_id)

        response = self._advance(session, result.profile, result.next_question_id, result.confirmation)
        session.last_update_id = update_id
        session.last_view = response.view.model_dump(mode="json")
        await self._sessions.save(session)
        return response

    # --- Маршрутизация события --------------------------------------------------

    async def _dispatch(
        self, request: TelegramUpdateRequest, session: TelegramSession
    ) -> TelegramUpdateResponse:
        if request.kind is TelegramUpdateKind.COMMAND:
            return await self._handle_command(request, session)
        if request.kind is TelegramUpdateKind.CALLBACK:
            return await self._handle_callback(request, session)
        if request.kind is TelegramUpdateKind.TEXT:
            return self._handle_text(request, session)
        # PHOTO обрабатывается отдельным методом: у него другое тело запроса.
        return TelegramUpdateResponse(
            view=TelegramView(messages=[TelegramMessage(text=UNKNOWN_ACTION_TEXT)]),
            profile_id=session.profile_id,
        )

    async def _handle_command(
        self, request: TelegramUpdateRequest, session: TelegramSession
    ) -> TelegramUpdateResponse:
        command = request.payload.split()[0].lower() if request.payload else ""
        if command == "/cancel":
            await self._sessions.delete(request.telegram_user_id)
            # Сессия удалена: дальше сохранять нечего, поэтому возвращаем
            # состояние с обнулённым ключом идемпотентности.
            session.draft = None
            session.position = None
            session.editing_question = None
            session.profile_id = None
            return TelegramUpdateResponse(
                view=TelegramView(messages=[TelegramMessage(text=CANCEL_TEXT)])
            )

        # /start и всё остальное: незавершённая анкета предлагается к продолжению.
        profile = self._load_draft(session)
        if (
            profile is not None
            and session.position
            and profile.questionnaire.completion_status
            is not CompletionStatus.CONFIRMED
        ):
            return TelegramUpdateResponse(
                view=TelegramView(
                    messages=[
                        TelegramMessage(text=RESUME_TEXT, buttons=_resume_buttons())
                    ]
                ),
                profile_id=session.profile_id,
            )
        return TelegramUpdateResponse(
            view=TelegramView(
                messages=[TelegramMessage(text=START_TEXT, buttons=_start_buttons())]
            ),
            profile_id=session.profile_id,
        )

    def _handle_text(
        self, request: TelegramUpdateRequest, session: TelegramSession
    ) -> TelegramUpdateResponse:
        # Текстовые кнопки старой раскладки: у пользователей они остались в чате.
        if request.payload == "▶ Начать анкету":
            return self._begin(request, session)
        if request.payload == "ℹ️ Подробнее об услуге":
            return TelegramUpdateResponse(
                view=TelegramView(
                    messages=[
                        TelegramMessage(
                            text=SERVICE_INFO_TEXT, buttons=_start_buttons()
                        )
                    ]
                ),
                profile_id=session.profile_id,
            )

        profile = self._load_draft(session)
        question_id = self._current_question(session)
        if profile is None or question_id is None:
            # Анкета не начата или диалог стоит на служебном экране: текст здесь
            # не ожидается, и записывать его некуда.
            return TelegramUpdateResponse(
                view=TelegramView(
                    messages=[
                        TelegramMessage(text=START_TEXT, buttons=_start_buttons())
                    ]
                ),
                profile_id=session.profile_id,
            )

        try:
            result = self._questionnaire.answer_text(profile, question_id, request.payload)
        except QuestionnaireValidationError as exc:
            return TelegramUpdateResponse(
                view=TelegramView(messages=[TelegramMessage(text=exc.user_message)]),
                profile_id=session.profile_id,
            )
        return self._advance(
            session, result.profile, result.next_question_id, result.confirmation
        )

    async def _handle_callback(
        self, request: TelegramUpdateRequest, session: TelegramSession
    ) -> TelegramUpdateResponse:
        action = request.payload

        if action == ACTION_SERVICE_INFO:
            return TelegramUpdateResponse(
                view=TelegramView(
                    messages=[
                        TelegramMessage(
                            kind=TelegramMessageKind.EDIT,
                            text=SERVICE_INFO_TEXT,
                            buttons=_start_buttons(),
                        )
                    ]
                ),
                profile_id=session.profile_id,
            )

        if action in (ACTION_START, ACTION_RESTART):
            if action == ACTION_RESTART:
                # Явное «начать заново»: прежний черновик отбрасывается целиком,
                # иначе ответы двух анкет смешались бы.
                session.draft = None
                session.position = None
                session.editing_question = None
                session.profile_id = None
            return self._begin(request, session, replace_current=True)

        if action == ACTION_RESUME:
            profile = self._load_draft(session)
            if profile is None:
                return TelegramUpdateResponse(
                    view=TelegramView(
                        messages=[
                            TelegramMessage(text=START_TEXT, buttons=_start_buttons())
                        ],
                        toast="Анкета не найдена. Отправьте /start.",
                        toast_alert=True,
                    )
                )
            question_id = self._current_question(session) or (
                profile.questionnaire.last_question_id
                or self._questionnaire.first_question_id()
            )
            session.position = question_id
            view = self._question_view(
                profile, question_id, replace_current=True, toast="Продолжаем анкету"
            )
            return TelegramUpdateResponse(view=view, profile_id=session.profile_id)

        profile = self._load_draft(session)
        if profile is None:
            return TelegramUpdateResponse(
                view=TelegramView(
                    messages=[
                        TelegramMessage(text=START_TEXT, buttons=_start_buttons())
                    ],
                    toast=UNKNOWN_ACTION_TEXT,
                    toast_alert=True,
                )
            )

        if action == ACTION_SKIP:
            question_id = self._current_question(session)
            if question_id is None:
                return self._stale_action(session)
            try:
                result = self._questionnaire.skip(profile, question_id)
            except QuestionnaireValidationError as exc:
                return self._toast(session, exc.user_message)
            return self._advance(
                session,
                result.profile,
                result.next_question_id,
                result.confirmation,
                replace_text="⏭️ Вопрос пропущен",
            )

        if action.startswith(PREFIX_DAY):
            selected, label = self._questionnaire.toggle_day(
                profile, action.removeprefix(PREFIX_DAY)
            )
            session.draft = profile.model_dump(mode="json")
            day = action.removeprefix(PREFIX_DAY)
            # Меняется только клавиатура: перерисовывать текст вопроса при каждом
            # переключении дня — лишнее мигание в чате.
            return TelegramUpdateResponse(
                view=TelegramView(
                    messages=[
                        TelegramMessage(
                            kind=TelegramMessageKind.EDIT,
                            text="",
                            buttons=_days_buttons(selected),
                        )
                    ],
                    toast=f"День {DAY_LABELS.get(day, day)} {label}",
                ),
                profile_id=session.profile_id,
            )

        if action == ACTION_DAYS_DONE:
            try:
                result = self._questionnaire.confirm_days(profile)
            except QuestionnaireValidationError as exc:
                return self._toast(session, exc.user_message)
            return self._advance(
                session,
                result.profile,
                result.next_question_id,
                result.confirmation,
                replace_text=result.confirmation,
                toast="Дни недели сохранены",
            )

        if action == ACTION_REVIEW_CONFIRM:
            session.position = POSITION_CONFIRM
            session.draft = profile.model_dump(mode="json")
            return TelegramUpdateResponse(
                view=TelegramView(
                    messages=[
                        TelegramMessage(
                            kind=TelegramMessageKind.EDIT,
                            text=CONFIRM_TEXT,
                            buttons=_confirm_buttons(),
                        )
                    ]
                ),
                profile_id=session.profile_id,
            )

        if action == ACTION_RETURN_TO_QUESTIONNAIRE:
            session.position = POSITION_REVIEW
            return TelegramUpdateResponse(
                view=self._review_view(profile), profile_id=session.profile_id
            )

        if action == ACTION_REVIEW_EDIT:
            return TelegramUpdateResponse(
                view=TelegramView(
                    messages=[
                        TelegramMessage(
                            kind=TelegramMessageKind.EDIT,
                            text="Выберите раздел, который хотите исправить:",
                            buttons=_edit_sections_buttons(),
                        )
                    ]
                ),
                profile_id=session.profile_id,
            )

        if action.startswith(PREFIX_EDIT_SECTION):
            section = action.removeprefix(PREFIX_EDIT_SECTION)
            return TelegramUpdateResponse(
                view=TelegramView(
                    messages=[
                        TelegramMessage(
                            kind=TelegramMessageKind.EDIT,
                            text="Выберите вопрос:",
                            buttons=_edit_questions_buttons(section),
                        )
                    ]
                ),
                profile_id=session.profile_id,
            )

        if action.startswith(PREFIX_EDIT_QUESTION):
            target = action.removeprefix(PREFIX_EDIT_QUESTION)
            try:
                self._questionnaire.begin_edit(profile, target)
            except QuestionnaireValidationError as exc:
                return self._toast(session, exc.user_message)
            session.editing_question = target
            session.position = target
            messages = [
                TelegramMessage(kind=TelegramMessageKind.EDIT, text="Исправьте ответ:")
            ]
            messages.extend(self._question_view(profile, target).messages)
            return TelegramUpdateResponse(
                view=TelegramView(messages=messages), profile_id=session.profile_id
            )

        if action == ACTION_FINAL_CONFIRM:
            return await self._finalize(session, profile)

        # Вариант ответа на вопрос. Проверяется последним: до этого разобраны все
        # служебные действия, и совпадение по имени исключено.
        question_id = self._question_by_option(action)
        if question_id is None:
            return self._stale_action(session)
        try:
            result = self._questionnaire.answer_choice(profile, question_id, action)
        except QuestionnaireValidationError as exc:
            return self._toast(session, exc.user_message)
        return self._advance(
            session,
            result.profile,
            result.next_question_id,
            result.confirmation,
            replace_text=result.confirmation,
        )

    # --- Переходы и рендер ------------------------------------------------------

    def _begin(
        self,
        request: TelegramUpdateRequest,
        session: TelegramSession,
        *,
        replace_current: bool = False,
    ) -> TelegramUpdateResponse:
        profile = self._questionnaire.start_profile(
            request.telegram_user_id, request.username
        )
        first = self._questionnaire.first_question_id()
        session.draft = profile.model_dump(mode="json")
        session.position = first
        session.editing_question = None
        session.profile_id = None
        view = self._question_view(
            profile, first, replace_current=replace_current, toast="Начинаем анкетирование"
        )
        return TelegramUpdateResponse(view=view)

    def _advance(
        self,
        session: TelegramSession,
        profile: FitnessProfile,
        next_question_id: str | None,
        confirmation: str,
        *,
        replace_text: str | None = None,
        toast: str | None = None,
    ) -> TelegramUpdateResponse:
        """Сохраняет черновик и переходит к следующему шагу.

        Правка из сводки возвращает в сводку, а не к следующему по порядку
        вопросу: иначе пользователь, исправивший один ответ, проходил бы анкету
        заново с этого места.
        """
        session.draft = profile.model_dump(mode="json")

        messages: list[TelegramMessage] = []
        if replace_text is not None:
            messages.append(
                TelegramMessage(kind=TelegramMessageKind.EDIT, text=replace_text)
            )

        if session.editing_question:
            session.editing_question = None
            session.position = POSITION_REVIEW
            messages.extend(self._review_view(profile).messages)
            return TelegramUpdateResponse(
                view=TelegramView(messages=messages, toast=toast),
                profile_id=session.profile_id,
            )

        if next_question_id is None:
            session.position = POSITION_REVIEW
            messages.extend(self._review_view(profile).messages)
            return TelegramUpdateResponse(
                view=TelegramView(messages=messages, toast=toast),
                profile_id=session.profile_id,
            )

        session.position = next_question_id
        if replace_text is None:
            messages.append(TelegramMessage(text=confirmation))
        messages.extend(self._question_view(profile, next_question_id).messages)
        return TelegramUpdateResponse(
            view=TelegramView(messages=messages, toast=toast),
            profile_id=session.profile_id,
        )

    def _question_view(
        self,
        profile: FitnessProfile,
        question_id: str,
        *,
        replace_current: bool = False,
        toast: str | None = None,
    ) -> TelegramView:
        prompt = self._questionnaire.build_prompt(profile, question_id)
        buttons = (
            _days_buttons(prompt.selected_days)
            if prompt.kind is QuestionKind.MULTISELECT
            else _question_buttons(question_id, prompt.skippable)
        )
        return TelegramView(
            messages=[
                TelegramMessage(
                    text=prompt.text,
                    buttons=buttons,
                    delete_current=replace_current,
                )
            ],
            toast=toast,
        )

    def _review_view(self, profile: FitnessProfile) -> TelegramView:
        return TelegramView(
            messages=[
                TelegramMessage(
                    text=render_review_html(profile), html=True, buttons=_review_buttons()
                )
            ]
        )

    async def _finalize(
        self, session: TelegramSession, profile: FitnessProfile
    ) -> TelegramUpdateResponse:
        """Подтверждение анкеты: сохранение профиля и уведомление администратора.

        Генерацию здесь не запускаем: она выполняется вызывающим слоем через
        оркестратор. Иначе ответ пользователю ждал бы AI-вызов длиной в минуты.
        """
        try:
            result = await self._finalization.finalize(profile)
        except ProfilePersistenceError:
            logger.exception(
                "event=telegram_finalize_failed",
                extra={"telegram_user_id": session.telegram_user_id},
            )
            return self._toast(
                session, "Не удалось сохранить анкету. Попробуйте ещё раз чуть позже."
            )

        session.draft = result.profile.model_dump(mode="json")
        session.profile_id = result.profile.profile_id
        session.position = POSITION_CONFIRM

        number = result.profile.display_number or result.profile.profile_id or "—"
        messages = [
            TelegramMessage(
                kind=TelegramMessageKind.EDIT,
                text=f"✅ Спасибо! Ваша анкета принята. Номер: {number}",
            )
        ]

        # Уведомление администратору — только при первой финализации: повторное
        # подтверждение не должно присылать вторую копию заявки.
        if not result.already_finalized and self._admin_chat_id:
            messages.extend(self._admin_messages(result.profile))
            await self._mark_admin_notified(result.profile)
            session.draft = result.profile.model_dump(mode="json")

        # Сообщение о начале генерации отдаётся сразу, вместе с подтверждением:
        # пользователь не должен ждать в тишине, пока запустится оркестратор.
        if not result.already_finalized:
            messages.append(TelegramMessage(text=GENERATION_STARTED_TEXT))
        return TelegramUpdateResponse(
            view=TelegramView(messages=messages, toast="Анкета сохранена"),
            finished=True,
            profile_id=result.profile.profile_id,
        )

    def _admin_messages(self, profile: FitnessProfile) -> list[TelegramMessage]:
        """Сводка заявки и JSON-профиль администратору.

        Формат тот же, что был до переноса: текст со сводкой плюс приложенный
        JSON. Отличие только в транспорте — сообщения уходят Gateway вместе с
        ответом пользователю, а не отправляются из этого процесса.
        """
        number = profile.display_number or profile.profile_id or "profile"
        payload = json.dumps(
            profile.model_dump(mode="json"), ensure_ascii=False, indent=2
        )
        return [
            TelegramMessage(
                text=build_admin_summary(profile), chat_id=self._admin_chat_id
            ),
            TelegramMessage(
                text="",
                chat_id=self._admin_chat_id,
                document=TelegramDocument(
                    filename=f"{number}.json",
                    text_content=payload,
                    caption=f"JSON-профиль заявки {number}",
                ),
            ),
        ]

    async def _mark_admin_notified(self, profile: FitnessProfile) -> None:
        """Отмечает уведомление отправленным.

        Статус ставится оптимистично: фактическую отправку выполняет Gateway, и
        отчёта об этом сообщении контракт не предусматривает — заводить его ради
        одного статуса значит добавить седьмую операцию к контракту из шести.
        Цена ошибки низкая: администратор увидит заявку в админке и без
        уведомления, а «sent» здесь означает «передано шлюзу».
        """
        profile.admin_notification_status = "sent"
        if self._profiles is None:
            return
        try:
            await self._profiles.save(profile)
        except Exception:  # noqa: BLE001 — статус не должен ронять сценарий
            logger.warning(
                "event=admin_notification_status_persist_failed",
                extra={"profile_id": profile.profile_id},
            )

    # --- Вспомогательное --------------------------------------------------------

    def _load_draft(self, session: TelegramSession) -> FitnessProfile | None:
        """Черновик из сессии в доменный объект.

        Несовместимый черновик (например, собранный до изменения схемы анкеты)
        трактуется как отсутствие анкеты: падать на чтении нельзя, пользователю
        нужен ответ, а не молчание.
        """
        if session.draft is None:
            return None
        try:
            return FitnessProfile.model_validate(session.draft)
        except Exception:  # noqa: BLE001 — любая несовместимость схемы
            logger.warning(
                "event=telegram_draft_incompatible",
                extra={"telegram_user_id": session.telegram_user_id},
            )
            return None

    @staticmethod
    def _current_question(session: TelegramSession) -> str | None:
        if session.position in (None, POSITION_REVIEW, POSITION_CONFIRM):
            return None
        return session.position if session.position in QUESTIONS_BY_ID else None

    @staticmethod
    def _question_by_option(action: str) -> str | None:
        for question in QUESTIONS:
            if question.option_by_data(action) is not None:
                return question.id
        return None

    def _toast(self, session: TelegramSession, text: str) -> TelegramUpdateResponse:
        return TelegramUpdateResponse(
            view=TelegramView(toast=text, toast_alert=True),
            profile_id=session.profile_id,
        )

    def _stale_action(self, session: TelegramSession) -> TelegramUpdateResponse:
        """Нажатие на кнопку сообщения, которое больше не актуально.

        Такое бывает всегда: старые сообщения с кнопками остаются в чате. Молча
        игнорировать нельзя — Telegram оставит кнопку с индикатором ожидания.
        """
        return self._toast(session, UNKNOWN_ACTION_TEXT)
