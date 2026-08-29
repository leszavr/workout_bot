"""Имитация пользователя, проходящего анкету в Telegram.

Уровень имитации — `Dispatcher.feed_update`: синтетические `Update` подаются в
настоящий диспетчер с настоящими хендлерами, FSM в Redis и реальными переходами
состояний. Прямой вызов `QuestionnaireService` был бы проще, но обошёл бы
хендлеры, клавиатуры и `callback_data` — то есть весь транспортный слой, где и
живут ошибки этого рода.

Что имитатор НЕ проверяет: сеть до api.telegram.org (сессия подменена) и
поведение клиента Telegram. Всё остальное — реальный код бота.

Кнопки не угадываются: `callback_data` берётся из клавиатуры, которую бот
показал в ответе. Если разработчик переименует вариант ответа, имитатор
остановится с явной ошибкой, а не молча пройдёт анкету по-другому.
"""
from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field

from aiogram import Bot, Dispatcher
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from scripts.qa_harness.fake_telegram import FakeTelegramSession
from src.application.questionnaire.questions import QUESTIONS_BY_ID, QuestionKind

logger = logging.getLogger(__name__)

# Токен фиктивный: сессия перехвачена, к Telegram обращений нет.
FAKE_TOKEN = "42:QA-HARNESS-TOKEN-NOT-A-SECRET"


@dataclass
class ScriptedUser:
    """Ответы одного «пользователя» на вопросы анкеты.

    Ключ — id вопроса. Для `text` — строка ответа, для `choice` —
    `callback_data` варианта, для `multiselect` — список кодов дней.
    Вопрос без ответа пропускается, если он необязательный; обязательный без
    ответа — ошибка сценария, а не повод подставить заглушку.
    """

    name: str
    telegram_user_id: int
    answers: dict[str, object]
    # Ожидания к программе: их проверяет отдельный контролёр качества.
    expectations: dict[str, object] = field(default_factory=dict)


@dataclass
class QuestionnaireRun:
    """Результат прохождения анкеты одним пользователем."""

    user: ScriptedUser
    profile_id: str | None
    display_number: str | None
    answered: list[str]
    skipped: list[str]
    finalized: bool
    program_delivered: bool
    messages: list[str]


class TelegramUserSimulator:
    """Проходит анкету за пользователя через Dispatcher."""

    def __init__(
        self,
        dispatcher: Dispatcher,
        session: FakeTelegramSession,
        *,
        max_steps: int = 200,
    ) -> None:
        self._dispatcher = dispatcher
        self._session = session
        # Ограничение на число шагов: без него ошибка в сценарии («бот ждёт ответ,
        # которого имитатор не даёт») превращается в бесконечный цикл.
        self._max_steps = max_steps

    async def run(self, user: ScriptedUser) -> QuestionnaireRun:
        bot = Bot(token=FAKE_TOKEN, session=self._session)
        self._session.clear()
        answered: list[str] = []
        skipped: list[str] = []
        try:
            await self._send_text(bot, user, "/start")
            await self._press(bot, user, "start_qa")

            for _ in range(self._max_steps):
                question_id = await self._current_question(bot, user)
                if question_id is None:
                    break
                await self._answer(bot, user, question_id, answered, skipped)

            state = await self._state_name(bot, user)
            if state and state.endswith("review"):
                await self._press(bot, user, "review_confirm")
                await self._press(bot, user, "final_confirm")

            profile = await self._profile(bot, user)
            return QuestionnaireRun(
                user=user,
                profile_id=profile.get("profile_id") if profile else None,
                display_number=profile.get("display_number") if profile else None,
                answered=answered,
                skipped=skipped,
                finalized=bool(profile)
                and profile.get("questionnaire", {}).get("completion_status")
                == "confirmed",
                program_delivered=bool(self._session.documents()),
                messages=self._session.texts(),
            )
        finally:
            await bot.session.close()

    # --- Шаги -------------------------------------------------------------------

    async def _answer(
        self,
        bot: Bot,
        user: ScriptedUser,
        question_id: str,
        answered: list[str],
        skipped: list[str],
    ) -> None:
        question = QUESTIONS_BY_ID[question_id]
        value = user.answers.get(question_id)

        if value is None:
            if question.required:
                raise AssertionError(
                    f"{user.name}: на обязательный вопрос {question_id} нет ответа в сценарии"
                )
            if not self._session.find_button("skip_question"):
                raise AssertionError(
                    f"{user.name}: вопрос {question_id} нельзя пропустить, "
                    "но ответа в сценарии нет"
                )
            await self._press(bot, user, "skip_question")
            skipped.append(question_id)
            return

        if question.kind is QuestionKind.CHOICE:
            if not self._session.find_button(str(value)):
                raise AssertionError(
                    f"{user.name}: бот не показал вариант {value!r} для {question_id}"
                )
            await self._press(bot, user, str(value))
        elif question.kind is QuestionKind.MULTISELECT:
            for day in value:  # type: ignore[union-attr]
                await self._press(bot, user, f"day_{day}")
            await self._press(bot, user, "days_done")
        elif question.kind is QuestionKind.PHOTOS:
            # Фото не отправляем: загрузка файла требует реального Telegram, а
            # на состав программы фотографии оборудования не влияют.
            await self._press(bot, user, "skip_question")
            skipped.append(question_id)
            return
        else:
            await self._send_text(bot, user, str(value))

        answered.append(question_id)

    # --- Транспорт --------------------------------------------------------------

    async def _send_text(self, bot: Bot, user: ScriptedUser, text: str) -> None:
        update = Update(
            update_id=self._next_update_id(),
            message=Message(
                message_id=self._next_update_id(),
                date=dt.datetime.now(dt.timezone.utc),
                chat=Chat(id=user.telegram_user_id, type="private"),
                from_user=self._user(user),
                text=text,
            ),
        )
        await self._dispatcher.feed_update(bot, update)

    async def _press(self, bot: Bot, user: ScriptedUser, callback_data: str) -> None:
        update = Update(
            update_id=self._next_update_id(),
            callback_query=CallbackQuery(
                id=str(self._next_update_id()),
                from_user=self._user(user),
                chat_instance="qa-harness",
                data=callback_data,
                message=Message(
                    message_id=self._next_update_id(),
                    date=dt.datetime.now(dt.timezone.utc),
                    chat=Chat(id=user.telegram_user_id, type="private"),
                    from_user=self._user(user),
                    text=self._session.last.text if self._session.sent else "",
                ),
            ),
        )
        await self._dispatcher.feed_update(bot, update)

    @staticmethod
    def _user(user: ScriptedUser) -> User:
        return User(
            id=user.telegram_user_id,
            is_bot=False,
            first_name=user.name,
            username=f"qa_user_{user.telegram_user_id}",
        )

    _counter = 0

    @classmethod
    def _next_update_id(cls) -> int:
        cls._counter += 1
        return cls._counter

    # --- Состояние FSM ------------------------------------------------------------

    async def _fsm_context(self, bot: Bot, user: ScriptedUser):
        from aiogram.fsm.context import FSMContext
        from aiogram.fsm.storage.base import StorageKey

        key = StorageKey(
            bot_id=bot.id, chat_id=user.telegram_user_id, user_id=user.telegram_user_id
        )
        return FSMContext(storage=self._dispatcher.fsm.storage, key=key)

    async def _state_name(self, bot: Bot, user: ScriptedUser) -> str | None:
        context = await self._fsm_context(bot, user)
        state = await context.get_state()
        return str(state) if state else None

    async def _current_question(self, bot: Bot, user: ScriptedUser) -> str | None:
        """Какой вопрос сейчас ждёт ответа. None — анкета дошла до review.

        Состояние читается из FSM, а не угадывается по тексту сообщения: текст
        вопроса может измениться, а идентификатор состояния — часть контракта.
        """
        from apps.telegram_gateway.handlers.common import state_to_question_id

        return state_to_question_id(await self._state_name(bot, user))

    async def _profile(self, bot: Bot, user: ScriptedUser) -> dict | None:
        context = await self._fsm_context(bot, user)
        data = await context.get_data()
        raw = data.get("profile")
        return raw if isinstance(raw, dict) else None
