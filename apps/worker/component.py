"""Worker как компонент Component Registry.

Отдельный модуль по той же причине, что и у Telegram Gateway: точка входа не
должна превращаться в сборочный код, а metadata нужна тестам без запуска цикла.

`WORKER_CONTRACT_VERSION` — версия контракта worker ↔ Backend. Сейчас worker
общается с Backend только через реестр (heartbeat), поэтому контракт совпадает
с backend'овским и меняется вместе с ним, а не при каждом релизе.
"""
from __future__ import annotations

from src.domain.components import (
    ComponentMetadata,
    ComponentStatus,
    ComponentType,
)
from src.infrastructure.components.heartbeat_client import ComponentHeartbeatClient
from src.infrastructure.config import (
    BACKEND_INTERNAL_URL,
    BUILD_SHA,
    COMPONENT_REGION,
    INTERNAL_SERVICE_TOKEN,
    WORKER_COMPONENT_ID,
    WORKER_COMPONENT_NAME,
)
from src.version import WORKER_VERSION

WORKER_CONTRACT_VERSION = 1


def worker_metadata(
    *, status: ComponentStatus = ComponentStatus.HEALTHY
) -> ComponentMetadata:
    """Metadata экземпляра worker'а.

    Capabilities пусты: worker ничего не предоставляет другим компонентам, он
    обрабатывает собственную очередь. Объявлять здесь `telegram_delivery` было
    бы неверно — доставку он повторяет, но принимающей стороной для Backend не
    является.
    """
    return ComponentMetadata(
        component_id=WORKER_COMPONENT_ID,
        component_type=ComponentType.WORKER,
        name=WORKER_COMPONENT_NAME,
        region=COMPONENT_REGION,
        version=WORKER_VERSION,
        build_sha=BUILD_SHA or None,
        contract_version=WORKER_CONTRACT_VERSION,
        capabilities=[],
        status=status,
    )


def build_heartbeat_client() -> ComponentHeartbeatClient | None:
    """Клиент heartbeat либо None, если реестр не настроен.

    Как и у Gateway, регистрация не является условием работы: недоступность
    Backend не должна останавливать обработку повторов.
    """
    if not BACKEND_INTERNAL_URL or not INTERNAL_SERVICE_TOKEN:
        return None
    return ComponentHeartbeatClient(
        base_url=BACKEND_INTERNAL_URL,
        service_token=INTERNAL_SERVICE_TOKEN,
        metadata=worker_metadata(),
    )
