"""Тесты Component Registry, compatibility engine и deployment safety gate.

Без БД: логика совместимости — чистые функции над записями реестра, и
проверять её через PostgreSQL значило бы тестировать не то.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.application.components.compatibility import (
    deployment_safety,
    evaluate,
    evaluate_all,
)
from src.domain.components import (
    BACKEND_CONTRACT_VERSION,
    BACKEND_SUPPORTED_CONTRACTS,
    BLOCKED,
    COMPONENT_REQUIREMENTS,
    OFFLINE_AFTER,
    SAFE,
    Capability,
    CompatibilityState,
    ComponentInstance,
    ComponentMetadata,
    ComponentRequirement,
    ComponentStatus,
    ComponentType,
    parse_version,
)

NOW = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)

# Требование, на котором строятся проверки: контракт 3 (плюс 4 в переходный
# период), минимальная версия 1.8.0.
REQUIREMENT = ComponentRequirement(
    component_type=ComponentType.TELEGRAM_GATEWAY,
    supported_contracts=(3, 4),
    min_version="1.8.0",
)


def instance(
    *,
    component_id: str = "telegram-eu-1",
    contract: int = 3,
    version: str = "1.8.2",
    status: ComponentStatus = ComponentStatus.HEALTHY,
    heartbeat_age_seconds: int = 5,
    component_type: ComponentType = ComponentType.TELEGRAM_GATEWAY,
) -> ComponentInstance:
    seen = NOW - timedelta(seconds=heartbeat_age_seconds)
    return ComponentInstance(
        metadata=ComponentMetadata(
            component_id=component_id,
            component_type=component_type,
            name="Telegram Gateway EU",
            region="EU",
            version=version,
            build_sha="abc1234",
            contract_version=contract,
            capabilities=[Capability.TELEGRAM_POLLING, Capability.TELEGRAM_DELIVERY],
            status=status,
        ),
        last_heartbeat_at=seen,
        registered_at=seen,
        updated_at=seen,
    )


# --- Compatibility ---------------------------------------------------------


def test_contract_equal_to_required_is_compatible():
    verdict = evaluate(instance(contract=3), requirement=REQUIREMENT, now=NOW)
    assert verdict.state is CompatibilityState.COMPATIBLE
    assert not verdict.blocks_deployment


def test_newer_supported_contract_is_compatible():
    """Контракт 4 при required 3: Backend поддерживает оба (EXPAND-фаза)."""
    verdict = evaluate(instance(contract=4), requirement=REQUIREMENT, now=NOW)
    assert verdict.state is CompatibilityState.COMPATIBLE


def test_older_contract_requires_update():
    verdict = evaluate(instance(contract=2), requirement=REQUIREMENT, now=NOW)
    assert verdict.state is CompatibilityState.UPDATE_REQUIRED
    assert verdict.blocks_deployment


def test_contract_newer_than_backend_is_incompatible():
    """Обновлять нужно Backend, а не компонент — это другое состояние."""
    verdict = evaluate(instance(contract=5), requirement=REQUIREMENT, now=NOW)
    assert verdict.state is CompatibilityState.INCOMPATIBLE
    assert "обновите backend" in verdict.detail.lower()


def test_version_below_minimum_requires_update():
    verdict = evaluate(
        instance(contract=3, version="1.7.9"), requirement=REQUIREMENT, now=NOW
    )
    assert verdict.state is CompatibilityState.UPDATE_REQUIRED


def test_recommended_version_does_not_block():
    requirement = REQUIREMENT.model_copy(update={"recommended_version": "1.9.0"})
    verdict = evaluate(instance(version="1.8.2"), requirement=requirement, now=NOW)
    assert verdict.state is CompatibilityState.UPDATE_RECOMMENDED
    assert not verdict.blocks_deployment


def test_degraded_component_is_not_reported_as_compatible_only():
    verdict = evaluate(
        instance(status=ComponentStatus.DEGRADED), requirement=REQUIREMENT, now=NOW
    )
    assert verdict.state is CompatibilityState.HEALTHY
    assert "деград" in verdict.detail.lower()


def test_unknown_component_type_is_not_silently_compatible():
    """Тип без объявленных требований получает UNKNOWN, а не «всё в порядке».

    Взят `MAX_GATEWAY`: он заведён в enum заранее и реализации не имеет,
    поэтому требований к нему в коде нет. `WORKER` для этой проверки больше не
    подходит — с Phase 1.2-D у него есть собственное требование.
    """
    verdict = evaluate(
        instance(component_type=ComponentType.MAX_GATEWAY), requirement=None, now=NOW
    )
    assert verdict.state is CompatibilityState.UNKNOWN


def test_worker_requirement_is_declared():
    """Worker — полноценный компонент реестра, а не безымянный процесс.

    Без объявленного требования его вердикт был бы `UNKNOWN`, и deployment gate
    не смог бы сказать, совместим ли развёрнутый worker с Backend.
    """
    verdict = evaluate(
        instance(component_type=ComponentType.WORKER, contract=1, version="2.2.0"),
        requirement=None,
        now=NOW,
    )
    assert verdict.state is not CompatibilityState.UNKNOWN
    assert not verdict.blocks_deployment


# --- Offline detection -----------------------------------------------------


def test_stale_heartbeat_is_offline():
    stale = instance(heartbeat_age_seconds=int(OFFLINE_AFTER.total_seconds()) + 1)
    verdict = evaluate(stale, requirement=REQUIREMENT, now=NOW)
    assert verdict.state is CompatibilityState.OFFLINE


def test_single_missed_heartbeat_is_not_offline():
    """Порог заметно больше интервала: одна заминка не гасит компонент."""
    recent = instance(heartbeat_age_seconds=int(OFFLINE_AFTER.total_seconds()) - 1)
    verdict = evaluate(recent, requirement=REQUIREMENT, now=NOW)
    assert verdict.state is CompatibilityState.COMPATIBLE


def test_offline_component_keeps_last_known_version():
    stale = instance(heartbeat_age_seconds=10_000, version="1.8.2")
    verdict = evaluate(stale, requirement=REQUIREMENT, now=NOW)
    assert verdict.version == "1.8.2"
    assert verdict.contract_version == 3


# --- Multiple instances ----------------------------------------------------


def test_multiple_instances_of_same_type_are_evaluated_independently():
    instances = [
        instance(component_id="telegram-eu-1", contract=3),
        instance(component_id="telegram-eu-2", contract=2),
    ]
    verdicts = evaluate_all(
        instances,
        requirements={ComponentType.TELEGRAM_GATEWAY: REQUIREMENT},
        now=NOW,
    )
    by_id = {v.component_id: v.state for v in verdicts}
    assert by_id["telegram-eu-1"] is CompatibilityState.COMPATIBLE
    assert by_id["telegram-eu-2"] is CompatibilityState.UPDATE_REQUIRED


# --- Deployment safety gate ------------------------------------------------


def _safety(instances, requirements=None):
    return deployment_safety(
        instances,
        backend_version="2.2.0",
        backend_contracts=(3, 4),
        requirements=requirements or {ComponentType.TELEGRAM_GATEWAY: REQUIREMENT},
        now=NOW,
    )


def test_backend_update_with_compatible_contract_is_safe():
    report = _safety([instance(contract=3)])
    assert report.result == SAFE
    assert report.blocking == []


def test_backend_update_dropping_contract_is_blocked():
    """CONTRACT-фаза: Backend поддерживает только v4, Gateway остался на v3."""
    requirement = REQUIREMENT.model_copy(update={"supported_contracts": (4,)})
    report = _safety(
        [instance(contract=3)],
        requirements={ComponentType.TELEGRAM_GATEWAY: requirement},
    )
    assert report.result == BLOCKED
    assert [v.component_id for v in report.blocking] == ["telegram-eu-1"]


def test_frontend_only_update_is_safe():
    """Admin Web не имеет контракта: его обновление никого не блокирует."""
    web = instance(
        component_id="admin-web-ru-1", component_type=ComponentType.ADMIN_WEB
    )
    report = _safety([web], requirements={})
    assert report.result == SAFE


def test_telegram_only_update_does_not_require_backend_change():
    """Обновление Gateway в пределах поддерживаемых контрактов — независимо."""
    report = _safety([instance(contract=4, version="1.9.0")])
    assert report.result == SAFE


def test_offline_instance_does_not_block_deployment():
    report = _safety([instance(contract=2, heartbeat_age_seconds=10_000)])
    assert report.result == SAFE


def test_empty_registry_is_safe():
    assert _safety([]).result == SAFE


# --- Version parsing -------------------------------------------------------


@pytest.mark.parametrize(
    ("left", "right"),
    [("2.9.0", "2.10.0"), ("1.8.0", "1.8.1"), ("1.9.9", "2.0.0")],
)
def test_versions_compare_numerically_not_lexicographically(left, right):
    assert parse_version(left) < parse_version(right)


def test_prerelease_suffix_is_ignored():
    assert parse_version("2.2.0-rc1") == parse_version("2.2.0")


# --- Deployment manifest ---------------------------------------------------


def test_release_manifest_matches_code_contracts():
    """Манифест — вход deployment tooling: расхождение с кодом недопустимо."""
    root = Path(__file__).resolve().parents[2]
    manifest = json.loads((root / "deploy" / "release-manifest.json").read_text())

    backend = manifest["components"]["backend"]
    assert backend["contract"] == BACKEND_CONTRACT_VERSION
    assert tuple(backend["supported_contracts"]) == BACKEND_SUPPORTED_CONTRACTS

    for component_type in (ComponentType.TELEGRAM_GATEWAY, ComponentType.WORKER):
        requirement = COMPONENT_REQUIREMENTS[component_type]
        declared = manifest["requirements"][component_type.value]
        assert tuple(declared["supported_contracts"]) == requirement.supported_contracts
        assert declared["min_version"] == requirement.min_version


def test_every_declared_requirement_is_in_manifest():
    """Новый компонент нельзя завести в коде, не объявив его для деплоя.

    Без этой проверки требование, добавленное только в код, оставляло бы
    deployment tooling с устаревшим манифестом — то есть с молчаливо неверным
    вердиктом совместимости.
    """
    root = Path(__file__).resolve().parents[2]
    manifest = json.loads((root / "deploy" / "release-manifest.json").read_text())
    declared = set(manifest["requirements"])
    in_code = {t.value for t in COMPONENT_REQUIREMENTS}
    assert in_code == declared
