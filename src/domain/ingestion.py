"""Доменные модели ingestion внешних источников знаний об упражнениях.

Canonical сущность упражнения остаётся одна — ``src.domain.exercise.Exercise``.
Модели этого модуля описывают не второй каталог, а путь внешней записи к
canonical записи и след, который она оставляет:

1. ``ExternalSource`` / ``ExternalSourceVersion`` — что за источник и в каком
   состоянии он был прочитан. Без версии импорт невоспроизводим: «взято из
   GitHub» не отвечает на вопрос, из какого коммита.
2. ``ExternalExerciseRecord`` — нормализованная внешняя запись вместе с решением
   о ней. Запись остаётся в базе и после отклонения: причина, по которой
   упражнение не попало в каталог, — такой же результат этапа, как добавленное
   упражнение.
3. ``ExerciseSourceLink`` — из каких источников собрано canonical упражнение.
4. ``ExerciseFieldProvenance`` — происхождение конкретного поля. Merge не
   перезаписывает поля слепо, поэтому «откуда это значение» — вопрос про поле, а
   не про запись целиком.
5. ``ExerciseProgramObservation`` — наблюдение источника программ: сколько
   программ включают упражнение и с какими подходами. Это факт о чужих
   программах, а не предписание нашей: все поля названы ``typical_*`` и
   ``source_*`` именно поэтому.

Решение о внешней записи выражено перечислением ``IngestionDecision``, а не
булевым «импортировать». Различие между «это уже есть», «это вариант того же
упражнения» и «данных недостаточно, чтобы решить» существенно: первое допускает
обогащение, второе запрещает создание записи, третье запрещает любое
автоматическое действие.
"""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

MAX_SOURCE_KEY_LENGTH = 64
MAX_VERSION_LENGTH = 128
MAX_RECORD_ID_LENGTH = 128
MAX_NAME_LENGTH = 255
MAX_NOTE_LENGTH = 300


class ExternalSourceKind(StrEnum):
    """Вид источника.

    Различие принципиально: каталог упражнений может дать новое упражнение, а
    датасет программ — только знание о том, как упражнения используются. Строка
    датасета программ не является упражнением, и трактовать её как кандидата в
    caталог нельзя.
    """

    EXERCISE_CATALOG = "exercise_catalog"
    PROGRAM_DATASET = "program_dataset"


class QualityStatus(StrEnum):
    """Пригодность внешней записи к автоматическому использованию."""

    READY = "ready"
    REVIEW = "review"
    REJECT = "reject"


class IngestionDecision(StrEnum):
    """Что представляет собой внешняя запись относительно canonical каталога."""

    EXISTING = "existing"
    ENRICHABLE = "enrichable"
    NEW_RELEVANT = "new_relevant"
    DUPLICATE_VARIANT = "duplicate_variant"
    LOW_QUALITY = "low_quality"
    QUESTIONABLE = "questionable"
    UNKNOWN = "unknown"


class ImportStatus(StrEnum):
    """Что фактически сделано с записью при последнем применении плана."""

    PENDING = "pending"
    IMPORTED = "imported"
    ENRICHED = "enriched"
    SKIPPED = "skipped"
    REJECTED = "rejected"


class SourceLinkRelation(StrEnum):
    """Роль внешней записи для canonical упражнения."""

    ORIGIN = "origin"
    ENRICHMENT = "enrichment"
    DUPLICATE_VARIANT = "duplicate_variant"
    OBSERVATION = "observation"


class ExternalSource(BaseModel):
    """Реестровая запись источника.

    Условия использования данных и media хранятся отдельными полями: у обоих
    источников они разные, и для media источника B действует отдельное
    разрешение правообладателя, которое обязано быть видно вместе с данными.
    """

    model_config = ConfigDict(extra="forbid")

    source_key: str = Field(min_length=1, max_length=MAX_SOURCE_KEY_LENGTH)
    name: str = Field(min_length=1, max_length=160)
    kind: ExternalSourceKind
    homepage: str | None = Field(default=None, max_length=300)
    data_license: str | None = Field(default=None, max_length=200)
    media_license: str | None = Field(default=None, max_length=300)
    attribution: str | None = Field(default=None, max_length=300)
    notes: str | None = Field(default=None, max_length=500)
    is_active: bool = True


class ExternalSourceVersion(BaseModel):
    """Состояние источника на момент чтения."""

    model_config = ConfigDict(extra="forbid")

    source_key: str = Field(min_length=1, max_length=MAX_SOURCE_KEY_LENGTH)
    version: str = Field(min_length=1, max_length=MAX_VERSION_LENGTH)
    content_hash: str | None = Field(default=None, max_length=64)
    retrieved_at: datetime
    record_count: int = Field(default=0, ge=0)
    notes: str | None = Field(default=None, max_length=500)


class ExternalExerciseRecord(BaseModel):
    """Нормализованная внешняя запись и решение о ней."""

    model_config = ConfigDict(extra="forbid")

    source_key: str = Field(min_length=1, max_length=MAX_SOURCE_KEY_LENGTH)
    source_version: str = Field(min_length=1, max_length=MAX_VERSION_LENGTH)
    source_record_id: str = Field(min_length=1, max_length=MAX_RECORD_ID_LENGTH)
    record_hash: str = Field(min_length=1, max_length=64)
    raw_name: str = Field(min_length=1, max_length=MAX_NAME_LENGTH)
    normalized_name: str = Field(min_length=1, max_length=MAX_NAME_LENGTH)
    name_key: str = Field(min_length=1, max_length=MAX_NAME_LENGTH)
    payload: dict = Field(default_factory=dict)
    quality_score: float = Field(default=0.0, ge=0.0, le=1.0)
    quality_status: QualityStatus = QualityStatus.REVIEW
    quality_reasons: list[str] = Field(default_factory=list)
    decision: IngestionDecision = IngestionDecision.UNKNOWN
    match_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    match_reasons: list[str] = Field(default_factory=list)
    matched_external_id: str | None = Field(default=None, max_length=128)
    matched_source: str | None = Field(default=None, max_length=64)
    import_status: ImportStatus = ImportStatus.PENDING
    import_note: str | None = Field(default=None, max_length=MAX_NOTE_LENGTH)
    imported_at: datetime | None = None


class ExerciseSourceLink(BaseModel):
    """Связь canonical упражнения с внешней записью."""

    model_config = ConfigDict(extra="forbid")

    exercise_external_id: str = Field(min_length=1, max_length=128)
    exercise_source: str = Field(default="leszavr/workout", max_length=64)
    source_key: str = Field(min_length=1, max_length=MAX_SOURCE_KEY_LENGTH)
    source_record_id: str = Field(min_length=1, max_length=MAX_RECORD_ID_LENGTH)
    source_version: str = Field(min_length=1, max_length=MAX_VERSION_LENGTH)
    relation: SourceLinkRelation = SourceLinkRelation.ENRICHMENT
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reasons: list[str] = Field(default_factory=list)


class ExerciseFieldProvenance(BaseModel):
    """Происхождение значения одного поля canonical упражнения."""

    model_config = ConfigDict(extra="forbid")

    exercise_external_id: str = Field(min_length=1, max_length=128)
    exercise_source: str = Field(default="leszavr/workout", max_length=64)
    field: str = Field(min_length=1, max_length=48)
    source_key: str = Field(min_length=1, max_length=MAX_SOURCE_KEY_LENGTH)
    source_record_id: str = Field(min_length=1, max_length=MAX_RECORD_ID_LENGTH)
    source_version: str = Field(min_length=1, max_length=MAX_VERSION_LENGTH)
    value_hash: str = Field(min_length=1, max_length=64)
    reason: str | None = Field(default=None, max_length=64)


class ExerciseProgramObservation(BaseModel):
    """Наблюдение источника программ об упражнении.

    Ни одно поле не является предписанием. ``typical_reps_*`` отвечает на вопрос
    «что встречается в чужих программах», а не «сколько повторений назначить»:
    назначение делает генератор по методологии проекта.

    ``typical_hold_seconds_median`` отделено от повторений намеренно: в источнике
    отрицательное значение повторений обозначает удержание в секундах, и
    складывать его с повторениями значило бы считать среднее по двум разным
    величинам.
    """

    model_config = ConfigDict(extra="forbid")

    exercise_external_id: str = Field(min_length=1, max_length=128)
    exercise_source: str = Field(default="leszavr/workout", max_length=64)
    source_key: str = Field(min_length=1, max_length=MAX_SOURCE_KEY_LENGTH)
    source_version: str = Field(min_length=1, max_length=MAX_VERSION_LENGTH)
    source_record_id: str = Field(min_length=1, max_length=MAX_RECORD_ID_LENGTH)
    program_count: int = Field(default=0, ge=0)
    occurrence_count: int = Field(default=0, ge=0)
    typical_sets_median: float | None = None
    typical_sets_min: int | None = None
    typical_sets_max: int | None = None
    typical_reps_median: float | None = None
    typical_reps_min: int | None = None
    typical_reps_max: int | None = None
    typical_hold_seconds_median: float | None = None
    typical_intensity_median: float | None = None
    source_goals: dict = Field(default_factory=dict)
    source_levels: dict = Field(default_factory=dict)
    source_equipment_contexts: dict = Field(default_factory=dict)
