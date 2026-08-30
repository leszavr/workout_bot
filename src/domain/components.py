"""Домен распределённых компонентов: реестр, версии, контракты, совместимость.

Зачем нужен отдельный домен. Backend, Admin Web и Telegram Gateway
разворачиваются независимо и в разных сетевых сегментах (RU/EU), поэтому их
версии в общем случае не совпадают. Совместимость определяется **контрактом
взаимодействия**, а не равенством версий или git SHA: Gateway версии 1.8.2
корректно работает с Backend 2.2.0, если поддерживает требуемую версию
контракта.

Три независимые величины:

- `version` — версия сборки компонента (semver). Меняется при любом релизе;
- `build_sha` — git-коммит сборки. Нужен только для трассировки, в решении о
  совместимости не участвует;
- `contract_version` — версия контракта Backend ↔ компонент. Меняется **только**
  при изменении самого протокола взаимодействия.

Стратегия EXPAND → MIGRATE → CONTRACT поддерживается тем, что Backend
объявляет **множество** поддерживаемых контрактов, а не одно значение: пока в
нём есть и старая, и новая версия, компоненты миграруют по одному.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

# --- Типы и статусы ---------------------------------------------------------


class ComponentType(StrEnum):
    """Тип развёртываемого компонента.

    Значения `MAX_GATEWAY` и `SMTP_CONNECTOR` заведены заранее, чтобы
    подключение нового connector-компонента не требовало миграции схемы и
    изменения контракта Admin API. Реализации у них сейчас нет, и
    зарегистрировать такой компонент может только он сам — «пустых» записей
    реестр не создаёт.
    """

    BACKEND = "backend"
    ADMIN_WEB = "admin_web"
    TELEGRAM_GATEWAY = "telegram_gateway"
    WORKER = "worker"
    MAX_GATEWAY = "max_gateway"
    SMTP_CONNECTOR = "smtp_connector"


class ComponentStatus(StrEnum):
    """Состояние, которое компонент сообщает о себе сам.

    Отделено от `CompatibilityState`: компонент может быть полностью здоров и
    при этом несовместим с Backend. Обратное тоже верно.
    """

    STARTING = "starting"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    STOPPING = "stopping"


class CompatibilityState(StrEnum):
    """Вывод Backend о конкретном экземпляре компонента.

    `UNKNOWN` — компонент неизвестного типа или без объявленных требований:
    судить о совместимости не на чем, и притворяться, что всё в порядке,
    нельзя. `OFFLINE` — heartbeat просрочен; последняя известная версия
    сохраняется, но состояние совместимости больше не актуально.
    """

    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    COMPATIBLE = "compatible"
    UPDATE_RECOMMENDED = "update_recommended"
    UPDATE_REQUIRED = "update_required"
    INCOMPATIBLE = "incompatible"
    OFFLINE = "offline"


# Состояния, при которых компонент считается пригодным к работе.
OPERATIONAL_STATES = frozenset(
    {
        CompatibilityState.HEALTHY,
        CompatibilityState.COMPATIBLE,
        CompatibilityState.UPDATE_RECOMMENDED,
    }
)


class Capability(StrEnum):
    """Что компонент умеет делать.

    Capabilities описывают функцию, а не реализацию: Backend решает «есть ли
    кому доставить сообщение», не зная, Telegram это или MAX.
    """

    TELEGRAM_POLLING = "telegram_polling"
    TELEGRAM_DELIVERY = "telegram_delivery"


# --- Метаданные и записи реестра -------------------------------------------


class ComponentMetadata(BaseModel):
    """Machine-readable описание компонента (`/version`, heartbeat).

    Секретов здесь нет и быть не может: значение отдаётся Admin API и
    попадает в журналы.
    """

    model_config = ConfigDict(extra="forbid")

    component_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    component_type: ComponentType
    name: str = Field(min_length=1, max_length=120)
    region: str = Field(default="RU", min_length=1, max_length=32)
    version: str = Field(min_length=1, max_length=32)
    build_sha: str | None = Field(default=None, max_length=40)
    contract_version: int = Field(ge=1, le=1000)
    capabilities: list[Capability] = Field(default_factory=list)
    status: ComponentStatus = ComponentStatus.HEALTHY


class ComponentInstance(BaseModel):
    """Запись реестра: экземпляр компонента и время последнего heartbeat.

    Экземпляров одного типа может быть несколько (`telegram-eu-1`,
    `telegram-eu-2`): уникален `component_id`, а не `component_type`.
    """

    model_config = ConfigDict(extra="forbid")

    metadata: ComponentMetadata
    last_heartbeat_at: datetime
    registered_at: datetime
    updated_at: datetime


# --- Требования и вердикт --------------------------------------------------


class ComponentRequirement(BaseModel):
    """Что Backend требует от компонента данного типа.

    `supported_contracts` — множество, а не минимум: это и есть механизм
    EXPAND/MIGRATE/CONTRACT. Пока в нём две версии, компоненты обновляются
    независимо; убрать старую можно только после миграции всех экземпляров.

    `min_version` — граница совместимости по сборке для случаев, когда
    контракт не менялся, но старая сборка содержала дефект. `recommended_version`
    даёт мягкий сигнал `UPDATE_RECOMMENDED` без блокировки.
    """

    model_config = ConfigDict(extra="forbid")

    component_type: ComponentType
    supported_contracts: tuple[int, ...] = Field(min_length=1)
    min_version: str = Field(min_length=1, max_length=32)
    recommended_version: str | None = Field(default=None, max_length=32)

    @property
    def required_contract(self) -> int:
        """Минимальный контракт, который Backend ещё понимает."""
        return min(self.supported_contracts)


class CompatibilityVerdict(BaseModel):
    """Результат проверки одного экземпляра.

    `detail` — человекочитаемое пояснение для администратора; `state` и
    `required_contract` предназначены для машинной обработки в CI.
    """

    model_config = ConfigDict(extra="forbid")

    component_id: str
    component_type: ComponentType
    state: CompatibilityState
    contract_version: int | None = None
    required_contract: int | None = None
    supported_contracts: tuple[int, ...] = ()
    version: str | None = None
    min_version: str | None = None
    detail: str

    @property
    def blocks_deployment(self) -> bool:
        return self.state in (
            CompatibilityState.UPDATE_REQUIRED,
            CompatibilityState.INCOMPATIBLE,
        )


class DeploymentSafetyReport(BaseModel):
    """Ответ на вопрос «можно ли обновлять Backend, не сломав компоненты».

    `SAFE`/`BLOCKED` — единственное, что нужно CI; список вердиктов объясняет
    решение. Компоненты в состоянии `OFFLINE` деплой не блокируют: остановленный
    экземпляр невозможно сломать обновлением, а его версия при следующем
    старте станет известна заново.
    """

    model_config = ConfigDict(extra="forbid")

    result: str
    generated_at: datetime
    backend_version: str
    backend_contracts: tuple[int, ...]
    blocking: list[CompatibilityVerdict] = Field(default_factory=list)
    verdicts: list[CompatibilityVerdict] = Field(default_factory=list)


SAFE = "SAFE"
BLOCKED = "BLOCKED"


# --- Текущие контракты релиза ----------------------------------------------
#
# Единственный источник истины о контрактах. Deployment manifest
# (`deploy/release-manifest.json`) обязан совпадать с этими значениями —
# расхождение ловит тест, иначе манифест начнёт врать deployment tooling.

BACKEND_CONTRACT_VERSION = 1
BACKEND_SUPPORTED_CONTRACTS: tuple[int, ...] = (1,)

COMPONENT_REQUIREMENTS: dict[ComponentType, ComponentRequirement] = {
    ComponentType.TELEGRAM_GATEWAY: ComponentRequirement(
        component_type=ComponentType.TELEGRAM_GATEWAY,
        supported_contracts=BACKEND_SUPPORTED_CONTRACTS,
        min_version="2.2.0",
        recommended_version="2.2.0",
    ),
}

# Сколько ждём heartbeat, прежде чем считать экземпляр офлайном. Порог заметно
# больше интервала отправки: одна пропущенная отправка при сетевой заминке не
# должна показывать администратору «Gateway упал».
HEARTBEAT_INTERVAL = timedelta(seconds=60)
OFFLINE_AFTER = timedelta(seconds=180)


def parse_version(value: str) -> tuple[int, ...]:
    """Числовое сравнение версий вместо лексикографического.

    Лексикографически `"2.10.0" < "2.9.0"`, что дало бы ложный
    `UPDATE_REQUIRED`. Нечисловые суффиксы (`2.2.0-rc1`) отбрасываются:
    предрелиз сравнивается как соответствующий релиз — этого достаточно, а
    полный semver-порядок здесь не нужен.
    """
    parts: list[int] = []
    for chunk in value.split("."):
        digits = ""
        for char in chunk:
            if not char.isdigit():
                break
            digits += char
        parts.append(int(digits) if digits else 0)
    return tuple(parts) or (0,)
