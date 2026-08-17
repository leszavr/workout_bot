from __future__ import annotations

from datetime import datetime, timezone

from config import MAX_TEXT_LENGTH


def normalize_text(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    return value[:MAX_TEXT_LENGTH]


def build_empty_profile() -> dict:
    return {
        "schema_version": "1.0",
        "profile_id": None,
        "created_at": None,
        "updated_at": None,
        "source": {
            "platform": "telegram",
            "bot_user_id": None,
            "telegram_username": None,
        },
        "client": {
            "name": None,
            "age_years": None,
            "sex": None,
            "height_cm": None,
            "weight_kg": None,
            "waist_cm": None,
        },
        "goals": {
            "primary": None,
            "primary_custom": None,
            "secondary": [],
            "desired_result": None,
            "target_timeframe": None,
        },
        "training_background": {
            "experience_level": None,
            "current_frequency_per_week": 0,
            "current_activity_description": None,
            "current_exercises": [],
            "known_working_weights": [],
            "previous_training_notes": None,
        },
        "training_plan_preferences": {
            "sessions_per_week": 0,
            "preferred_days": [],
            "session_duration_minutes": 0,
            "preferred_training_time": None,
        },
        "training_location": {
            "primary_location": None,
            "gym_name": None,
            "available_equipment": [],
            "custom_equipment_description": None,
            "equipment_photos": [],
        },
        "health_and_limitations": {
            "has_limitations": False,
            "categories": [],
            "details": [],
            "doctor_recommendations": None,
            "movements_to_avoid": [],
            "medical_clearance_required": False,
        },
        "exercise_preferences": {
            "preferred_exercises": [],
            "disliked_exercises": [],
            "excluded_exercises": [],
            "exercise_goals": [],
        },
        "lifestyle": {
            "daily_activity_level": None,
            "cardio_preference": None,
            "cardio_notes": None,
        },
        "additional_information": {
            "schedule_constraints": None,
            "special_requests": None,
            "free_text": None,
        },
        "questionnaire": {
            "completed": False,
            "completion_status": "draft",
            "last_question_id": None,
            "skipped_questions": [],
        },
        "consents": {
            "data_processing_confirmed": False,
            "health_information_confirmed": False,
            "accuracy_confirmed": False,
        },
        "review": {
            "client_summary_confirmed": False,
            "client_corrections": [],
            "operator_notes": None,
        },
    }


def normalize_profile(profile: dict) -> dict:
    base = build_empty_profile()
    for key, value in base.items():
        if key not in profile:
            profile[key] = value
    for key, value in base.items():
        if isinstance(value, dict) and isinstance(profile.get(key), dict):
            for sub_key, sub_value in value.items():
                profile[key].setdefault(sub_key, sub_value)
    return profile


def set_profile_timestamps(profile: dict) -> dict:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    profile["updated_at"] = now
    if not profile.get("created_at"):
        profile["created_at"] = now
    return profile
