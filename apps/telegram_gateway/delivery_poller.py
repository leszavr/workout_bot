"""Отправка готовых программ пользователям (опрос очереди Backend).

Инициатором может быть только Gateway: он в EU за NAT, входящих подключений к
нему нет, а `api.telegram.org` доступен только оттуда. Поэтому Backend ставит
задание в очередь, а Gateway её опрашивает.

Задержка опроса добавляется к моменту «программа готова → файл ушёл». Она
некритична: к этому времени пользователь ждёт уже минуты, потраченные на
генерацию.

Файл на диск EU не сохраняется: он приходит в память, уходит в Telegram и
исчезает вместе с областью видимости.
"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.types import BufferedInputFile

from apps.telegram_gateway.backend_client import (
    BackendClient,
    BackendRejectedError,
    BackendUnavailableError,
)
from src.domain.telegram_contract import TelegramDeliveryResult, TelegramDeliveryTask

logger = logging.getLogger(__name__)


async def deliver_one(
    task: TelegramDeliveryTask, *, bot: Bot, client: BackendClient
) -> bool:
    """Отправляет одно задание и отчитывается о результате.

    Об итоге сообщаем всегда, включая неудачу: Backend расходует бюджет попыток
    и назначает повтор только по этому отчёту. Промолчав, мы оставили бы задание
    в состоянии отправки до истечения аренды.
    """
    try:
        content = await client.fetch_document(task.delivery_id)
    except BackendRejectedError as exc:
        # Программа удалена либо задание уже не в состоянии отправки. Повторять
        # запрос немедленно бессмысленно; сообщаем и отпускаем задание.
        await _report(
            client,
            task,
            TelegramDeliveryResult(delivered=False, error=f"document: {exc.detail}"),
        )
        return False
    except BackendUnavailableError:
        # Отчитаться тоже не сможем: аренда истечёт, и задание вернётся в очередь.
        logger.warning(
            "event=delivery_document_unavailable delivery_id=%s", task.delivery_id
        )
        return False

    try:
        message = await bot.send_document(
            chat_id=task.chat_id,
            document=BufferedInputFile(content, filename=task.filename),
            caption=task.caption or None,
        )
    except Exception as exc:  # noqa: BLE001 — любой отказ Telegram
        logger.warning(
            "event=delivery_send_failed delivery_id=%s error=%s",
            task.delivery_id,
            exc.__class__.__name__,
        )
        await _report(
            client,
            task,
            TelegramDeliveryResult(
                delivered=False,
                error=f"{exc.__class__.__name__}: {str(exc)[:200]}",
            ),
        )
        return False

    await _report(
        client,
        task,
        TelegramDeliveryResult(delivered=True, message_id=message.message_id),
    )
    logger.info(
        "event=delivery_sent delivery_id=%s message_id=%s",
        task.delivery_id,
        message.message_id,
    )
    return True


async def poll_once(*, bot: Bot, client: BackendClient, owner: str, limit: int) -> int:
    """Один проход очереди. Возвращает число отправленных файлов."""
    try:
        tasks = await client.claim_deliveries(owner=owner, limit=limit)
    except (BackendUnavailableError, BackendRejectedError):
        logger.warning("event=delivery_claim_failed")
        return 0

    sent = 0
    for task in tasks:
        if await deliver_one(task, bot=bot, client=client):
            sent += 1
    return sent


async def run_delivery_poller(
    *,
    bot: Bot,
    client: BackendClient,
    owner: str,
    interval_seconds: float,
    limit: int,
    stop: asyncio.Event,
) -> int:
    """Цикл опроса до остановки. Возвращает число проходов.

    Ошибки прохода перехватываются внутри: цикл обязан выжить недоступность
    Backend, иначе временный обрыв туннеля остановил бы доставку до перезапуска
    контейнера.

    Ожидание — через `Event.wait` с таймаутом, а не `sleep`: остановка не должна
    ждать полный интервал.
    """
    cycles = 0
    while not stop.is_set():
        try:
            await poll_once(bot=bot, client=client, owner=owner, limit=limit)
        except Exception:  # noqa: BLE001 — цикл не должен умирать
            logger.exception("event=delivery_poll_error")
        cycles += 1
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_seconds)
        except asyncio.TimeoutError:
            continue
    return cycles


async def _report(
    client: BackendClient, task: TelegramDeliveryTask, result: TelegramDeliveryResult
) -> None:
    try:
        await client.report_delivery(task.delivery_id, result)
    except (BackendUnavailableError, BackendRejectedError):
        # Отчёт потерян: аренда истечёт, и задание вернётся в очередь. Файл при
        # успешной отправке уйдёт повторно — это цена того, что подтверждение
        # доставки не может быть атомарным с самой отправкой.
        logger.warning(
            "event=delivery_report_failed delivery_id=%s delivered=%s",
            task.delivery_id,
            result.delivered,
        )
