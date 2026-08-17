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


async def test_adapter_content_not_string():
    def handler(request: httpx.Request) -> httpx.Response:
        data = _openai_response()
        data["choices"][0]["message"]["content"] = 12345
        return httpx.Response(200, json=data)

    adapter = _adapter_with(handler)
    with pytest.raises(AIInvalidResponseError):
        await adapter.generate(_REQUEST, _CONNECTION, "some-model")
