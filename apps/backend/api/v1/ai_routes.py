"""Admin API v1: управление AI-конфигурацией (этап 3B).

Все endpoint'ы защищены JWT (require_admin). Используются отдельные
request/response DTO; database-модели наружу не возвращаются.

Гарантии безопасности:
- API key никогда не возвращается (только has_api_key + masked_api_key);
- секреты не попадают в audit metadata и логи;
- удаление модели, привязанной к задаче, запрещено.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from apps.backend.api.v1.ai_dependencies import build_ai_components
from apps.backend.auth import AuthenticatedUser, require_admin, require_viewer
from src.application.ai.admin_service import AIDependencyError
from src.domain.ai.config import (
    AIEndpoint,
    AIModel,
    AIProvider,
    AITaskConfig,
    AITaskModelBinding,
    PromptTemplate,
)
from src.domain.ai.enums import AIProtocol, AITaskType
from src.domain.ai.errors import AIConfigurationError
from src.errors import ProfilePersistenceError, WorkoutBotError

router = APIRouter(prefix="/api/v1/admin/ai")

# Типовые ответы endpoint'ов (для OpenAPI-документации).
_NOT_FOUND = {404: {"description": "Entity not found"}}
_CONFLICT = {409: {"description": "Conflict (uniqueness or dependency)"}}
_NOT_FOUND_CONFLICT = {**_NOT_FOUND, **_CONFLICT}
_UNPROCESSABLE = {422: {"description": "Validation or configuration error"}}

_PROVIDER_NOT_FOUND = "Provider not found"
_ENDPOINT_NOT_FOUND = "Endpoint not found"
_MODEL_NOT_FOUND = "Model not found"


# --- Request/Response DTO -------------------------------------------------------


class ProviderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    slug: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_-]+$")
    protocol: AIProtocol = AIProtocol.OPENAI_COMPATIBLE
    enabled: bool = True
    priority: int = Field(default=100, ge=0, le=1000)


class ProviderPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=100)
    slug: str | None = Field(default=None, min_length=1, max_length=64, pattern=r"^[a-z0-9_-]+$")
    protocol: AIProtocol | None = None
    enabled: bool | None = None
    priority: int | None = Field(default=None, ge=0, le=1000)


class ProviderOut(BaseModel):
    id: int
    name: str
    slug: str
    protocol: str
    enabled: bool
    priority: int
    created_at: str | None = None
    updated_at: str | None = None


class EndpointCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    base_url: str = Field(min_length=8, max_length=500)
    api_key: str | None = Field(default=None, max_length=500)
    timeout_seconds: int = Field(default=60, ge=1, le=600)
    max_retries: int = Field(default=2, ge=0, le=5)
    enabled: bool = True
    priority: int = Field(default=100, ge=0, le=1000)


class EndpointPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=100)
    base_url: str | None = Field(default=None, min_length=8, max_length=500)
    timeout_seconds: int | None = Field(default=None, ge=1, le=600)
    max_retries: int | None = Field(default=None, ge=0, le=5)
    enabled: bool | None = None
    priority: int | None = Field(default=None, ge=0, le=1000)


class EndpointSecretSet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_key: str = Field(min_length=1, max_length=500)


class EndpointOut(BaseModel):
    """Response DTO эндпоинта. Поля api_key/secret_reference НЕТ намеренно."""

    id: int
    provider_id: int
    name: str
    base_url: str
    timeout_seconds: int
    max_retries: int
    enabled: bool
    priority: int
    has_api_key: bool = False
    masked_api_key: str | None = None
    last_test_at: str | None = None
    last_test_status: str | None = None
    last_test_error_type: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class ModelCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())
    model_id: str = Field(min_length=1, max_length=200)
    display_name: str = Field(min_length=1, max_length=200)
    enabled: bool = True
    priority: int = Field(default=100, ge=0, le=1000)
    context_window: int | None = Field(default=None, ge=1, le=10_000_000)
    max_output_tokens: int | None = Field(default=None, ge=1, le=1_000_000)
    supports_structured_output: bool = False
    supports_json_schema: bool = False
    supports_streaming: bool = False


class ModelPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())
    model_id: str | None = Field(default=None, min_length=1, max_length=200)
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    enabled: bool | None = None
    priority: int | None = Field(default=None, ge=0, le=1000)
    context_window: int | None = Field(default=None, ge=1, le=10_000_000)
    max_output_tokens: int | None = Field(default=None, ge=1, le=1_000_000)
    supports_structured_output: bool | None = None
    supports_json_schema: bool | None = None
    supports_streaming: bool | None = None


class ModelOut(BaseModel):
    model_config = ConfigDict(protected_namespaces=())
    id: int
    endpoint_id: int
    model_id: str
    display_name: str
    enabled: bool
    priority: int
    context_window: int | None = None
    max_output_tokens: int | None = None
    supports_structured_output: bool
    supports_json_schema: bool
    supports_streaming: bool
    created_at: str | None = None
    updated_at: str | None = None


class TaskConfigPut(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())
    enabled: bool = False
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1, le=1_000_000)
    timeout_seconds: int = Field(default=120, ge=1, le=600)
    prompt_version: int | None = Field(default=None, ge=1)
    model_ids: list[int] | None = Field(
        default=None,
        description="Упорядоченный список pk моделей: [0]=primary, далее fallback",
    )


class BindingOut(BaseModel):
    model_config = ConfigDict(protected_namespaces=())
    id: int
    task_config_id: int
    model_id: int
    priority: int
    is_primary: bool


class TaskConfigOut(BaseModel):
    id: int
    task_type: str
    enabled: bool
    temperature: float
    max_tokens: int | None = None
    timeout_seconds: int
    prompt_version: int | None = None
    bindings: list[BindingOut] = []
    created_at: str | None = None
    updated_at: str | None = None


class PromptCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_type: AITaskType
    version: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=200)
    system_prompt: str = Field(min_length=1)
    user_template: str = Field(min_length=1)
    enabled: bool = True


class PromptOut(BaseModel):
    id: int
    task_type: str
    version: int
    name: str
    system_prompt: str
    user_template: str
    enabled: bool
    created_at: str | None = None


# --- Helpers --------------------------------------------------------------------


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def _dependency_conflict(exc: AIDependencyError) -> HTTPException:
    """409 с машиночитаемым списком блокеров.

    UI должен объяснить, что именно мешает удалению, а не показывать
    единственную строку текста.
    """
    return HTTPException(
        status_code=409,
        detail={"message": str(exc), "blockers": exc.blockers},
    )


def _provider_out(provider: AIProvider) -> ProviderOut:
    return ProviderOut(
        id=provider.id or 0,
        name=provider.name,
        slug=provider.slug,
        protocol=provider.protocol.value,
        enabled=provider.enabled,
        priority=provider.priority,
        created_at=_iso(provider.created_at),
        updated_at=_iso(provider.updated_at),
    )


async def _endpoint_out(components, endpoint: AIEndpoint) -> EndpointOut:
    has_key = False
    masked = None
    if endpoint.secret_reference:
        view = await components.admin.endpoint_secret_view(endpoint.id or 0)
        has_key = view["has_api_key"]
        masked = view["masked_api_key"]
    return EndpointOut(
        id=endpoint.id or 0,
        provider_id=endpoint.provider_id,
        name=endpoint.name,
        base_url=endpoint.base_url,
        timeout_seconds=endpoint.timeout_seconds,
        max_retries=endpoint.max_retries,
        enabled=endpoint.enabled,
        priority=endpoint.priority,
        has_api_key=has_key,
        masked_api_key=masked,
        last_test_at=_iso(endpoint.last_test_at),
        last_test_status=endpoint.last_test_status,
        last_test_error_type=endpoint.last_test_error_type,
        created_at=_iso(endpoint.created_at),
        updated_at=_iso(endpoint.updated_at),
    )


def _model_out(model: AIModel) -> ModelOut:
    return ModelOut(
        id=model.id or 0,
        endpoint_id=model.endpoint_id,
        model_id=model.model_id,
        display_name=model.display_name,
        enabled=model.enabled,
        priority=model.priority,
        context_window=model.context_window,
        max_output_tokens=model.max_output_tokens,
        supports_structured_output=model.supports_structured_output,
        supports_json_schema=model.supports_json_schema,
        supports_streaming=model.supports_streaming,
        created_at=_iso(model.created_at),
        updated_at=_iso(model.updated_at),
    )


# --- Providers ------------------------------------------------------------------


@router.get("/providers")
async def list_providers(_: Annotated[AuthenticatedUser, Depends(require_viewer)]) -> dict:
    components = build_ai_components()
    providers = await components.providers.list()
    return {"total": len(providers), "items": [_provider_out(p) for p in providers]}


@router.post("/providers", status_code=201, responses=_CONFLICT)
async def create_provider(
    body: ProviderCreate, admin: Annotated[AuthenticatedUser, Depends(require_admin)]
) -> ProviderOut:
    components = build_ai_components()
    provider = AIProvider(
        name=body.name,
        slug=body.slug,
        protocol=body.protocol,
        enabled=body.enabled,
        priority=body.priority,
    )
    try:
        created = await components.admin.create_provider(provider, actor=admin.login)
    except ProfilePersistenceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _provider_out(created)


@router.get("/providers/{provider_id}", responses=_NOT_FOUND)
async def get_provider(
    provider_id: int, _: Annotated[AuthenticatedUser, Depends(require_viewer)]
) -> ProviderOut:
    components = build_ai_components()
    provider = await components.providers.get(provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail=_PROVIDER_NOT_FOUND)
    return _provider_out(provider)


@router.patch("/providers/{provider_id}", responses=_NOT_FOUND_CONFLICT)
async def patch_provider(
    provider_id: int,
    body: ProviderPatch,
    admin: Annotated[AuthenticatedUser, Depends(require_admin)],
) -> ProviderOut:
    components = build_ai_components()
    fields = body.model_dump(exclude_none=True)
    if "protocol" in fields:
        fields["protocol"] = fields["protocol"].value
    try:
        updated = await components.admin.update_provider(provider_id, actor=admin.login, **fields)
    except ProfilePersistenceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if updated is None:
        raise HTTPException(status_code=404, detail=_PROVIDER_NOT_FOUND)
    return _provider_out(updated)


@router.delete("/providers/{provider_id}", status_code=204, responses=_NOT_FOUND_CONFLICT)
async def delete_provider(
    provider_id: int, admin: Annotated[AuthenticatedUser, Depends(require_admin)]
) -> None:
    components = build_ai_components()
    if await components.providers.get(provider_id) is None:
        raise HTTPException(status_code=404, detail=_PROVIDER_NOT_FOUND)
    try:
        deleted = await components.admin.delete_provider(provider_id, actor=admin.login)
    except AIDependencyError as exc:
        raise _dependency_conflict(exc) from exc
    except ProfilePersistenceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail=_PROVIDER_NOT_FOUND)


# --- Endpoints ------------------------------------------------------------------


@router.get("/providers/{provider_id}/endpoints")
async def list_endpoints(
    provider_id: int, _: Annotated[AuthenticatedUser, Depends(require_viewer)]
) -> dict:
    components = build_ai_components()
    endpoints = await components.endpoints.list_for_provider(provider_id)
    items = [await _endpoint_out(components, e) for e in endpoints]
    return {"total": len(items), "items": items}


@router.post("/providers/{provider_id}/endpoints", status_code=201, responses=_NOT_FOUND_CONFLICT)
async def create_endpoint(
    provider_id: int,
    body: EndpointCreate,
    admin: Annotated[AuthenticatedUser, Depends(require_admin)],
) -> EndpointOut:
    components = build_ai_components()
    if await components.providers.get(provider_id) is None:
        raise HTTPException(status_code=404, detail=_PROVIDER_NOT_FOUND)
    endpoint = AIEndpoint(
        provider_id=provider_id,
        name=body.name,
        base_url=body.base_url,
        timeout_seconds=body.timeout_seconds,
        max_retries=body.max_retries,
        enabled=body.enabled,
        priority=body.priority,
    )
    try:
        created = await components.admin.create_endpoint(
            endpoint, api_key=body.api_key, actor=admin.login
        )
    except ProfilePersistenceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return await _endpoint_out(components, created)


@router.patch("/endpoints/{endpoint_id}", responses=_NOT_FOUND_CONFLICT)
async def patch_endpoint(
    endpoint_id: int,
    body: EndpointPatch,
    admin: Annotated[AuthenticatedUser, Depends(require_admin)],
) -> EndpointOut:
    components = build_ai_components()
    fields = body.model_dump(exclude_none=True)
    try:
        updated = await components.admin.update_endpoint(endpoint_id, actor=admin.login, **fields)
    except ProfilePersistenceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if updated is None:
        raise HTTPException(status_code=404, detail=_ENDPOINT_NOT_FOUND)
    return await _endpoint_out(components, updated)


@router.delete("/endpoints/{endpoint_id}", status_code=204, responses=_NOT_FOUND_CONFLICT)
async def delete_endpoint(
    endpoint_id: int, admin: Annotated[AuthenticatedUser, Depends(require_admin)]
) -> None:
    components = build_ai_components()
    if await components.endpoints.get(endpoint_id) is None:
        raise HTTPException(status_code=404, detail=_ENDPOINT_NOT_FOUND)
    try:
        deleted = await components.admin.delete_endpoint(endpoint_id, actor=admin.login)
    except AIDependencyError as exc:
        raise _dependency_conflict(exc) from exc
    except ProfilePersistenceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail=_ENDPOINT_NOT_FOUND)


@router.put("/endpoints/{endpoint_id}/secret", responses=_NOT_FOUND)
async def set_endpoint_secret(
    endpoint_id: int,
    body: EndpointSecretSet,
    admin: Annotated[AuthenticatedUser, Depends(require_admin)],
) -> dict:
    """Установка/ротация API key. Старый ключ заменяется атомарно."""
    components = build_ai_components()
    try:
        await components.admin.rotate_endpoint_secret(endpoint_id, body.api_key, actor=admin.login)
    except AIConfigurationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    view = await components.admin.endpoint_secret_view(endpoint_id)
    return {"has_api_key": view["has_api_key"], "masked_api_key": view["masked_api_key"]}


@router.post("/endpoints/{endpoint_id}/test", responses=_NOT_FOUND)
async def test_endpoint(
    endpoint_id: int, _: Annotated[AuthenticatedUser, Depends(require_admin)]
) -> dict:
    """Connection test: нейтральный минимальный запрос, без персональных данных."""
    components = build_ai_components()
    try:
        return await components.gateway.test_endpoint(endpoint_id)
    except AIConfigurationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# --- Models ---------------------------------------------------------------------


@router.get("/endpoints/{endpoint_id}/models")
async def list_models(
    endpoint_id: int, _: Annotated[AuthenticatedUser, Depends(require_viewer)]
) -> dict:
    components = build_ai_components()
    models = await components.models.list_for_endpoint(endpoint_id)
    return {"total": len(models), "items": [_model_out(m) for m in models]}


@router.post("/endpoints/{endpoint_id}/models", status_code=201, responses=_NOT_FOUND_CONFLICT)
async def create_model(
    endpoint_id: int,
    body: ModelCreate,
    admin: Annotated[AuthenticatedUser, Depends(require_admin)],
) -> ModelOut:
    components = build_ai_components()
    if await components.endpoints.get(endpoint_id) is None:
        raise HTTPException(status_code=404, detail=_ENDPOINT_NOT_FOUND)
    model = AIModel(
        endpoint_id=endpoint_id,
        model_id=body.model_id,
        display_name=body.display_name,
        enabled=body.enabled,
        priority=body.priority,
        context_window=body.context_window,
        max_output_tokens=body.max_output_tokens,
        supports_structured_output=body.supports_structured_output,
        supports_json_schema=body.supports_json_schema,
        supports_streaming=body.supports_streaming,
    )
    try:
        created = await components.admin.create_model(model, actor=admin.login)
    except ProfilePersistenceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _model_out(created)


@router.patch("/models/{model_pk}", responses=_NOT_FOUND_CONFLICT)
async def patch_model(
    model_pk: int,
    body: ModelPatch,
    admin: Annotated[AuthenticatedUser, Depends(require_admin)],
) -> ModelOut:
    components = build_ai_components()
    fields = body.model_dump(exclude_none=True)
    try:
        updated = await components.admin.update_model(model_pk, actor=admin.login, **fields)
    except ProfilePersistenceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if updated is None:
        raise HTTPException(status_code=404, detail=_MODEL_NOT_FOUND)
    return _model_out(updated)


@router.delete("/models/{model_pk}", status_code=204, responses=_NOT_FOUND_CONFLICT)
async def delete_model(
    model_pk: int, admin: Annotated[AuthenticatedUser, Depends(require_admin)]
) -> None:
    components = build_ai_components()
    if await components.models.get(model_pk) is None:
        raise HTTPException(status_code=404, detail=_MODEL_NOT_FOUND)
    try:
        deleted = await components.admin.delete_model(model_pk, actor=admin.login)
    except AIDependencyError as exc:
        raise _dependency_conflict(exc) from exc
    except WorkoutBotError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ProfilePersistenceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail=_MODEL_NOT_FOUND)


# --- Task configs ----------------------------------------------------------------


def _binding_dump(binding: AITaskModelBinding) -> dict:
    return BindingOut(
        id=binding.id or 0,
        task_config_id=binding.task_config_id,
        model_id=binding.model_id,
        priority=binding.priority,
        is_primary=binding.is_primary,
    ).model_dump()


def _task_item(task_type: AITaskType, config: AITaskConfig | None, bindings: list) -> dict:
    return {
        "id": config.id if config else None,
        "task_type": task_type.value,
        "enabled": config.enabled if config else False,
        "temperature": config.temperature if config else 0.7,
        "max_tokens": config.max_tokens if config else None,
        "timeout_seconds": config.timeout_seconds if config else 120,
        "prompt_version": config.prompt_version if config else None,
        "bindings": [_binding_dump(b) for b in bindings],
        "created_at": _iso(config.created_at) if config else None,
        "updated_at": _iso(config.updated_at) if config else None,
    }


@router.get("/tasks")
async def list_tasks(_: Annotated[AuthenticatedUser, Depends(require_viewer)]) -> dict:
    """Все типы задач: существующие конфигурации + дефолты для остальных."""
    components = build_ai_components()
    configs = await components.tasks.list()
    by_type = {c.task_type: c for c in configs}
    items: list[dict] = []
    for task_type in AITaskType:
        config = by_type.get(task_type)
        bindings = (
            await components.tasks.list_bindings(config.id)
            if config and config.id
            else []
        )
        items.append(_task_item(task_type, config, bindings))
    return {"total": len(items), "items": items}


@router.get("/tasks/{task_type}", responses=_NOT_FOUND)
async def get_task(
    task_type: AITaskType, _: Annotated[AuthenticatedUser, Depends(require_viewer)]
) -> TaskConfigOut:
    components = build_ai_components()
    config, bindings = await components.admin.get_task(task_type)
    if config is None:
        raise HTTPException(status_code=404, detail="Task config not found")
    return TaskConfigOut(
        id=config.id or 0,
        task_type=config.task_type.value,
        enabled=config.enabled,
        temperature=config.temperature,
        max_tokens=config.max_tokens,
        timeout_seconds=config.timeout_seconds,
        prompt_version=config.prompt_version,
        bindings=[
            BindingOut(
                id=b.id or 0,
                task_config_id=b.task_config_id,
                model_id=b.model_id,
                priority=b.priority,
                is_primary=b.is_primary,
            )
            for b in bindings
        ],
        created_at=_iso(config.created_at),
        updated_at=_iso(config.updated_at),
    )


@router.put("/tasks/{task_type}", responses=_UNPROCESSABLE)
async def put_task(
    task_type: AITaskType,
    body: TaskConfigPut,
    admin: Annotated[AuthenticatedUser, Depends(require_admin)],
) -> TaskConfigOut:
    components = build_ai_components()
    config = AITaskConfig(
        task_type=task_type,
        enabled=body.enabled,
        temperature=body.temperature,
        max_tokens=body.max_tokens,
        timeout_seconds=body.timeout_seconds,
        prompt_version=body.prompt_version,
    )
    try:
        # Включение задачи в заведомо нерабочем состоянии запрещено на сервере,
        # а не только в UI.
        if config.enabled:
            await components.readiness.validate_enable(config, body.model_ids)
        saved, bindings = await components.admin.configure_task(
            config, model_pks=body.model_ids, actor=admin.login
        )
    except AIConfigurationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return TaskConfigOut(
        id=saved.id or 0,
        task_type=saved.task_type.value,
        enabled=saved.enabled,
        temperature=saved.temperature,
        max_tokens=saved.max_tokens,
        timeout_seconds=saved.timeout_seconds,
        prompt_version=saved.prompt_version,
        bindings=[
            BindingOut(
                id=b.id or 0,
                task_config_id=b.task_config_id,
                model_id=b.model_id,
                priority=b.priority,
                is_primary=b.is_primary,
            )
            for b in bindings
        ],
        created_at=_iso(saved.created_at),
        updated_at=_iso(saved.updated_at),
    )


# --- Prompts ---------------------------------------------------------------------


@router.get("/prompts/{task_type}")
async def list_prompts(
    task_type: AITaskType, _: Annotated[AuthenticatedUser, Depends(require_viewer)]
) -> dict:
    components = build_ai_components()
    templates = await components.prompts.list_for_task(task_type)
    items = [
        PromptOut(
            id=t.id or 0,
            task_type=t.task_type.value,
            version=t.version,
            name=t.name,
            system_prompt=t.system_prompt,
            user_template=t.user_template,
            enabled=t.enabled,
            created_at=_iso(t.created_at),
        ).model_dump()
        for t in templates
    ]
    next_version = await components.admin.next_prompt_version(task_type)
    return {"total": len(items), "items": items, "next_version": next_version}


@router.post("/prompts", status_code=201, responses=_CONFLICT)
async def create_prompt(
    body: PromptCreate, admin: Annotated[AuthenticatedUser, Depends(require_admin)]
) -> PromptOut:
    components = build_ai_components()
    template = PromptTemplate(
        task_type=body.task_type,
        version=body.version,
        name=body.name,
        system_prompt=body.system_prompt,
        user_template=body.user_template,
        enabled=body.enabled,
    )
    try:
        created = await components.admin.create_prompt_version(template, actor=admin.login)
    except WorkoutBotError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ProfilePersistenceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return PromptOut(
        id=created.id or 0,
        task_type=created.task_type.value,
        version=created.version,
        name=created.name,
        system_prompt=created.system_prompt,
        user_template=created.user_template,
        enabled=created.enabled,
        created_at=_iso(created.created_at),
    )


# --- Observability ----------------------------------------------------------------


@router.get("/readiness")
async def task_readiness(
    _: Annotated[AuthenticatedUser, Depends(require_viewer)],
    task_type: AITaskType = AITaskType.WORKOUT_GENERATION,
) -> dict:
    """Сводная готовность AI-задачи: чек-лист, эффективная цепочка, стратегия.

    Живых запросов к провайдеру не выполняет: используется сохранённый
    результат последней проверки подключения.
    """
    components = build_ai_components()
    report = await components.readiness.report(task_type)
    return asdict(report)


@router.get("/usage")
async def recent_usage(_: Annotated[AuthenticatedUser, Depends(require_viewer)]) -> dict:
    components = build_ai_components()
    items = await components.admin.recent_usage(limit=50)
    return {"total": len(items), "items": items}


@router.get("/audit")
async def recent_audit(_: Annotated[AuthenticatedUser, Depends(require_viewer)]) -> dict:
    components = build_ai_components()
    items = await components.admin.recent_audit(limit=50)
    return {"total": len(items), "items": items}


@router.get("/fallback-events")
async def recent_fallback_events(_: Annotated[AuthenticatedUser, Depends(require_viewer)]) -> dict:
    """Почему программа сгенерирована не AI.

    Запрошенный и фактический генератор, машиночитаемая причина, время.
    Персональных данных и содержимого программы здесь нет.
    """
    components = build_ai_components()
    items = await components.admin.recent_fallback_events(limit=50)
    return {"total": len(items), "items": items}


# --- Infrastructure health ---------------------------------------------------------


@router.get("/infrastructure-health")
async def infrastructure_health(_: Annotated[AuthenticatedUser, Depends(require_viewer)]) -> dict:
    """Дерево provider → endpoint → model → задачи с состояниями.

    Строится динамически из конфигурации: новый провайдер или модель
    появляются здесь без изменений во frontend. Запросов к провайдерам не
    делает — используются сохранённый connection test и журнал вызовов.
    """
    components = build_ai_components()
    report = await components.health.report()
    return asdict(report)


@router.post("/infrastructure-health/refresh")
async def refresh_infrastructure_health(
    _: Annotated[AuthenticatedUser, Depends(require_admin)],
) -> dict:
    """Активная проверка включённых эндпоинтов и свежее состояние.

    Использует существующий connection test (минимальный ping), а не
    генерацию программы: health-проверка не должна быть дорогой.
    """
    components = build_ai_components()
    report = await components.health.refresh(components.gateway.test_endpoint)
    return asdict(report)
