"""Строгая Pydantic-модель упражнения (Exercise Catalog).

Canonical ID упражнения — стабильный ``external_id`` + ``source``;
упражнения не определяются только строковым названием.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class Exercise(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_id: str = Field(min_length=1, max_length=128, description="Стабильный ID в источнике")
    source: str = Field(default="leszavr/workout", max_length=64)
    source_version: str | None = Field(default=None, max_length=64)

    name: str = Field(min_length=1, max_length=255)
    name_ru: str | None = Field(default=None, max_length=255)
    aliases: list[str] = Field(default_factory=list)

    description: str | None = None
    technique: str | None = Field(default=None, description="Техника выполнения (шаги)")
    technique_ru: str | None = None
    common_mistakes: str | None = None

    primary_muscles: list[str] = Field(default_factory=list)
    secondary_muscles: list[str] = Field(default_factory=list)

    equipment: list[str] = Field(default_factory=list)
    exercise_type: str | None = Field(default=None, max_length=64)
    difficulty: str | None = Field(default=None, max_length=32)
    force: str | None = Field(default=None, max_length=16)
    mechanic: str | None = Field(default=None, max_length=16)

    contraindications: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    images: list[str] = Field(default_factory=list)
    is_active: bool = True

    created_at: datetime | None = None
    updated_at: datetime | None = None
