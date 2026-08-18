"""API v1: auth, dashboard, profiles, users, exercises.

Внутренний интерфейс: чтение данных из PostgreSQL. Все endpoint'ы,
кроме /auth/login, защищены JWT (require_admin).
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from apps.backend.api.v1.dependencies import build_program_service
from apps.backend.auth import (
    LoginRequest,
    TokenResponse,
    issue_token,
    require_admin,
    verify_credentials,
)
from src.domain.program import WorkoutProgram
from src.errors import ProgramGenerationError, ProgramValidationError
from src.infrastructure.persistence.postgres.db import get_session_factory
from src.infrastructure.persistence.postgres.models import (
    ConsentRow,
    ExerciseRow,
    ProfileRow,
    UserRow,
    WorkoutProgramRow,
)

router = APIRouter(prefix="/api/v1")

# --- Auth ---------------------------------------------------------------------

@router.post(
    "/auth/login",
    responses={401: {"description": "Invalid credentials"}},
)
async def login(body: LoginRequest) -> TokenResponse:
    if not verify_credentials(body.login, body.password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return TokenResponse(access_token=issue_token(body.login))


# --- Dashboard ----------------------------------------------------------------

@router.get("/dashboard")
async def dashboard(_: Annotated[str, Depends(require_admin)]) -> dict:
    async with get_session_factory()() as session:
        users_total = (await session.execute(select(func.count()).select_from(UserRow))).scalar_one()
        profiles_total = (await session.execute(select(func.count()).select_from(ProfileRow))).scalar_one()
        profiles_today = (
            await session.execute(
                select(func.count()).select_from(ProfileRow).where(
                    func.date(ProfileRow.created_at) == func.current_date()
                )
            )
        ).scalar_one()
        exercises_total = (
            await session.execute(
                select(func.count()).select_from(ExerciseRow).where(ExerciseRow.is_active.is_(True))
            )
        ).scalar_one()
        programs_total = (
            await session.execute(select(func.count()).select_from(WorkoutProgramRow))
        ).scalar_one()
    return {
        "users_total": users_total,
        "profiles_total": profiles_total,
        "profiles_today": profiles_today,
        "exercises_total": exercises_total,
        "programs_total": programs_total,
    }


# --- Profiles -----------------------------------------------------------------

@router.get("/profiles")
async def list_profiles(
    _: Annotated[str, Depends(require_admin)],
    search: Annotated[str | None, Query(max_length=100)] = None,
    status: Annotated[str | None, Query(max_length=32)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    stmt = select(ProfileRow)
    if search:
        like = f"%{search}%"
        stmt = stmt.where(
            ProfileRow.profile_id.ilike(like)
            | ProfileRow.display_number.ilike(like)
            | ProfileRow.data["client"]["name"].astext.ilike(like)
        )
    if status:
        stmt = stmt.where(ProfileRow.status == status)

    async with get_session_factory()() as session:
        total = (
            await session.execute(select(func.count()).select_from(stmt.subquery()))
        ).scalar_one()
        rows = (
            (await session.execute(stmt.order_by(ProfileRow.created_at.desc()).limit(limit).offset(offset)))
            .scalars()
            .all()
        )

    items = []
    for row in rows:
        data = row.data or {}
        client = data.get("client", {})
        goals = data.get("goals", {})
        items.append(
            {
                "profile_id": row.profile_id,
                "display_number": row.display_number,
                "name": client.get("name"),
                "age": client.get("age_years"),
                "primary_goal": goals.get("primary"),
                "status": row.status,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
        )
    return {"total": total, "items": items}


@router.get(
    "/profiles/{profile_id}",
    responses={404: {"description": "Profile not found"}},
)
async def get_profile(profile_id: str, _: Annotated[str, Depends(require_admin)]) -> dict:
    async with get_session_factory()() as session:
        row = (
            await session.execute(select(ProfileRow).where(ProfileRow.profile_id == profile_id))
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="Profile not found")
        consents = []
        if row.user_id is not None:
            consent_rows = (
                (await session.execute(select(ConsentRow).where(ConsentRow.user_id == row.user_id)))
                .scalars()
                .all()
            )
            consents = [
                {
                    "consent_type": c.consent_type,
                    "consent_version": c.consent_version,
                    "granted": c.granted,
                    "granted_at": c.granted_at.isoformat() if c.granted_at else None,
                    "source": c.source,
                }
                for c in consent_rows
            ]
    return {
        "profile_id": row.profile_id,
        "display_number": row.display_number,
        "status": row.status,
        "profile_version": row.profile_version,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "data": row.data,
        "consents": consents,
    }


# --- Users --------------------------------------------------------------------

@router.get("/users")
async def list_users(
    _: Annotated[str, Depends(require_admin)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    async with get_session_factory()() as session:
        total = (await session.execute(select(func.count()).select_from(UserRow))).scalar_one()
        rows = (
            (await session.execute(select(UserRow).order_by(UserRow.created_at.desc()).limit(limit).offset(offset)))
            .scalars()
            .all()
        )
    return {
        "total": total,
        "items": [
            {
                "id": r.id,
                "telegram_user_id": r.telegram_user_id,
                "telegram_username": r.telegram_username,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
    }


@router.get(
    "/users/{user_id}",
    responses={404: {"description": "User not found"}},
)
async def get_user(user_id: int, _: Annotated[str, Depends(require_admin)]) -> dict:
    async with get_session_factory()() as session:
        row = (await session.execute(select(UserRow).where(UserRow.id == user_id))).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="User not found")
        profiles = (
            (await session.execute(select(ProfileRow).where(ProfileRow.user_id == user_id)))
            .scalars()
            .all()
        )
    return {
        "id": row.id,
        "telegram_user_id": row.telegram_user_id,
        "telegram_username": row.telegram_username,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "profiles": [
            {"profile_id": p.profile_id, "display_number": p.display_number, "status": p.status}
            for p in profiles
        ],
    }


# --- Exercises ----------------------------------------------------------------

@router.get("/exercises")
async def list_exercises(
    _: Annotated[str, Depends(require_admin)],
    search: Annotated[str | None, Query(max_length=100)] = None,
    exercise_type: Annotated[str | None, Query(max_length=64)] = None,
    difficulty: Annotated[str | None, Query(max_length=32)] = None,
    equipment: Annotated[str | None, Query(max_length=64)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    stmt = select(ExerciseRow).where(ExerciseRow.is_active.is_(True))
    if search:
        like = f"%{search}%"
        stmt = stmt.where(ExerciseRow.name.ilike(like) | ExerciseRow.name_ru.ilike(like))
    if exercise_type:
        stmt = stmt.where(ExerciseRow.exercise_type == exercise_type)
    if difficulty:
        stmt = stmt.where(ExerciseRow.difficulty == difficulty)
    if equipment:
        stmt = stmt.where(ExerciseRow.equipment.contains([equipment]))

    async with get_session_factory()() as session:
        total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
        rows = (
            (await session.execute(stmt.order_by(ExerciseRow.name).limit(limit).offset(offset)))
            .scalars()
            .all()
        )
    return {
        "total": total,
        "items": [
            {
                "id": r.id,
                "external_id": r.external_id,
                "name": r.name,
                "name_ru": r.name_ru,
                "equipment": r.equipment or [],
                "primary_muscles": r.primary_muscles or [],
                "difficulty": r.difficulty,
                "exercise_type": r.exercise_type,
                "source": r.source,
                "is_active": r.is_active,
            }
            for r in rows
        ],
    }


@router.get(
    "/exercises/external/{external_id}",
    responses={404: {"description": "Exercise not found"}},
)
async def get_exercise_by_external_id(
    external_id: str,
    _: Annotated[str, Depends(require_admin)],
    source: Annotated[str | None, Query(max_length=64)] = None,
) -> dict:
    """Поиск упражнения по каноническому external_id (+source).

    Используется web-интерфейсом для перехода из программы на карточку
    упражнения (программы ссылаются на external_id, а не на surrogate id).
    """
    stmt = select(ExerciseRow).where(ExerciseRow.external_id == external_id)
    if source:
        stmt = stmt.where(ExerciseRow.source == source)
    async with get_session_factory()() as session:
        row = (await session.execute(stmt.limit(1))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Exercise not found")
    return {
        "id": row.id,
        "external_id": row.external_id,
        "source": row.source,
        "source_version": row.source_version,
        "name": row.name,
        "name_ru": row.name_ru,
        "aliases": row.aliases or [],
        "description": row.description,
        "technique": row.technique,
        "technique_ru": row.technique_ru,
        "common_mistakes": row.common_mistakes,
        "primary_muscles": row.primary_muscles or [],
        "secondary_muscles": row.secondary_muscles or [],
        "equipment": row.equipment or [],
        "exercise_type": row.exercise_type,
        "difficulty": row.difficulty,
        "force": row.force,
        "mechanic": row.mechanic,
        "contraindications": row.contraindications or [],
        "limitations": row.limitations or [],
        "images": row.images or [],
        "is_active": row.is_active,
    }


@router.get(
    "/exercises/{exercise_id}",
    responses={404: {"description": "Exercise not found"}},
)
async def get_exercise(exercise_id: int, _: Annotated[str, Depends(require_admin)]) -> dict:
    async with get_session_factory()() as session:
        row = (
            await session.execute(select(ExerciseRow).where(ExerciseRow.id == exercise_id))
        ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Exercise not found")
    return {
        "id": row.id,
        "external_id": row.external_id,
        "source": row.source,
        "source_version": row.source_version,
        "name": row.name,
        "name_ru": row.name_ru,
        "aliases": row.aliases or [],
        "description": row.description,
        "technique": row.technique,
        "technique_ru": row.technique_ru,
        "common_mistakes": row.common_mistakes,
        "primary_muscles": row.primary_muscles or [],
        "secondary_muscles": row.secondary_muscles or [],
        "equipment": row.equipment or [],
        "exercise_type": row.exercise_type,
        "difficulty": row.difficulty,
        "force": row.force,
        "mechanic": row.mechanic,
        "contraindications": row.contraindications or [],
        "limitations": row.limitations or [],
        "images": row.images or [],
        "is_active": row.is_active,
    }


# --- Programs -----------------------------------------------------------------


def _program_summary(program: WorkoutProgram) -> dict:
    return {
        "program_id": program.program_id,
        "profile_id": program.profile_id,
        "version": program.version,
        "status": program.status.value,
        "title": program.title,
        "generation_source": program.generation.source.value,
        "generator_version": program.generation.generator_version,
        "training_days_per_week": program.training_days_per_week,
        "duration_weeks": program.duration_weeks,
        "created_at": program.created_at.isoformat() if program.created_at else None,
    }


@router.get("/programs")
async def list_programs(
    _: Annotated[str, Depends(require_admin)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    service = build_program_service()
    total, programs = await service.list_all(limit=limit, offset=offset)
    return {"total": total, "items": [_program_summary(p) for p in programs]}


@router.get(
    "/programs/{program_id}",
    responses={404: {"description": "Program not found"}},
)
async def get_program(
    program_id: str,
    _: Annotated[str, Depends(require_admin)],
    version: Annotated[int | None, Query(ge=1)] = None,
) -> dict:
    service = build_program_service()
    program = await service.get(program_id, version)
    if program is None:
        raise HTTPException(status_code=404, detail="Program not found")
    versions = await service.list_versions(program_id)
    return {
        "program": program.model_dump(mode="json"),
        "versions": [
            {"version": v.version, "status": v.status.value, "created_at": v.created_at.isoformat() if v.created_at else None}
            for v in versions
        ],
    }


@router.get("/profiles/{profile_id}/programs")
async def list_profile_programs(
    profile_id: str, _: Annotated[str, Depends(require_admin)]
) -> dict:
    service = build_program_service()
    programs = await service.list_for_profile(profile_id)
    return {"total": len(programs), "items": [_program_summary(p) for p in programs]}


class GenerateProgramRequest(BaseModel):
    """Запрос генерации программы с выбором генератора."""

    model_config = ConfigDict(extra="forbid")

    generator: str = Field(default="deterministic", pattern=r"^(deterministic|ai)$")
    prompt_version: int | None = Field(default=None, ge=1)


# --- AI Providers (публичный API для UI) ----------------------------------------


@router.get("/ai/providers")
async def list_ai_providers(_: Annotated[str, Depends(require_admin)]) -> dict:
    """Список AI-провайдеров для UI (без секретов).

    Возвращает только безопасную информацию:
    provider_id, display_name, type, enabled, available_models.
    """
    from apps.backend.api.v1.ai_dependencies import build_ai_components

    components = build_ai_components()
    providers = await components.providers.list()

    items = []
    for provider in providers:
        if not provider.enabled:
            continue
        # Получаем модели провайдера через endpoints
        endpoints = await components.endpoints.list_for_provider(provider.id or 0)
        models = []
        for endpoint in endpoints:
            if not endpoint.enabled:
                continue
            endpoint_models = await components.models.list_for_endpoint(endpoint.id or 0)
            models.extend(
                {
                    "model_id": m.model_id,
                    "display_name": m.display_name,
                    "endpoint_id": endpoint.id,
                }
                for m in endpoint_models
                if m.enabled
            )
        items.append(
            {
                "provider_id": provider.id,
                "slug": provider.slug,
                "display_name": provider.name,
                "type": provider.protocol.value,
                "enabled": provider.enabled,
                "available_models": models,
            }
        )
    return {"total": len(items), "items": items}


@router.post(
    "/profiles/{profile_id}/programs/generate",
    responses={
        404: {"description": "Profile not found"},
        422: {"description": "Generation or validation failed"},
    },
)
async def generate_program(
    profile_id: str,
    _: Annotated[str, Depends(require_admin)],
    body: GenerateProgramRequest | None = None,
) -> dict:
    """Запуск генерации программы (deterministic или AI)."""
    request = body or GenerateProgramRequest()
    service = build_program_service(generator_type=request.generator)
    try:
        result = await service.generate(profile_id)
    except ProgramGenerationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ProgramValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "program": result.program.model_dump(mode="json"),
        "pool_stats": {
            "total_exercises": result.candidate_pool.total_exercises,
            "candidates_included": len(result.candidate_pool.included),
            "candidates_excluded": len(result.candidate_pool.excluded),
            "safe_allowed": len(result.safe_pool.allowed),
            "safe_excluded": len(result.safe_pool.excluded),
            "safe_warnings": len(result.safe_pool.warnings),
            "safe_requires_review": len(result.safe_pool.requires_review),
            "active_restrictions": [r.value for r in result.safe_pool.active_restrictions],
        },
    }
