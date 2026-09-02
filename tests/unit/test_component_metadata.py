"""Тесты metadata компонентов и клиента heartbeat.

Проверяется то, что ломается молча: состав metadata (в неё не должны попасть
секреты), идемпотентность повторной отправки и то, что недоступный Backend не
останавливает компонент.
"""
from __future__ import annotations

import logging

import httpx
import pytest

from apps.telegram_gateway.component import (
    GATEWAY_CAPABILITIES,
    GATEWAY_CONTRACT_VERSION,
    gateway_metadata,
)
from src.domain.components import Capability, ComponentStatus, ComponentType
from src.infrastructure.components.heartbeat_client import (
    SERVICE_TOKEN_HEADER,
    ComponentHeartbeatClient,
)
from src.version import APP_VERSION, GATEWAY_VERSION

SECRET_TOKEN = "test-service-token"


def _client(handler, *, metadata=None) -> ComponentHeartbeatClient:
    transport = httpx.MockTransport(handler)
    return ComponentHeartbeatClient(
        base_url="http://backend:8000",
        service_token=SECRET_TOKEN,
        metadata=metadata or gateway_metadata(),
        client=httpx.AsyncClient(transport=transport),
    )


def _ok(payload: dict | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=payload
            or {
                "accepted": True,
                "component_id": "telegram-local-1",
                "compatibility": {"state": "compatible", "detail": "ok"},
            },
        )

    return handler


# --- Component metadata ----------------------------------------------------


def test_gateway_metadata_reports_version_build_and_contract():
    metadata = gateway_metadata()
    assert metadata.component_type is ComponentType.TELEGRAM_GATEWAY
    assert metadata.version == GATEWAY_VERSION
    assert metadata.contract_version == GATEWAY_CONTRACT_VERSION
    assert metadata.capabilities == GATEWAY_CAPABILITIES
    assert Capability.TELEGRAM_DELIVERY in metadata.capabilities


def test_gateway_version_is_independent_from_backend():
    """Версии не связаны: обновление Backend не требует развёртывания EU.

    Совместимость определяет `contract_version`. Совпадение версий было бы
    ложным критерием — тогда любая правка предметной логики требовала бы
    переразвёртывания шлюза.
    """
    assert GATEWAY_VERSION != APP_VERSION


def test_metadata_payload_contains_no_secrets():
    """Metadata уходит в реестр и в Admin UI: секретов там быть не может."""
    payload = gateway_metadata().model_dump(mode="json")
    forbidden = {"token", "secret", "password", "api_key", "bot_token", "dsn", "url"}
    assert not forbidden & {key.lower() for key in payload}


def test_status_is_explicit_not_guessed():
    metadata = gateway_metadata(status=ComponentStatus.DEGRADED)
    assert metadata.status is ComponentStatus.DEGRADED


# --- Heartbeat client ------------------------------------------------------


async def test_heartbeat_sends_service_token_header():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["token"] = request.headers.get(SERVICE_TOKEN_HEADER)
        seen["path"] = request.url.path
        return httpx.Response(200, json={"accepted": True, "compatibility": {}})

    client = _client(handler)
    assert await client.send_once() is not None
    assert seen["token"] == SECRET_TOKEN
    assert seen["path"] == "/internal/v1/components/heartbeat"


async def test_repeated_heartbeat_sends_identical_payload():
    """Повтор безопасен: компонент не хранит состояние регистрации."""
    payloads: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(httpx.Request("POST", request.url, content=request.content).read().decode())
        return httpx.Response(200, json={"accepted": True, "compatibility": {}})

    client = _client(handler)
    await client.send_once()
    await client.send_once()
    assert payloads[0] == payloads[1]


async def test_backend_unavailable_does_not_raise():
    """Мониторинг не должен становиться точкой отказа бизнес-функции."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("backend unreachable")

    assert await _client(handler).send_once() is None


async def test_rejected_heartbeat_returns_none():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "Invalid service token"})

    assert await _client(handler).send_once() is None


async def test_incompatible_verdict_is_logged_as_error(caplog):
    handler = _ok(
        {
            "accepted": True,
            "compatibility": {
                "state": "update_required",
                "detail": "Контракт компонента v1; Backend поддерживает v2",
            },
        }
    )
    with caplog.at_level(logging.ERROR):
        await _client(handler).send_once()
    assert any("component_incompatible" in r.message for r in caplog.records)


async def test_failure_log_does_not_contain_service_token(caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    with caplog.at_level(logging.WARNING):
        await _client(handler).send_once()
    assert SECRET_TOKEN not in caplog.text
