"""Internal API: регистрация компонентов и deployment safety gate.

Отдельный роутер и отдельная аутентификация (`X-Internal-Service-Token`):
здесь работают процессы, а не администраторы. Admin JWT сюда не подходит — у
Telegram Gateway нет пользователя и роли.

`/internal/v1/components/heartbeat` — регистрация и heartbeat одной
идемпотентной операцией: компонент не помнит, регистрировался ли он раньше.

`/internal/v1/deployment-safety` доступен по тому же service-токену, потому что
его потребитель — CI/CD, а не браузер: получить admin JWT в пайплайне нельзя.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from apps.backend.api.v1.component_dependencies import build_component_registry
from apps.backend.service_auth import require_service_token
from src.domain.components import (
    BACKEND_CONTRACT_VERSION,
    BACKEND_SUPPORTED_CONTRACTS,
    HEARTBEAT_INTERVAL,
    ComponentMetadata,
)
from src.version import APP_VERSION

router = APIRouter(prefix="/internal/v1", tags=["internal"])


@router.post("/components/heartbeat", dependencies=[Depends(require_service_token)])
async def heartbeat(metadata: ComponentMetadata) -> dict:
    """Принимает metadata компонента и отвечает вердиктом совместимости.

    Вердикт возвращается вызывающему специально: компонент узнаёт о своей
    несовместимости сам и пишет это в собственный лог, а не только в админку.
    Интервал отправки задаёт Backend — компоненту не нужно знать порог офлайна.
    """
    instance, verdict = await build_component_registry().register(metadata)
    return {
        "accepted": True,
        "component_id": instance.metadata.component_id,
        "registered_at": instance.registered_at.isoformat(),
        "last_heartbeat_at": instance.last_heartbeat_at.isoformat(),
        "heartbeat_interval_seconds": int(HEARTBEAT_INTERVAL.total_seconds()),
        "backend": {
            "version": APP_VERSION,
            "contract_version": BACKEND_CONTRACT_VERSION,
            "supported_contracts": list(BACKEND_SUPPORTED_CONTRACTS),
        },
        "compatibility": verdict.model_dump(mode="json"),
    }


@router.get("/deployment-safety", dependencies=[Depends(require_service_token)])
async def deployment_safety_gate() -> dict:
    """SAFE/BLOCKED для CI: сломает ли обновление Backend живые компоненты."""
    report = await build_component_registry().deployment_safety()
    return report.model_dump(mode="json")


@router.get("/components/{component_id}", dependencies=[Depends(require_service_token)])
async def component_state(component_id: str) -> dict:
    """Состояние одного экземпляра. Нужно deployment-скриптам после рестарта."""
    registry = build_component_registry()
    for instance, verdict in await registry.list_with_verdicts():
        if instance.metadata.component_id == component_id:
            return {
                "component": instance.model_dump(mode="json"),
                "compatibility": verdict.model_dump(mode="json"),
            }
    return {"component": None, "compatibility": None}
