"""Контролируемый baseline генерации программ через production pipeline.

Скрипт намеренно не обращается к AI Gateway или генератору напрямую. Каждый
запрос проходит через ``ProgramGenerationOrchestrator``: readiness gate,
фильтрация, safety, validator, persistence и telemetry остаются штатными.

Пример запуска в staging::

    python -m scripts.run_generation_benchmark \
        --run-id 1 \
        --report /tmp/benchmark-run-1.json

Для repeatability указываются только заранее выбранные profile IDs и новый номер
запуска. Это не retry: каждый профиль получает ровно один новый admin request.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from apps.backend.api.v1.dependencies import build_generation_orchestrator
from src.application.programs.orchestrator import GenerationRequest
from src.domain.enums import (
    CardioPreference,
    DailyActivityLevel,
    ExperienceLevel,
    GenerationJobStatus,
    PrimaryGoal,
    Sex,
    TrainingLocationType,
)
from src.domain.generation import GenerationTrigger
from src.domain.profile import FitnessProfile
from src.errors import WorkoutBotError
from src.infrastructure.persistence.postgres.db import dispose_engine, get_session_factory
from src.infrastructure.persistence.postgres.profile_repository import PostgresProfileRepository


@dataclass(frozen=True)
class BenchmarkScenario:
    benchmark_id: str
    purpose: str
    age_years: int
    sex: Sex
    goal: PrimaryGoal
    experience: ExperienceLevel
    location: TrainingLocationType
    sessions_per_week: int
    equipment: tuple[str, ...] = ()
    custom_equipment: str | None = None
    movements_to_avoid: tuple[str, ...] = ()
    schedule_constraints: str | None = None
    cardio_preference: CardioPreference = CardioPreference.OKAY
    special_request: str | None = None


SCENARIOS: tuple[BenchmarkScenario, ...] = (
    BenchmarkScenario("BENCH-001", "beginner-bodyweight", 24, Sex.FEMALE, PrimaryGoal.HEALTH_FITNESS, ExperienceLevel.NEVER, TrainingLocationType.HOME, 2),
    BenchmarkScenario("BENCH-002", "beginner-weight-loss-dumbbells", 31, Sex.MALE, PrimaryGoal.WEIGHT_LOSS, ExperienceLevel.UNDER_3_MONTHS, TrainingLocationType.HOME, 3, ("гантели",)),
    BenchmarkScenario("BENCH-003", "return-after-break-bands-spine", 42, Sex.FEMALE, PrimaryGoal.RETURN_TO_TRAINING, ExperienceLevel.LONG_BREAK, TrainingLocationType.HOME, 2, ("резиновые ленты",), movements_to_avoid=("без тяжёлой нагрузки на позвоночник",)),
    BenchmarkScenario("BENCH-004", "intermediate-hypertrophy-gym", 29, Sex.MALE, PrimaryGoal.MUSCLE_GAIN, ExperienceLevel.THREE_TWELVE_MONTHS, TrainingLocationType.GYM, 3),
    BenchmarkScenario("BENCH-005", "advanced-strength-gym", 36, Sex.MALE, PrimaryGoal.STRENGTH, ExperienceLevel.OVER_1_YEAR, TrainingLocationType.GYM, 4),
    BenchmarkScenario("BENCH-006", "home-dumbbells-and-bands", 34, Sex.FEMALE, PrimaryGoal.MUSCLE_GAIN, ExperienceLevel.THREE_TWELVE_MONTHS, TrainingLocationType.HOME, 3, ("гантели", "резиновые петли")),
    BenchmarkScenario("BENCH-007", "gym-explicit-machines", 48, Sex.MALE, PrimaryGoal.HEALTH_FITNESS, ExperienceLevel.UNDER_3_MONTHS, TrainingLocationType.GYM, 3, ("тренажёры", "блок")),
    BenchmarkScenario("BENCH-008", "bodyweight-endurance", 22, Sex.FEMALE, PrimaryGoal.ENDURANCE, ExperienceLevel.UNDER_3_MONTHS, TrainingLocationType.HOME, 4, cardio_preference=CardioPreference.LOVE),
    BenchmarkScenario("BENCH-009", "weight-loss-cardio-excluded", 39, Sex.FEMALE, PrimaryGoal.WEIGHT_LOSS, ExperienceLevel.THREE_TWELVE_MONTHS, TrainingLocationType.GYM, 3, cardio_preference=CardioPreference.EXCLUDE),
    BenchmarkScenario("BENCH-010", "strength-home-kettlebell", 45, Sex.MALE, PrimaryGoal.STRENGTH, ExperienceLevel.OVER_1_YEAR, TrainingLocationType.HOME, 3, ("гири",)),
    BenchmarkScenario("BENCH-011", "limited-dumbbell-only", 27, Sex.FEMALE, PrimaryGoal.HEALTH_FITNESS, ExperienceLevel.UNDER_3_MONTHS, TrainingLocationType.HOME, 2, ("гантели",), special_request="Только гантели и упражнения с весом тела."),
    BenchmarkScenario("BENCH-012", "limited-bands-only", 33, Sex.MALE, PrimaryGoal.MUSCLE_GAIN, ExperienceLevel.THREE_TWELVE_MONTHS, TrainingLocationType.HOME, 3, ("резиновые ленты",), special_request="Есть только резиновые ленты, другого инвентаря нет."),
    BenchmarkScenario("BENCH-013", "knee-restriction", 52, Sex.FEMALE, PrimaryGoal.HEALTH_FITNESS, ExperienceLevel.LONG_BREAK, TrainingLocationType.GYM, 2, movements_to_avoid=("избегать глубокого сгибания колен",)),
    BenchmarkScenario("BENCH-014", "no-overhead-loading", 37, Sex.MALE, PrimaryGoal.MUSCLE_GAIN, ExperienceLevel.THREE_TWELVE_MONTHS, TrainingLocationType.GYM, 3, movements_to_avoid=("без работы с весом над головой",)),
    BenchmarkScenario("BENCH-015", "no-high-impact", 30, Sex.FEMALE, PrimaryGoal.WEIGHT_LOSS, ExperienceLevel.UNDER_3_MONTHS, TrainingLocationType.HOME, 3, ("гантели",), movements_to_avoid=("без прыжков и бега",)),
    BenchmarkScenario("BENCH-016", "schedule-two-short-sessions", 41, Sex.MALE, PrimaryGoal.HEALTH_FITNESS, ExperienceLevel.UNDER_3_MONTHS, TrainingLocationType.GYM, 2, schedule_constraints="Только вторник и суббота, не более 35 минут на тренировку."),
    BenchmarkScenario("BENCH-017", "schedule-four-morning-sessions", 26, Sex.FEMALE, PrimaryGoal.ENDURANCE, ExperienceLevel.OVER_1_YEAR, TrainingLocationType.BOTH, 4, ("гантели", "резиновые ленты"), schedule_constraints="Четыре утренние тренировки в будни, до 45 минут."),
    BenchmarkScenario("BENCH-018", "older-adult-return-to-training", 63, Sex.FEMALE, PrimaryGoal.RETURN_TO_TRAINING, ExperienceLevel.LONG_BREAK, TrainingLocationType.HOME, 2, ("резиновые ленты",), movements_to_avoid=("без тяжёлой нагрузки на позвоночник", "без прыжков")),
    BenchmarkScenario("BENCH-019", "young-advanced-hypertrophy", 20, Sex.MALE, PrimaryGoal.MUSCLE_GAIN, ExperienceLevel.OVER_1_YEAR, TrainingLocationType.GYM, 4, special_request="Приоритет гипертрофии верхней части тела, но программа должна оставаться сбалансированной."),
    BenchmarkScenario("BENCH-020", "mixed-location-general-fitness", 55, Sex.MALE, PrimaryGoal.HEALTH_FITNESS, ExperienceLevel.THREE_TWELVE_MONTHS, TrainingLocationType.BOTH, 3, ("гантели", "резиновые ленты", "тренажёры")),
)


def build_profile(scenario: BenchmarkScenario) -> FitnessProfile:
    """Создаёт синтетическую анкету без Telegram identity или персональных данных."""
    profile = FitnessProfile(profile_id=scenario.benchmark_id)
    profile.client.age_years = scenario.age_years
    profile.client.sex = scenario.sex
    profile.client.height_cm = 170
    profile.client.weight_kg = 70
    profile.goals.primary = scenario.goal
    profile.goals.desired_result = f"Контролируемый benchmark: {scenario.purpose}"
    profile.training_background.experience_level = scenario.experience
    profile.training_background.current_frequency_per_week = scenario.sessions_per_week
    profile.training_plan_preferences.sessions_per_week = scenario.sessions_per_week
    profile.training_plan_preferences.session_duration_minutes = (
        35 if scenario.schedule_constraints else 60
    )
    profile.training_location.primary_location = scenario.location
    profile.training_location.available_equipment = list(scenario.equipment)
    profile.training_location.custom_equipment_description = scenario.custom_equipment
    profile.health_and_limitations.has_limitations = bool(scenario.movements_to_avoid)
    profile.health_and_limitations.movements_to_avoid = list(scenario.movements_to_avoid)
    profile.lifestyle.daily_activity_level = DailyActivityLevel.LIGHT_WALKING
    profile.lifestyle.cardio_preference = scenario.cardio_preference
    profile.additional_information.schedule_constraints = scenario.schedule_constraints
    profile.additional_information.special_requests = scenario.special_request
    profile.review.operator_notes = f"Controlled generation benchmark: {scenario.purpose}"
    return profile


def build_profiles(selected_ids: set[str] | None = None) -> list[tuple[BenchmarkScenario, FitnessProfile]]:
    scenarios = (
        SCENARIOS
        if selected_ids is None
        else tuple(item for item in SCENARIOS if item.benchmark_id in selected_ids)
    )
    if selected_ids is not None and len(scenarios) != len(selected_ids):
        known = {item.benchmark_id for item in scenarios}
        raise ValueError(f"Неизвестные benchmark IDs: {sorted(selected_ids - known)}")
    return [(scenario, build_profile(scenario)) for scenario in scenarios]


def _utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _program_summary(result) -> dict:
    program = result.program
    return {
        "program_id": program.program_id,
        "program_version": program.version,
        "generation_source": program.generation.source.value,
        "provider": program.generation.provider,
        "model": program.generation.model,
        "prompt_version": program.generation.prompt_version,
        "fallback_used": program.generation.fallback_used,
        "training_days_per_week": program.training_days_per_week,
        "exercise_count": sum(len(day.exercises) for day in program.training_days),
    }


async def run(*, run_id: int, report: Path, selected_ids: set[str] | None = None) -> dict:
    """Сохраняет controlled profiles и выполняет один штатный AI-request на каждый."""
    if run_id < 1:
        raise ValueError("run_id должен быть >= 1")

    sessions = get_session_factory()
    profiles = PostgresProfileRepository(sessions)
    controlled_profiles = build_profiles(selected_ids)
    for _, profile in controlled_profiles:
        await profiles.save(profile)

    orchestrator = build_generation_orchestrator()
    output: dict = {
        "started_at": _utcnow(),
        "run_id": run_id,
        "requested_generator": "ai",
        "allow_fallback": False,
        "results": [],
    }

    for scenario, profile in controlled_profiles:
        entry = {
            "benchmark_id": scenario.benchmark_id,
            "purpose": scenario.purpose,
            "profile": {
                "goal": scenario.goal.value,
                "experience": scenario.experience.value,
                "location": scenario.location.value,
                "sessions_per_week": scenario.sessions_per_week,
                "equipment": list(scenario.equipment),
                "movements_to_avoid": list(scenario.movements_to_avoid),
                "schedule_constraints": scenario.schedule_constraints,
            },
            "request_key": f"benchmark-20260908-{scenario.benchmark_id}-run-{run_id}",
        }
        try:
            result = await orchestrator.generate(
                GenerationRequest(
                    profile_id=profile.profile_id or scenario.benchmark_id,
                    trigger=GenerationTrigger.ADMIN_REQUEST,
                    requested_generator="ai",
                    allow_fallback=False,
                    client_idempotency_key=entry["request_key"],
                )
            )
            entry.update(
                {
                    "status": result.status.value,
                    "job_id": result.job.job_id if result.job else None,
                    "job_attempts": result.job.attempts if result.job else None,
                    "requested_generator": result.requested_generator,
                    "actual_generator": result.actual_generator,
                    "fallback_used": result.fallback_used,
                    "fallback_reason_code": result.fallback_reason_code,
                    "program": _program_summary(result),
                }
            )
        except WorkoutBotError as exc:
            entry.update(
                {
                    "status": GenerationJobStatus.FAILED.value,
                    "error_type": exc.__class__.__name__,
                    "error_code": getattr(exc, "generation_error_code", None),
                    "error_message": str(exc),
                }
            )
        output["results"].append(entry)

    output["completed_at"] = _utcnow()
    output["summary"] = {
        "profiles": len(controlled_profiles),
        "succeeded": sum(item["status"] == GenerationJobStatus.SUCCEEDED.value for item in output["results"]),
        "failed": sum(item["status"] == GenerationJobStatus.FAILED.value for item in output["results"]),
    }
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument(
        "--profile-id",
        action="append",
        dest="profile_ids",
        help="Ограничить запуск заданным BENCH-ID; флаг можно повторять.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    async def runner() -> dict:
        try:
            return await run(
                run_id=args.run_id,
                report=args.report,
                selected_ids=set(args.profile_ids) if args.profile_ids else None,
            )
        finally:
            await dispose_engine()

    output = asyncio.run(runner())
    print(json.dumps(output["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
