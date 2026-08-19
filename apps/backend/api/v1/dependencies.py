"""Фабрика зависимостей backend: сборка ProgramService.

Routes не создают бизнес-логику напрямую — только запрашивают сервис.
"""
from __future__ import annotations

import httpx

from src.application.ai.program_generator import AIProgramGenerator, PromptLoader
from src.application.media.service import ExerciseMediaService
from src.application.programs.filtering import ExerciseFilter
from src.application.programs.generator import DeterministicProgramGenerator
from src.application.programs.safety import SafetyEngine
from src.application.programs.service import ProgramService
from src.application.programs.validator import ProgramValidator
from src.infrastructure.media.object_storage import create_object_storage
from src.infrastructure.persistence.postgres.db import get_session_factory
from src.infrastructure.persistence.postgres.exercise_media_repository import (
    ExerciseMediaRepository,
)
from src.infrastructure.persistence.postgres.exercise_repository import (
    ExerciseRepository,
)
from src.infrastructure.persistence.postgres.profile_repository import (
    PostgresProfileRepository,
)
from src.infrastructure.persistence.postgres.program_repository import (
    PostgresProgramRepository,
)


def build_program_service(generator_type: str = "deterministic") -> ProgramService:
    """Собирает ProgramService с указанным генератором.

    Args:
        generator_type: "deterministic" или "ai"
    """
    session_factory = get_session_factory()

    if generator_type == "ai":
        generator = build_ai_program_generator()
    else:
        generator = DeterministicProgramGenerator()

    return ProgramService(
        profile_repository=PostgresProfileRepository(session_factory),
        exercise_repository=ExerciseRepository(session_factory),
        program_repository=PostgresProgramRepository(session_factory),
        generator=generator,
        exercise_filter=ExerciseFilter(),
        safety_engine=SafetyEngine(),
        validator=ProgramValidator(),
    )


def build_exercise_media_service() -> ExerciseMediaService:
    session_factory = get_session_factory()
    return ExerciseMediaService(
        repository=ExerciseMediaRepository(session_factory),
        storage=create_object_storage(),
    )


def build_ai_program_generator(http_client: httpx.AsyncClient | None = None) -> AIProgramGenerator:
    """Собирает AIProgramGenerator с AIGateway."""
    from apps.backend.api.v1.ai_dependencies import build_ai_components
    from src.infrastructure.persistence.postgres.ai_repository import PromptTemplateRepository

    components = build_ai_components(http_client)
    session_factory = get_session_factory()
    prompt_repo = PromptTemplateRepository(session_factory)

    return AIProgramGenerator(
        gateway=components.gateway,
        prompt_loader=PromptLoader(prompt_repo),
        validator=ProgramValidator(),
    )
