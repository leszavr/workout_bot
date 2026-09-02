"""Хендлеры Gateway: приём обновления Telegram и показ ответа Backend.

Один роутер вместо четырёх (start / questionnaire / review / errors). Прежнее
деление отражало структуру анкеты — какие вопросы текстовые, какие с кнопками,
где сводка. Этого знания у Gateway больше нет: любое обновление — это событие,
и обрабатывается оно одинаково.

FSM aiogram не используется. Состояние диалога живёт в RU: раньше в FSM лежал
сериализованный профиль с ответами анкеты, а Gateway размещается в EU, где
персональные данные хранить нельзя.
"""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message, Update

from apps.telegram_gateway import view_renderer
from apps.telegram_gateway.backend_client import (
    BackendRejectedError,
    BackendUnavailableError,
)
from apps.telegram_gateway.runtime import get_backend_client
from src.domain.telegram_contract import (
    TelegramUpdateKind,
    TelegramUpdateRequest,
)

logger = logging.getLogger(__name__)
router = Router(name="telegram_gateway.dialog")

BACKEND_UNAVAILABLE_TEXT = (
    "Сервис временно недоступен. Ваши ответы сохранены — отправьте сообщение "
    "ещё раз через минуту, и мы продолжим с того же места."
)
PHOTO_TOO_LARGE_TEXT = (
    "Фотография слишком большая. Отправьте снимок меньшего размера или "
    "пропустите вопрос."
)
PHOTO_FAILED_TEXT = "Не удалось сохранить фото. Попробуйте ещё раз или пропустите вопрос."


@router.message(F.photo)
async def handle_photo(message: Message, event_update: Update) -> None:
    """Фотография оборудования.

    Скачивается в память и уходит в RU: доступ к Bot API есть только у Gateway,
    а хранить пользовательский контент в EU нельзя. На диск ничего не пишется.
    """
    if message.from_user is None:
        return
    photo = message.photo[-1]
    try:
        tg_file = await message.bot.get_file(photo.file_id)
        buffer = await message.bot.download_file(tg_file.file_path)
        content = buffer.read()
    except Exception:  # noqa: BLE001 — ошибка скачивания из Telegram
        logger.warning("event=photo_download_failed")
        await message.answer(PHOTO_FAILED_TEXT)
        return

    extension = "." + (
        tg_file.file_path.rsplit(".", 1)[-1] if "." in (tg_file.file_path or "") else "jpg"
    )
    try:
        response = await get_backend_client().send_photo(
            update_id=event_update.update_id,
            telegram_user_id=str(message.from_user.id),
            chat_id=str(message.chat.id),
            file_id=photo.file_id,
            extension=extension,
            content=content,
        )
    except BackendUnavailableError:
        logger.warning("event=backend_unavailable stage=photo")
        await message.answer(BACKEND_UNAVAILABLE_TEXT)
        return
    except BackendRejectedError as exc:
        if exc.status_code == 413:
            await message.answer(PHOTO_TOO_LARGE_TEXT)
            return
        logger.warning("event=backend_rejected stage=photo status=%s", exc.status_code)
        await message.answer(PHOTO_FAILED_TEXT)
        return

    await view_renderer.render(
        response.view, bot=message.bot, chat_id=str(message.chat.id), source=message
    )


@router.message(F.text)
async def handle_text(message: Message, event_update: Update) -> None:
    """Текст и команды. Различает их Backend, а не Gateway.

    Команда отличается от обычного текста только префиксом, и решать, что
    означает `/start`, — часть логики диалога, которая живёт в RU.
    """
    if message.from_user is None or message.text is None:
        return
    kind = (
        TelegramUpdateKind.COMMAND
        if message.text.startswith("/")
        else TelegramUpdateKind.TEXT
    )
    await _exchange(
        message,
        TelegramUpdateRequest(
            update_id=event_update.update_id,
            telegram_user_id=str(message.from_user.id),
            chat_id=str(message.chat.id),
            username=message.from_user.username,
            kind=kind,
            payload=message.text,
        ),
    )


@router.callback_query()
async def handle_callback(callback: CallbackQuery, event_update: Update) -> None:
    """Нажатие кнопки. Значение `callback_data` определяет Backend.

    Фильтра по конкретным значениям нет намеренно: список действий задаёт
    Backend, и держать его копию в EU означало бы возвращать сюда знание об
    анкете, а новое действие требовало бы обновления Gateway.
    """
    if callback.from_user is None or callback.message is None:
        return
    await _exchange(
        callback,
        TelegramUpdateRequest(
            # `update_id` самого обновления Telegram, а не `message_id` кнопки:
            # у нескольких нажатий на одном сообщении (переключение дней недели)
            # `message_id` совпадает, и Backend счёл бы второе нажатие
            # дубликатом первого. Переотправку неподтверждённого обновления
            # Telegram делает с тем же `update_id` — именно это и нужно ключу
            # идемпотентности.
            update_id=event_update.update_id,
            telegram_user_id=str(callback.from_user.id),
            chat_id=str(callback.message.chat.id),
            username=callback.from_user.username,
            kind=TelegramUpdateKind.CALLBACK,
            payload=callback.data or "",
        ),
    )


async def _exchange(
    source: Message | CallbackQuery, request: TelegramUpdateRequest
) -> None:
    """Обмен с Backend и показ ответа.

    Недоступность Backend не роняет polling и не приводит к перезапуску: диалог
    продолжается позже с того же места, потому что позиция и ответы лежат в RU.
    """
    try:
        response = await get_backend_client().handle_update(request)
    except BackendUnavailableError:
        logger.warning("event=backend_unavailable stage=update")
        await _notify_unavailable(source)
        return
    except BackendRejectedError as exc:
        logger.warning("event=backend_rejected stage=update status=%s", exc.status_code)
        await _notify_unavailable(source)
        return

    await view_renderer.render(
        response.view, bot=source.bot, chat_id=request.chat_id, source=source
    )


async def _notify_unavailable(source: Message | CallbackQuery) -> None:
    """Сообщение о недоступности. Пользователь не должен остаться в тишине."""
    try:
        if isinstance(source, CallbackQuery):
            await source.answer(BACKEND_UNAVAILABLE_TEXT, show_alert=True)
            return
        await source.answer(BACKEND_UNAVAILABLE_TEXT)
    except Exception:  # noqa: BLE001 — Telegram тоже может быть недоступен
        logger.warning("event=unavailable_notice_failed")
