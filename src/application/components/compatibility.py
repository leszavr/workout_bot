"""Проверка совместимости зарегистрированных компонентов с Backend.

Чистые функции без ввода-вывода: на входе записи реестра и требования, на
выходе вердикт. Поэтому одна и та же логика обслуживает и Admin UI, и
deployment safety gate в CI, и тесты — без базы данных.

Порядок проверок важен. Сначала офлайн (о неработающем экземпляре нечего
говорить), затем контракт (жёсткая граница совместимости), затем версия
сборки (мягкая). Обратный порядок сообщал бы «обновите версию» там, где
несовместим сам протокол.
"""
from __future__ import annotations

from datetime import datetime, timezone

from src.domain.components import (
    BLOCKED,
    COMPONENT_REQUIREMENTS,
    OFFLINE_AFTER,
    SAFE,
    CompatibilityState,
    CompatibilityVerdict,
    ComponentInstance,
    ComponentRequirement,
    ComponentStatus,
    ComponentType,
    DeploymentSafetyReport,
    parse_version,
)


def _offline(instance: ComponentInstance, *, now: datetime) -> bool:
    return now - instance.last_heartbeat_at > OFFLINE_AFTER


def evaluate(
    instance: ComponentInstance,
    *,
    requirement: ComponentRequirement | None = None,
    now: datetime | None = None,
) -> CompatibilityVerdict:
    """Вердикт по одному экземпляру компонента."""
    now = now or datetime.now(timezone.utc)
    meta = instance.metadata
    requirement = requirement or COMPONENT_REQUIREMENTS.get(meta.component_type)

    base = {
        "component_id": meta.component_id,
        "component_type": meta.component_type,
        "contract_version": meta.contract_version,
        "version": meta.version,
    }
    if requirement is not None:
        base["required_contract"] = requirement.required_contract
        base["supported_contracts"] = requirement.supported_contracts
        base["min_version"] = requirement.min_version

    if _offline(instance, now=now):
        seconds = int((now - instance.last_heartbeat_at).total_seconds())
        return CompatibilityVerdict(
            **base,
            state=CompatibilityState.OFFLINE,
            detail=f"Нет heartbeat {seconds} с",
        )

    if requirement is None:
        # Компонент зарегистрировался, но Backend не объявляет требований к
        # этому типу. Считать его совместимым нельзя: проверка не выполнялась.
        return CompatibilityVerdict(
            **base,
            state=CompatibilityState.UNKNOWN,
            detail="Backend не объявляет требований к этому типу компонента",
        )

    if meta.contract_version not in requirement.supported_contracts:
        supported = ", ".join(f"v{c}" for c in sorted(requirement.supported_contracts))
        if meta.contract_version < requirement.required_contract:
            return CompatibilityVerdict(
                **base,
                state=CompatibilityState.UPDATE_REQUIRED,
                detail=(
                    f"Контракт компонента v{meta.contract_version}; "
                    f"Backend поддерживает {supported}"
                ),
            )
        # Контракт новее всего, что понимает Backend: обновлять нужно Backend,
        # а не компонент, поэтому это не UPDATE_REQUIRED.
        return CompatibilityVerdict(
            **base,
            state=CompatibilityState.INCOMPATIBLE,
            detail=(
                f"Контракт компонента v{meta.contract_version} новее Backend "
                f"({supported}): обновите Backend"
            ),
        )

    if parse_version(meta.version) < parse_version(requirement.min_version):
        return CompatibilityVerdict(
            **base,
            state=CompatibilityState.UPDATE_REQUIRED,
            detail=(
                f"Версия {meta.version} ниже минимально поддерживаемой "
                f"{requirement.min_version}"
            ),
        )

    if requirement.recommended_version and parse_version(meta.version) < parse_version(
        requirement.recommended_version
    ):
        return CompatibilityVerdict(
            **base,
            state=CompatibilityState.UPDATE_RECOMMENDED,
            detail=f"Доступна версия {requirement.recommended_version}",
        )

    if meta.status is ComponentStatus.DEGRADED:
        # Контракт совместим, но компонент сам сообщает о проблеме. Скрывать
        # это за «COMPATIBLE» нельзя: администратор не увидел бы деградацию.
        return CompatibilityVerdict(
            **base,
            state=CompatibilityState.HEALTHY,
            detail="Совместим; компонент сообщает о деградации",
        )

    return CompatibilityVerdict(
        **base,
        state=CompatibilityState.COMPATIBLE,
        detail=f"Контракт v{meta.contract_version} поддерживается",
    )


def evaluate_all(
    instances: list[ComponentInstance],
    *,
    requirements: dict[ComponentType, ComponentRequirement] | None = None,
    now: datetime | None = None,
) -> list[CompatibilityVerdict]:
    now = now or datetime.now(timezone.utc)
    source = requirements if requirements is not None else COMPONENT_REQUIREMENTS
    return [
        evaluate(i, requirement=source.get(i.metadata.component_type), now=now)
        for i in instances
    ]


def deployment_safety(
    instances: list[ComponentInstance],
    *,
    backend_version: str,
    backend_contracts: tuple[int, ...],
    requirements: dict[ComponentType, ComponentRequirement] | None = None,
    now: datetime | None = None,
) -> DeploymentSafetyReport:
    """Можно ли обновлять Backend, не сломав уже развёрнутые компоненты.

    Блокируют только зарегистрированные и живые экземпляры с несовместимым
    контрактом или слишком старой сборкой. `UPDATE_RECOMMENDED` не блокирует:
    это сигнал, а не запрет. `OFFLINE` и `UNKNOWN` тоже не блокируют — судить
    о них нечем, и остановка деплоя из-за выключенного экземпляра сделала бы
    gate неработоспособным.
    """
    now = now or datetime.now(timezone.utc)
    verdicts = evaluate_all(instances, requirements=requirements, now=now)
    blocking = [v for v in verdicts if v.blocks_deployment]
    return DeploymentSafetyReport(
        result=BLOCKED if blocking else SAFE,
        generated_at=now,
        backend_version=backend_version,
        backend_contracts=backend_contracts,
        blocking=blocking,
        verdicts=verdicts,
    )
