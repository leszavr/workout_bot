"""Admin API v1: база знаний об оборудовании (Gym Knowledge Base).

Чтение доступно любой роли (`require_viewer`), изменение — только администратору
(`require_admin`): словарь оборудования определяет, какие упражнения система
считает выполнимыми, и правка его наблюдателем меняла бы результат генерации.

Ответы формируются отдельными DTO. Строки БД наружу не возвращаются: surrogate
`id`, служебные метки времени и внутренние источники записи клиенту не нужны и
не должны становиться частью публичного контракта.

Совместимость вычисляется здесь же, но не этим слоем: HTTP-обработчик собирает
входные данные и вызывает детерминированный сервис. AI в вычислении не участвует
ни на одном шаге.
"""
from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from apps.backend.api.v1.equipment_dependencies import (
    build_equipment_knowledge_service,
    build_equipment_profile_repository,
    build_exercise_knowledge_repository,
)
from apps.backend.auth import AuthenticatedUser, require_admin, require_viewer
from src.domain.equipment import (
    ID_PATTERN,
    AliasMatchMode,
    CompatibilityResult,
    EquipmentAlias,
    EquipmentAvailability,
    EquipmentItem,
    EquipmentOwnerType,
    EquipmentProfile,
    EquipmentProfileItem,
    EquipmentRequirement,
    EquipmentUsage,
    ExerciseAlternative,
    ExerciseEquipmentRequirement,
    KnowledgeConfidence,
    KnowledgeSource,
)
from src.domain.generation import safe_error_message
from src.errors import ProfilePersistenceError
from src.infrastructure.persistence.postgres.equipment_repository import (
    EquipmentInUseError,
    EquipmentKnowledgeError,
    EquipmentQuery,
)
from src.infrastructure.persistence.postgres.exercise_knowledge_repository import (
    ExerciseKnowledgeError,
    ExerciseRef,
)

router = APIRouter(prefix="/api/v1/admin/knowledge")

_NOT_FOUND = {404: {"description": "Entity not found"}}
_CONFLICT = {409: {"description": "Conflict or entity in use"}}
_UNPROCESSABLE = {422: {"description": "Validation error"}}

_EQUIPMENT_NOT_FOUND = "Equipment not found"

DEFAULT_EXERCISE_SOURCE = "leszavr/workout"


# --- DTO ------------------------------------------------------------------------


class ActiveFilter(StrEnum):
    """Состояние записи словаря.

    Отдельное перечисление вместо `bool | None` — по той же причине, что в
    каталоге упражнений: пустая строка в `?is_active=` не является валидным
    bool, и «показать все» иначе нельзя выразить осознанно.
    """

    ACTIVE = "active"
    INACTIVE = "inactive"
    ALL = "all"


class UsageFilter(StrEnum):
    """Используется ли запись словаря хотя бы одним упражнением."""

    USED = "used"
    UNUSED = "unused"
    ALL = "all"


class AliasIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    alias: str = Field(min_length=1, max_length=120)
    match_mode: AliasMatchMode = AliasMatchMode.EXACT


class EquipmentIn(BaseModel):
    """Создание и обновление записи словаря.

    `equipment_id` задаётся явно и не генерируется: на оборудование ссылаются
    данные, миграции и отчёты, и читаемый стабильный ключ здесь важнее удобства
    ввода.

    `specializes` объявляет запись частным случаем родовой: `leg_press`
    специализирует `resistance_machine`. Требование родового закрывается
    частным, обратное неверно.
    """

    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    equipment_id: str = Field(pattern=ID_PATTERN, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    name_ru: str = Field(min_length=1, max_length=120)
    category: str = Field(pattern=ID_PATTERN, max_length=64)
    description: str | None = Field(default=None, max_length=300)
    capabilities: list[str] = Field(default_factory=list, max_length=50)
    aliases: list[AliasIn] = Field(default_factory=list, max_length=100)
    specializes: str | None = Field(default=None, max_length=64)
    manufacturer: str | None = Field(default=None, max_length=120)
    model_name: str | None = Field(default=None, max_length=120)
    is_active: bool = True


class EquipmentPatch(BaseModel):
    """Частичное изменение. Не переданные поля сохраняют текущее значение."""

    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    name: str | None = Field(default=None, min_length=1, max_length=120)
    name_ru: str | None = Field(default=None, min_length=1, max_length=120)
    category: str | None = Field(default=None, pattern=ID_PATTERN, max_length=64)
    description: str | None = Field(default=None, max_length=300)
    capabilities: list[str] | None = Field(default=None, max_length=50)
    aliases: list[AliasIn] | None = Field(default=None, max_length=100)
    specializes: str | None = Field(default=None, max_length=64)
    manufacturer: str | None = Field(default=None, max_length=120)
    model_name: str | None = Field(default=None, max_length=120)
    is_active: bool | None = None


class RequirementIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    equipment_id: str | None = Field(default=None, max_length=64)
    capability_id: str | None = Field(default=None, max_length=64)
    requirement: EquipmentRequirement = EquipmentRequirement.REQUIRED
    alternative_group: int | None = Field(default=None, ge=1)
    confidence: KnowledgeConfidence = KnowledgeConfidence.CONFIRMED
    notes: str | None = Field(default=None, max_length=300)


class RequirementsIn(BaseModel):
    """Набор требований упражнения целиком.

    Замена, а не добавление: набор требований — единое утверждение об
    упражнении, и «добавить строку» без удаления противоречащей ей дало бы
    невыполнимую комбинацию.
    """

    model_config = ConfigDict(extra="forbid")
    requirements: list[RequirementIn] = Field(default_factory=list, max_length=50)


class ProfileItemIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    equipment_id: str = Field(max_length=64)
    quantity: int | None = Field(default=None, ge=0, le=1000)
    availability: EquipmentAvailability = EquipmentAvailability.AVAILABLE
    confidence: KnowledgeConfidence = KnowledgeConfidence.CONFIRMED
    extra_capabilities: list[str] = Field(default_factory=list, max_length=50)
    notes: str | None = Field(default=None, max_length=300)


class ProfileIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_key: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    owner_type: EquipmentOwnerType = EquipmentOwnerType.USER
    owner_ref: str | None = Field(default=None, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    items: list[ProfileItemIn] = Field(default_factory=list, max_length=200)
    # Что означает отсутствие позиции в профиле. Для домашнего профиля, где
    # перечислено всё, — «нет». Для зала, о котором известно только название, —
    # «неизвестно»: придумывать отсутствие тренажёра нельзя.
    assume_unlisted_unavailable: bool = False
    notes: str | None = Field(default=None, max_length=300)
    is_active: bool = True


class CompatibilityRequest(BaseModel):
    """Проверка совместимости набора упражнений с перечнем оборудования."""

    model_config = ConfigDict(extra="forbid")

    exercise_external_ids: list[str] = Field(min_length=1, max_length=200)
    exercise_source: str = Field(default=DEFAULT_EXERCISE_SOURCE, max_length=64)
    available_equipment: list[str] = Field(default_factory=list, max_length=200)
    unavailable_equipment: list[str] = Field(default_factory=list, max_length=200)
    assume_unlisted_unavailable: bool = False
    profile_key: str | None = Field(default=None, max_length=64)


# --- Сериализация ---------------------------------------------------------------


def _alias_out(alias: EquipmentAlias) -> dict:
    return {"alias": alias.alias, "match_mode": alias.match_mode.value}


def _equipment_out(item: EquipmentItem, *, exercise_count: int | None = None) -> dict:
    payload = {
        "equipment_id": item.equipment_id,
        "name": item.name,
        "name_ru": item.name_ru,
        "category": item.category,
        "description": item.description,
        "capabilities": item.capabilities,
        "aliases": [_alias_out(a) for a in item.aliases],
        "specializes": item.specializes,
        "manufacturer": item.manufacturer,
        "model_name": item.model_name,
        "is_active": item.is_active,
    }
    if exercise_count is not None:
        payload["exercise_count"] = exercise_count
    return payload


def _requirement_out(requirement: ExerciseEquipmentRequirement) -> dict:
    return {
        "equipment_id": requirement.equipment_id,
        "capability_id": requirement.capability_id,
        "requirement": requirement.requirement.value,
        "alternative_group": requirement.alternative_group,
        "confidence": requirement.confidence.value,
        "source": requirement.source.value,
        "notes": requirement.notes,
    }


def _alternative_out(alternative: ExerciseAlternative) -> dict:
    return {
        "alternative_external_id": alternative.alternative_external_id,
        "alternative_source": alternative.alternative_source,
        "substitution": alternative.substitution.value,
        "score": alternative.score,
        "rationale": alternative.rationale,
        "source": alternative.source.value,
    }


def _profile_out(profile: EquipmentProfile) -> dict:
    return {
        "profile_key": profile.profile_key,
        "owner_type": profile.owner_type.value,
        "owner_ref": profile.owner_ref,
        "name": profile.name,
        "assume_unlisted_unavailable": profile.assume_unlisted_unavailable,
        "notes": profile.notes,
        "is_active": profile.is_active,
        "items": [
            {
                "equipment_id": item.equipment_id,
                "quantity": item.quantity,
                "availability": item.availability.value,
                "confidence": item.confidence.value,
                "extra_capabilities": item.extra_capabilities,
                "source": item.source.value,
                "notes": item.notes,
            }
            for item in profile.items
        ],
    }


def _compatibility_out(result: CompatibilityResult) -> dict:
    return {
        "exercise_external_id": result.exercise_external_id,
        "status": result.status.value,
        "reason": result.reason.value,
        "missing": result.missing,
        "matched": result.matched,
        "unknown": result.unknown,
        "checks": [
            {
                "requirement": check.requirement.value,
                "alternative_group": check.alternative_group,
                "equipment_id": check.equipment_id,
                "capability_id": check.capability_id,
                "availability": check.availability.value,
                "satisfied_by": check.satisfied_by,
            }
            for check in result.checks
        ],
    }


def _knowledge_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, EquipmentInUseError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, (EquipmentKnowledgeError, ExerciseKnowledgeError)):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=422, detail=safe_error_message(exc))


# --- Словарь оборудования -------------------------------------------------------


@router.get("/equipment")
async def list_equipment(
    _: Annotated[AuthenticatedUser, Depends(require_viewer)],
    search: Annotated[str | None, Query(max_length=100)] = None,
    category: Annotated[list[str] | None, Query()] = None,
    capability: Annotated[list[str] | None, Query()] = None,
    is_active: Annotated[ActiveFilter, Query()] = ActiveFilter.ACTIVE,
    usage: Annotated[UsageFilter, Query()] = UsageFilter.ALL,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    """Страница словаря с серверными фильтрами и числом связанных упражнений.

    Пагинация серверная, как и в каталоге упражнений: словарь пополняется, и
    выгрузка целиком в браузер перестанет работать незаметно.
    """
    query = EquipmentQuery(
        search=search,
        categories=_clean(category),
        capabilities=_clean(capability),
        is_active=None
        if is_active is ActiveFilter.ALL
        else is_active is ActiveFilter.ACTIVE,
        in_use=None if usage is UsageFilter.ALL else usage is UsageFilter.USED,
    )
    service = build_equipment_knowledge_service()
    try:
        total, items = await service.search_equipment(query, limit=limit, offset=offset)
    except ProfilePersistenceError as exc:
        raise HTTPException(status_code=422, detail=safe_error_message(exc)) from exc
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [
            _equipment_out(usage_row.item, exercise_count=usage_row.exercise_count)
            for usage_row in items
        ],
    }


@router.get("/equipment/categories")
async def list_categories(
    _: Annotated[AuthenticatedUser, Depends(require_viewer)],
) -> dict:
    service = build_equipment_knowledge_service()
    return {"items": await service.categories()}


@router.get("/capabilities")
async def list_capabilities(
    _: Annotated[AuthenticatedUser, Depends(require_viewer)],
) -> dict:
    service = build_equipment_knowledge_service()
    items = await service.list_capabilities()
    return {
        "total": len(items),
        "items": [
            {
                "capability_id": c.capability_id,
                "name": c.name,
                "name_ru": c.name_ru,
                "description": c.description,
                "is_active": c.is_active,
            }
            for c in items
        ],
    }


@router.get("/equipment/{equipment_id}", responses=_NOT_FOUND)
async def get_equipment(
    equipment_id: str, _: Annotated[AuthenticatedUser, Depends(require_viewer)]
) -> dict:
    service = build_equipment_knowledge_service()
    item = await service.get_equipment(equipment_id)
    if item is None:
        raise HTTPException(status_code=404, detail=_EQUIPMENT_NOT_FOUND)
    counts = await build_exercise_knowledge_repository().requirement_counts_by_equipment()
    return _equipment_out(item, exercise_count=counts.get(equipment_id, 0))


@router.post("/equipment", status_code=201, responses={**_CONFLICT, **_UNPROCESSABLE})
async def create_equipment(
    body: EquipmentIn, _: Annotated[AuthenticatedUser, Depends(require_admin)]
) -> dict:
    service = build_equipment_knowledge_service()
    if await service.get_equipment(body.equipment_id) is not None:
        raise HTTPException(
            status_code=409, detail=f"Оборудование {body.equipment_id} уже существует"
        )
    item = _item_from_in(body)
    try:
        created = await service.save_equipment(item)
    except (EquipmentKnowledgeError, ProfilePersistenceError) as exc:
        raise _knowledge_http_error(exc) from exc
    return _equipment_out(created)


@router.patch(
    "/equipment/{equipment_id}", responses={**_NOT_FOUND, **_UNPROCESSABLE}
)
async def patch_equipment(
    equipment_id: str,
    body: EquipmentPatch,
    _: Annotated[AuthenticatedUser, Depends(require_admin)],
) -> dict:
    service = build_equipment_knowledge_service()
    current = await service.get_equipment(equipment_id)
    if current is None:
        raise HTTPException(status_code=404, detail=_EQUIPMENT_NOT_FOUND)
    updated = current.model_copy(
        update={
            key: value
            for key, value in {
                "name": body.name,
                "name_ru": body.name_ru,
                "category": body.category,
                "description": body.description,
                "capabilities": body.capabilities,
                "aliases": (
                    [
                        EquipmentAlias(
                            alias=a.alias,
                            match_mode=a.match_mode,
                            source=KnowledgeSource.ADMIN,
                        )
                        for a in body.aliases
                    ]
                    if body.aliases is not None
                    else None
                ),
                "specializes": body.specializes,
                "manufacturer": body.manufacturer,
                "model_name": body.model_name,
                "is_active": body.is_active,
            }.items()
            if value is not None
        }
    )
    try:
        saved = await service.save_equipment(updated)
    except (EquipmentKnowledgeError, ProfilePersistenceError) as exc:
        raise _knowledge_http_error(exc) from exc
    return _equipment_out(saved)


@router.post("/equipment/{equipment_id}/deactivate", responses=_NOT_FOUND)
async def deactivate_equipment(
    equipment_id: str, _: Annotated[AuthenticatedUser, Depends(require_admin)]
) -> dict:
    """Скрывает оборудование, не разрывая ссылки на него.

    Основной способ вывести запись из обращения: требования упражнений,
    ссылающиеся на неё, остаются историческим фактом.
    """
    service = build_equipment_knowledge_service()
    if not await service.deactivate_equipment(equipment_id):
        raise HTTPException(status_code=404, detail=_EQUIPMENT_NOT_FOUND)
    item = await service.get_equipment(equipment_id)
    return _equipment_out(item) if item else {"equipment_id": equipment_id}


@router.delete(
    "/equipment/{equipment_id}", status_code=204, responses={**_NOT_FOUND, **_CONFLICT}
)
async def delete_equipment(
    equipment_id: str, _: Annotated[AuthenticatedUser, Depends(require_admin)]
) -> None:
    service = build_equipment_knowledge_service()
    try:
        deleted = await service.delete_equipment(equipment_id)
    except (EquipmentInUseError, ProfilePersistenceError) as exc:
        raise _knowledge_http_error(exc) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail=_EQUIPMENT_NOT_FOUND)


# --- Требования упражнения ------------------------------------------------------


@router.get("/exercises/{external_id}/requirements")
async def list_requirements(
    external_id: str,
    _: Annotated[AuthenticatedUser, Depends(require_viewer)],
    source: Annotated[str, Query(max_length=64)] = DEFAULT_EXERCISE_SOURCE,
) -> dict:
    service = build_equipment_knowledge_service()
    ref = ExerciseRef(external_id, source)
    requirements = await service.list_requirements(ref)
    return {
        "exercise_external_id": external_id,
        "exercise_source": source,
        "total": len(requirements),
        "items": [_requirement_out(r) for r in requirements],
    }


@router.put(
    "/exercises/{external_id}/requirements", responses={**_NOT_FOUND, **_UNPROCESSABLE}
)
async def replace_requirements(
    external_id: str,
    body: RequirementsIn,
    _: Annotated[AuthenticatedUser, Depends(require_admin)],
    source: Annotated[str, Query(max_length=64)] = DEFAULT_EXERCISE_SOURCE,
) -> dict:
    service = build_equipment_knowledge_service()
    ref = ExerciseRef(external_id, source)
    try:
        requirements = [
            ExerciseEquipmentRequirement(
                exercise_external_id=external_id,
                exercise_source=source,
                equipment_id=item.equipment_id,
                capability_id=item.capability_id,
                requirement=item.requirement,
                alternative_group=item.alternative_group,
                confidence=item.confidence,
                source=KnowledgeSource.ADMIN,
                notes=item.notes,
            )
            for item in body.requirements
        ]
    except ValueError as exc:
        # Нарушение правил самой модели: заполнены обе ссылки или ALTERNATIVE без
        # группы. Это ошибка запроса, а не сбой.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        saved = await service.replace_requirements(ref, requirements)
    except (
        EquipmentKnowledgeError,
        ExerciseKnowledgeError,
        ProfilePersistenceError,
    ) as exc:
        raise _knowledge_http_error(exc) from exc
    return {
        "exercise_external_id": external_id,
        "exercise_source": source,
        "total": len(saved),
        "items": [_requirement_out(r) for r in saved],
    }


@router.get("/exercises/{external_id}/alternatives")
async def list_alternatives(
    external_id: str,
    _: Annotated[AuthenticatedUser, Depends(require_viewer)],
    source: Annotated[str, Query(max_length=64)] = DEFAULT_EXERCISE_SOURCE,
) -> dict:
    service = build_equipment_knowledge_service()
    alternatives = await service.list_alternatives(ExerciseRef(external_id, source))
    return {
        "exercise_external_id": external_id,
        "exercise_source": source,
        "total": len(alternatives),
        "items": [_alternative_out(a) for a in alternatives],
    }


# --- Профили оборудования -------------------------------------------------------


@router.get("/profiles")
async def list_profiles(
    _: Annotated[AuthenticatedUser, Depends(require_viewer)],
    owner_type: Annotated[EquipmentOwnerType | None, Query()] = None,
) -> dict:
    repository = build_equipment_profile_repository()
    profiles = await repository.list(owner_type=owner_type)
    return {"total": len(profiles), "items": [_profile_out(p) for p in profiles]}


@router.get("/profiles/{profile_key}", responses=_NOT_FOUND)
async def get_profile(
    profile_key: str, _: Annotated[AuthenticatedUser, Depends(require_viewer)]
) -> dict:
    profile = await build_equipment_profile_repository().get(profile_key)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    return _profile_out(profile)


@router.put("/profiles/{profile_key}", responses=_UNPROCESSABLE)
async def upsert_profile(
    profile_key: str,
    body: ProfileIn,
    _: Annotated[AuthenticatedUser, Depends(require_admin)],
) -> dict:
    if body.profile_key != profile_key:
        raise HTTPException(
            status_code=422, detail="profile_key в пути и в теле должны совпадать"
        )
    profile = EquipmentProfile(
        profile_key=profile_key,
        owner_type=body.owner_type,
        owner_ref=body.owner_ref,
        name=body.name,
        items=[
            EquipmentProfileItem(
                equipment_id=item.equipment_id,
                quantity=item.quantity,
                availability=item.availability,
                confidence=item.confidence,
                extra_capabilities=item.extra_capabilities,
                source=KnowledgeSource.ADMIN,
                notes=item.notes,
            )
            for item in body.items
        ],
        assume_unlisted_unavailable=body.assume_unlisted_unavailable,
        source=KnowledgeSource.ADMIN,
        notes=body.notes,
        is_active=body.is_active,
    )
    try:
        saved = await build_equipment_profile_repository().upsert(profile)
    except (EquipmentKnowledgeError, ProfilePersistenceError) as exc:
        raise _knowledge_http_error(exc) from exc
    return _profile_out(saved)


@router.delete("/profiles/{profile_key}", status_code=204, responses=_NOT_FOUND)
async def delete_profile(
    profile_key: str, _: Annotated[AuthenticatedUser, Depends(require_admin)]
) -> None:
    if not await build_equipment_profile_repository().delete(profile_key):
        raise HTTPException(status_code=404, detail="Profile not found")


# --- Совместимость --------------------------------------------------------------


@router.post("/compatibility", responses={**_NOT_FOUND, **_UNPROCESSABLE})
async def check_compatibility(
    body: CompatibilityRequest,
    _: Annotated[AuthenticatedUser, Depends(require_viewer)],
) -> dict:
    """Детерминированная проверка «упражнения × оборудование».

    Оборудование задаётся либо перечислением, либо ссылкой на профиль. AI в
    вычислении не участвует: результат зависит только от данных базы знаний.
    """
    service = build_equipment_knowledge_service()
    refs = [
        ExerciseRef(external_id, body.exercise_source)
        for external_id in body.exercise_external_ids
    ]
    if body.profile_key:
        profile = await build_equipment_profile_repository().get(body.profile_key)
        if profile is None:
            raise HTTPException(status_code=404, detail="Profile not found")
        results = await service.check_against_profile(refs, profile)
    else:
        from src.application.equipment.compatibility import AvailableEquipment

        results = await service.check_compatibility(
            refs,
            AvailableEquipment(
                available=frozenset(body.available_equipment),
                unavailable=frozenset(body.unavailable_equipment),
                assume_unlisted_unavailable=body.assume_unlisted_unavailable,
            ),
        )
    return {
        "exercise_source": body.exercise_source,
        "items": [
            _compatibility_out(results[ref.as_key()])
            for ref in refs
            if ref.as_key() in results
        ],
    }


# --- Health ---------------------------------------------------------------------


@router.get("/health")
async def knowledge_health(
    _: Annotated[AuthenticatedUser, Depends(require_viewer)],
) -> dict:
    """Полнота и целостность базы знаний. Все числа считаются из базы."""
    service = build_equipment_knowledge_service()
    health = await service.health()
    return {
        **health.model_dump(),
        "equipment_known_ratio": round(health.equipment_known_ratio, 4),
        "unmapped_summary": await service.unmapped_summary(),
    }


@router.get("/unmapped")
async def list_unmapped(
    _: Annotated[AuthenticatedUser, Depends(require_viewer)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    """Значения оборудования источника, не получившие canonical ID.

    Существует, чтобы пробел в данных был видим: без этого «оборудование не
    распознано» неотличимо от «оборудование не нужно».
    """
    repository = build_exercise_knowledge_repository()
    total, items = await repository.list_unmapped(limit=limit, offset=offset)
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [
            {
                "exercise_external_id": item.exercise_external_id,
                "exercise_source": item.exercise_source,
                "raw_value": item.raw_value,
                "reason": item.reason.value,
                "notes": item.notes,
            }
            for item in items
        ],
    }


# --- Вспомогательное ------------------------------------------------------------


def _clean(values: list[str] | None) -> tuple[str, ...]:
    """Непустые значения фильтра без дублей, с сохранением порядка."""
    if not values:
        return ()
    return tuple(dict.fromkeys(v for v in (value.strip() for value in values) if v))


def _item_from_in(body: EquipmentIn) -> EquipmentItem:
    return EquipmentItem(
        equipment_id=body.equipment_id,
        name=body.name,
        name_ru=body.name_ru,
        category=body.category,
        description=body.description,
        capabilities=body.capabilities,
        aliases=[
            EquipmentAlias(
                alias=a.alias, match_mode=a.match_mode, source=KnowledgeSource.ADMIN
            )
            for a in body.aliases
        ],
        specializes=body.specializes,
        manufacturer=body.manufacturer,
        model_name=body.model_name,
        source=KnowledgeSource.ADMIN,
        is_active=body.is_active,
    )
