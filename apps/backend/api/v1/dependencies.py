"""Фабрика зависимостей backend: сборка ProgramService.

Routes не создают бизнес-логику напрямую — только запрашивают сервис.
"""
from __future__ import annotations

from src.application.programs.filtering import ExerciseFilter
from src.application.programs.generator import DeterministicProgramGenerator
from src.application.programs.safety import SafetyEngine
from src.application.programs.service import ProgramService
from src.application.programs.validator import ProgramValidator
from src.infrastructure.persistence.postgres.db import get_session_factory
from src.infrastructure.persistence.postgres.exercise_repository import (
    ExerciseRepository,
)
from src.infrastructure.persistence.postgres.profile_repository import (
    PostgresProfileRepository,
)
from src.infrastructure.persistence.postgres.program_repository import (
    PostgresProgramRepository,
)


def build_program_service() -> ProgramService:
    session_factory = get_session_factory()
    return ProgramService(
        profile_repository=PostgresProfileRepository(session_factory),
        exercise_repository=ExerciseRepository(session_factory),
        program_repository=PostgresProgramRepository(session_factory),
        generator=DeterministicProgramGenerator(),
        exercise_filter=ExerciseFilter(),
        safety_engine=SafetyEngine(),
        validator=ProgramValidator(),
    )
