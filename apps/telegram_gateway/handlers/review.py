"""Handler review / подтверждения / редактирования.

Финализация идемпотентна: повторное нажатие «Подтвердить» не создаёт дубликат.
Уведомление администратору имеет явный статус доставки (pending/sent/failed).

После успешной финализации автоматически запускается program pipeline
(Stage 5): генерация → HTML → доставка. Ошибки pipeline не ломают
сохранённый профиль. Идемпотентность: при повторном finalize существующая
валидная программа переиспользуется, дубликаты не создаются.
"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from apps.telegram_gateway.handlers.common import (
    get_admin_notification_service,
    get_services,
    load_profile,
    show_question,
    store_profile,
)
from apps.telegram_gateway.keyboards.inline import (
    confirm_kb,
    edit_questions_kb,
    edit_sections_kb,
    review_kb,
)
from apps.telegram_gateway.pipeline import (
    build_program_pipeline,
    is_auto_generation_enabled,
)
from apps.telegram_gateway.states.questionnaire_states import QuestionnaireStates
from src.application.programs.pipeline import PipelineOutcome
from src.application.questionnaire.review import render_review_html
from src.errors import ProfilePersistenceError, QuestionnaireValidationError
from src.infrastructure.config import ADMIN_CHAT_ID
from src.infrastructure.telegram.admin_sender import TelegramAdminSender

logger = logging.getLogger(__name__)
router = Router(name="telegram_gateway.review")

# Сильные ссылки на фоновые задачи pipeline: event loop хранит только
# слабые ссылки, без этого set задача может быть собрана GC mid-execution.
_BACKGROUND_TASKS: set[asyncio.Task] = set()


async def render_review(message: Message, state: FSMContext) -> None:
    profile = await load_profile(state)
    if profile is None:
        return
    await message.answer(render_review_html(profile), reply_markup=review_kb(), parse_mode="HTML")


@router.callback_query(F.data == "review_confirm")
async def review_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    profile = await load_profile(state)
    if profile is None:
        return
    await store_profile(state, profile)
    await state.set_state(QuestionnaireStates.confirm)
    await callback.message.edit_text(
        "Подтверждаю, что:\n"
        "✅ Указанные данные верны\n"
        "✅ Информация о здоровье указана корректно\n"
        "✅ Я понимаю, что программа не заменяет консультацию врача\n\n",
        reply_markup=confirm_kb(),
    )
    await callback.answer("Подтверждение принято")


@router.callback_query(F.data == "final_confirm")
async def final_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    profile = await load_profile(state)
    if profile is None:
        return
    services = get_services()
    try:
        result = await services.finalization.finalize(profile)
    except ProfilePersistenceError:
        logger.exception("profile_persistence_failed", extra={"profile_id": profile.profile_id})
        await callback.answer(
            "Не удалось сохранить анкету. Попробуйте ещё раз чуть позже.", show_alert=True
        )
        return

    await store_profile(state, result.profile)

    # Уведомление администратору — только при первой финализации.
    if not result.already_finalized:
        sender = TelegramAdminSender(callback.bot, ADMIN_CHAT_ID) if ADMIN_CHAT_ID else None
        notification = get_admin_notification_service(services.repository, sender)
        await notification.notify(result.profile)
        await store_profile(state, result.profile)

    number = result.profile.display_number or result.profile.profile_id or "—"
    await callback.message.edit_text(
        f"✅ Спасибо! Ваша анкета принята. Номер: {number}"
    )
    await callback.answer("Анкета сохранена")

    # Автогенерация программы после успешного сохранения профиля.
    # Ошибки pipeline не ломают сохранённый профиль; задача выполняется
    # в фоне, чтобы не блокировать handler.
    if is_auto_generation_enabled() and result.profile.profile_id:
        task = asyncio.create_task(
            run_program_pipeline(
                bot=callback.bot,
                chat_id=str(callback.from_user.id),
                profile_id=result.profile.profile_id,
                already_finalized=result.already_finalized,
            )
        )
        _BACKGROUND_TASKS.add(task)
        task.add_done_callback(_BACKGROUND_TASKS.discard)


async def run_program_pipeline(
    *,
    bot: Bot,
    chat_id: str,
    profile_id: str,
    already_finalized: bool,
) -> None:
    """Генерация + HTML + доставка. Все ошибки приводятся к user-facing сообщению."""
    try:
        pipeline = build_program_pipeline(bot)
    except Exception:  # noqa: BLE001 — сборка pipeline не должна падать молча
        logger.exception("program_pipeline_build_failed", extra={"profile_id": profile_id})
        try:
            await bot.send_message(
                chat_id,
                "Не удалось автоматически сформировать программу. "
                "Мы получили уведомление об ошибке.",
            )
        except Exception:  # noqa: BLE001
            logger.exception("user_message_send_failed", extra={"profile_id": profile_id})
        return

    if not already_finalized:
        try:
            await bot.send_message(chat_id, "⏳ Формируем вашу персональную программу...")
        except Exception:  # noqa: BLE001
            logger.exception("user_message_send_failed", extra={"profile_id": profile_id})

    try:
        result = await pipeline.run_for_user(
            profile_id=profile_id, chat_id=chat_id, reuse_existing=True
        )
    except Exception:  # noqa: BLE001 — pipeline уже ловит типовые ошибки; защита от непредвиденных
        logger.exception("program_pipeline_unhandled_error", extra={"profile_id": profile_id})
        return

    if result.user_message:
        try:
            await bot.send_message(chat_id, result.user_message)
        except Exception:  # noqa: BLE001
            logger.exception("user_message_send_failed", extra={"profile_id": profile_id})

    # Исход фиксируется всегда: пользователь видит только обобщённое сообщение,
    # и без записи стадии причина отказа доставки не восстанавливается по логу.
    log = logger.info if result.outcome is PipelineOutcome.DELIVERED else logger.warning
    log(
        "event=program_pipeline_finished",
        extra={
            "profile_id": profile_id,
            "outcome": result.outcome.value,
            "program_id": result.program.program_id if result.program else None,
            "program_version": result.program.version if result.program else None,
        },
    )


@router.callback_query(F.data == "return_to_questionnaire")
async def return_to_questionnaire(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(QuestionnaireStates.review)
    await render_review(callback.message, state)
    await callback.answer()


@router.callback_query(F.data == "review_edit")
async def review_edit(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        "Выберите раздел, который хотите исправить:", reply_markup=edit_sections_kb()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("edit_section_"))
async def select_edit_section(callback: CallbackQuery) -> None:
    section = callback.data.removeprefix("edit_section_")
    await callback.message.edit_text("Выберите вопрос:", reply_markup=edit_questions_kb(section))
    await callback.answer()


@router.callback_query(F.data.startswith("edit_question_"))
async def select_question_to_edit(callback: CallbackQuery, state: FSMContext) -> None:
    target_id = callback.data.removeprefix("edit_question_")
    profile = await load_profile(state)
    if profile is None:
        return
    service = get_services().questionnaire
    try:
        service.begin_edit(profile, target_id)
    except QuestionnaireValidationError as exc:
        await callback.answer(exc.user_message, show_alert=True)
        return
    await state.update_data(editing_question=target_id)
    await state.set_state(getattr(QuestionnaireStates, target_id))
    await callback.message.edit_text("Исправьте ответ:")
    await show_question(callback.message, service, profile, target_id)
    await callback.answer()
