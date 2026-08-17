from __future__ import annotations

from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from handlers.questionnaire import ask_question
from keyboards.inline import resume_qa_kb, start_qa_kb
from keyboards.reply import get_main_menu_kb
from services.profile_builder import build_empty_profile
from services.storage import log_user_response, next_profile_id
from states.questionnaire_states import QuestionnaireStates

router = Router()


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    current_state = await state.get_state()
    profile = (await state.get_data()).get("profile")
    if current_state and profile and not profile["questionnaire"].get("completed"):
        await message.answer(
            "У вас есть незавершённая анкета. Продолжить заполнение или начать заново?",
            reply_markup=resume_qa_kb(),
        )
        return

    await state.clear()
    profile = build_empty_profile()
    profile["source"]["bot_user_id"] = str(message.from_user.id)
    profile["source"]["telegram_username"] = message.from_user.username or None
    profile["profile_id"] = next_profile_id()
    profile["created_at"] = profile["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    await state.update_data(profile=profile)
    await message.answer(
        "🏋️ Индивидуальная программа тренировок\n\n"
        "Я помогу собрать информацию для составления персональной программы.\n"
        "Анкета займёт 5–10 минут.\n\n"
        "На основе ваших ответов будет подготовлена программа тренировок в виде удобного файла, который открывается на смартфоне и компьютере.\n\n"
        "⚠️ Не все вопросы анкеты обязательны для заполнения, но чем полнее будет заполнена анкета, "
        "тем точнее будет подобрана программа.\n\n"
        "Вопросы, отмеченные звёздочкой (*), обязательны. Необязательные можно пропустить.\n\n"
        "Готовы начать?",
        reply_markup=start_qa_kb(),
    )


@router.message(Command("status"))
async def cmd_status(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    profile = data.get("profile") or build_empty_profile()
    answered = 0
    for value in profile.values():
        if isinstance(value, dict):
            for subvalue in value.values():
                if subvalue not in (None, [], {}, ""):
                    answered += 1
    await message.answer(f"📊 Текущий прогресс: вы ответили на {answered} из 36 вопросов.")


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    current = await state.get_state()
    await state.clear()
    await message.answer("❌ Анкета сброшена. Чтобы начать заново, отправьте /start.", reply_markup=get_main_menu_kb())
    log_user_response(profile_id="system", user_id=message.from_user.id, message=f"cancelled from state={current}")


@router.callback_query(F.data == "start_qa")
async def callback_start_qa(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.message.delete()
    await state.set_state(QuestionnaireStates.q01_name)
    await ask_question(callback.message, state, "q01_name")
    await callback.answer("Начинаем анкетирование")


@router.callback_query(F.data == "resume_qa")
async def callback_resume_qa(callback: CallbackQuery, state: FSMContext) -> None:
    current_state = await state.get_state()
    question_id = current_state.rsplit(":", 1)[-1] if current_state else None
    if not question_id or not hasattr(QuestionnaireStates, question_id):
        question_id = (await state.get_data()).get("profile", {}).get("questionnaire", {}).get("last_question_id")
    question_id = question_id or "q01_name"
    await callback.message.delete()
    await state.set_state(getattr(QuestionnaireStates, question_id))
    await ask_question(callback.message, state, question_id)
    await callback.answer("Продолжаем анкету")


@router.callback_query(F.data == "restart_qa")
async def callback_restart_qa(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    profile = build_empty_profile()
    profile["source"]["bot_user_id"] = str(callback.from_user.id)
    profile["source"]["telegram_username"] = callback.from_user.username or None
    profile["profile_id"] = next_profile_id()
    profile["created_at"] = profile["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    await state.update_data(profile=profile)
    await callback.message.delete()
    await state.set_state(QuestionnaireStates.q01_name)
    await ask_question(callback.message, state, "q01_name")
    await callback.answer("Начинаем новую анкету")


@router.callback_query(F.data == "show_service_info")
async def callback_service_info(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.message.edit_text(
        "ℹ️ Услуга включает сбор анкеты, подбор целей, ограничений и режима тренировок, а затем формирование структурированного профиля клиента для дальнейшей генерации программы.\n\n"
        "Если готовы — нажмите кнопку ниже.",
        reply_markup=start_qa_kb(),
    )
    await callback.answer()


@router.message(F.text == "▶ Начать анкету")
async def text_start_qa(message: Message, state: FSMContext) -> None:
    await state.set_state(QuestionnaireStates.q01_name)
    await ask_question(message, state, "q01_name")


@router.message(F.text == "ℹ️ Подробнее об услуге")
async def text_service_info(message: Message, state: FSMContext) -> None:
    await message.answer(
        "ℹ️ Услуга включает сбор анкеты, подбор целей, ограничений и режима тренировок, а затем формирование структурированного профиля клиента для дальнейшей генерации программы.\n\n"
        "Если готовы — нажмите кнопку ниже.",
        reply_markup=start_qa_kb(),
    )
