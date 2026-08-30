"""API v1: auth, dashboard, profiles, users, exercises.

Внутренний интерфейс: чтение данных из PostgreSQL. Все endpoint'ы,
кроме /auth/login, защищены JWT. Чтение доступно любой роли
(`require_viewer`), изменение — только роли admin (`require_admin`).
"""
from __future__ import annotations

import logging
from dataclasses import asdict
from enum import StrEnum
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from apps.backend.api.v1.dependencies import (
    build_delivery_repository,
    build_exercise_repository,
    build_generation_orchestrator,
    build_profile_admin_service,
    build_program_html_service,
    build_program_service,
)
from apps.backend.api.v1.user_dependencies import build_admin_user_service
from apps.backend.auth import (
    AuthenticatedUser,
    ChangePasswordRequest,
    LoginRequest,
    TokenResponse,
    current_user,
    issue_token,
    require_admin,
    require_viewer,
    verify_env_admin,
)
from src.application.auth.service import AdminUserError
from src.application.deletion import DeleteBlockedError
from src.application.programs.orchestrator import (
    GenerationRequest,
    OrchestratorResult,
)
from src.application.programs.telegram_delivery import build_filename
from src.domain.auth import AdminRole
from src.domain.enums import ProgramDeliveryStatus
from src.domain.generation import (
    GenerationErrorCode,
    GenerationTrigger,
    safe_error_message,
)
from src.domain.program import WorkoutProgram
from src.errors import (
    GenerationAlreadyRunningError,
    GenerationFailedError,
    HtmlRenderError,
    IdempotencyKeyConflictError,
    ProfilePersistenceError,
    ProgramDeliveryError,
    ProgramGenerationError,
    ProgramPersistenceError,
)
from src.infrastructure.persistence.postgres.db import get_session_factory
from src.infrastructure.persistence.postgres.exercise_repository import ExerciseQuery
from src.infrastructure.persistence.postgres.models import (
    ConsentRow,
    ExerciseRow,
    ProfileRow,
    ProgramDeliveryRow,
    UserRow,
    WorkoutProgramRow,
)

router = APIRouter(prefix="/api/v1")

logger = logging.getLogger(__name__)

# --- Auth ---------------------------------------------------------------------


class CurrentUserOut(BaseModel):
    """Кто вошёл. Хеш пароля и секреты сюда не попадают."""

    login: str
    role: str
    display_name: str | None = None
    must_change_password: bool = False
    # true — вход выполнен аварийным администратором из переменных окружения.
    is_env_admin: bool = False
    can_write: bool = False


@router.post(
    "/auth/login",
    responses={401: {"description": "Invalid credentials"}},
)
async def login(body: LoginRequest) -> TokenResponse:
    """Вход: сначала пользователи из БД, затем аварийный env-администратор.

    Причина отказа не детализируется намеренно: ответ не должен подсказывать,
    существует ли такой логин.
    """
    service = build_admin_user_service()
    user = await service.authenticate(body.login, body.password)
    if user is not None:
        return TokenResponse(
            access_token=issue_token(
                user.login,
                role=user.role,
                user_id=user.id,
                must_change_password=user.must_change_password,
            ),
            role=user.role.value,
            must_change_password=user.must_change_password,
        )

    if verify_env_admin(body.login, body.password):
        return TokenResponse(
            access_token=issue_token(body.login, role=AdminRole.ADMIN),
            role=AdminRole.ADMIN.value,
        )

    raise HTTPException(status_code=401, detail="Invalid credentials")


@router.get("/auth/me")
async def whoami(
    user: Annotated[AuthenticatedUser, Depends(current_user)],
) -> CurrentUserOut:
    """Профиль текущего пользователя.

    Намеренно использует `current_user`, а не `require_viewer`: этот endpoint
    обязан работать в состоянии «пароль нужно сменить», иначе интерфейс не
    сможет показать нужный экран.
    """
    display_name = None
    if user.user_id is not None:
        stored = await build_admin_user_service().get_user(user.user_id)
        if stored is not None:
            display_name = stored.display_name
    return CurrentUserOut(
        login=user.login,
        role=user.role.value,
        display_name=display_name,
        must_change_password=user.must_change_password,
        is_env_admin=user.is_env_admin,
        can_write=user.can_write,
    )


@router.post(
    "/auth/change-password",
    responses={
        400: {"description": "Password rejected"},
        409: {"description": "Env admin cannot change password via API"},
    },
)
async def change_own_password(
    body: ChangePasswordRequest,
    user: Annotated[AuthenticatedUser, Depends(current_user)],
) -> TokenResponse:
    """Смена собственного пароля. Требует текущий пароль.

    Доступна в состоянии «пароль нужно сменить» — это единственный выход из
    него. В ответ выдаётся новый токен уже без флага обязательной смены.
    """
    if user.user_id is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "Аварийный администратор задаётся переменными окружения: "
                "смените ADMIN_PASSWORD в конфигурации сервера."
            ),
        )
    service = build_admin_user_service()
    try:
        await service.change_own_password(
            user.user_id,
            current_password=body.current_password,
            new_password=body.new_password,
        )
    except AdminUserError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return TokenResponse(
        access_token=issue_token(
            user.login, role=user.role, user_id=user.user_id, must_change_password=False
        ),
        role=user.role.value,
    )


# --- Dashboard ----------------------------------------------------------------

@router.get("/dashboard")
async def dashboard(_: Annotated[AuthenticatedUser, Depends(require_viewer)]) -> dict:
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
#
# Раздел анкет накапливается: анкета остаётся в списке и после того, как
# программа по ней собрана и отправлена. Поэтому список сообщает, исполнена ли
# анкета (собрана программа, отправлена пользователю), умеет по этим признакам
# сортировать, а неактуальные анкеты можно удалить.

# Разрешённые способы сортировки. Белый список, а не имя колонки из запроса:
# подстановка произвольного поля в ORDER BY — это и SQL-инъекция, и утечка
# внутренней схемы в публичный контракт.
_PROFILE_SORTS = {
    "created_desc": "новые сверху",
    "created_asc": "старые сверху",
    "generated_first": "сначала с готовой программой",
    "not_generated_first": "сначала без программы",
    "delivered_first": "сначала отправленные пользователю",
    "not_delivered_first": "сначала неотправленные",
}


def _profile_sort_clause(sort: str, has_program, delivered):
    """ORDER BY для выбранного способа сортировки.

    Внутри каждой группы порядок всегда «новые сверху»: без второго ключа
    строки внутри группы шли бы в произвольном порядке базы, и список менялся
    бы между открытиями.
    """
    newest = ProfileRow.created_at.desc()
    if sort == "created_asc":
        return (ProfileRow.created_at.asc(),)
    if sort == "generated_first":
        return (has_program.desc(), newest)
    if sort == "not_generated_first":
        return (has_program.asc(), newest)
    if sort == "delivered_first":
        return (delivered.desc(), newest)
    if sort == "not_delivered_first":
        return (delivered.asc(), newest)
    return (newest,)


@router.get("/profiles")
async def list_profiles(
    _: Annotated[AuthenticatedUser, Depends(require_viewer)],
    search: Annotated[str | None, Query(max_length=100)] = None,
    status: Annotated[str | None, Query(max_length=32)] = None,
    generated: Annotated[bool | None, Query()] = None,
    delivered: Annotated[bool | None, Query()] = None,
    sort: Annotated[str, Query(pattern="^[a-z_]+$", max_length=32)] = "created_desc",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    """Список анкет с признаками исполнения, фильтрами и сортировкой.

    Признаки вычисляются подзапросами, а не хранятся в анкете: дублирующие
    флаги рассинхронизировались бы с фактическим составом программ и доставок.
    """
    if sort not in _PROFILE_SORTS:
        raise HTTPException(
            status_code=422,
            detail=f"Неизвестный порядок сортировки. Допустимые: {', '.join(_PROFILE_SORTS)}",
        )

    # Программа собрана хотя бы одна.
    has_program = (
        select(WorkoutProgramRow.id)
        .where(WorkoutProgramRow.profile_id == ProfileRow.profile_id)
        .exists()
    )
    # Программа доставлена пользователю в Telegram. Единственный достоверно
    # известный факт получения: Bot API не сообщает, открыл ли человек документ,
    # поэтому «скачано пользователем» не отслеживается вовсе, а не угадывается.
    delivered_exists = (
        select(ProgramDeliveryRow.id)
        .where(
            ProgramDeliveryRow.profile_id == ProfileRow.profile_id,
            ProgramDeliveryRow.status == ProgramDeliveryStatus.SENT.value,
        )
        .exists()
    )

    stmt = select(
        ProfileRow,
        has_program.label("has_program"),
        delivered_exists.label("delivered"),
    )
    if search:
        like = f"%{search}%"
        stmt = stmt.where(
            ProfileRow.profile_id.ilike(like)
            | ProfileRow.display_number.ilike(like)
            | ProfileRow.data["client"]["name"].astext.ilike(like)
        )
    if status:
        stmt = stmt.where(ProfileRow.status == status)
    if generated is not None:
        stmt = stmt.where(has_program if generated else ~has_program)
    if delivered is not None:
        stmt = stmt.where(delivered_exists if delivered else ~delivered_exists)

    order_by = _profile_sort_clause(sort, has_program, delivered_exists)

    async with get_session_factory()() as session:
        total = (
            await session.execute(select(func.count()).select_from(stmt.subquery()))
        ).scalar_one()
        rows = (
            await session.execute(stmt.order_by(*order_by).limit(limit).offset(offset))
        ).all()

    # Дата отправки нужна только для показанной страницы, поэтому берётся
    # отдельным запросом по её profile_id, а не джойном ко всему списку.
    page_ids = [row[0].profile_id for row in rows]
    summaries = await build_delivery_repository().summaries_for_profiles(page_ids)

    items = []
    for row, row_has_program, row_delivered in rows:
        data = row.data or {}
        client = data.get("client", {})
        goals = data.get("goals", {})
        summary = summaries.get(row.profile_id)
        items.append(
            {
                "profile_id": row.profile_id,
                "display_number": row.display_number,
                "name": client.get("name"),
                "age": client.get("age_years"),
                "primary_goal": goals.get("primary"),
                "status": row.status,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                # Маркеры исполнения анкеты.
                "has_program": bool(row_has_program),
                "delivered": bool(row_delivered),
                "delivered_at": (
                    summary.delivered_at.isoformat()
                    if summary and summary.delivered_at
                    else None
                ),
                "delivery_status": (
                    summary.last_status.value if summary and summary.last_status else None
                ),
            }
        )
    return {"total": total, "items": items, "sort": sort}


@router.get(
    "/profiles/{profile_id}",
    responses={404: {"description": "Profile not found"}},
)
async def get_profile(profile_id: str, _: Annotated[AuthenticatedUser, Depends(require_viewer)]) -> dict:
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


@router.delete(
    "/profiles/{profile_id}",
    status_code=204,
    responses={
        404: {"description": "Profile not found"},
        409: {"description": "Профиль нельзя удалить: есть собранные программы"},
    },
)
async def delete_profile(
    profile_id: str, admin: Annotated[AuthenticatedUser, Depends(require_admin)]
) -> None:
    """Удаляет анкету, по которой нет программ.

    Анкету с программами удалить нельзя (409 со списком блокеров): её заполнял
    человек в боте и восстановить её невозможно, а программу всегда можно
    собрать заново. Порядок действий — сначала программы, потом анкета.

    Записи доставок удаляются вместе с анкетой, `generation_jobs` — каскадом
    базы: там внешний ключ есть.
    """
    service = build_profile_admin_service()
    async with get_session_factory()() as session:
        exists = (
            await session.execute(
                select(ProfileRow.id).where(ProfileRow.profile_id == profile_id)
            )
        ).scalar_one_or_none()
    if exists is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    try:
        result = await service.delete_profile(profile_id)
    except DeleteBlockedError as exc:
        raise HTTPException(
            status_code=409, detail={"message": str(exc), "blockers": exc.blockers}
        ) from exc
    except (ProfilePersistenceError, ProgramDeliveryError) as exc:
        raise HTTPException(status_code=422, detail=safe_error_message(exc)) from exc
    logger.info(
        "event=profile_deleted_by_admin",
        extra={"profile_id": profile_id, "actor": admin.login, **result},
    )


# --- Users --------------------------------------------------------------------

@router.get("/users")
async def list_users(
    _: Annotated[AuthenticatedUser, Depends(require_viewer)],
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
async def get_user(user_id: int, _: Annotated[AuthenticatedUser, Depends(require_viewer)]) -> dict:
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

# Сортировка и трёхзначные фильтры объявлены перечислениями, а не свободными
# строками: значение уходит в ORDER BY, и произвольная строка из запроса там
# означала бы и инъекцию, и выдачу внутренних имён колонок наружу. Неверное
# значение FastAPI отклоняет сам, до обращения к базе.


class SortOrder(StrEnum):
    ASC = "asc"
    DESC = "desc"


class ExerciseSort(StrEnum):
    NAME = "name"
    NAME_RU = "name_ru"
    EXERCISE_TYPE = "exercise_type"
    DIFFICULTY = "difficulty"
    FORCE = "force"
    MECHANIC = "mechanic"
    CREATED_AT = "created_at"


# Значения фильтров каталога приходят списками: «штанга или гантели» — один
# запрос, а не два. FastAPI собирает повторяющийся query-параметр в список.
def _clean(values: list[str] | None) -> tuple[str, ...]:
    """Непустые значения фильтра без дублей, с сохранением порядка."""
    if not values:
        return ()
    return tuple(dict.fromkeys(v for v in (value.strip() for value in values) if v))


class ActiveFilter(StrEnum):
    """Состояние упражнения в каталоге.

    Отдельное перечисление вместо `bool | None`: пустая строка в
    `?is_active=` отклонялась как невалидный bool, и «показать все» приходилось
    выражать отсутствием параметра — то есть значение по умолчанию нельзя было
    переопределить осознанно.
    """

    ACTIVE = "active"
    INACTIVE = "inactive"
    ALL = "all"


class MediaFilter(StrEnum):
    """Наличие фотографий у упражнения."""

    WITH = "with"
    WITHOUT = "without"
    ALL = "all"


@router.get("/exercises")
async def list_exercises(
    _: Annotated[AuthenticatedUser, Depends(require_viewer)],
    search: Annotated[str | None, Query(max_length=100)] = None,
    exercise_type: Annotated[list[str] | None, Query()] = None,
    difficulty: Annotated[list[str] | None, Query()] = None,
    equipment: Annotated[list[str] | None, Query()] = None,
    primary_muscle: Annotated[list[str] | None, Query()] = None,
    force: Annotated[list[str] | None, Query()] = None,
    mechanic: Annotated[list[str] | None, Query()] = None,
    # По умолчанию — только активные: из них собираются программы. Значение
    # `all` нужно администратору, иначе «упражнений 873» в сводке не сходилось
    # бы со списком, где отключённые скрыты.
    is_active: Annotated[ActiveFilter, Query()] = ActiveFilter.ACTIVE,
    media: Annotated[MediaFilter, Query()] = MediaFilter.ALL,
    sort_by: Annotated[ExerciseSort, Query()] = ExerciseSort.NAME,
    order: Annotated[SortOrder, Query()] = SortOrder.ASC,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    with_facets: Annotated[bool, Query()] = False,
) -> dict:
    """Страница каталога с серверными фильтрами, сортировкой и счётчиками.

    Фильтры и сортировка выполняются в базе, а не на клиенте: 873 упражнения
    можно передать целиком, но тогда «первые 50 по алфавиту» и «первые 50
    сложных» — разные выборки, а фильтр применялся бы к произвольной части
    каталога.

    `with_facets=true` добавляет число упражнений по каждому значению признака
    в текущей выборке. Счётчики считаются по тому же фильтру, что и список:
    иначе они обещали бы результаты, которых после уточнения фильтра нет.
    """
    query = ExerciseQuery(
        search=search,
        exercise_types=_clean(exercise_type),
        difficulties=_clean(difficulty),
        equipment=_clean(equipment),
        primary_muscles=_clean(primary_muscle),
        forces=_clean(force),
        mechanics=_clean(mechanic),
        is_active=None
        if is_active is ActiveFilter.ALL
        else is_active is ActiveFilter.ACTIVE,
        has_media=None if media is MediaFilter.ALL else media is MediaFilter.WITH,
    )
    repository = build_exercise_repository()
    try:
        total, rows = await repository.search_rows(
            query,
            limit=limit,
            offset=offset,
            sort_by=sort_by.value,
            descending=order is SortOrder.DESC,
        )
        facets = asdict(await repository.facets(query)) if with_facets else None
    except ProfilePersistenceError as exc:
        raise HTTPException(status_code=422, detail=safe_error_message(exc)) from exc

    payload: dict = {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [
            {
                # Surrogate id остаётся в списке: по нему открывается карточка
                # (`/exercises/{id}`). Каноническим идентификатором остаётся
                # (external_id, source) — на него ссылаются программы.
                "id": row.id,
                "external_id": row.external_id,
                "name": row.name,
                "name_ru": row.name_ru,
                "equipment": row.equipment or [],
                "primary_muscles": row.primary_muscles or [],
                "secondary_muscles": row.secondary_muscles or [],
                "difficulty": row.difficulty,
                "exercise_type": row.exercise_type,
                "force": row.force,
                "mechanic": row.mechanic,
                "source": row.source,
                "is_active": row.is_active,
                "has_media": bool(row.images),
            }
            for row in rows
        ],
    }
    if facets is not None:
        payload["facets"] = facets
    return payload


def _serialize_exercise(row: ExerciseRow, media_items: list[dict] | None = None) -> dict:
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
        "media": media_items or [],
    }


async def _media_items_for(row: ExerciseRow) -> list[dict]:
    from apps.backend.api.v1.dependencies import build_exercise_media_service
    from src.infrastructure.config import EXERCISE_MEDIA_MAX_PER_EXERCISE

    service = build_exercise_media_service()
    try:
        assets = await service.list_for_exercise(
            row.external_id, row.source, limit=EXERCISE_MEDIA_MAX_PER_EXERCISE
        )
    except Exception:  # noqa: BLE001 — media недоступны → пустой список
        return []
    return [
        {
            "sequence": a.sequence,
            "mime_type": a.mime_type,
            "width": a.width,
            "height": a.height,
            "size_bytes": a.size_bytes,
            "source": a.source,
            "license": a.license,
            "url": f"/api/v1/media/exercises/{a.exercise_external_id}/{a.sequence}?source={a.exercise_source}",
        }
        for a in assets
    ]


@router.get(
    "/exercises/external/{external_id}",
    responses={404: {"description": "Exercise not found"}},
)
async def get_exercise_by_external_id(
    external_id: str,
    _: Annotated[AuthenticatedUser, Depends(require_viewer)],
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
    return _serialize_exercise(row, await _media_items_for(row))


@router.get(
    "/exercises/{exercise_id}",
    responses={404: {"description": "Exercise not found"}},
)
async def get_exercise(exercise_id: int, _: Annotated[AuthenticatedUser, Depends(require_viewer)]) -> dict:
    async with get_session_factory()() as session:
        row = (
            await session.execute(select(ExerciseRow).where(ExerciseRow.id == exercise_id))
        ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Exercise not found")
    return _serialize_exercise(row, await _media_items_for(row))


# --- Programs -----------------------------------------------------------------


def _program_summary(program: WorkoutProgram, *, delivered: bool | None = None) -> dict:
    summary = {
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
    if delivered is not None:
        # Отправлена ли программа пользователю в Telegram. Заполняется только
        # там, где сводка доставок уже прочитана: тянуть её в каждый ответ ради
        # одного флага незачем.
        summary["delivered"] = delivered
    return summary


async def _delivered_flags(profile_ids: list[str]) -> dict[str, bool]:
    """Какие анкеты получили программу в Telegram. Один запрос на страницу."""
    summaries = await build_delivery_repository().summaries_for_profiles(profile_ids)
    return {pid: summary.delivered for pid, summary in summaries.items()}


@router.get("/programs")
async def list_programs(
    _: Annotated[AuthenticatedUser, Depends(require_viewer)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    service = build_program_service()
    total, programs = await service.list_all(limit=limit, offset=offset)
    delivered = await _delivered_flags([p.profile_id for p in programs])
    return {
        "total": total,
        "items": [
            _program_summary(p, delivered=delivered.get(p.profile_id, False))
            for p in programs
        ],
    }


@router.get(
    "/programs/{program_id}",
    responses={404: {"description": "Program not found"}},
)
async def get_program(
    program_id: str,
    _: Annotated[AuthenticatedUser, Depends(require_viewer)],
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
    profile_id: str, _: Annotated[AuthenticatedUser, Depends(require_viewer)]
) -> dict:
    service = build_program_service()
    programs = await service.list_for_profile(profile_id)
    delivered = await _delivered_flags([profile_id])
    was_delivered = delivered.get(profile_id, False)
    return {
        "total": len(programs),
        "items": [_program_summary(p, delivered=was_delivered) for p in programs],
    }


@router.delete(
    "/programs/{program_id}",
    status_code=204,
    responses={404: {"description": "Program not found"}},
)
async def delete_program(
    program_id: str, admin: Annotated[AuthenticatedUser, Depends(require_admin)]
) -> None:
    """Удаляет программу со всеми версиями и записями её доставок.

    Блокеров нет: программа производна от анкеты и всегда может быть собрана
    заново. Версии не удаляются по одной — `program_id` и есть программа, а
    версии её история; частичное удаление оставило бы дыры и сбило бы нумерацию
    следующей версии.

    Ссылка из `generation_jobs` обнуляется каскадом базы (`ON DELETE SET NULL`):
    история операций генерации сохраняется намеренно.
    """
    if await build_program_service().get(program_id) is None:
        raise HTTPException(status_code=404, detail="Program not found")
    try:
        result = await build_profile_admin_service().delete_program(program_id)
    except (ProgramPersistenceError, ProgramDeliveryError) as exc:
        raise HTTPException(status_code=422, detail=safe_error_message(exc)) from exc
    logger.info(
        "event=program_deleted_by_admin",
        extra={"program_id": program_id, "actor": admin.login, **result},
    )


@router.get(
    "/programs/{program_id}/html",
    response_class=Response,
    responses={
        200: {"content": {"text/html": {}}, "description": "Готовый HTML программы"},
        404: {"description": "Program not found"},
        422: {"description": "Render failed"},
    },
)
async def get_program_html(
    program_id: str,
    _: Annotated[AuthenticatedUser, Depends(require_viewer)],
    version: Annotated[int | None, Query(ge=1)] = None,
    download: Annotated[bool, Query()] = False,
) -> Response:
    """Тот же HTML, что получает пользователь в Telegram.

    Рендер идёт через `ProgramHtmlService`, поэтому админка и доставка не могут
    разойтись: вложенные фотографии, media mode и вёрстка берутся из одного
    источника. `download=true` отдаёт файл, иначе документ открывается в
    соседней вкладке.
    """
    program = await build_program_service().get(program_id, version)
    if program is None:
        raise HTTPException(status_code=404, detail="Program not found")
    try:
        html = await build_program_html_service().render(program)
    except HtmlRenderError as exc:
        raise HTTPException(status_code=422, detail=safe_error_message(exc)) from exc

    disposition = "attachment" if download else "inline"
    filename = build_filename(program.profile_id, program.version)
    return Response(
        content=html,
        media_type="text/html; charset=utf-8",
        headers={"Content-Disposition": f'{disposition}; filename="{filename}"'},
    )


class GenerateProgramRequest(BaseModel):
    """Запрос генерации программы с выбором генератора."""

    model_config = ConfigDict(extra="forbid")

    generator: str = Field(default="deterministic", pattern=r"^(deterministic|ai)$")
    prompt_version: int | None = Field(default=None, ge=1)
    # Необязательный клиентский ключ логической генерации: позволяет повторить
    # тот же запрос (сетевой ретрай, повторная отправка) и получить прежний
    # результат. Серверная защита от дубликатов работает и без него.
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=100)


# --- AI Providers (публичный API для UI) ----------------------------------------


@router.get("/ai/providers")
async def list_ai_providers(_: Annotated[AuthenticatedUser, Depends(require_viewer)]) -> dict:
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
        409: {
            "description": "Generation is already running, or the idempotency key "
            "was reused with different request parameters"
        },
        422: {"description": "Generation or validation failed"},
        502: {"description": "AI service call failed"},
    },
)
async def generate_program(
    profile_id: str,
    _: Annotated[AuthenticatedUser, Depends(require_admin)],
    body: GenerateProgramRequest | None = None,
) -> dict:
    """Запуск сборки программы выбранным генератором.

    Phase 1.2-C: запрос обслуживает тот же `ProgramGenerationOrchestrator`,
    что и автогенерация после подтверждения анкеты. Отличие только в запросе:
    администратор выбрал генератор явно, поэтому `allow_fallback=False` —
    подменять его молча нельзя, иначе администратор не узнает, что AI не
    сработал.

    Идемпотентность серверная: повторный запрос той же логической генерации не
    создаёт вторую программу. Пока предыдущий запрос выполняется, повтор
    получает 409, а не второй job. Тот же 409 возвращается, если `idempotency_key`
    переиспользован с другим генератором: отдать программу от прежнего
    генератора значило бы отменить явный выбор администратора.
    """
    request = body or GenerateProgramRequest()
    orchestrator = build_generation_orchestrator()
    try:
        result = await orchestrator.generate(
            GenerationRequest(
                profile_id=profile_id,
                trigger=GenerationTrigger.ADMIN_REQUEST,
                requested_generator=request.generator,
                allow_fallback=False,
                client_idempotency_key=request.idempotency_key,
            )
        )
    except (GenerationAlreadyRunningError, IdempotencyKeyConflictError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except GenerationFailedError as exc:
        raise _generation_http_error(exc) from exc
    except ProgramGenerationError as exc:
        raise HTTPException(status_code=422, detail=safe_error_message(exc)) from exc
    return {
        "program": result.program.model_dump(mode="json"),
        "generation": _generation_summary(result),
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


# Коды отказа, относящиеся к внешнему AI-сервису: это не ошибка запроса
# администратора, поэтому отвечаем 502, а не 422.
_AI_ERROR_CODES = frozenset(
    {
        GenerationErrorCode.AI_NOT_CONFIGURED.value,
        GenerationErrorCode.AI_UNSUPPORTED_PROTOCOL.value,
        GenerationErrorCode.AI_TIMEOUT.value,
        GenerationErrorCode.AI_CONNECTION_FAILED.value,
        GenerationErrorCode.AI_RATE_LIMITED.value,
        GenerationErrorCode.AI_INVALID_RESPONSE.value,
        GenerationErrorCode.AI_RUNTIME_FAILURE.value,
    }
)


def _generation_http_error(exc: GenerationFailedError) -> HTTPException:
    """HTTP-статус по стабильному коду отказа генерации.

    Слой API не разбирает внутренние типы исключений AI Gateway: решение
    принимается по коду, который оркестратор зафиксировал в момент отказа.

    Текст проходит `safe_error_message` ещё раз: сообщение отказа собирается из
    причин попыток (ошибка провайдера, detail readiness gate, сообщения
    валидатора), и слой, отдающий его наружу, не должен полагаться на то, что
    каждый источник уже был очищен.
    """
    message = safe_error_message(exc)
    if exc.generation_error_code in _AI_ERROR_CODES:
        return HTTPException(
            status_code=502,
            detail=f"Не удалось получить ответ от ИИ: {message}. "
            "Программу можно собрать алгоритмом подбора.",
        )
    return HTTPException(status_code=422, detail=message)


def _generation_summary(result: OrchestratorResult) -> dict:
    """Operational-состояние генерации для админки.

    Наружу отдаются только статус, попытки, код ошибки и фактическая стратегия:
    internal id записи и idempotency key клиенту не нужны.

    Контракт полей: `job_id`, `attempts`, `last_error_code` nullable, потому что
    job-контур опционален на уровне application-слоя (оркестратор работает и без
    него). `status` не nullable: успешный ответ этого endpoint'а всегда означает
    завершённую генерацию, и клиенту не нужно различать «job не создавался» и
    «job успешен» — оба случая для него одинаковы.
    """
    job = result.job
    return {
        "reused_existing": result.reused_existing,
        "job_id": job.job_id if job else None,
        "status": result.status.value,
        "attempts": job.attempts if job else None,
        "last_error_code": job.last_error_code if job else None,
        "requested_generator": result.requested_generator or None,
        "actual_generator": result.actual_generator or None,
        "fallback_used": result.fallback_used,
        "fallback_reason_code": result.fallback_reason_code,
    }
