"""Telegram Gateway как компонент Component Registry.

Здесь собрано всё, что Gateway сообщает о себе: идентификатор экземпляра,
версия сборки, версия контракта и capabilities. Отдельный модуль нужен, чтобы
точка входа не превращалась в сборочный код, а тесты могли проверить metadata
без запуска polling.

`GATEWAY_CONTRACT_VERSION` — версия контракта Gateway ↔ Backend. Меняется
только при изменении самого протокола, а не при каждом релизе бота: иначе
независимый деплой стал бы невозможен.
"""
from __future__ import annotations

from src.domain.components import (
    Capability,
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
    TELEGRAM_COMPONENT_ID,
    TELEGRAM_COMPONENT_NAME,
)
from src.version import GATEWAY_VERSION

GATEWAY_CONTRACT_VERSION = 1

GATEWAY_CAPABILITIES = [
    Capability.TELEGRAM_POLLING,
    Capability.TELEGRAM_DELIVERY,
]


def gateway_metadata(
    *, status: ComponentStatus = ComponentStatus.HEALTHY
) -> ComponentMetadata:
    return ComponentMetadata(
        component_id=TELEGRAM_COMPONENT_ID,
        component_type=ComponentType.TELEGRAM_GATEWAY,
        name=TELEGRAM_COMPONENT_NAME,
        region=COMPONENT_REGION,
        version=GATEWAY_VERSION,
        build_sha=BUILD_SHA or None,
        contract_version=GATEWAY_CONTRACT_VERSION,
        capabilities=GATEWAY_CAPABILITIES,
        status=status,
    )


def build_heartbeat_client() -> ComponentHeartbeatClient | None:
    """Клиент heartbeat либо None, если регистрация не настроена.

    Отсутствие адреса Backend или service-токена — не ошибка: локальная
    разработка запускает бота без реестра. Падать здесь значило бы сделать
    мониторинг обязательным условием работы бизнес-функции.
    """
    if not BACKEND_INTERNAL_URL or not INTERNAL_SERVICE_TOKEN:
        return None
    return ComponentHeartbeatClient(
        base_url=BACKEND_INTERNAL_URL,
        service_token=INTERNAL_SERVICE_TOKEN,
        metadata=gateway_metadata(),
    )
