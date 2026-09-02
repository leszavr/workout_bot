"""Фоновый исполнитель: retry и recovery (Phase 1.2-D).

    python -m apps.worker.main

Зачем отдельный процесс, а не задача внутри Backend. Backend обслуживает
HTTP-запросы, и его перезапуск при деплое админки останавливал бы обработку
повторов; масштабировать одно вместе с другим тоже пришлось бы принудительно.
Отдельный контейнер виден в compose, перезапускается независимо и попадает в
Component Registry как самостоятельный компонент — администратор видит, работает
исполнитель или нет.

Внешний cron не выбран по той же причине наблюдаемости: он не отвечает на
вопрос «выполняется ли обработка сейчас», не имеет собственного состояния и
добавляет зависимость от хоста, которую нельзя проверить из приложения.

Состояние процесса нулевое: очередь повторов, номера попыток и аренда лежат в
PostgreSQL. Worker можно убить в любой момент — после рестарта он найдёт свои
незакрытые job'ы по просроченной аренде и вернёт их в очередь.

Redis здесь не используется вовсе: взаимное исключение обеспечивает
`SELECT ... FOR UPDATE SKIP LOCKED` в PostgreSQL, поэтому потеря Redis не
влияет на обработку.
"""
from __future__ import annotations

import asyncio
import logging
import signal

from src.application.programs.retry_service import RetryCoordinator
from src.infrastructure.components.heartbeat_client import ComponentHeartbeatClient
from src.infrastructure.config import (
    DATABASE_URL,
    WORKER_COMPONENT_ID,
    WORKER_POLL_INTERVAL_SECONDS,
)
from src.infrastructure.logging_setup import setup_logging

logger = logging.getLogger(__name__)


async def run_forever(
    coordinator: RetryCoordinator,
    *,
    interval_seconds: float,
    stop: asyncio.Event,
) -> int:
    """Цикл обработки до сигнала остановки. Возвращает число выполненных проходов.

    Ошибки внутри прохода уже перехвачены координатором: цикл обязан выжить
    любую ошибку одной операции, иначе временный сбой БД остановил бы обработку
    до перезапуска контейнера.

    Ожидание сделано через `Event.wait` с таймаутом, а не через `sleep`:
    остановка не должна ждать полный интервал.
    """
    cycles = 0
    while not stop.is_set():
        await coordinator.run_once()
        cycles += 1
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_seconds)
        except asyncio.TimeoutError:
            continue
    return cycles


async def main() -> None:
    setup_logging()
    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL is empty. The worker operates on persistent job state "
            "and cannot run without PostgreSQL."
        )

    # Импорт внутри функции: сборка контура тянет весь AI-стек, и модуль должен
    # оставаться импортируемым в тестах без конфигурации.
    from apps.worker.component import build_heartbeat_client, worker_metadata
    from apps.worker.wiring import build_retry_coordinator

    coordinator = build_retry_coordinator()
    stop = asyncio.Event()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        # Контейнер останавливается сигналом: без обработчика текущий проход
        # обрывался бы посреди работы, оставляя job под просроченной арендой.
        loop.add_signal_handler(sig, stop.set)

    heartbeat: ComponentHeartbeatClient | None = build_heartbeat_client()
    heartbeat_task = asyncio.create_task(heartbeat.run()) if heartbeat else None

    metadata = worker_metadata()
    logger.info(
        "event=worker_started component_id=%s version=%s contract=%s "
        "interval=%s heartbeat=%s",
        metadata.component_id,
        metadata.version,
        metadata.contract_version,
        WORKER_POLL_INTERVAL_SECONDS,
        "on" if heartbeat_task else "off",
    )
    try:
        await run_forever(
            coordinator,
            interval_seconds=WORKER_POLL_INTERVAL_SECONDS,
            stop=stop,
        )
    finally:
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
            await heartbeat.close()
        logger.info("event=worker_stopped component_id=%s", WORKER_COMPONENT_ID)


if __name__ == "__main__":
    asyncio.run(main())
