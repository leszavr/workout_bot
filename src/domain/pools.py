"""Доменные DTO пулов упражнений: ExerciseCandidatePool и SafeExercisePool.

Результат фильтрации и safety-слоя — не просто ``list[Exercise]``,
а объяснимые структуры: для каждого упражнения можно ответить
«почему включено» и «почему исключено».

Эти объекты в будущем передаются AI-генератору вместо всего каталога.
"""
from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field

from src.domain.enums import MovementRestriction, SafetyDecision
from src.domain.exercise import Exercise


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


class ExclusionRecord(BaseModel):
    """Причина исключения упражнения на этапе фильтрации."""

    model_config = ConfigDict(extra="forbid")

    exercise_external_id: str
    exercise_name: str
    reason: str = Field(max_length=200)


class ExerciseCandidatePool(BaseModel):
    """Результат Exercise Filtering Engine (до safety-правил)."""

    model_config = ConfigDict(extra="forbid")

    profile_id: str
    generated_at: datetime = Field(default_factory=_utcnow)
    total_exercises: int = Field(ge=0)
    included: list[Exercise] = Field(default_factory=list)
    excluded: list[ExclusionRecord] = Field(default_factory=list)


class SafetyRuleOutcome(BaseModel):
    """Результат применения одного safety-правила к упражнению."""

    model_config = ConfigDict(extra="forbid")

    rule_id: str
    decision: SafetyDecision
    restriction: MovementRestriction | None = None
    reason: str = Field(max_length=300)


class SafeExercisePool(BaseModel):
    """Результат применения Safety Framework к кандидатному пулу.

    Единственный источник упражнений для генератора программ.
    """

    model_config = ConfigDict(extra="forbid")

    profile_id: str
    generated_at: datetime = Field(default_factory=_utcnow)
    allowed: list[Exercise] = Field(default_factory=list)
    warnings: dict[str, list[str]] = Field(
        default_factory=dict,
        description="external_id -> список предупреждений",
    )
    excluded: list[ExclusionRecord] = Field(default_factory=list)
    requires_review: list[ExclusionRecord] = Field(default_factory=list)
    review_notes: list[str] = Field(
        default_factory=list,
        description="Пул-уровневые замечания, требующие ручного рассмотрения",
    )
    applied_rules: list[str] = Field(default_factory=list, description="ID применённых правил")
    active_restrictions: list[MovementRestriction] = Field(default_factory=list)

    def allowed_ids(self) -> set[str]:
        return {e.external_id for e in self.allowed}

    def allowed_sources(self) -> dict[str, str]:
        """external_id -> source каталога.

        Ссылка на упражнение канонична только как пара `external_id` + `source`,
        поэтому потребителям нужен не только набор идентификаторов.
        """
        return {e.external_id: e.source for e in self.allowed}

    def get_allowed(self, external_id: str) -> Exercise | None:
        for exercise in self.allowed:
            if exercise.external_id == external_id:
                return exercise
        return None
