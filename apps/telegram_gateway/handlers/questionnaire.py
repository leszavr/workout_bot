"""Handler анкеты: только транспорт.

получить сообщение → вызвать QuestionnaireService → отправить результат.
Ни структуры профиля, ни сохранения, ни бизнес-правил здесь нет.
"""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from apps.telegram_gateway.handlers.common import (
    get_services,
    load_profile,
    show_question,
    state_to_question_id,
    store_profile,
)
from apps.telegram_gateway.keyboards.inline import preferred_days_kb
from apps.telegram_gateway.states.questionnaire_states import QuestionnaireStates
from src.application.questionnaire.questions import QUESTIONS, QuestionKind
from src.errors import QuestionnaireValidationError

logger = logging.getLogger(__name__)
router = Router(name="telegram_gateway.questionnaire")

# callback_data → question_id для всех вопросов с вариантами ответа.
CALLBACK_TO_QUESTION: dict[str, str] = {
    option.callback_data: question.id
    for question in QUESTIONS
    for option in question.options
}


async def _advance_or_review(
    event: Message | CallbackQuery,
    state: FSMContext,
    profile,
    next_question_id: str | None,
    confirmation: str,
) -> None:
    """Сохраняет профиль и переходит к следующему вопросу или review."""
    from apps.telegram_gateway.handlers.review import render_review

    await store_profile(state, profile)
    target = event.message if isinstance(event, CallbackQuery) else event

    if (await state.get_data()).get("editing_question"):
        await state.update_data(editing_question=None)
        await state.set_state(QuestionnaireStates.review)
        await render_review(target, state)
        return

    if next_question_id is None:
        await state.set_state(QuestionnaireStates.review)
        await render_review(target, state)
        return

    await state.set_state(getattr(QuestionnaireStates, next_question_id))
    await target.answer(confirmation)
    await show_question(target, get_services().questionnaire, profile, next_question_id)


@router.message(F.text)
async def handle_text_answer(message: Message, state: FSMContext) -> None:
    question_id = state_to_question_id(await state.get_state())
    if question_id is None:
        return
    profile = await load_profile(state)
    if profile is None:
        return
    service = get_services().questionnaire
    try:
        result = service.answer_text(profile, question_id, message.text or "")
    except QuestionnaireValidationError as exc:
        await message.answer(exc.user_message)
        return
    await _advance_or_review(
        message, state, result.profile, result.next_question_id, result.confirmation
    )


@router.message(F.photo)
async def handle_photo(message: Message, state: FSMContext) -> None:
    question_id = state_to_question_id(await state.get_state())
    if question_id != "q19_equipment_photos":
        return
    profile = await load_profile(state)
    if profile is None:
        return
    service = get_services().questionnaire
    try:
        photo = message.photo[-1]
        tg_file = await message.bot.get_file(photo.file_id)
        buffer = await message.bot.download_file(tg_file.file_path)
        extension = "." + (tg_file.file_path.rsplit(".", 1)[-1] if "." in tg_file.file_path else "jpg")
        result = service.add_photo(profile, photo.file_id, buffer.read(), extension)
    except QuestionnaireValidationError as exc:
        await message.answer(exc.user_message)
        return
    except Exception:  # noqa: BLE001 — ошибки скачивания/записи файла
        await message.answer("Не удалось сохранить фото. Попробуйте ещё раз или пропустите вопрос.")
        return
    await _advance_or_review(
        message, state, result.profile, result.next_question_id, result.confirmation
    )


@router.callback_query(F.data == "skip_question")
async def skip_question(callback: CallbackQuery, state: FSMContext) -> None:
    question_id = state_to_question_id(await state.get_state())
    if question_id is None:
        return
    profile = await load_profile(state)
    if profile is None:
        return
    service = get_services().questionnaire
    try:
        result = service.skip(profile, question_id)
    except QuestionnaireValidationError as exc:
        await callback.answer(exc.user_message, show_alert=True)
        return
    await callback.message.edit_text("⏭️ Вопрос пропущен")
    await _advance_or_review(
        callback, state, result.profile, result.next_question_id, result.confirmation
    )
    await callback.answer()


@router.callback_query(F.data.startswith("day_"))
async def toggle_day(callback: CallbackQuery, state: FSMContext) -> None:
    profile = await load_profile(state)
    if profile is None:
        return
    service = get_services().questionnaire
    day = callback.data.removeprefix("day_")
    selected, action = service.toggle_day(profile, day)
    await store_profile(state, profile)
    await callback.message.edit_reply_markup(reply_markup=preferred_days_kb(selected))
    from src.application.questionnaire.labels import RU_LABELS

    await callback.answer(f"День {RU_LABELS.get(day, day)} {action}")


@router.callback_query(F.data == "days_done")
async def confirm_days(callback: CallbackQuery, state: FSMContext) -> None:
    profile = await load_profile(state)
    if profile is None:
        return
    service = get_services().questionnaire
    try:
        result = service.confirm_days(profile)
    except QuestionnaireValidationError as exc:
        await callback.answer(exc.user_message, show_alert=True)
        return
    await callback.message.edit_text(result.confirmation)
    await _advance_or_review(
        callback, state, result.profile, result.next_question_id, result.confirmation
    )
    await callback.answer("Дни недели сохранены")


@router.callback_query(F.data.in_(CALLBACK_TO_QUESTION))
async def handle_choice(callback: CallbackQuery, state: FSMContext) -> None:
    """Единая точка обработки всех вопросов с вариантами ответа.

    Фильтр обязателен: aiogram отдаёт обновление первому подошедшему
    хендлеру и дальше по роутерам не идёт. Без него анкета перехватывала бы
    review/confirm/edit-коллбэки соседнего роутера и молча их теряла.
    """
    question_id = CALLBACK_TO_QUESTION[callback.data or ""]
    profile = await load_profile(state)
    if profile is None:
        return
    service = get_services().questionnaire
    try:
        result = service.answer_choice(profile, question_id, callback.data)
    except QuestionnaireValidationError as exc:
        await callback.answer(exc.user_message, show_alert=True)
        return
    await callback.message.edit_text(result.confirmation)
    await _advance_or_review(
        callback, state, result.profile, result.next_question_id, result.confirmation
    )
    await callback.answer()
