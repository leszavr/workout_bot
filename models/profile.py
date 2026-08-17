from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ClientProfile(BaseModel):
    schema_version: str = "1.0"
    profile_id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    source: dict[str, Any] = Field(default_factory=dict)
    client: dict[str, Any] = Field(default_factory=dict)
    goals: dict[str, Any] = Field(default_factory=dict)
    training_background: dict[str, Any] = Field(default_factory=dict)
    training_plan_preferences: dict[str, Any] = Field(default_factory=dict)
    training_location: dict[str, Any] = Field(default_factory=dict)
    health_and_limitations: dict[str, Any] = Field(default_factory=dict)
    exercise_preferences: dict[str, Any] = Field(default_factory=dict)
    lifestyle: dict[str, Any] = Field(default_factory=dict)
    additional_information: dict[str, Any] = Field(default_factory=dict)
    questionnaire: dict[str, Any] = Field(default_factory=dict)
    consents: dict[str, Any] = Field(default_factory=dict)
    review: dict[str, Any] = Field(default_factory=dict)

    class Config:
        extra = "allow"
