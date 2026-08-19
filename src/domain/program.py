"""Строгие Pydantic-модели программы тренировок (WorkoutProgram).

Программа — версионируемый агрегат: каждая генерация создаёт новую версию,
исторические версии не перезаписываются. Модель пригодна для JSON-сериализации,
хранения в PostgreSQL (JSONB), API и последующей генерации через AI
(контракт ProgramGenerator от конкретной реализации не зависит).

Каноническая ссылка на упражнение — ``external_id + source`` (как в каталоге),
а не строковое название и не surrogate id базы данных.
"""
from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.domain.enums import GenerationSource, ProgramStatus

MAX_NOTES_LENGTH = 1000
GENERATOR_VERSION = "deterministic-1.0.0"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


class ProgramExercise(BaseModel):
    """Одно упражнение в тренировочном дне."""

    model_config = ConfigDict(extra="forbid")

    exercise_external_id: str = Field(min_length=1, max_length=128)
    exercise_source: str = Field(default="leszavr/workout", max_length=64)
    order: int = Field(ge=1, le=50)
    sets: int = Field(ge=1, le=10)
    repetitions_min: int = Field(ge=1, le=200)
    repetitions_max: int = Field(ge=1, le=200)
    rest_seconds: int = Field(ge=0, le=600)
    intensity: str | None = Field(default=None, max_length=64, description="Например: RPE 7, умеренно")
    notes: str | None = Field(default=None, max_length=MAX_NOTES_LENGTH)
    technique_notes: str | None = Field(default=None, max_length=MAX_NOTES_LENGTH)

    @model_validator(mode="after")
    def _reps_range_consistent(self) -> "ProgramExercise":
        if self.repetitions_max < self.repetitions_min:
            raise ValueError("repetitions_max must be >= repetitions_min")
        return self


class TrainingDay(BaseModel):
    """Тренировочный день: фокус + упорядоченный список упражнений."""

    model_config = ConfigDict(extra="forbid")

    day_number: int = Field(ge=1, le=7)
    title: str = Field(min_length=1, max_length=120)
    focus: str = Field(min_length=1, max_length=120)
    exercises: list[ProgramExercise] = Field(min_length=1, max_length=15)


class GenerationInfo(BaseModel):
    """Метаданные генерации: источник, версия генератора и AI-параметры."""

    model_config = ConfigDict(extra="forbid")

    source: GenerationSource = GenerationSource.DETERMINISTIC
    generator_version: str = Field(default=GENERATOR_VERSION, max_length=64)
    safe_pool_size: int | None = Field(default=None, ge=0)
    candidate_pool_total: int | None = Field(default=None, ge=0)
    # AI-метаданные (заполняются только при source=ai)
    provider: str | None = Field(default=None, max_length=64)
    model: str | None = Field(default=None, max_length=200)
    prompt_version: int | None = Field(default=None, ge=1)
    # Метаданные оркестрации (Stage 5): какой генератор запрашивали,
    # какой реально сработал, был ли fallback и почему.
    requested_generator: GenerationSource | None = None
    actual_generator: GenerationSource | None = None
    fallback_used: bool = False
    fallback_reason: str | None = Field(default=None, max_length=500)


class ProgressionPlan(BaseModel):
    """Правила прогрессии нагрузки (консервативные, без медицинских обещаний)."""

    model_config = ConfigDict(extra="forbid")

    description: str | None = Field(default=None, max_length=MAX_NOTES_LENGTH)
    weekly_increase_percent: float | None = Field(default=None, ge=0, le=20)


class WorkoutProgram(BaseModel):
    """Агрегат программы тренировок. Единственная структура, которую сохраняет репозиторий."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    program_id: str | None = Field(default=None, max_length=64)
    profile_id: str = Field(min_length=1, max_length=64)
    version: int = Field(default=1, ge=1)
    status: ProgramStatus = ProgramStatus.DRAFT

    generation: GenerationInfo = Field(default_factory=GenerationInfo)
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=MAX_NOTES_LENGTH)
    duration_weeks: int = Field(ge=1, le=52)
    training_days_per_week: int = Field(ge=1, le=7)
    training_days: list[TrainingDay] = Field(min_length=1, max_length=7)

    progression: ProgressionPlan = Field(default_factory=ProgressionPlan)
    safety_notes: list[str] = Field(default_factory=list)

    created_at: datetime | None = None
    updated_at: datetime | None = None

    @model_validator(mode="after")
    def _structure_consistent(self) -> "WorkoutProgram":
        if len(self.training_days) != self.training_days_per_week:
            raise ValueError(
                f"training_days count ({len(self.training_days)}) "
                f"!= training_days_per_week ({self.training_days_per_week})"
            )
        for i, day in enumerate(self.training_days, start=1):
            if day.day_number != i:
                raise ValueError(f"day_number must be sequential, got {day.day_number} at position {i}")
        return self

    def touch(self) -> None:
        now = _utcnow()
        self.updated_at = now
        if self.created_at is None:
            self.created_at = now
