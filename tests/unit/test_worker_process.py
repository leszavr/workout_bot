"""Цикл и metadata worker-процесса (Phase 1.2-D).

Проверяется то, что относится к процессу, а не к retry-логике: цикл выживает
ошибку прохода, останавливается по сигналу, не ждёт полный интервал при
остановке, и компонент корректно объявляет себя реестру.
"""
from __future__ import annotations

import asyncio

import pytest

from apps.worker.component import WORKER_CONTRACT_VERSION, worker_metadata
from apps.worker.main import run_forever
from src.domain.components import (
    BACKEND_SUPPORTED_CONTRACTS,
    COMPONENT_REQUIREMENTS,
    ComponentStatus,
    ComponentType,
)


class _Coordinator:
    """Считает проходы и по требованию падает."""

    def __init__(self, *, fail_first: bool = False, stop_after: int | None = None,
                 stop: asyncio.Event | None = None) -> None:
        self.cycles = 0
        self._fail_first = fail_first
        self._stop_after = stop_after
        self._stop = stop

    async def run_once(self):
        self.cycles += 1
        if self._stop_after is not None and self.cycles >= self._stop_after:
            assert self._stop is not None
            self._stop.set()
        if self._fail_first and self.cycles == 1:
            raise RuntimeError("проход упал")
        return None


class TestWorkerLoop:
    async def test_stop_interrupts_the_wait(self):
        """Остановка не ждёт интервал: иначе SIGTERM висел бы до таймаута.

        Интервал заведомо огромный, сигнал приходит извне во время ожидания.
        Если бы пауза была обычным `sleep`, тест упал бы по таймауту.
        """
        stop = asyncio.Event()
        coordinator = _Coordinator()

        async def _signal_later() -> None:
            await asyncio.sleep(0.05)
            stop.set()

        cycles, _ = await asyncio.wait_for(
            asyncio.gather(
                run_forever(coordinator, interval_seconds=3600, stop=stop),
                _signal_later(),
            ),
            timeout=2,
        )

        assert cycles == 1
        assert coordinator.cycles == 1

    async def test_loop_runs_repeatedly_until_stopped(self):
        stop = asyncio.Event()
        coordinator = _Coordinator(stop_after=3, stop=stop)

        cycles = await asyncio.wait_for(
            run_forever(coordinator, interval_seconds=0.01, stop=stop), timeout=2
        )

        assert cycles == 3

    async def test_already_stopped_loop_does_no_work(self):
        stop = asyncio.Event()
        stop.set()
        coordinator = _Coordinator()

        assert await run_forever(coordinator, interval_seconds=1, stop=stop) == 0
        assert coordinator.cycles == 0

    async def test_loop_dies_on_unhandled_error(self):
        """Проход обязан ловить свои ошибки сам.

        Тест фиксирует границу ответственности: `run_forever` не глотает
        исключения, потому что молчаливое их подавление здесь скрыло бы дефект
        координатора. Перехват живёт в `RetryCoordinator.run_once`, где известно,
        какая именно операция упала.
        """
        stop = asyncio.Event()
        with pytest.raises(RuntimeError):
            await run_forever(
                _Coordinator(fail_first=True), interval_seconds=0.01, stop=stop
            )


class TestWorkerComponent:
    def test_metadata_declares_worker_type(self):
        meta = worker_metadata()
        assert meta.component_type is ComponentType.WORKER
        assert meta.contract_version == WORKER_CONTRACT_VERSION
        assert meta.status is ComponentStatus.HEALTHY
        # Worker ничего не предоставляет другим компонентам: он обрабатывает
        # свою очередь, а не принимает запросы.
        assert meta.capabilities == []

    def test_contract_matches_backend(self):
        """Worker собран из того же пакета: расхождение контрактов бессмысленно."""
        assert WORKER_CONTRACT_VERSION in BACKEND_SUPPORTED_CONTRACTS

    def test_registry_knows_worker_requirements(self):
        assert ComponentType.WORKER in COMPONENT_REQUIREMENTS
        requirement = COMPONENT_REQUIREMENTS[ComponentType.WORKER]
        assert requirement.supported_contracts == BACKEND_SUPPORTED_CONTRACTS
