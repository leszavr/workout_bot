"""Unit-тесты пробы готовности модели.

Проба нужна потому, что отказ модели обнаруживался слишком поздно: на staging
сломанная модель рвала соединение через 30-90 с, другая возвращала структуру без
`choices` через 601 с. Две таких модели исчерпывали бюджет генерации, и до
рабочих в конце цепочки дело не доходило.
"""
from __future__ import annotations

import pytest

from src.application.ai.model_probe import ModelProbeService, ProbeVerdict
from src.application.ai.selection import ModelCandidate
from src.domain.ai.config import AIEndpoint, AIModel, AIProvider
from src.domain.ai.enums import AIProtocol
from src.domain.ai.errors import AIConnectionError, AIInvalidResponseError
from src.infrastructure.ai.adapters import AdapterResult

pytestmark = pytest.mark.asyncio


def _candidate(model_pk: int = 1, model_id: str = "test-model") -> ModelCandidate:
    return ModelCandidate(
        model=AIModel(
            id=model_pk, endpoint_id=10, model_id=model_id, display_name=model_id
        ),
        endpoint=AIEndpoint(
            id=10,
            provider_id=1,
            name="E",
            base_url="https://x.example/v1",
            secret_reference="ref",
        ),
        provider=AIProvider(
            id=1, name="P", slug="p", protocol=AIProtocol.OPENAI_COMPATIBLE
        ),
        priority=1,
        is_primary=True,
    )


class FakeAdapter:
    """Адаптер со сценарием ответов на пробу."""

    def __init__(self, outcomes: list) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[str] = []

    async def probe(self, connection, model_id: str):
        self.calls.append(model_id)
        outcome = self.outcomes.pop(0) if self.outcomes else _ok()
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _ok() -> AdapterResult:
    return AdapterResult(content="готов", model="test-model")


class FakeRegistry:
    def __init__(self, adapter) -> None:
        self.adapter = adapter

    def get(self, protocol):
        return self.adapter


class FakeSecrets:
    async def get(self, reference: str) -> str | None:
        return "secret-value"


class FakeClock:
    """Управляемое время: TTL проверяется без ожидания."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _service(adapter, *, ttl: int = 300, clock=None) -> ModelProbeService:
    return ModelProbeService(
        adapter_registry=FakeRegistry(adapter),
        secret_store=FakeSecrets(),
        failure_ttl_seconds=ttl,
        clock=clock or FakeClock(),
    )


# --- Основное поведение -----------------------------------------------------------


async def test_working_model_is_available():
    adapter = FakeAdapter([_ok()])
    verdict = await _service(adapter).check(_candidate())

    assert verdict.available is True
    assert verdict.cached is False
    assert adapter.calls == ["test-model"]


async def test_broken_model_is_not_available():
    """Отказ пробы несёт класс ошибки: по нему видно, что именно сломано."""
    adapter = FakeAdapter([AIInvalidResponseError("нет структуры choices/message")])
    verdict = await _service(adapter).check(_candidate())

    assert verdict.available is False
    assert verdict.error_type == "AIInvalidResponseError"
    assert "choices" in verdict.detail


async def test_probe_uses_the_model_being_checked():
    """Пробуется именно та модель, а не подключение целиком.

    Проверка подключения в обоих наблюдавшихся отказах отвечала успехом:
    неисправна была модель.
    """
    adapter = FakeAdapter([_ok()])
    await _service(adapter).check(_candidate(model_id="z-ai/glm-5.3-flash"))

    assert adapter.calls == ["z-ai/glm-5.3-flash"]


# --- Кеш отказов ------------------------------------------------------------------


async def test_failure_is_remembered_and_not_reprobed():
    """Мёртвая модель не пробуется повторно в пределах TTL.

    Иначе в прогоне из двадцати анкет проба сломанной модели выполнялась бы
    двадцать раз.
    """
    adapter = FakeAdapter([AIConnectionError("нет соединения")])
    service = _service(adapter)
    candidate = _candidate()

    first = await service.check(candidate)
    second = await service.check(candidate)

    assert first.available is False and first.cached is False
    assert second.available is False and second.cached is True
    # Второй раз к адаптеру не обращались.
    assert len(adapter.calls) == 1


async def test_success_is_not_cached():
    """Удачная проба не кешируется: сразу за ней идёт настоящий запрос.

    Кеш успеха ничего не экономил бы, зато мог подтверждать работоспособность
    модели, которая уже упала.
    """
    adapter = FakeAdapter([_ok(), _ok()])
    service = _service(adapter)
    candidate = _candidate()

    await service.check(candidate)
    await service.check(candidate)

    assert len(adapter.calls) == 2


async def test_failure_expires_after_ttl():
    """Восстановившаяся модель возвращается в работу без вмешательства."""
    clock = FakeClock()
    adapter = FakeAdapter([AIConnectionError("нет соединения"), _ok()])
    service = _service(adapter, ttl=300, clock=clock)
    candidate = _candidate()

    assert (await service.check(candidate)).available is False
    clock.advance(301)
    assert (await service.check(candidate)).available is True
    assert len(adapter.calls) == 2


async def test_forget_clears_failure_immediately():
    """Администратору не нужно ждать TTL после починки провайдера."""
    adapter = FakeAdapter([AIConnectionError("нет соединения"), _ok()])
    service = _service(adapter)
    candidate = _candidate()

    await service.check(candidate)
    service.forget(candidate.model.id)

    assert (await service.check(candidate)).available is True


async def test_failure_cache_is_per_model():
    """Отказ одной модели не перекрывает другие."""
    adapter = FakeAdapter([AIConnectionError("нет соединения"), _ok()])
    service = _service(adapter)

    assert (await service.check(_candidate(1, "broken"))).available is False
    assert (await service.check(_candidate(2, "working"))).available is True


# --- Устойчивость -----------------------------------------------------------------


async def test_unexpected_probe_error_treats_model_as_available():
    """Дефект в пробе не должен перекрывать работоспособную модель.

    Цена ошибки асимметрична: ложный отказ отнимает модель, ложное разрешение
    лишь возвращает поведение к тому, что было до пробы.
    """
    adapter = FakeAdapter([RuntimeError("ошибка в самой пробе")])
    verdict = await _service(adapter).check(_candidate())

    assert verdict.available is True


async def test_unexpected_error_is_not_cached():
    """Сбой пробы не запоминается: он ничего не говорит о модели."""
    adapter = FakeAdapter([RuntimeError("сбой"), _ok()])
    service = _service(adapter)
    candidate = _candidate()

    await service.check(candidate)
    await service.check(candidate)

    assert len(adapter.calls) == 2
