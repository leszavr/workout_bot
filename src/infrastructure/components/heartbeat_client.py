"""HTTP-клиент регистрации компонента в Backend.

Живёт в infrastructure, потому что это транспорт: application-слой о httpx не
знает. Клиент используется Telegram Gateway и подойдёт любому будущему
connector-компоненту без изменений — он не содержит Telegram-специфики.

Ключевое свойство: **heartbeat не является условием работы компонента**.
Недоступность Backend или реестра не должна останавливать обработку анкет —
иначе мониторинг стал бы точкой отказа бизнес-функции. Поэтому ошибки
логируются и цикл продолжается.

Регистрация и heartbeat — один и тот же запрос: у компонента нет состояния
«уже зарегистрирован», и после его перезапуска или пересоздания записи в БД
контур восстанавливается сам.
"""
from __future__ import annotations

import asyncio
import logging

import httpx

from src.domain.components import HEARTBEAT_INTERVAL, ComponentMetadata

logger = logging.getLogger(__name__)

HEARTBEAT_PATH = "/internal/v1/components/heartbeat"
SERVICE_TOKEN_HEADER = "X-Internal-Service-Token"
REQUEST_TIMEOUT_SECONDS = 10.0


class ComponentHeartbeatClient:
    def __init__(
        self,
        *,
        base_url: str,
        service_token: str,
        metadata: ComponentMetadata,
        interval_seconds: float = HEARTBEAT_INTERVAL.total_seconds(),
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._service_token = service_token
        self._metadata = metadata
        self._interval = interval_seconds
        self._client = client
        self._owns_client = client is None

    async def send_once(self) -> dict | None:
        """Одна отправка. Возвращает ответ Backend либо None при сбое."""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS)
        try:
            response = await self._client.post(
                f"{self._base_url}{HEARTBEAT_PATH}",
                json=self._metadata.model_dump(mode="json"),
                headers={SERVICE_TOKEN_HEADER: self._service_token},
            )
        except httpx.HTTPError as exc:
            # Класс ошибки, а не текст: URL и заголовки в лог не попадают.
            logger.warning(
                "event=component_heartbeat_failed component_id=%s error=%s",
                self._metadata.component_id,
                exc.__class__.__name__,
            )
            return None

        if response.status_code >= 400:
            logger.warning(
                "event=component_heartbeat_rejected component_id=%s status=%s",
                self._metadata.component_id,
                response.status_code,
            )
            return None

        payload = response.json()
        state = (payload.get("compatibility") or {}).get("state")
        # Несовместимость видна и на стороне компонента: иначе о ней знала бы
        # только админка, а в логах Gateway не было бы следа.
        if state in ("update_required", "incompatible"):
            logger.error(
                "event=component_incompatible component_id=%s state=%s detail=%s",
                self._metadata.component_id,
                state,
                (payload.get("compatibility") or {}).get("detail"),
            )
        else:
            logger.info(
                "event=component_heartbeat_ok component_id=%s state=%s",
                self._metadata.component_id,
                state,
            )
        return payload

    async def run(self) -> None:
        """Фоновый цикл до отмены задачи."""
        while True:
            await self.send_once()
            await asyncio.sleep(self._interval)

    async def close(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None
