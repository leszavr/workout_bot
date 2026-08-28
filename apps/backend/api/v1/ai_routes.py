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
from src.domain.ai.enums import IMPLEMENTED_TASK_TYPES, AIProtocol, AITaskType
from src.domain.ai.errors import AIConfigurationError, AIError
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


class DiscoveredModelOut(BaseModel):
    """Модель, предложенная самим сервисом (ещё не сохранённая)."""

    model_config = ConfigDict(protected_namespaces=())
    model_id: str
    display_name: str
    owned_by: str | None = None
    # Уже добавлена на этом подключении: список должен показывать это сразу.
    already_added: bool = False


class ModelsBulkAdd(BaseModel):
    """Выбранные из списка сервиса модели."""

    model_config = ConfigDict(extra="forbid", protected_namespaces=())
    model_ids: list[str] = Field(min_length=1, max_length=100)


class ModelsProbe(BaseModel):
    """Параметры ещё не сохранённого подключения для запроса списка моделей."""

    model_config = ConfigDict(extra="forbid")
    base_url: str = Field(min_length=8, max_length=500)
    api_key: str | None = Field(default=None, max_length=500)
    protocol: AIProtocol = AIProtocol.OPENAI_COMPATIBLE


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
    # Номер версии необязателен: администратор итеративно правит инструкцию, а
    # не ведёт нумерацию вручную. Без него берётся следующий свободный.
    version: int | None = Field(default=None, ge=1)
    name: str = Field(min_length=1, max_length=200)
    system_prompt: str = Field(min_length=1, max_length=200_000)
    user_template: str = Field(min_length=1, max_length=200_000)
    enabled: bool = True


class PromptPatch(BaseModel):
    """Правка существующей инструкции.

    `task_type` и `version` не меняются: это идентичность промпта, на которую
    ссылается конфигурация задачи.
    """

    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=200)
    system_prompt: str | None = Field(default=None, min_length=1, max_length=200_000)
    user_template: str | None = Field(default=None, min_length=1, max_length=200_000)
    enabled: bool | None = None


class PromptOut(BaseModel):
    """Полное представление инструкции: текст не усечён.

    Списочный ответ отдаёт превью отдельным полем, а сам текст остаётся целым:
    администратор должен видеть ровно то, что уходит в модель.
    """

    id: int
    task_type: str
    version: int
    name: str
    system_prompt: str
    user_template: str
    enabled: bool
    # Выбрана в настройках задачи: удалять такую нельзя.
    in_use: bool = False
    created_at: str | None = None
    updated_at: str | None = None


class PromptListItem(BaseModel):
    """Строка списка: без полных текстов, но с превью для узнавания."""

    id: int
    task_type: str
    version: int
    name: str
    enabled: bool
    in_use: bool = False
    system_prompt_preview: str
    system_prompt_length: int
    user_template_length: int
    created_at: str | None = None
    updated_at: str | None = None


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


def _display_name(model_id: str) -> str:
    """Читаемое имя из идентификатора модели: «vendor/name:free» → «name».

    Логика та же, что при разборе списка моделей сервиса: администратор
    выбирает модели галочками, отдельного поля имени в этом сценарии нет.
    """
    name = model_id.split("/")[-1]
    return name.split(":")[0] or model_id


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


@router.post("/probe-models", responses={502: {"description": "Provider call failed"}})
async def probe_models(
    body: ModelsProbe, _: Annotated[AuthenticatedUser, Depends(require_admin)]
) -> dict:
    """Список моделей по введённым адресу и ключу, до создания подключения.

    Нужно на первичной настройке: модель выбирается из списка сервиса, а не
    переписывается из документации. Переданный ключ не сохраняется.
    """
    components = build_ai_components()
    try:
        discovered = await components.gateway.probe_models(
            protocol=body.protocol,
            base_url=body.base_url,
            api_key=body.api_key,
        )
    except AIError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Не удалось получить список моделей: {exc}",
        ) from exc
    items = [
        DiscoveredModelOut(
            model_id=m.model_id, display_name=m.display_name, owned_by=m.owned_by
        )
        for m in discovered
    ]
    return {"total": len(items), "items": items}


@router.get("/endpoints/{endpoint_id}/available-models", responses={**_NOT_FOUND, 502: {"description": "Provider call failed"}})
async def discover_endpoint_models(
    endpoint_id: int, _: Annotated[AuthenticatedUser, Depends(require_admin)]
) -> dict:
    """Модели, которые сервис отдаёт сам (справочный запрос к провайдеру).

    Ничего не сохраняет: администратор выбирает нужные и добавляет их
    отдельным действием. Уже добавленные помечаются, чтобы список не
    предлагал дубликаты.
    """
    components = build_ai_components()
    if await components.endpoints.get(endpoint_id) is None:
        raise HTTPException(status_code=404, detail=_ENDPOINT_NOT_FOUND)
    try:
        discovered = await components.gateway.discover_models(endpoint_id)
    except AIConfigurationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AIError as exc:
        # Сбой на стороне сервиса — не ошибка запроса администратора.
        raise HTTPException(
            status_code=502,
            detail=f"Не удалось получить список моделей: {exc}",
        ) from exc

    saved = {m.model_id for m in await components.models.list_for_endpoint(endpoint_id)}
    items = [
        DiscoveredModelOut(
            model_id=m.model_id,
            display_name=m.display_name,
            owned_by=m.owned_by,
            already_added=m.model_id in saved,
        )
        for m in discovered
    ]
    return {"total": len(items), "items": items}


@router.post("/endpoints/{endpoint_id}/models/bulk", status_code=201, responses=_NOT_FOUND_CONFLICT)
async def add_models_bulk(
    endpoint_id: int,
    body: ModelsBulkAdd,
    admin: Annotated[AuthenticatedUser, Depends(require_admin)],
) -> dict:
    """Добавляет отмеченные в списке модели одним действием."""
    components = build_ai_components()
    if await components.endpoints.get(endpoint_id) is None:
        raise HTTPException(status_code=404, detail=_ENDPOINT_NOT_FOUND)

    # Имя для показа выводим из идентификатора: отдельного ввода на каждую
    # модель в списочном выборе нет, переименовать можно потом.
    pairs = [(mid.strip(), _display_name(mid.strip())) for mid in body.model_ids if mid.strip()]
    if not pairs:
        raise HTTPException(status_code=422, detail="Не выбрано ни одной модели")
    try:
        created, skipped = await components.admin.add_models(
            endpoint_id, pairs, actor=admin.login
        )
    except ProfilePersistenceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "added": [_model_out(m) for m in created],
        "skipped": skipped,
    }


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
    """Задачи, которые система действительно выполняет.

    Нереализованные типы наружу не выдаются: настройка, которая ни на что не
    влияет, только путает администратора.
    """
    components = build_ai_components()
    configs = await components.tasks.list()
    by_type = {c.task_type: c for c in configs}
    items: list[dict] = []
    for task_type in sorted(IMPLEMENTED_TASK_TYPES, key=lambda t: t.value):
        config = by_type.get(task_type)
        bindings = (
            await components.tasks.list_bindings(config.id)
            if config and config.id
            else []
        )
        items.append(_task_item(task_type, config, bindings))
    return {"total": len(items), "items": items}


def _ensure_task_implemented(task_type: AITaskType) -> None:
    """Нереализованную задачу нельзя ни читать, ни настраивать.

    Иначе в системе появляется конфигурация, которую никогда никто не вызовет.
    """
    if task_type not in IMPLEMENTED_TASK_TYPES:
        raise HTTPException(
            status_code=404,
            detail=f"Задача «{task_type.value}» системой не выполняется",
        )


@router.get("/tasks/{task_type}", responses=_NOT_FOUND)
async def get_task(
    task_type: AITaskType, _: Annotated[AuthenticatedUser, Depends(require_viewer)]
) -> TaskConfigOut:
    _ensure_task_implemented(task_type)
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
    _ensure_task_implemented(task_type)
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
#
# Инструкция для модели — не деталь реализации, а рабочий материал
# администратора: без возможности прочитать и поправить её приходилось лезть в
# базу или пересобирать образ на каждую итерацию промпт-инжиниринга.

_PROMPT_NOT_FOUND = "Prompt not found"
# Превью в списке: узнать инструкцию по началу текста, не загружая её целиком.
_PROMPT_PREVIEW_CHARS = 160


def _prompt_out(template: PromptTemplate, *, in_use: bool) -> PromptOut:
    return PromptOut(
        id=template.id or 0,
        task_type=template.task_type.value,
        version=template.version,
        name=template.name,
        system_prompt=template.system_prompt,
        user_template=template.user_template,
        enabled=template.enabled,
        in_use=in_use,
        created_at=_iso(template.created_at),
        updated_at=_iso(template.updated_at),
    )


def _prompt_list_item(template: PromptTemplate, *, in_use: bool) -> PromptListItem:
    preview = " ".join(template.system_prompt.split())[:_PROMPT_PREVIEW_CHARS]
    return PromptListItem(
        id=template.id or 0,
        task_type=template.task_type.value,
        version=template.version,
        name=template.name,
        enabled=template.enabled,
        in_use=in_use,
        system_prompt_preview=preview,
        system_prompt_length=len(template.system_prompt),
        user_template_length=len(template.user_template),
        created_at=_iso(template.created_at),
        updated_at=_iso(template.updated_at),
    )


async def _prompt_in_use(components, template: PromptTemplate) -> bool:
    """Выбрана ли инструкция в настройках задачи.

    Ссылка логическая (`ai_task_configs.prompt_version`), поэтому проверяется
    сервисным слоем, а не внешним ключом.
    """
    dependencies = await components.admin.prompt_dependencies(template.id or 0)
    return not dependencies.safe


@router.get("/prompts/{task_type}")
async def list_prompts(
    task_type: AITaskType, _: Annotated[AuthenticatedUser, Depends(require_viewer)]
) -> dict:
    """Список инструкций задачи: превью, метаданные, признак использования."""
    _ensure_task_implemented(task_type)
    components = build_ai_components()
    templates = await components.prompts.list_for_task(task_type)
    config = await components.tasks.get(task_type)
    active_version = config.prompt_version if config else None
    items = [
        _prompt_list_item(t, in_use=t.version == active_version).model_dump()
        for t in templates
    ]
    next_version = await components.admin.next_prompt_version(task_type)
    return {
        "total": len(items),
        "items": items,
        "next_version": next_version,
        "active_version": active_version,
    }


@router.get("/prompts/detail/{prompt_id}", responses=_NOT_FOUND)
async def get_prompt(
    prompt_id: int, _: Annotated[AuthenticatedUser, Depends(require_viewer)]
) -> PromptOut:
    """Полный текст инструкции. Ничего не усекается.

    Путь с префиксом `detail`, потому что `/prompts/{task_type}` уже занят
    списком: числовой id и тип задачи иначе разбирались бы одним маршрутом.
    """
    components = build_ai_components()
    template = await components.admin.get_prompt(prompt_id)
    if template is None:
        raise HTTPException(status_code=404, detail=_PROMPT_NOT_FOUND)
    return _prompt_out(template, in_use=await _prompt_in_use(components, template))


@router.post("/prompts", status_code=201, responses=_CONFLICT)
async def create_prompt(
    body: PromptCreate, admin: Annotated[AuthenticatedUser, Depends(require_admin)]
) -> PromptOut:
    _ensure_task_implemented(body.task_type)
    components = build_ai_components()
    version = body.version or await components.admin.next_prompt_version(body.task_type)
    template = PromptTemplate(
        task_type=body.task_type,
        version=version,
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
    return _prompt_out(created, in_use=False)


@router.patch("/prompts/detail/{prompt_id}", responses=_NOT_FOUND)
async def patch_prompt(
    prompt_id: int,
    body: PromptPatch,
    admin: Annotated[AuthenticatedUser, Depends(require_admin)],
) -> PromptOut:
    """Правка текста и метаданных существующей инструкции."""
    components = build_ai_components()
    fields = body.model_dump(exclude_none=True)
    updated = await components.admin.update_prompt(
        prompt_id, actor=admin.login, **fields
    )
    if updated is None:
        raise HTTPException(status_code=404, detail=_PROMPT_NOT_FOUND)
    return _prompt_out(updated, in_use=await _prompt_in_use(components, updated))


@router.delete("/prompts/detail/{prompt_id}", status_code=204, responses=_NOT_FOUND_CONFLICT)
async def delete_prompt(
    prompt_id: int, admin: Annotated[AuthenticatedUser, Depends(require_admin)]
) -> None:
    """Удаление инструкции. Выбранную задачей удалить нельзя (409)."""
    components = build_ai_components()
    if await components.admin.get_prompt(prompt_id) is None:
        raise HTTPException(status_code=404, detail=_PROMPT_NOT_FOUND)
    try:
        deleted = await components.admin.delete_prompt(prompt_id, actor=admin.login)
    except AIDependencyError as exc:
        raise _dependency_conflict(exc) from exc
    except ProfilePersistenceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail=_PROMPT_NOT_FOUND)


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


@router.get("/model-attempts")
async def recent_model_attempts(_: Annotated[AuthenticatedUser, Depends(require_viewer)]) -> dict:
    """Что происходило с моделями внутри одной AI-генерации.

    Отвечает на вопрос, на который журнал вызовов ответить не может: прошёл ли
    первый ответ модели, запрашивалось ли исправление, почему модель была
    оставлена и перешла ли система к следующей. Промпты, ответы моделей и
    персональные данные здесь не хранятся.
    """
    components = build_ai_components()
    items = await components.admin.recent_model_attempts(limit=50)
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
