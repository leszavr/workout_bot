"""Адаптеры AI-провайдеров и реестр протоколов.

Архитектура строится вокруг ПРОТОКОЛОВ, а не брендов моделей:
OpenAICompatibleAdapter работает с любым OpenAI-compatible endpoint.
Доменный слой ничего не знает о конкретных провайдерах и моделях.

Все ошибки нормализуются в структурированные типы из src.domain.ai.errors —
разные сбои не скрываются под одним Exception.
"""
from __future__ import annotations

import abc
import asyncio
import logging
import time
from dataclasses import dataclass, field

import httpx
from pydantic import BaseModel, ConfigDict, Field

from src.domain.ai.enums import AIProtocol, AIResponseFormat
from src.domain.ai.errors import (
    AIConnectionError,
    AIInvalidResponseError,
    AIProviderError,
    AIRateLimitError,
    AITimeoutError,
    AIUnsupportedProtocolError,
)
from src.domain.ai.gateway import AIMessage

logger = logging.getLogger(__name__)

# Повторяем только идемпотентные/безопасные для повтора сбои.
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_RETRY_BACKOFF_BASE = 0.5


@dataclass
class EndpointConnection:
    """Технические параметры подключения (без секретов в логах)."""

    base_url: str
    api_key: str | None = None
    timeout_seconds: int = 60
    max_retries: int = 2


@dataclass
class AdapterResult:
    """Нормализованный результат адаптера (не формат OpenAI)."""

    content: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    latency_ms: int | None = None
    raw_metadata: dict = field(default_factory=dict)


class AdapterRequest(BaseModel):
    """Запрос на уровне адаптера (протокольно-нейтральный)."""

    model_config = ConfigDict(extra="forbid")

    messages: list[AIMessage] = Field(min_length=1)
    temperature: float | None = None
    max_tokens: int | None = Field(default=None, ge=1)
    response_format: AIResponseFormat = AIResponseFormat.TEXT


class AIProviderAdapter(abc.ABC):
    """Контракт адаптера протокола. Один адаптер — один протокол API."""

    @abc.abstractmethod
    async def generate(
        self,
        request: AdapterRequest,
        connection: EndpointConnection,
        model_id: str,
    ) -> AdapterResult:
        """Выполняет запрос к провайдеру. Бросает структурированные AIError."""


class OpenAICompatibleAdapter(AIProviderAdapter):
    """Адаптер для любого OpenAI-compatible endpoint (chat/completions).

    Таймауты, ретраи с экспоненциальной задержкой, маппинг HTTP-ошибок
    в структурированные типы. HTTP-клиент инжектится для тестируемости.
    """

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client

    def _build_payload(self, request: AdapterRequest, model_id: str) -> dict:
        payload: dict = {
            "model": model_id,
            "messages": [
                {"role": m.role, "content": m.content} for m in request.messages
            ],
        }
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens
        if request.response_format == AIResponseFormat.JSON:
            payload["response_format"] = {"type": "json_object"}
        return payload

    @staticmethod
    def _classify_error(response: httpx.Response) -> AIError | None:
        """Возвращает ошибку для повторной попытки или None при успехе."""
        if response.status_code == 429:
            return AIRateLimitError("Провайдер вернул rate limit (429)")
        if response.status_code in _RETRYABLE_STATUS:
            return AIProviderError(
                f"Провайдер вернул {response.status_code}",
                status_code=response.status_code,
            )
        if response.status_code >= 400:
            # 4xx (кроме 429) не повторяем: ошибка запроса/авторизации.
            return AIProviderError(
                f"Провайдер вернул {response.status_code}: {_safe_error_text(response)}",
                status_code=response.status_code,
            )
        return None

    async def _attempt_once(
        self,
        url: str,
        headers: dict,
        payload: dict,
        connection: EndpointConnection,
        model_id: str,
    ) -> AdapterResult | AIError:
        """Одна попытка: результат либо структурированная ошибка."""
        client = self._client or httpx.AsyncClient()
        try:
            response = await client.post(
                url, headers=headers, json=payload, timeout=connection.timeout_seconds
            )
        except httpx.TimeoutException as exc:
            error: AIError = AITimeoutError(
                f"Таймаут запроса к AI-эндпоинту ({connection.timeout_seconds}s)"
            )
            error.__cause__ = exc
            return error
        except httpx.HTTPError as exc:
            error = AIConnectionError("Не удалось соединиться с AI-эндпоинтом")
            error.__cause__ = exc
            return error
        classified = self._classify_error(response)
        if classified is not None:
            return classified
        return self._parse_response(response, model_id)

    async def generate(
        self,
        request: AdapterRequest,
        connection: EndpointConnection,
        model_id: str,
    ) -> AdapterResult:
        url = connection.base_url.rstrip("/") + "/chat/completions"
        headers = {"Content-Type": "application/json"}
        if connection.api_key:
            headers["Authorization"] = f"Bearer {connection.api_key}"
        payload = self._build_payload(request, model_id)

        started = time.monotonic()
        attempts = connection.max_retries + 1
        last_error: AIError | None = None

        for attempt in range(attempts):
            outcome = await self._attempt_once(url, headers, payload, connection, model_id)
            if isinstance(outcome, AdapterResult):
                outcome.latency_ms = int((time.monotonic() - started) * 1000)
                return outcome
            last_error = outcome
            if (
                isinstance(outcome, AIProviderError)
                and outcome.status_code not in _RETRYABLE_STATUS
            ):
                raise outcome
            if attempt < attempts - 1:
                await asyncio.sleep(_RETRY_BACKOFF_BASE * (2**attempt))

        # Все попытки исчерпаны.
        assert last_error is not None
        raise last_error

    @staticmethod
    def _parse_response(response: httpx.Response, model_id: str) -> AdapterResult:
        try:
            data = response.json()
        except ValueError as exc:
            raise AIInvalidResponseError(
                "Ответ провайдера не является корректным JSON"
            ) from exc

        try:
            choices = data["choices"]
            content = choices[0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AIInvalidResponseError(
                "Ответ провайдера не содержит ожидаемой структуры choices/message"
            ) from exc
        if not isinstance(content, str):
            raise AIInvalidResponseError("Поле content в ответе не является строкой")

        usage = data.get("usage") or {}
        return AdapterResult(
            content=content,
            model=str(data.get("model") or model_id),
            input_tokens=_as_int(usage.get("prompt_tokens")),
            output_tokens=_as_int(usage.get("completion_tokens")),
            total_tokens=_as_int(usage.get("total_tokens")),
            raw_metadata={"id": data.get("id"), "finish_reason": _finish_reason(data)},
        )


def _finish_reason(data: dict) -> str | None:
    try:
        return data["choices"][0].get("finish_reason")
    except (KeyError, IndexError, TypeError):
        return None


def _as_int(value) -> int | None:
    return int(value) if isinstance(value, (int, float)) else None


def _safe_error_text(response: httpx.Response) -> str:
    """Текст ошибки провайдера без чувствительных данных (обрезанный)."""
    try:
        text = response.text
    except Exception:  # noqa: BLE001
        return ""
    return text[:300]


class ProviderAdapterRegistry:
    """Реестр адаптеров по протоколам.

    Добавление нового протокола — registry.register(...), без изменения
    доменной логики. Незарегистрированный протокол → явная ошибка.
    """

    def __init__(self) -> None:
        self._adapters: dict[AIProtocol, AIProviderAdapter] = {}

    def register(self, protocol: AIProtocol, adapter: AIProviderAdapter) -> None:
        self._adapters[protocol] = adapter

    def get(self, protocol: AIProtocol) -> AIProviderAdapter:
        adapter = self._adapters.get(protocol)
        if adapter is None:
            raise AIUnsupportedProtocolError(
                f"Для протокола '{protocol.value}' не зарегистрирован адаптер"
            )
        return adapter

    def protocols(self) -> list[str]:
        return [p.value for p in self._adapters]


def build_default_registry(
    http_client: httpx.AsyncClient | None = None,
) -> ProviderAdapterRegistry:
    """Реестр по умолчанию: только реально работающий openai_compatible.

    anthropic/custom намеренно НЕ регистрируются — фиктивных адаптеров нет.
    """
    registry = ProviderAdapterRegistry()
    registry.register(AIProtocol.OPENAI_COMPATIBLE, OpenAICompatibleAdapter(http_client))
    return registry
