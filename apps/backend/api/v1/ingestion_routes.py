"""Admin API v1: ingestion внешних источников знаний об упражнениях.

Только чтение. Это осознанное ограничение объёма: этап вводит источники и
слияние, и админке нужна видимость результата, а не второй интерфейс управления
данными. Запуск ingestion остаётся операцией обслуживания
(`scripts/ingest_external_exercises.py`), потому что требует локальной копии
источника, которой у backend нет и быть не должно.

Что можно увидеть: источники с их версиями и условиями использования, каждую
внешнюю запись с решением, уверенностью, качеством и причинами, происхождение
полей canonical упражнения и программные наблюдения.

Чего эндпоинты сознательно не отдают: `payload` staging-записи целиком.
Он содержит полные инструкции источника на нескольких языках, и отдавать их
списком означало бы возвращать мегабайты на каждый запрос страницы. Отдельная
запись отдаётся целиком — там это оправдано.
"""
from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from apps.backend.api.v1.ingestion_dependencies import build_ingestion_repository
from apps.backend.auth import AuthenticatedUser, require_viewer
from src.domain.ingestion import (
    ExerciseFieldProvenance,
    ExerciseProgramObservation,
    ExternalExerciseRecord,
    ImportStatus,
    IngestionDecision,
    QualityStatus,
)
from src.errors import ProfilePersistenceError
from src.infrastructure.persistence.postgres.ingestion_repository import RecordQuery

router = APIRouter(prefix="/api/v1/admin/ingestion")

DEFAULT_EXERCISE_SOURCE = "leszavr/workout"


class RecordSort(StrEnum):
    """Порядок выдачи записей.

    Перечисление, а не имя колонки из запроса: подстановка произвольной строки в
    ORDER BY — это и инъекция, и утечка схемы в публичный контракт.
    """

    NAME = "name"
    CONFIDENCE = "confidence"
    QUALITY = "quality"


def _record_out(record: ExternalExerciseRecord, *, full: bool = False) -> dict:
    payload = {
        "source_key": record.source_key,
        "source_version": record.source_version,
        "source_record_id": record.source_record_id,
        "raw_name": record.raw_name,
        "normalized_name": record.normalized_name,
        "quality_score": round(record.quality_score, 4),
        "quality_status": record.quality_status.value,
        "quality_reasons": record.quality_reasons,
        "decision": record.decision.value,
        "match_confidence": round(record.match_confidence, 4),
        "match_reasons": record.match_reasons,
        "matched_external_id": record.matched_external_id,
        "matched_source": record.matched_source,
        "import_status": record.import_status.value,
        "import_note": record.import_note,
        "imported_at": record.imported_at,
    }
    if full:
        payload["record_hash"] = record.record_hash
        payload["name_key"] = record.name_key
        payload["payload"] = record.payload
    return payload


def _provenance_out(entry: ExerciseFieldProvenance) -> dict:
    return {
        "field": entry.field,
        "source_key": entry.source_key,
        "source_record_id": entry.source_record_id,
        "source_version": entry.source_version,
        "reason": entry.reason,
    }


def _observation_out(observation: ExerciseProgramObservation) -> dict:
    """Наблюдение источника программ.

    Поля названы так же, как в базе (`typical_*`, `source_*`): это статистика
    чужих программ, а не назначение нашей, и переименовывать её в «рекомендуемые
    подходы» на границе API значило бы менять смысл данных.
    """
    return {
        "source_key": observation.source_key,
        "source_version": observation.source_version,
        "program_count": observation.program_count,
        "occurrence_count": observation.occurrence_count,
        "typical_sets_median": observation.typical_sets_median,
        "typical_sets_min": observation.typical_sets_min,
        "typical_sets_max": observation.typical_sets_max,
        "typical_reps_median": observation.typical_reps_median,
        "typical_reps_min": observation.typical_reps_min,
        "typical_reps_max": observation.typical_reps_max,
        "typical_hold_seconds_median": observation.typical_hold_seconds_median,
        "typical_intensity_median": observation.typical_intensity_median,
        "source_goals": observation.source_goals,
        "source_levels": observation.source_levels,
        "source_equipment_contexts": observation.source_equipment_contexts,
    }


@router.get("/sources")
async def list_sources(
    _: Annotated[AuthenticatedUser, Depends(require_viewer)],
) -> dict:
    """Источники с последней прочитанной версией.

    Условия использования данных и media отдаются вместе с источником: у media
    внешнего каталога отдельный правообладатель, и его указание обязано быть
    видно там же, где видны сами данные.
    """
    repository = build_ingestion_repository()
    try:
        sources = await repository.list_sources()
        versions = await repository.latest_versions()
        counts = await repository.record_counts()
    except ProfilePersistenceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    items = []
    for source in sources:
        version = versions.get(source.source_key)
        items.append(
            {
                "source_key": source.source_key,
                "name": source.name,
                "kind": source.kind.value,
                "homepage": source.homepage,
                "data_license": source.data_license,
                "media_license": source.media_license,
                "attribution": source.attribution,
                "notes": source.notes,
                "is_active": source.is_active,
                "version": version.version if version else None,
                "content_hash": version.content_hash if version else None,
                "retrieved_at": version.retrieved_at if version else None,
                "record_count": version.record_count if version else 0,
                "counts": counts.get(source.source_key, {}),
            }
        )
    return {"items": items}


@router.get("/records")
async def list_records(
    _: Annotated[AuthenticatedUser, Depends(require_viewer)],
    source: Annotated[list[str] | None, Query()] = None,
    decision: Annotated[list[IngestionDecision] | None, Query()] = None,
    quality: Annotated[list[QualityStatus] | None, Query()] = None,
    status: Annotated[list[ImportStatus] | None, Query()] = None,
    search: Annotated[str | None, Query(max_length=120)] = None,
    min_confidence: Annotated[float | None, Query(ge=0.0, le=1.0)] = None,
    max_confidence: Annotated[float | None, Query(ge=0.0, le=1.0)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    """Страница внешних записей с решением по каждой.

    Фильтры серверные: каталог внешних записей больше страницы, и фильтрация на
    клиенте отвечала бы на другой вопрос — «что нашлось среди первых 50».
    """
    repository = build_ingestion_repository()
    query = RecordQuery(
        source_keys=tuple(source or ()),
        decisions=tuple(d.value for d in (decision or ())),
        quality_statuses=tuple(q.value for q in (quality or ())),
        import_statuses=tuple(s.value for s in (status or ())),
        search=search,
        min_confidence=min_confidence,
        max_confidence=max_confidence,
    )
    try:
        total, items = await repository.list_records(query, limit=limit, offset=offset)
    except ProfilePersistenceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [_record_out(item) for item in items],
    }


@router.get("/records/{source_key:path}/{source_record_id}", responses={404: {}})
async def get_record(
    source_key: str,
    source_record_id: str,
    _: Annotated[AuthenticatedUser, Depends(require_viewer)],
) -> dict:
    """Одна внешняя запись целиком, включая payload источника."""
    repository = build_ingestion_repository()
    try:
        _, items = await repository.list_records(
            RecordQuery(source_keys=(source_key,)), limit=200, offset=0
        )
    except ProfilePersistenceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    match = next(
        (item for item in items if item.source_record_id == source_record_id), None
    )
    if match is None:
        raise HTTPException(status_code=404, detail="External record not found")
    return _record_out(match, full=True)


@router.get("/exercises/{external_id}/provenance")
async def exercise_provenance(
    external_id: str,
    _: Annotated[AuthenticatedUser, Depends(require_viewer)],
    source: Annotated[str, Query(max_length=64)] = DEFAULT_EXERCISE_SOURCE,
) -> dict:
    """Происхождение полей упражнения, связи источников и наблюдения.

    Три набора возвращаются вместе, потому что отвечают на один вопрос
    администратора — «откуда это упражнение и что в нём чужое», — и три запроса
    ради одного экрана были бы разделением без причины.
    """
    repository = build_ingestion_repository()
    try:
        provenance = await repository.field_provenance_for(external_id, source)
        links = await repository.links_for_exercises([(external_id, source)])
        observations = await repository.observations_for(external_id, source)
    except ProfilePersistenceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return {
        "exercise_external_id": external_id,
        "exercise_source": source,
        "fields": [_provenance_out(entry) for entry in provenance],
        "sources": [
            {
                "source_key": link.source_key,
                "source_record_id": link.source_record_id,
                "source_version": link.source_version,
                "relation": link.relation.value,
                "confidence": round(link.confidence, 4),
                "reasons": link.reasons,
            }
            for link in links.get((external_id, source), [])
        ],
        "program_observations": [
            _observation_out(observation) for observation in observations
        ],
    }


@router.get("/health")
async def ingestion_health(
    _: Annotated[AuthenticatedUser, Depends(require_viewer)],
) -> dict:
    """Числа, которыми проверяется результат ingestion.

    Все считаются запросами к базе: захардкоженная метрика показывала бы
    состояние на момент написания кода, а не текущее.
    """
    repository = build_ingestion_repository()
    try:
        counters = await repository.health_counters()
        counts = await repository.record_counts()
    except ProfilePersistenceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {**counters, "by_source": counts}
