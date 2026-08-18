"""AIProgramGenerationContext: минимизированный DTO для AI-генерации.

КРИТИЧЕСКОЕ ТРЕБОВАНИЕ БЕЗОПАСНОСТИ:
В AI-запрос НЕ должны попадать:
- telegram_user_id / telegram_username
- profile_id как идентификатор пользователя
- имя пользователя, email, телефон, IP
- технические идентификаторы

Контекст содержит ТОЛЬКО данные, необходимые для составления программы:
возраст, пол, цели, опыт, предпочтения, ограничения движений и
SafeExercisePool (упражнения, прошедшие safety-фильтрацию).
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from src.domain.enums import (
    CardioPreference,
    ExperienceLevel,
    MovementRestriction,
    PrimaryGoal,
    Sex,
    TrainingLocationType,
)
from src.domain.pools import SafeExercisePool
from src.domain.profile import FitnessProfile

# Максимум упражнений, передаваемых в AI-промпт (защита от переполнения контекста).
MAX_EXERCISES_IN_PROMPT = 150


class ExerciseBrief(BaseModel):
    """Краткое представление упражнения для AI (без лишних полей)."""

    model_config = ConfigDict(extra="forbid")

    external_id: str
    name: str
    name_ru: str | None = None
    primary_muscles: list[str] = Field(default_factory=list)
    equipment: list[str] = Field(default_factory=list)
    exercise_type: str | None = None
    difficulty: str | None = None
    mechanic: str | None = None


class AIProgramGenerationContext(BaseModel):
    """Минимизированный контекст для AI-генерации программы.

    Не содержит персональных идентификаторов — только данные,
    необходимые для принятия решений о программе тренировок.
    """

    model_config = ConfigDict(extra="forbid")

    # Антропометрия (без имени!)
    age_years: int | None = None
    sex: Sex | None = None
    height_cm: int | None = None
    weight_kg: float | None = None

    # Цели и опыт
    primary_goal: PrimaryGoal | None = None
    desired_result: str | None = None
    experience_level: ExperienceLevel | None = None

    # План тренировок
    sessions_per_week: int = 3
    session_duration_minutes: int | None = None
    preferred_days: list[str] = Field(default_factory=list)

    # Место и оборудование
    training_location: TrainingLocationType | None = None
    available_equipment: list[str] = Field(default_factory=list)

    # Предпочтения по упражнениям
    preferred_exercises: list[str] = Field(default_factory=list)
    disliked_exercises: list[str] = Field(default_factory=list)

    # Ограничения (нормализованные, без медицинских диагнозов)
    movement_restrictions: list[MovementRestriction] = Field(default_factory=list)
    cardio_preference: CardioPreference | None = None

    # SafeExercisePool — единственные упражнения, которые AI может использовать
    safe_pool: list[ExerciseBrief] = Field(default_factory=list)
    safe_pool_size: int = 0
    pool_warnings: dict[str, list[str]] = Field(default_factory=dict)


def build_generation_context(
    profile: FitnessProfile,
    safe_pool: SafeExercisePool,
) -> AIProgramGenerationContext:
    """Создаёт минимизированный контекст из профиля и safe-пула.

    Намеренно исключает:
    - profile.source (telegram_user_id, username)
    - profile.client.name
    - profile.profile_id / display_number
    - любые технические идентификаторы
    """
    # Ограничиваем количество упражнений в промпте
    exercises = safe_pool.allowed[:MAX_EXERCISES_IN_PROMPT]

    return AIProgramGenerationContext(
        age_years=profile.client.age_years,
        sex=profile.client.sex,
        height_cm=profile.client.height_cm,
        weight_kg=profile.client.weight_kg,
        primary_goal=profile.goals.primary,
        desired_result=profile.goals.desired_result,
        experience_level=profile.training_background.experience_level,
        sessions_per_week=max(1, profile.training_plan_preferences.sessions_per_week or 3),
        session_duration_minutes=profile.training_plan_preferences.session_duration_minutes or None,
        preferred_days=[d.value for d in profile.training_plan_preferences.preferred_days],
        training_location=profile.training_location.primary_location,
        available_equipment=profile.training_location.available_equipment,
        preferred_exercises=profile.exercise_preferences.preferred_exercises,
        disliked_exercises=profile.exercise_preferences.disliked_exercises,
        movement_restrictions=safe_pool.active_restrictions,
        cardio_preference=profile.lifestyle.cardio_preference,
        safe_pool=[
            ExerciseBrief(
                external_id=e.external_id,
                name=e.name,
                name_ru=e.name_ru,
                primary_muscles=e.primary_muscles,
                equipment=e.equipment,
                exercise_type=e.exercise_type,
                difficulty=e.difficulty,
                mechanic=e.mechanic,
            )
            for e in exercises
        ],
        safe_pool_size=len(safe_pool.allowed),
        pool_warnings={
            k: v for k, v in safe_pool.warnings.items()
            if k in {e.external_id for e in exercises}
        },
    )