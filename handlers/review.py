from __future__ import annotations

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from handlers.questionnaire import render_review
from states.questionnaire_states import QuestionnaireStates

router = Router()


@router.callback_query(lambda c: c.data == "return_to_questionnaire")
async def return_to_questionnaire(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(QuestionnaireStates.q01_name)
    await callback.message.answer("Вернулись к анкете. Продолжайте с начала.")
    await callback.answer()


@router.message(lambda m: m.text and m.text.startswith("/"))
async def route_start_commands(message: Message) -> None:
    if message.text == "/start":
        await message.answer("Для запуска анкеты используйте /start")
