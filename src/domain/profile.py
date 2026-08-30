"""Строгие Pydantic-модели профиля клиента.

Профиль больше не является произвольным ``dict[str, Any]``:
перед сохранением он обязан пройти Pydantic-валидацию.
Структура полей совместима с существующими JSON-файлами (schema_version 1.0).

Все модели используют ``validate_assignment=True``. Без него Pydantic
проверяет данные только при создании и разборе, а присваивание полю проходит
без проверки: код анкеты мог записать в ``list[str]`` обычную строку, ошибка
всплывала лишь при следующем чтении профиля — то есть на следующем шаге
анкеты, где причина уже не видна. С валидацией присваивания несовместимое
значение отклоняется в точке записи.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.domain.consents import ConsentRecord
from src.domain.enums import (
    CardioPreference,
    CompletionStatus,
    DailyActivityLevel,
    ExperienceLevel,
    PreferredTrainingTime,
    PrimaryGoal,
    Sex,
    TargetTimeframe,
    TrainingLocationType,
    Weekday,
)

MAX_TEXT_LENGTH = 2000


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


class UserIdentity(BaseModel):
    """Идентификация пользователя в источнике (Telegram)."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    platform: str = "telegram"
    bot_user_id: str | None = Field(default=None, description="Telegram user id")
    telegram_username: str | None = Field(default=None, max_length=64)


class ClientData(BaseModel):
    """Базовые антропометрические данные клиента."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    name: str | None = Field(default=None, min_length=2, max_length=50)
    age_years: int | None = Field(default=None, ge=14, le=100)
    sex: Sex | None = None
    height_cm: int | None = Field(default=None, ge=120, le=250)
    weight_kg: float | None = Field(default=None, ge=30, le=300)
    waist_cm: int | None = Field(default=None, ge=40, le=200)


class Goals(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    primary: PrimaryGoal | None = None
    primary_custom: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    secondary: list[str] = Field(default_factory=list)
    desired_result: str | None = Field(default=None, min_length=5, max_length=MAX_TEXT_LENGTH)
    target_timeframe: TargetTimeframe | None = None


class WorkingWeight(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    exercise: str = Field(default="", max_length=200)
    weight: float = Field(default=0.0, ge=0)
    unit: str = "kg"
    sets_reps: str = Field(default="", max_length=100)
    notes: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)


class TrainingBackground(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    experience_level: ExperienceLevel | None = None
    current_frequency_per_week: int = Field(default=0, ge=0, le=14)
    current_activity_description: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    current_exercises: list[str] = Field(default_factory=list)
    known_working_weights: list[WorkingWeight] = Field(default_factory=list)
    previous_training_notes: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)


class TrainingPlanPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    sessions_per_week: int = Field(default=0, ge=0, le=7)
    preferred_days: list[Weekday] = Field(default_factory=list)
    session_duration_minutes: int = Field(default=0, ge=0, le=300)
    preferred_training_time: PreferredTrainingTime | None = None


class TrainingLocation(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    primary_location: TrainingLocationType | None = None
    gym_name: str | None = Field(default=None, max_length=200)
    available_equipment: list[str] = Field(default_factory=list)
    custom_equipment_description: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    equipment_photos: list[str] = Field(default_factory=list, description="file_id или пути сохранённых фото")


class LimitationDetail(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    category: str = "general"
    user_description: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    triggers: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    current_status: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)


class HealthAndLimitations(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    has_limitations: bool = False
    categories: list[str] = Field(default_factory=list)
    details: list[LimitationDetail] = Field(default_factory=list)
    doctor_recommendations: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    movements_to_avoid: list[str] = Field(default_factory=list)
    medical_clearance_required: bool = False


class ExercisePreferences(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    preferred_exercises: list[str] = Field(default_factory=list)
    disliked_exercises: list[str] = Field(default_factory=list)
    excluded_exercises: list[str] = Field(default_factory=list)
    exercise_goals: list[str] = Field(default_factory=list)


class Lifestyle(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    daily_activity_level: DailyActivityLevel | None = None
    cardio_preference: CardioPreference | None = None
    cardio_notes: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)


class AdditionalInformation(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    schedule_constraints: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    special_requests: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    free_text: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)


class QuestionnaireMeta(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    completed: bool = False
    completion_status: CompletionStatus = CompletionStatus.DRAFT
    last_question_id: str | None = None
    skipped_questions: list[str] = Field(default_factory=list)


class ReviewMeta(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    client_summary_confirmed: bool = False
    client_corrections: list[str] = Field(default_factory=list)
    operator_notes: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)


class FitnessProfile(BaseModel):
    """Агрегат анкеты клиента. Единственная структура, которую сохраняет репозиторий."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    schema_version: str = "1.0"
    profile_id: str | None = None
    display_number: str | None = Field(
        default=None,
        description="Человекочитаемый номер заявки (REQ-YYYYMMDD-NNNNN), присваивается при финализации",
    )
    created_at: datetime | None = None
    updated_at: datetime | None = None
    source: UserIdentity = Field(default_factory=UserIdentity)
    client: ClientData = Field(default_factory=ClientData)
    goals: Goals = Field(default_factory=Goals)
    training_background: TrainingBackground = Field(default_factory=TrainingBackground)
    training_plan_preferences: TrainingPlanPreferences = Field(default_factory=TrainingPlanPreferences)
    training_location: TrainingLocation = Field(default_factory=TrainingLocation)
    health_and_limitations: HealthAndLimitations = Field(default_factory=HealthAndLimitations)
    exercise_preferences: ExercisePreferences = Field(default_factory=ExercisePreferences)
    lifestyle: Lifestyle = Field(default_factory=Lifestyle)
    additional_information: AdditionalInformation = Field(default_factory=AdditionalInformation)
    questionnaire: QuestionnaireMeta = Field(default_factory=QuestionnaireMeta)
    consents: list["ConsentRecord"] = Field(default_factory=list)
    review: ReviewMeta = Field(default_factory=ReviewMeta)
    admin_notification_status: str = Field(
        default="pending",
        description="Статус доставки уведомления администратору: pending | sent | failed",
    )

    @field_validator("consents", mode="before")
    @classmethod
    def _migrate_legacy_consents(cls, value: Any) -> Any:
        """Старые профили хранили consents как dict из bool — мигрируем в список записей."""
        if isinstance(value, dict):
            records = []
            mapping = {
                "data_processing_confirmed": "data_processing",
                "health_information_confirmed": "health_information",
                "accuracy_confirmed": "accuracy",
            }
            for key, scope in mapping.items():
                if value.get(key):
                    records.append({"scope": scope})
            return records
        return value

    def touch(self) -> None:
        now = _utcnow()
        self.updated_at = now
        if self.created_at is None:
            self.created_at = now
