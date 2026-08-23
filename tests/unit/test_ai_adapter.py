"""Unit-тесты ProviderAdapterRegistry и OpenAICompatibleAdapter.

HTTP-транспорт мокируется через httpx.MockTransport: проверяются
success, timeout, connection error, 429, 5xx, invalid JSON, provider error,
а также выбор адаптера по протоколу.
"""
from __future__ import annotations

import httpx
import pytest

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
from src.infrastructure.ai.adapters import (
    AdapterRequest,
    AIProviderAdapter,
    EndpointConnection,
    OpenAICompatibleAdapter,
    ProviderAdapterRegistry,
    build_default_registry,
)

_CONNECTION = EndpointConnection(
    base_url="https://ai.example.com/v1",
    api_key="sk-test-secret-key-1234",
    timeout_seconds=5,
    max_retries=0,
)

_REQUEST = AdapterRequest(
    messages=[AIMessage(role="user", content="hello")],
    temperature=0.5,
    max_tokens=100,
)


def _openai_response(content: str = "hi", usage: dict | None = None) -> dict:
    return {
        "id": "chatcmpl-1",
        "model": "some-model",
        "choices": [
            {"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}
        ],
        "usage": usage or {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12},
    }


def _adapter_with(handler) -> OpenAICompatibleAdapter:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    return OpenAICompatibleAdapter(client)


# --- Registry -------------------------------------------------------------------


def test_registry_selects_adapter_by_protocol():
    registry = build_default_registry()
    adapter = registry.get(AIProtocol.OPENAI_COMPATIBLE)
    assert isinstance(adapter, OpenAICompatibleAdapter)


def test_registry_unknown_protocol_raises():
    registry = ProviderAdapterRegistry()
    with pytest.raises(AIUnsupportedProtocolError):
        registry.get(AIProtocol.ANTHROPIC)


def test_registry_adapter_can_be_replaced():
    class FakeAdapter:
        pass

    registry = ProviderAdapterRegistry()
    fake = FakeAdapter()
    registry.register(AIProtocol.OPENAI_COMPATIBLE, fake)  # type: ignore[arg-type]
    assert registry.get(AIProtocol.OPENAI_COMPATIBLE) is fake


def test_default_registry_has_no_fake_adapters():
    """anthropic/custom намеренно не зарегистрированы (нет фиктивных реализаций)."""
    registry = build_default_registry()
    assert registry.protocols() == ["openai_compatible"]


# --- Adapter: success ------------------------------------------------------------


async def test_adapter_success_parses_content_and_usage():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("Authorization")
        captured["user_agent"] = request.headers.get("User-Agent")
        return httpx.Response(200, json=_openai_response("ответ"))

    adapter = _adapter_with(handler)
    result = await adapter.generate(_REQUEST, _CONNECTION, "some-model")

    assert result.content == "ответ"
    assert result.model == "some-model"
    assert result.input_tokens == 5
    assert result.output_tokens == 7
    assert result.total_tokens == 12
    assert result.latency_ms is not None
    assert captured["url"] == "https://ai.example.com/v1/chat/completions"
    assert captured["auth"] == "Bearer sk-test-secret-key-1234"
    # Свой User-Agent обязателен: клиенты по умолчанию httpx отклоняет
    # защитный контур части сервисов (Cloudflare отвечает HTML-страницей).
    assert captured["user_agent"] and "httpx" not in captured["user_agent"]


async def test_adapter_json_response_format_flag():
    def handler(request: httpx.Request) -> httpx.Response:
        import json as json_mod

        body = json_mod.loads(request.content)
        assert body["response_format"] == {"type": "json_object"}
        return httpx.Response(200, json=_openai_response("{}"))

    adapter = _adapter_with(handler)
    request = _REQUEST.model_copy(update={"response_format": AIResponseFormat.JSON})
    result = await adapter.generate(request, _CONNECTION, "some-model")
    assert result.content == "{}"


async def test_adapter_missing_usage_gives_none_tokens():
    def handler(request: httpx.Request) -> httpx.Response:
        data = _openai_response()
        data.pop("usage")
        return httpx.Response(200, json=data)

    adapter = _adapter_with(handler)
    result = await adapter.generate(_REQUEST, _CONNECTION, "some-model")
    assert result.input_tokens is None
    assert result.output_tokens is None
    assert result.total_tokens is None


# --- Adapter: errors ---------------------------------------------------------------


async def test_adapter_timeout_raises_structured_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timed out")

    adapter = _adapter_with(handler)
    with pytest.raises(AITimeoutError):
        await adapter.generate(_REQUEST, _CONNECTION, "some-model")


async def test_adapter_connection_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    adapter = _adapter_with(handler)
    with pytest.raises(AIConnectionError):
        await adapter.generate(_REQUEST, _CONNECTION, "some-model")


async def test_adapter_rate_limit_429():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "rate limited"})

    adapter = _adapter_with(handler)
    with pytest.raises(AIRateLimitError):
        await adapter.generate(_REQUEST, _CONNECTION, "some-model")


async def test_adapter_5xx_provider_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="service unavailable")

    adapter = _adapter_with(handler)
    with pytest.raises(AIProviderError) as exc_info:
        await adapter.generate(_REQUEST, _CONNECTION, "some-model")
    assert exc_info.value.status_code == 503


async def test_adapter_4xx_not_retried():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(401, json={"error": "invalid api key"})

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    adapter = OpenAICompatibleAdapter(client)
    connection = EndpointConnection(
        base_url="https://ai.example.com/v1",
        api_key="bad",
        timeout_seconds=5,
        max_retries=3,
    )
    with pytest.raises(AIProviderError) as exc_info:
        await adapter.generate(_REQUEST, connection, "some-model")
    assert exc_info.value.status_code == 401
    assert calls["count"] == 1  # 4xx не повторяем


async def test_adapter_retries_on_5xx_then_succeeds():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] < 2:
            return httpx.Response(502, text="bad gateway")
        return httpx.Response(200, json=_openai_response("ok"))

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    adapter = OpenAICompatibleAdapter(client)
    connection = EndpointConnection(
        base_url="https://ai.example.com/v1",
        timeout_seconds=5,
        max_retries=2,
    )
    result = await adapter.generate(_REQUEST, connection, "some-model")
    assert result.content == "ok"
    assert calls["count"] == 2


async def test_adapter_invalid_json_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not-json{{{")

    adapter = _adapter_with(handler)
    with pytest.raises(AIInvalidResponseError):
        await adapter.generate(_REQUEST, _CONNECTION, "some-model")


async def test_adapter_missing_choices_structure():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": True})

    adapter = _adapter_with(handler)
    with pytest.raises(AIInvalidResponseError):
        await adapter.generate(_REQUEST, _CONNECTION, "some-model")


async def test_adapter_content_of_unknown_type_is_rejected():
    def handler(request: httpx.Request) -> httpx.Response:
        data = _openai_response()
        data["choices"][0]["message"]["content"] = 12345
        return httpx.Response(200, json=data)

    adapter = _adapter_with(handler)
    with pytest.raises(AIInvalidResponseError):
        await adapter.generate(_REQUEST, _CONNECTION, "some-model")


# --- Формы поля content у разных провайдеров -------------------------------------
#
# Ответ без текста — это успешный ответ, а не сбой транспорта. Именно на этом
# спотыкалась проверка связи: она просит один токен, «размышляющая» модель
# тратит его на размышления и возвращает content: null.


async def test_adapter_null_content_is_empty_string_not_error():
    def handler(request: httpx.Request) -> httpx.Response:
        data = _openai_response()
        data["choices"][0]["message"]["content"] = None
        data["choices"][0]["finish_reason"] = "length"
        return httpx.Response(200, json=data)

    adapter = _adapter_with(handler)
    result = await adapter.generate(_REQUEST, _CONNECTION, "some-model")

    assert result.content == ""
    assert result.raw_metadata["finish_reason"] == "length"


async def test_adapter_content_missing_entirely_is_empty_string():
    def handler(request: httpx.Request) -> httpx.Response:
        data = _openai_response()
        del data["choices"][0]["message"]["content"]
        return httpx.Response(200, json=data)

    adapter = _adapter_with(handler)
    result = await adapter.generate(_REQUEST, _CONNECTION, "some-model")

    assert result.content == ""


async def test_adapter_content_as_parts_is_joined():
    def handler(request: httpx.Request) -> httpx.Response:
        data = _openai_response()
        data["choices"][0]["message"]["content"] = [
            {"type": "text", "text": '{"a": '},
            {"type": "text", "text": "1}"},
        ]
        return httpx.Response(200, json=data)

    adapter = _adapter_with(handler)
    result = await adapter.generate(_REQUEST, _CONNECTION, "some-model")

    assert result.content == '{"a": 1}'


async def test_adapter_message_not_object_is_rejected():
    def handler(request: httpx.Request) -> httpx.Response:
        data = _openai_response()
        data["choices"][0]["message"] = "hi"
        return httpx.Response(200, json=data)

    adapter = _adapter_with(handler)
    with pytest.raises(AIInvalidResponseError):
        await adapter.generate(_REQUEST, _CONNECTION, "some-model")


# --- Отказ защитного контура vs отказ авторизации ---------------------------------
#
# Сервисы за Cloudflare периодически отклоняют совершенно корректные запросы
# пустым 403. Это случайный отказ края, а не «неверный ключ»: повтор помогает.
# Настоящий отказ по ключу приходит с JSON-описанием и повторяться не должен.


async def test_empty_403_is_retried_then_succeeds():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] < 3:
            return httpx.Response(
                403, content=b"", headers={"content-type": "application/octet-stream"}
            )
        return httpx.Response(200, json=_openai_response("ok"))

    adapter = _adapter_with(handler)
    connection = EndpointConnection(
        base_url="https://ai.example.com/v1", timeout_seconds=5, max_retries=2
    )
    result = await adapter.generate(_REQUEST, connection, "some-model")
    assert result.content == "ok"
    assert calls["count"] == 3


async def test_html_403_challenge_is_retryable():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(
            403,
            text="<!DOCTYPE html><html>Attention Required</html>",
            headers={"content-type": "text/html; charset=UTF-8"},
        )

    adapter = _adapter_with(handler)
    connection = EndpointConnection(
        base_url="https://ai.example.com/v1", timeout_seconds=5, max_retries=1
    )
    with pytest.raises(AIProviderError) as exc_info:
        await adapter.generate(_REQUEST, connection, "some-model")
    assert exc_info.value.status_code == 403
    assert calls["count"] == 2


async def test_json_403_is_not_retried():
    """Отказ API по ключу или квоте повторять бессмысленно."""
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(403, json={"error": {"code": "insufficient_user_quota"}})

    adapter = _adapter_with(handler)
    connection = EndpointConnection(
        base_url="https://ai.example.com/v1", timeout_seconds=5, max_retries=3
    )
    with pytest.raises(AIProviderError) as exc_info:
        await adapter.generate(_REQUEST, connection, "some-model")
    assert exc_info.value.status_code == 403
    assert calls["count"] == 1


async def test_html_401_from_edge_is_retryable():
    """Правило общее для статусов, а не для одного кода.

    OpenAI-совместимый API отвечает JSON, поэтому HTML-страница с 401 — это
    отказ защиты перед сервисом, а не «неверный ключ».
    """
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(
                401,
                text="<!DOCTYPE html><html>Attention Required</html>",
                headers={"content-type": "text/html"},
            )
        return httpx.Response(200, json=_openai_response("ok"))

    adapter = _adapter_with(handler)
    connection = EndpointConnection(
        base_url="https://ai.example.com/v1", timeout_seconds=5, max_retries=2
    )
    result = await adapter.generate(_REQUEST, connection, "some-model")
    assert result.content == "ok"
    assert calls["count"] == 2


async def test_json_401_is_not_retried():
    """Настоящий отказ по ключу приходит от API в JSON и повторов не требует."""
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(401, json={"error": {"message": "Invalid token"}})

    adapter = _adapter_with(handler)
    connection = EndpointConnection(
        base_url="https://ai.example.com/v1", timeout_seconds=5, max_retries=3
    )
    with pytest.raises(AIProviderError) as exc_info:
        await adapter.generate(_REQUEST, connection, "some-model")
    assert calls["count"] == 1
    assert "Invalid token" in str(exc_info.value)


# --- Сообщения об ошибках -----------------------------------------------------------
#
# Сообщение видит администратор при подключении сервиса, поэтому оно должно
# называть причину, а не показывать сырой ответ.


async def test_html_rejection_message_has_no_raw_html():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            text="<!DOCTYPE html><!--[if lt IE 7]> <html class='no-js ie6'>...",
            headers={"content-type": "text/html; charset=UTF-8"},
        )

    adapter = _adapter_with(handler)
    connection = EndpointConnection(
        base_url="https://ai.example.com/v1", timeout_seconds=5, max_retries=0
    )
    with pytest.raises(AIProviderError) as exc_info:
        await adapter.generate(_REQUEST, connection, "some-model")
    message = str(exc_info.value)
    assert "DOCTYPE" not in message and "<html" not in message
    assert "403" in message


async def test_api_error_message_is_extracted_from_any_shape():
    """Форма JSON-ошибки у провайдеров разная — сообщение должно находиться."""
    shapes = [
        {"error": {"message": "Invalid token"}},
        {"error": {"code": "insufficient_user_quota"}},
        {"error": "plain text error"},
        {"message": "top level message"},
        {"detail": "detail field"},
    ]
    for payload in shapes:
        def handler(request: httpx.Request, payload=payload) -> httpx.Response:
            return httpx.Response(401, json=payload)

        adapter = _adapter_with(handler)
        connection = EndpointConnection(
            base_url="https://ai.example.com/v1", timeout_seconds=5, max_retries=0
        )
        with pytest.raises(AIProviderError) as exc_info:
            await adapter.generate(_REQUEST, connection, "some-model")
        message = str(exc_info.value)
        expected = (
            payload.get("error")
            if isinstance(payload.get("error"), str)
            else (
                (payload.get("error") or {}).get("message")
                or (payload.get("error") or {}).get("code")
                if isinstance(payload.get("error"), dict)
                else payload.get("message") or payload.get("detail")
            )
        )
        assert expected in message, f"{payload} → {message}"


async def test_404_explains_wrong_base_url():
    """Типичная ошибка настройки: адрес без /v1 или не тот сервис."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="Not Found", headers={"content-type": "text/plain"})

    adapter = _adapter_with(handler)
    connection = EndpointConnection(
        base_url="https://ai.example.com", timeout_seconds=5, max_retries=0
    )
    with pytest.raises(AIProviderError) as exc_info:
        await adapter.generate(_REQUEST, connection, "some-model")
    assert "адрес" in str(exc_info.value).lower()


async def test_404_is_not_retried():
    """Неверный адрес повторами не исправить."""
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(404, text="Not Found", headers={"content-type": "text/plain"})

    adapter = _adapter_with(handler)
    connection = EndpointConnection(
        base_url="https://ai.example.com", timeout_seconds=5, max_retries=3
    )
    with pytest.raises(AIProviderError):
        await adapter.generate(_REQUEST, connection, "some-model")
    assert calls["count"] == 1


async def test_non_json_400_from_edge_is_retried():
    """Балансировщик иногда отвечает «400 Bad Request» простым текстом.

    Настоящую ошибку запроса API описывает в JSON, поэтому текстовый 400 —
    это отказ контура перед сервисом, и его стоит повторить.
    """
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(
                400, text="400 Bad Request", headers={"content-type": "text/plain"}
            )
        return httpx.Response(200, json=_openai_response("ok"))

    adapter = _adapter_with(handler)
    connection = EndpointConnection(
        base_url="https://ai.example.com/v1", timeout_seconds=5, max_retries=2
    )
    result = await adapter.generate(_REQUEST, connection, "some-model")
    assert result.content == "ok"
    assert calls["count"] == 2


async def test_json_400_from_api_is_not_retried():
    """Ошибку в параметрах запроса повторять бессмысленно."""
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(
            400, json={"error": {"message": "unsupported parameter: top_k"}}
        )

    adapter = _adapter_with(handler)
    connection = EndpointConnection(
        base_url="https://ai.example.com/v1", timeout_seconds=5, max_retries=3
    )
    with pytest.raises(AIProviderError) as exc_info:
        await adapter.generate(_REQUEST, connection, "some-model")
    assert calls["count"] == 1
    assert "top_k" in str(exc_info.value)


async def test_retry_after_header_is_respected(monkeypatch):
    """Паузу, о которой попросил сервис, берём из Retry-After."""
    delays: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        delays.append(seconds)

    monkeypatch.setattr("src.infrastructure.ai.adapters.asyncio.sleep", fake_sleep)

    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(429, json={"error": "slow down"}, headers={"retry-after": "7"})
        return httpx.Response(200, json=_openai_response("ok"))

    adapter = _adapter_with(handler)
    connection = EndpointConnection(
        base_url="https://ai.example.com/v1", timeout_seconds=5, max_retries=2
    )
    result = await adapter.generate(_REQUEST, connection, "some-model")
    assert result.content == "ok"
    assert delays == [7.0]


async def test_retry_delay_is_capped(monkeypatch):
    """Пауза не растёт бесконечно: иначе таймаут задачи уходит на ожидание."""
    delays: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        delays.append(seconds)

    monkeypatch.setattr("src.infrastructure.ai.adapters.asyncio.sleep", fake_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="overloaded")

    adapter = _adapter_with(handler)
    connection = EndpointConnection(
        base_url="https://ai.example.com/v1", timeout_seconds=5, max_retries=5
    )
    with pytest.raises(AIProviderError):
        await adapter.generate(_REQUEST, connection, "some-model")
    assert delays == sorted(delays)  # пауза растёт
    assert max(delays) <= 20.0  # но не безгранично


# --- Перечисление доступных моделей ------------------------------------------------


async def test_list_models_parses_openai_format():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["user_agent"] = request.headers.get("User-Agent")
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "qwen/qwen3-max:free", "owned_by": "custom"},
                    {"id": "claude-opus-5"},
                    {"id": "qwen/qwen3-max:free"},  # дубликат отбрасывается
                    {"id": ""},  # пустое значение игнорируется
                ]
            },
        )

    adapter = _adapter_with(handler)
    models = await adapter.list_models(_CONNECTION)

    assert captured["url"] == "https://ai.example.com/v1/models"
    assert captured["user_agent"] and "httpx" not in captured["user_agent"]
    assert [m.model_id for m in models] == ["qwen/qwen3-max:free", "claude-opus-5"]
    # Идентификатор передаётся как есть, а показывается человеку короткое имя.
    assert models[0].display_name == "qwen3-max"
    assert models[0].owned_by == "custom"


async def test_list_models_rejects_unexpected_structure():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": ["a", "b"]})

    adapter = _adapter_with(handler)
    with pytest.raises(AIInvalidResponseError):
        await adapter.list_models(_CONNECTION)


async def test_list_models_explains_html_page_instead_of_api():
    """Частая ошибка настройки: в base_url забыли /v1 и отвечает сайт."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text="<!DOCTYPE html><html><body>Landing page</body></html>",
            headers={"content-type": "text/html; charset=utf-8"},
        )

    adapter = _adapter_with(handler)
    with pytest.raises(AIInvalidResponseError) as exc_info:
        await adapter.list_models(_CONNECTION)
    message = str(exc_info.value)
    assert "/v1" in message
    assert "<html" not in message


async def test_list_models_maps_provider_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid api key"})

    adapter = _adapter_with(handler)
    with pytest.raises(AIProviderError) as exc_info:
        await adapter.list_models(_CONNECTION)
    assert exc_info.value.status_code == 401


async def test_list_models_retries_edge_rejection():
    """Пустой 403 от защитного контура не должен выглядеть как «моделей нет»."""
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(
                403, content=b"", headers={"content-type": "application/octet-stream"}
            )
        return httpx.Response(200, json={"data": [{"id": "m-1"}]})

    adapter = _adapter_with(handler)
    connection = EndpointConnection(
        base_url="https://ai.example.com/v1", timeout_seconds=5, max_retries=1
    )
    models = await adapter.list_models(connection)
    assert [m.model_id for m in models] == ["m-1"]
    assert calls["count"] == 2


async def test_list_models_unsupported_protocol_by_default():
    """Адаптер, не умеющий перечислять модели, говорит об этом явно."""

    class BareAdapter(AIProviderAdapter):
        async def generate(self, request, connection, model_id):  # noqa: ANN001
            raise NotImplementedError

    with pytest.raises(AIUnsupportedProtocolError):
        await BareAdapter().list_models(_CONNECTION)
