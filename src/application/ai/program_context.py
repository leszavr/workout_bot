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
from src.application.programs.session_planning import (
    TOLERANCE_MINUTES,
    plan_session,
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


class SessionPlanBrief(BaseModel):
    """Расчёт одного занятия для промпта.

    Модель не умеет оценивать длительность тренировки: прошлый прогон дал разброс
    от 4 до 84 минут при заявленных 60–90. Расчёт выполняет приложение
    (`session_planning`) и передаёт числа как ориентир — тот же, что использует
    алгоритмический генератор, поэтому оба пути дают сопоставимый объём.
    """

    model_config = ConfigDict(extra="forbid")

    total_minutes: int
    warmup_minutes: int
    cooldown_minutes: int
    main_minutes: int
    tolerance_minutes: int
    exercises: int
    sets: int
    reps_min: int
    reps_max: int
    rest_seconds: int
    approach: str
    # True — заявленное время занять нечем без потери характера тренировки
    # (например, 150 минут щадящей программы). Модель должна сообщить
    # фактическую длительность, а не обещать заявленную.
    capped: bool = False


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

    # Текущее самочувствие: жалобы, о которых человек сообщил перед генерацией.
    # Поле есть до появления соответствующего вопроса в анкете и остаётся пустым:
    # модель должна получать состояние человека тем же путём, что цель и опыт, а
    # не отдельной подсистемой, когда вопрос появится.
    current_condition: str | None = None

    # Расчёт занятия: сколько работы вмещается в заявленное время. Передаётся как
    # ориентир, а не как готовый ответ — модель вправе отступить, если состояние
    # человека того требует, но обязана уложиться в заявленное время.
    session_plan: SessionPlanBrief | None = None

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

    plan = plan_session(profile)
    return AIProgramGenerationContext(
        session_plan=SessionPlanBrief(
            total_minutes=plan.total_minutes,
            warmup_minutes=plan.warmup_minutes,
            cooldown_minutes=plan.cooldown_minutes,
            main_minutes=plan.main_minutes,
            tolerance_minutes=TOLERANCE_MINUTES,
            exercises=plan.exercises,
            sets=plan.prescription.sets,
            reps_min=plan.prescription.reps_min,
            reps_max=plan.prescription.reps_max,
            rest_seconds=plan.prescription.rest_seconds,
            approach=plan.prescription.tempo,
            capped=plan.capped,
        ),
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