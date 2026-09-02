"""Сценарии развёртывания и контрактный gate.

Проверяется не код компонента, а правила деплоя: какие изменения требуют
переразвёртывания EU-шлюза, а какие нет, и что gate блокирует заведомо
несовместимую пару.

Почему это тест, а не документация: правило «Backend обновился — Gateway не
трогаем» держится на том, что совместимость решает `contract_version`, а не
совпадение версий. Достаточно один раз завести версионную проверку «на всякий
случай», и независимое развёртывание тихо перестанет работать.
"""
from __future__ import annotations

import datetime as dt

import pytest

from scripts.check_contracts import check
from src.application.components.compatibility import evaluate
from src.domain.components import (
    BACKEND_SUPPORTED_CONTRACTS,
    COMPONENT_REQUIREMENTS,
    CompatibilityState,
    ComponentInstance,
    ComponentMetadata,
    ComponentRequirement,
    ComponentStatus,
    ComponentType,
)
from src.domain.telegram_contract import TELEGRAM_CONTRACT_VERSION
from src.version import APP_VERSION, GATEWAY_VERSION, WORKER_VERSION

NOW = dt.datetime(2026, 9, 2, 12, 0, tzinfo=dt.timezone.utc)


def _instance(
    *,
    component_type: ComponentType = ComponentType.TELEGRAM_GATEWAY,
    version: str = GATEWAY_VERSION,
    contract: int = TELEGRAM_CONTRACT_VERSION,
) -> ComponentInstance:
    return ComponentInstance(
        metadata=ComponentMetadata(
            component_id=f"{component_type.value}-1",
            component_type=component_type,
            name="test",
            region="EU",
            version=version,
            contract_version=contract,
            status=ComponentStatus.HEALTHY,
        ),
        registered_at=NOW,
        last_heartbeat_at=NOW,
        updated_at=NOW,
    )


class TestStaticGate:
    def test_manifest_matches_code(self):
        assert check() == []

    def test_gate_detects_manifest_drift(self, monkeypatch):
        """Проверка обязана падать при расхождении, а не быть формальностью."""
        import scripts.check_contracts as gate

        monkeypatch.setitem(gate.CODE_VERSIONS, "telegram_gateway", "99.0.0")
        problems = gate.check()
        assert any("telegram_gateway.version" in problem for problem in problems)


class TestDeploymentScenarios:
    """Пять сценариев независимого развёртывания."""

    def test_admin_web_change_does_not_affect_others(self):
        """Админка не имеет версии контракта: её обновление никого не касается."""
        assert ComponentType.ADMIN_WEB not in COMPONENT_REQUIREMENTS

    def test_backend_change_with_same_contract_keeps_gateway(self):
        """Backend обновился, контракт тот же — шлюз остаётся развёрнутым."""
        verdict = evaluate(_instance(), requirement=None, now=NOW)
        assert verdict.state is CompatibilityState.COMPATIBLE
        assert not verdict.blocks_deployment

    def test_gateway_version_bump_does_not_need_backend(self):
        """Правка polling в шлюзе не требует обновления Backend."""
        verdict = evaluate(
            _instance(version="1.4.2"), requirement=None, now=NOW
        )
        assert not verdict.blocks_deployment

    def test_worker_change_is_independent(self):
        verdict = evaluate(
            _instance(component_type=ComponentType.WORKER, version=WORKER_VERSION),
            requirement=None,
            now=NOW,
        )
        assert not verdict.blocks_deployment

    def test_incompatible_contract_is_blocked(self):
        """Backend поддерживает [1], шлюз использует 2 → развёртывание блокируется."""
        verdict = evaluate(_instance(contract=2), requirement=None, now=NOW)
        assert verdict.state is CompatibilityState.INCOMPATIBLE
        assert verdict.blocks_deployment

    def test_older_contract_is_blocked_when_unsupported(self):
        """Обратная сторона: снятый с поддержки контракт тоже блокируется."""
        requirement = ComponentRequirement(
            component_type=ComponentType.TELEGRAM_GATEWAY,
            supported_contracts=(2,),
            min_version="1.0.0",
        )
        verdict = evaluate(_instance(contract=1), requirement=requirement, now=NOW)
        assert verdict.blocks_deployment


class TestVersionsAreNotCompatibilityCriteria:
    def test_versions_differ_between_components(self):
        """Совпадение версий не требуется и не должно требоваться."""
        assert len({APP_VERSION, GATEWAY_VERSION}) == 2

    def test_version_mismatch_alone_does_not_block(self):
        """Разные версии при одном контракте — норма, а не блокировка."""
        verdict = evaluate(_instance(version="9.9.9"), requirement=None, now=NOW)
        assert not verdict.blocks_deployment

    def test_build_sha_is_not_a_criterion(self):
        """Git SHA — только трассировка: в решении о совместимости его нет."""
        with_sha = _instance()
        with_sha.metadata.build_sha = "deadbeef"
        other = _instance()
        other.metadata.build_sha = "cafebabe"

        assert (
            evaluate(with_sha, requirement=None, now=NOW).state
            is evaluate(other, requirement=None, now=NOW).state
        )


class TestExpandContract:
    def test_backend_can_support_two_contracts_at_once(self):
        """Expand/contract: обе версии живут одновременно, шлюз обновляется потом."""
        requirement = ComponentRequirement(
            component_type=ComponentType.TELEGRAM_GATEWAY,
            supported_contracts=(1, 2),
            min_version="1.0.0",
        )
        for contract in (1, 2):
            verdict = evaluate(
                _instance(contract=contract), requirement=requirement, now=NOW
            )
            assert not verdict.blocks_deployment

    def test_current_contract_is_supported(self):
        assert TELEGRAM_CONTRACT_VERSION in BACKEND_SUPPORTED_CONTRACTS
