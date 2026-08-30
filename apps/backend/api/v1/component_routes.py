"""Admin API: раздел «Инфраструктура / Компоненты».

Только чтение и удаление записи. CRUD произвольных коннекторов здесь
намеренно нет: компонент попадает в реестр, когда сам сообщает о себе, а не
когда администратор описал его руками. Ручная запись означала бы, что админка
показывает желаемое состояние вместо фактического.

Backend описывает себя сам и в реестре не хранится: он и есть тот, кто ведёт
реестр, и запись о нём была бы избыточной копией собственного `/version`.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from apps.backend.api.v1.component_dependencies import build_component_registry
from apps.backend.auth import AuthenticatedUser, require_admin, require_viewer
from src.domain.components import (
    BACKEND_CONTRACT_VERSION,
    BACKEND_SUPPORTED_CONTRACTS,
    CompatibilityState,
    ComponentStatus,
    ComponentType,
)
from src.infrastructure.config import BUILD_SHA, COMPONENT_REGION
from src.version import APP_VERSION

router = APIRouter(prefix="/api/v1/admin/components", tags=["components"])


def _backend_self() -> dict:
    """Backend как компонент. Источник — собственная сборка, а не реестр."""
    return {
        "component_id": "backend",
        "component_type": ComponentType.BACKEND.value,
        "name": "Workout Bot Backend",
        "region": COMPONENT_REGION,
        "version": APP_VERSION,
        "build_sha": BUILD_SHA or None,
        "contract_version": BACKEND_CONTRACT_VERSION,
        "supported_contracts": list(BACKEND_SUPPORTED_CONTRACTS),
        "capabilities": [],
        "status": ComponentStatus.HEALTHY.value,
        "compatibility_state": CompatibilityState.HEALTHY.value,
        "compatibility_detail": "Компонент, ведущий реестр",
        "last_heartbeat_at": None,
        "registered_at": None,
        "self_reported": True,
    }


@router.get("")
async def list_components(
    _: Annotated[AuthenticatedUser, Depends(require_viewer)],
) -> dict:
    registry = build_component_registry()
    items = [_backend_self()]
    for instance, verdict in await registry.list_with_verdicts():
        meta = instance.metadata
        items.append(
            {
                "component_id": meta.component_id,
                "component_type": meta.component_type.value,
                "name": meta.name,
                "region": meta.region,
                "version": meta.version,
                "build_sha": meta.build_sha,
                "contract_version": meta.contract_version,
                "supported_contracts": list(verdict.supported_contracts),
                "capabilities": [c.value for c in meta.capabilities],
                "status": meta.status.value,
                "compatibility_state": verdict.state.value,
                "compatibility_detail": verdict.detail,
                "required_contract": verdict.required_contract,
                "min_version": verdict.min_version,
                "last_heartbeat_at": instance.last_heartbeat_at.isoformat(),
                "registered_at": instance.registered_at.isoformat(),
                "self_reported": False,
            }
        )
    return {
        "total": len(items),
        "items": items,
        "backend": {
            "version": APP_VERSION,
            "build_sha": BUILD_SHA or None,
            "contract_version": BACKEND_CONTRACT_VERSION,
            "supported_contracts": list(BACKEND_SUPPORTED_CONTRACTS),
        },
        "requirements": {
            key.value: value for key, value in registry.requirements().items()
        },
    }


@router.get("/deployment-safety")
async def deployment_safety(
    _: Annotated[AuthenticatedUser, Depends(require_viewer)],
) -> dict:
    """Тот же вердикт, что видит CI. Админ должен видеть причину блокировки."""
    report = await build_component_registry().deployment_safety()
    return report.model_dump(mode="json")


@router.delete("/{component_id}", status_code=204)
async def forget_component(
    component_id: str,
    _: Annotated[AuthenticatedUser, Depends(require_admin)],
) -> None:
    """Убирает выведенный из эксплуатации экземпляр.

    Работающий компонент зарегистрируется снова следующим heartbeat: удаление
    не выключает его, а лишь чистит реестр от мёртвых записей.
    """
    if not await build_component_registry().forget(component_id):
        raise HTTPException(status_code=404, detail="Component not found")
