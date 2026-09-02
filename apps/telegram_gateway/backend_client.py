"""HTTP-клиент Backend для Telegram Gateway.

Единственный канал Gateway к данным: PostgreSQL, Redis Backend и application-слой
ему недоступны. Всё, что раньше делалось вызовом функции в том же процессе,
теперь проходит через этот клиент.

Аутентификация — тот же service-токен, что у Component Registry: у Gateway нет
пользователя и роли, поэтому admin JWT здесь не подходит.

Повторы. Три попытки с короткой паузой лечат мгновенную заминку туннеля RU↔EU,
пока пользователь ещё ждёт ответа в чате. Повторять дольше нельзя: он остался бы
в тишине. Повтор безопасен, потому что все операции контракта идемпотентны —
приём события по `update_id`, приём отчёта о доставке по её идентификатору.

Повторяются только сетевые отказы и 5xx. Ответ 4xx повтором не лечится: он
означает, что запрос неверен, и второй такой же вернёт то же самое.
"""
from __future__ import annotations

import asyncio
import logging

import httpx

from src.domain.telegram_contract import (
    TelegramDeliveryResult,
    TelegramDeliveryTask,
    TelegramUpdateRequest,
    TelegramUpdateResponse,
)
from src.infrastructure.components.heartbeat_client import SERVICE_TOKEN_HEADER

logger = logging.getLogger(__name__)


class BackendUnavailableError(Exception):
    """Backend не ответил за отведённые попытки.

    Отдельный тип, а не `httpx`-исключение: вызывающему нужно отличить «данные
    недоступны, скажи пользователю подождать» от ошибки в собственном коде.
    """


class BackendRejectedError(Exception):
    """Backend ответил 4xx: запрос неверен, повтор не поможет."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(f"HTTP {status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


class BackendClient:
    def __init__(
        self,
        *,
        base_url: str,
        service_token: str,
        timeout_seconds: float,
        retries: int,
        retry_delay_seconds: float,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
            headers={SERVICE_TOKEN_HEADER: service_token},
        )
        self._retries = max(1, retries)
        self._delay = retry_delay_seconds

    async def close(self) -> None:
        await self._client.aclose()

    # --- Контракт --------------------------------------------------------------

    async def contract_version(self) -> int:
        payload = await self._request("GET", "/internal/v1/telegram/contract")
        return int(payload["contract_version"])

    async def handle_update(
        self, request: TelegramUpdateRequest
    ) -> TelegramUpdateResponse:
        payload = await self._request(
            "POST",
            "/internal/v1/telegram/updates",
            json=request.model_dump(mode="json"),
        )
        return TelegramUpdateResponse.model_validate(payload)

    async def send_photo(
        self,
        *,
        update_id: int,
        telegram_user_id: str,
        chat_id: str,
        file_id: str,
        extension: str,
        content: bytes,
    ) -> TelegramUpdateResponse:
        """Передаёт байты фотографии в RU.

        Содержимое уходит телом запроса и в EU не сохраняется: оно существует
        только в памяти процесса между скачиванием из Telegram и этим вызовом.
        """
        payload = await self._request(
            "POST",
            "/internal/v1/telegram/photo",
            params={
                "update_id": update_id,
                "telegram_user_id": telegram_user_id,
                "chat_id": chat_id,
                "file_id": file_id,
                "extension": extension,
            },
            content=content,
        )
        return TelegramUpdateResponse.model_validate(payload)

    async def claim_deliveries(
        self, *, owner: str, limit: int
    ) -> list[TelegramDeliveryTask]:
        payload = await self._request(
            "POST",
            "/internal/v1/telegram/deliveries/claim",
            params={"owner": owner, "limit": limit},
        )
        return [TelegramDeliveryTask.model_validate(item) for item in payload]

    async def fetch_document(self, delivery_id: int) -> bytes:
        """Файл программы. Возвращается в память, на диск EU не пишется."""
        response = await self._raw(
            "GET", f"/internal/v1/telegram/deliveries/{delivery_id}/document"
        )
        return response.content

    async def report_delivery(
        self, delivery_id: int, result: TelegramDeliveryResult
    ) -> None:
        await self._request(
            "POST",
            f"/internal/v1/telegram/deliveries/{delivery_id}/result",
            json=result.model_dump(mode="json"),
        )

    # --- Транспорт --------------------------------------------------------------

    async def _request(self, method: str, path: str, **kwargs):
        response = await self._raw(method, path, **kwargs)
        return response.json()

    async def _raw(self, method: str, path: str, **kwargs) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(1, self._retries + 1):
            try:
                response = await self._client.request(method, path, **kwargs)
            except httpx.HTTPError as exc:
                last_error = exc
                logger.warning(
                    "event=backend_request_failed path=%s attempt=%s error=%s",
                    path,
                    attempt,
                    exc.__class__.__name__,
                )
            else:
                if response.status_code < 400:
                    return response
                if response.status_code < 500:
                    # 4xx повтором не лечится. Тело ошибки не логируется целиком:
                    # в нём бывает содержимое запроса.
                    raise BackendRejectedError(
                        response.status_code, _safe_detail(response)
                    )
                last_error = BackendUnavailableError(
                    f"HTTP {response.status_code}"
                )
                logger.warning(
                    "event=backend_request_failed path=%s attempt=%s status=%s",
                    path,
                    attempt,
                    response.status_code,
                )

            if attempt < self._retries:
                await asyncio.sleep(self._delay * attempt)

        raise BackendUnavailableError(
            f"Backend не ответил после {self._retries} попыток: {last_error}"
        )


def _safe_detail(response: httpx.Response) -> str:
    """Короткое описание отказа без тела запроса."""
    try:
        payload = response.json()
    except ValueError:
        return response.reason_phrase or "request rejected"
    detail = payload.get("detail") if isinstance(payload, dict) else None
    if isinstance(detail, str):
        return detail[:200]
    return str(detail)[:200] if detail is not None else "request rejected"
