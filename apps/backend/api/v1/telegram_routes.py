"""Internal API для Telegram Gateway.

Отдельный роутер: у Gateway нет пользователя и роли, поэтому admin JWT здесь не
подходит — аутентификация та же, что у Component Registry
(`X-Internal-Service-Token`, сравнение в константное время).

Шесть операций, ни одной лишней. Gateway транспортирует и отображает, поэтому
всё, что нужно помнить между шагами диалога, помнит Backend:

    GET    /internal/v1/telegram/contract           версия контракта
    POST   /internal/v1/telegram/updates            событие Telegram → что показать
    POST   /internal/v1/telegram/photo              байты фотографии оборудования
    POST   /internal/v1/telegram/deliveries/claim   забрать задания на отправку
    GET    /internal/v1/telegram/deliveries/{id}/document   файл программы
    POST   /internal/v1/telegram/deliveries/{id}/result     итог отправки

`claim` — POST, хотя и «получает» задания: он меняет состояние (переводит
доставку в `sending` и берёт аренду), и GET с побочным эффектом ввёл бы в
заблуждение любой кэш и любой прокси на пути RU↔EU.

Файл программы отдаётся отдельным вызовом, а не в теле задания: в EU он не
сохраняется, живёт в памяти процесса Gateway до отправки в Telegram, и
включение его в список заданий заставило бы передавать все файлы пачкой.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response

from apps.backend.api.v1.telegram_dependencies import (
    build_delivery_queue_service,
    build_telegram_dialog_service,
    start_program_generation,
)
from apps.backend.service_auth import require_service_token
from src.domain.telegram_contract import (
    TELEGRAM_CONTRACT_VERSION,
    TelegramDeliveryResult,
    TelegramDeliveryTask,
    TelegramUpdateRequest,
    TelegramUpdateResponse,
)
from src.errors import ProgramDeliveryError
from src.infrastructure.config import (
    MAX_PHOTO_SIZE_MB,
    TELEGRAM_DELIVERY_LEASE_SECONDS,
)
from src.version import APP_VERSION

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/internal/v1/telegram",
    tags=["internal-telegram"],
    dependencies=[Depends(require_service_token)],
)

MAX_PHOTO_BYTES = MAX_PHOTO_SIZE_MB * 1024 * 1024


@router.get("/contract")
async def contract_version() -> dict:
    """Версия контракта. Нужна Gateway для проверки совместимости при старте."""
    return {
        "contract_version": TELEGRAM_CONTRACT_VERSION,
        "backend_version": APP_VERSION,
    }


@router.post("/updates", response_model=TelegramUpdateResponse)
async def handle_update(request: TelegramUpdateRequest) -> TelegramUpdateResponse:
    """Событие Telegram: возвращает готовое к отображению описание.

    Идемпотентна по `update_id`: повтор (переотправка Telegram, retry Gateway
    при таймауте) возвращает тот же вид, а не продвигает анкету на второй шаг.

    После финализации анкеты здесь же запускается генерация — она уходит в
    фоновую задачу, потому что ответ пользователю не должен ждать AI-вызов.
    """
    response = await build_telegram_dialog_service().handle(request)
    if response.finished and response.profile_id and not response.duplicate:
        await start_program_generation(
            profile_id=response.profile_id, chat_id=request.chat_id
        )
    return response


@router.post("/photo", response_model=TelegramUpdateResponse)
async def handle_photo(
    request: Request,
    update_id: int = Query(...),
    telegram_user_id: str = Query(..., max_length=64),
    chat_id: str = Query(..., max_length=64),
    file_id: str = Query(..., max_length=128),
    extension: str = Query(..., max_length=10),
) -> TelegramUpdateResponse:
    """Фотография оборудования: байты от Gateway, запись — в RU.

    Gateway скачивает файл из Telegram (только у него есть доступ к Bot API) и
    передаёт содержимое сюда. На диск EU файл не попадает.

    Тело запроса — сами байты, метаданные в query. multipart здесь не нужен:
    файл ровно один, и `multipart/form-data` потребовал бы новой зависимости
    ради разбора конверта, который нечего разбирать.

    Чтение идёт потоком с проверкой лимита на каждом фрагменте: заголовку
    `Content-Length` доверять нельзя, а чтение без ограничения означает приём
    файла любого размера в память процесса.
    """
    content = bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content) > MAX_PHOTO_BYTES:
            raise HTTPException(
                status_code=413, detail=f"Файл больше {MAX_PHOTO_SIZE_MB} МБ"
            )
    if not content:
        raise HTTPException(status_code=422, detail="Пустое тело запроса")

    return await build_telegram_dialog_service().handle_photo(
        update_id=update_id,
        telegram_user_id=telegram_user_id,
        chat_id=chat_id,
        file_id=file_id,
        content=bytes(content),
        extension=extension,
    )


@router.post("/deliveries/claim", response_model=list[TelegramDeliveryTask])
async def claim_deliveries(
    owner: str = Query(..., max_length=64),
    limit: int = Query(default=5, ge=1, le=20),
) -> list[TelegramDeliveryTask]:
    """Забирает задания на отправку файлов.

    `owner` — идентификатор экземпляра Gateway: он попадает в аренду, поэтому
    два экземпляра не получат одно задание. Пустой список — нормальный ответ:
    очередь пуста.
    """
    return await build_delivery_queue_service().claim(
        owner=owner, lease_seconds=TELEGRAM_DELIVERY_LEASE_SECONDS, limit=limit
    )


@router.get(
    "/deliveries/{delivery_id}/document",
    response_class=Response,
    responses={200: {"content": {"text/html": {}}}},
)
async def delivery_document(delivery_id: int) -> Response:
    """Файл программы для отправки. Отдаётся только по захваченной доставке."""
    try:
        filename, content = await build_delivery_queue_service().render_document(
            delivery_id
        )
    except ProgramDeliveryError as exc:
        # 409, а не 404: запись существует, но находится не в том состоянии либо
        # программа удалена. Различать эти случаи Gateway не нужно — в обоих он
        # обязан отчитаться о неудаче и не повторять запрос немедленно.
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return Response(
        content=content,
        media_type="text/html; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/deliveries/{delivery_id}/result")
async def report_delivery(delivery_id: int, result: TelegramDeliveryResult) -> dict:
    """Итог отправки. Здесь расходуется бюджет попыток и назначается повтор."""
    try:
        record = await build_delivery_queue_service().report(delivery_id, result)
    except ProgramDeliveryError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "delivery_id": delivery_id,
        "status": record.status.value,
        "attempts": record.attempts,
        "retry_scheduled": record.next_attempt_at is not None,
    }
