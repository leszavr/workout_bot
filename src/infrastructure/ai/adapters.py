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
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import httpx
from pydantic import BaseModel, ConfigDict, Field

from src.domain.ai.enums import AIProtocol, AIResponseFormat
from src.domain.ai.errors import (
    AIConnectionError,
    AIError,
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
# Статусы, которыми защитный контур (WAF, прокси, балансировщик) отклоняет
# запрос, не доводя его до API. Отличить такой отказ от отказа самого API можно
# по содержимому: OpenAI-совместимый API всегда отвечает JSON. 404 сюда не
# входит: там причина обычно в неверном адресе, и повтор не поможет.
_EDGE_REJECTION_STATUS = {400, 401, 403, 405, 406, 408, 421, 429}
# Перегрузка шлюза не проходит за доли секунды: слишком частые повторы просто
# тратят таймаут задачи, ничего не меняя.
_RETRY_BACKOFF_BASE = 2.0
_MAX_RETRY_DELAY = 20.0

# Подсказки по типовым отказам API. Формулировки нейтральные: правила общие
# для любого OpenAI-совместимого сервиса, а не для конкретного поставщика.
_STATUS_HINTS = {
    400: "Проверьте адрес подключения и название модели.",
    401: "Проверьте ключ доступа.",
    402: "Проверьте баланс и тариф у поставщика.",
    403: "Проверьте права ключа, баланс и доступность модели по этому ключу.",
    404: "Проверьте адрес подключения и название модели.",
    422: "Сервис не принял параметры запроса.",
    429: "Превышен лимит запросов у поставщика.",
}

# Часть сервисов стоит за Cloudflare, который отклоняет клиенты без
# User-Agent (в ответ приходит HTML challenge, а не JSON API). Свой
# User-Agent — обязательное условие работы с такими сервисами.
_USER_AGENT = "workout-bot/1.0 (+ai-gateway)"


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


@dataclass
class _FailedAttempt:
    """Неудачная попытка запроса.

    Решение «повторять или нет» принимается там, где известен ответ, а не по
    типу исключения: один и тот же 403 может быть отказом авторизации
    (повтор бесполезен) и отказом защитного контура (повтор помогает).
    """

    error: AIError
    retryable: bool
    retry_after: float | None = None


@dataclass
class DiscoveredModel:
    """Модель, о которой сообщил сам сервис (результат GET /models)."""

    model_id: str
    display_name: str
    owned_by: str | None = None


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

    async def list_models(
        self, connection: EndpointConnection
    ) -> list[DiscoveredModel]:
        """Модели, доступные на подключении.

        Протокол может не уметь их перечислять, поэтому по умолчанию —
        явная ошибка вместо пустого списка: «моделей нет» и «спросить нельзя»
        администратору нужно различать.
        """
        raise AIUnsupportedProtocolError(
            "Этот протокол не умеет перечислять доступные модели"
        )


class OpenAICompatibleAdapter(AIProviderAdapter):
    """Адаптер для любого OpenAI-compatible endpoint (chat/completions).

    Таймауты, ретраи с экспоненциальной задержкой, маппинг HTTP-ошибок
    в структурированные типы. HTTP-клиент инжектится для тестируемости.
    """

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client

    @asynccontextmanager
    async def _http(self):
        """HTTP-клиент на время запроса.

        Инжектированный клиент принадлежит вызывающему коду и закрывается им.
        Свой создаём и закрываем сами: незакрытые клиенты держат соединения и
        со временем ломают исходящие запросы в долгоживущем процессе.
        """
        if self._client is not None:
            yield self._client
            return
        async with httpx.AsyncClient() as client:
            yield client

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
    def _classify_error(response: httpx.Response) -> _FailedAttempt | None:
        """Возвращает описание неудачной попытки или None при успехе."""
        status = response.status_code
        if status == 429:
            return _FailedAttempt(
                AIRateLimitError(_provider_error_message(response)),
                retryable=True,
                retry_after=_retry_after_seconds(response),
            )
        if status in _RETRYABLE_STATUS:
            return _FailedAttempt(
                AIProviderError(_provider_error_message(response), status_code=status),
                retryable=True,
                retry_after=_retry_after_seconds(response),
            )
        if _is_edge_rejection(response):
            # Ответ пришёл не от API, а от защитного контура перед ним
            # (Cloudflare и аналоги): HTML-страница или пустое тело вместо
            # JSON. Ключ и запрос при этом корректны, отказ случайный —
            # попытку имеет смысл повторить.
            return _FailedAttempt(
                AIProviderError(_provider_error_message(response), status_code=status),
                retryable=True,
                retry_after=_retry_after_seconds(response),
            )
        if status >= 400:
            # Остальные 4xx не повторяем: это отказ самого API (ключ, квота,
            # неверный запрос) — повтор ничего не изменит.
            return _FailedAttempt(
                AIProviderError(_provider_error_message(response), status_code=status),
                retryable=False,
            )
        return None

    async def _attempt_once(
        self,
        url: str,
        headers: dict,
        payload: dict,
        connection: EndpointConnection,
        model_id: str,
    ) -> AdapterResult | _FailedAttempt:
        """Одна попытка: результат либо описание сбоя."""
        try:
            async with self._http() as client:
                response = await client.post(
                    url, headers=headers, json=payload, timeout=connection.timeout_seconds
                )
        except httpx.TimeoutException as exc:
            error: AIError = AITimeoutError(
                f"сервис не ответил за отведённое время ({connection.timeout_seconds} с)"
            )
            error.__cause__ = exc
            return _FailedAttempt(error, retryable=True)
        except httpx.HTTPError as exc:
            error = AIConnectionError("не удалось соединиться с сервисом ИИ")
            error.__cause__ = exc
            return _FailedAttempt(error, retryable=True)
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
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": _USER_AGENT,
        }
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
            last_error = outcome.error
            if not outcome.retryable:
                raise outcome.error
            if attempt < attempts - 1:
                await asyncio.sleep(_retry_delay(attempt, outcome.retry_after))

        # Все попытки исчерпаны.
        assert last_error is not None
        raise last_error

    async def list_models(
        self, connection: EndpointConnection
    ) -> list[DiscoveredModel]:
        """Список моделей по OpenAI-совместимому GET /models.

        Случайные отказы края (пустой 403, обрыв соединения) повторяются, как и
        при обычном запросе: иначе администратор увидит «список получить не
        удалось» там, где сервис на самом деле доступен.
        """
        url = connection.base_url.rstrip("/") + "/models"
        headers = {"Accept": "application/json", "User-Agent": _USER_AGENT}
        if connection.api_key:
            headers["Authorization"] = f"Bearer {connection.api_key}"

        attempts = connection.max_retries + 1
        last_error: AIError | None = None
        for attempt in range(attempts):
            outcome = await self._fetch_models_once(url, headers, connection)
            if isinstance(outcome, list):
                return outcome
            last_error = outcome.error
            if not outcome.retryable:
                raise outcome.error
            if attempt < attempts - 1:
                await asyncio.sleep(_retry_delay(attempt, outcome.retry_after))

        assert last_error is not None
        raise last_error

    async def _fetch_models_once(
        self, url: str, headers: dict, connection: EndpointConnection
    ) -> list[DiscoveredModel] | _FailedAttempt:
        try:
            async with self._http() as client:
                response = await client.get(
                    url, headers=headers, timeout=connection.timeout_seconds
                )
        except httpx.TimeoutException as exc:
            error: AIError = AITimeoutError(
                f"сервис не ответил за отведённое время ({connection.timeout_seconds} с)"
            )
            error.__cause__ = exc
            return _FailedAttempt(error, retryable=True)
        except httpx.HTTPError as exc:
            error = AIConnectionError("не удалось соединиться с сервисом ИИ")
            error.__cause__ = exc
            return _FailedAttempt(error, retryable=True)

        failure = self._classify_error(response)
        if failure is not None:
            return failure
        return _parse_models_response(response)

    @staticmethod
    def _extract_content(message: dict) -> str | None:
        """Текст ответа из message; None — формат распознать не удалось.

        Провайдеры отдают content по-разному, и все варианты ниже — успешный
        ответ, а не сбой:
        - строка: обычный случай;
        - null: модель не написала текст. Так отвечают «размышляющие»
          модели, когда лимит токенов ушёл на размышления, а также модели,
          вернувшие только вызов инструмента;
        - список частей [{"type": "text", "text": "..."}]: формат
          мультимодальных ответов, его используют многие роутеры.

        Пустой текст здесь не ошибка: пригодность ответа проверяет тот, кто
        его запросил. Для проверки связи достаточно самого факта ответа, а
        для генерации программы пустой ответ отсеет разбор JSON — с понятной
        причиной вместо «поле не того типа».
        """
        content = message.get("content")
        if isinstance(content, str):
            return content
        if content is None:
            return ""
        if isinstance(content, list):
            return "".join(
                part["text"]
                for part in content
                if isinstance(part, dict) and isinstance(part.get("text"), str)
            )
        return None

    @staticmethod
    def _parse_response(response: httpx.Response, model_id: str) -> AdapterResult:
        try:
            data = response.json()
        except ValueError as exc:
            raise AIInvalidResponseError(
                "Ответ провайдера не является корректным JSON"
            ) from exc

        try:
            message = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AIInvalidResponseError(
                "Ответ провайдера не содержит ожидаемой структуры choices/message"
            ) from exc
        if not isinstance(message, dict):
            raise AIInvalidResponseError(
                "Ответ провайдера не содержит ожидаемой структуры choices/message"
            )
        content = OpenAICompatibleAdapter._extract_content(message)
        if content is None:
            raise AIInvalidResponseError(
                "Ответ провайдера содержит текст в неизвестном формате"
            )

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


def _model_display_name(model_id: str) -> str:
    """Читаемое имя из идентификатора: «vendor/qwen3-max:free» → «qwen3-max».

    Идентификаторы у роутеров содержат вендора и суффиксы тарифа, которые в
    списке только мешают. Сам model_id при этом остаётся нетронутым.
    """
    name = model_id.split("/")[-1]
    return name.split(":")[0] or model_id


def _parse_models_response(response: httpx.Response) -> list[DiscoveredModel]:
    """Разбор OpenAI-совместимого ответа GET /models.

    Порядок сохраняется как у сервиса, дубликаты по model_id отбрасываются.
    """
    # Успешный ответ не по адресу API (например, HTML страницы сайта, когда в
    # base_url забыли /v1) — самая частая ошибка настройки, поэтому она
    # объясняется отдельно, а не как «неверный формат».
    if "json" not in response.headers.get("content-type", "").lower():
        raise AIInvalidResponseError(
            "по этому адресу отвечает не OpenAI-совместимый API. "
            "Проверьте адрес подключения: он обычно оканчивается на /v1"
        )
    try:
        data = response.json()
    except ValueError as exc:
        raise AIInvalidResponseError(
            "Список моделей пришёл не в формате JSON"
        ) from exc

    items = data.get("data") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise AIInvalidResponseError(
            "ответ сервиса не содержит списка моделей (ожидается поле data). "
            "Проверьте адрес подключения"
        )

    models: list[DiscoveredModel] = []
    seen: set[str] = set()
    for item in items:
        model_id = item.get("id") if isinstance(item, dict) else item
        if not isinstance(model_id, str) or not model_id.strip():
            continue
        model_id = model_id.strip()
        if model_id in seen:
            continue
        seen.add(model_id)
        owned_by = item.get("owned_by") if isinstance(item, dict) else None
        models.append(
            DiscoveredModel(
                model_id=model_id,
                display_name=_model_display_name(model_id),
                owned_by=owned_by if isinstance(owned_by, str) else None,
            )
        )
    return models


def _as_int(value) -> int | None:
    return int(value) if isinstance(value, (int, float)) else None


def _retry_after_seconds(response: httpx.Response) -> float | None:
    """Пауза, о которой попросил сам сервис (заголовок Retry-After)."""
    raw = response.headers.get("retry-after")
    if not raw:
        return None
    try:
        seconds = float(raw.strip())
    except ValueError:
        return None  # формат HTTP-даты не поддерживаем: пауза берётся своя
    if seconds < 0:
        return None
    return min(seconds, _MAX_RETRY_DELAY)


def _retry_delay(attempt: int, retry_after: float | None) -> float:
    """Просьбу сервиса уважаем, иначе растущая пауза с верхней границей."""
    if retry_after is not None:
        return retry_after
    return min(_RETRY_BACKOFF_BASE * (2**attempt), _MAX_RETRY_DELAY)


def _is_edge_rejection(response: httpx.Response) -> bool:
    """Ответ пришёл не от API, а от защитного контура перед ним.

    Признак универсальный и не привязан ни к одному сервису: OpenAI-совместимый
    API отвечает JSON, поэтому HTML-страница или пустое тело означают, что
    запрос до API не дошёл — его отклонил WAF/прокси (Cloudflare и аналоги).
    Ключ и сам запрос при этом корректны, отказ обычно случайный.
    """
    if response.status_code < 400:
        return False
    if response.status_code not in _EDGE_REJECTION_STATUS:
        return False
    return _error_payload(response) is None


def _error_payload(response: httpx.Response) -> dict | None:
    """JSON-описание ошибки от самого API или None, если ответ не JSON."""
    content_type = response.headers.get("content-type", "").lower()
    if "json" not in content_type:
        return None
    try:
        data = response.json()
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _api_error_text(payload: dict) -> str | None:
    """Текст ошибки из JSON-ответа. Формы у провайдеров разные."""
    error = payload.get("error")
    if isinstance(error, dict):
        for key in ("message", "detail", "code", "type"):
            value = error.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    if isinstance(error, str) and error.strip():
        return error.strip()
    for key in ("message", "detail"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _provider_error_message(response: httpx.Response) -> str:
    """Человекочитаемая причина отказа без сырого HTML и секретов.

    Сообщение видит администратор при подключении сервиса, поэтому оно должно
    объяснять, что проверить. Правила общие для любого OpenAI-совместимого
    сервиса: разбирается стандартная форма JSON-ошибки, а ответ не от API
    описывается отдельно — вываливать в интерфейс HTML-страницу бессмысленно.
    """
    status = response.status_code
    payload = _error_payload(response)
    if payload is not None:
        text = _api_error_text(payload)
        if text:
            detail = f"сервис ответил ошибкой {status}: {text[:200]}"
            hint = _STATUS_HINTS.get(status)
            return f"{detail}. {hint}" if hint else detail
        return f"сервис ответил ошибкой {status} без описания причины"

    if status in _EDGE_REJECTION_STATUS:
        return (
            f"запрос отклонён до обращения к API (HTTP {status}): ответ пришёл "
            "от защиты сервиса, а не от самого сервиса ИИ"
        )
    if status == 404:
        return (
            f"по этому адресу нет OpenAI-совместимого API (HTTP {status}). "
            "Проверьте адрес подключения: он обычно оканчивается на /v1"
        )
    hint = _STATUS_HINTS.get(status)
    detail = f"сервис ответил {status} без описания причины"
    return f"{detail}. {hint}" if hint else detail


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
