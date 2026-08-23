"""Handler /start и входа в анкету. Только транспорт."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from apps.telegram_gateway.handlers.common import (
    get_services,
    load_profile,
    show_question,
    state_to_question_id,
    store_profile,
)
from apps.telegram_gateway.keyboards.inline import resume_qa_kb, start_qa_kb
from apps.telegram_gateway.states.questionnaire_states import QuestionnaireStates
from src.domain.enums import CompletionStatus

logger = logging.getLogger(__name__)
router = Router(name="telegram_gateway.start")

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


async def _begin_questionnaire(
    target: Message, state: FSMContext, user_id: int, username: str | None
) -> None:
    service = get_services().questionnaire
    profile = service.start_profile(str(user_id), username)
    await store_profile(state, profile)
    first = service.first_question_id()
    await state.set_state(getattr(QuestionnaireStates, first))
    await show_question(target, service, profile, first)


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    current_state = await state.get_state()
    profile = await load_profile(state)
    if (
        current_state
        and profile is not None
        and profile.questionnaire.completion_status is not CompletionStatus.CONFIRMED
    ):
        await message.answer(
            "У вас есть незавершённая анкета. Продолжить заполнение или начать заново?",
            reply_markup=resume_qa_kb(),
        )
        return
    await state.clear()
    await message.answer(START_TEXT, reply_markup=start_qa_kb())


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("❌ Анкета сброшена. Чтобы начать заново, отправьте /start.")
    logger.info(
        "event=questionnaire_cancelled user_id=%s", message.from_user.id if message.from_user else "?"
    )


@router.callback_query(F.data == "start_qa")
async def callback_start_qa(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.message.delete()
    await _begin_questionnaire(callback.message, state, callback.from_user.id, callback.from_user.username)
    await callback.answer("Начинаем анкетирование")


@router.callback_query(F.data == "resume_qa")
async def callback_resume_qa(callback: CallbackQuery, state: FSMContext) -> None:
    profile = await load_profile(state)
    if profile is None:
        await callback.answer("Анкета не найдена. Отправьте /start.", show_alert=True)
        return
    question_id = state_to_question_id(await state.get_state())
    if question_id is None:
        question_id = profile.questionnaire.last_question_id or get_services().questionnaire.first_question_id()
    await callback.message.delete()
    await state.set_state(getattr(QuestionnaireStates, question_id))
    await show_question(callback.message, get_services().questionnaire, profile, question_id)
    await callback.answer("Продолжаем анкету")


@router.callback_query(F.data == "restart_qa")
async def callback_restart_qa(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.delete()
    await _begin_questionnaire(callback.message, state, callback.from_user.id, callback.from_user.username)
    await callback.answer("Начинаем новую анкету")


@router.callback_query(F.data == "show_service_info")
async def callback_service_info(callback: CallbackQuery) -> None:
    await callback.message.edit_text(SERVICE_INFO_TEXT, reply_markup=start_qa_kb())
    await callback.answer()


@router.message(F.text == "▶ Начать анкету")
async def text_start_qa(message: Message, state: FSMContext) -> None:
    await _begin_questionnaire(message, state, message.from_user.id, message.from_user.username)


@router.message(F.text == "ℹ️ Подробнее об услуге")
async def text_service_info(message: Message) -> None:
    await message.answer(SERVICE_INFO_TEXT, reply_markup=start_qa_kb())
