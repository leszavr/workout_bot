"""Интеграционные тесты pipeline генерации программ против реальной PostgreSQL.

Сквозной сценарий:
    Create Profile → Filter Exercises → Apply Safety → Generate Program
    → Validate Program → Save Program → Retrieve Program.
Плюс версионирование: повторная генерация не уничтожает старую версию.

Phase 1.2-C: генерация идёт через единственную точку —
`ProgramGenerationOrchestrator`; `ProgramService` отвечает только за чтение.

Требуют DATABASE_URL (docker compose postgres). Иначе пропускаются.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from src.application.programs.filtering import ExerciseFilter
from src.application.programs.generator import DeterministicProgramGenerator
from src.application.programs.orchestrator import (
    GenerationRequest,
    ProgramGenerationOrchestrator,
)
from src.application.programs.safety import SafetyEngine
from src.application.programs.service import ProgramService
from src.application.programs.validator import ProgramValidator
from src.domain.enums import (
    ExperienceLevel,
    PrimaryGoal,
    ProgramStatus,
    TrainingLocationType,
)
from src.domain.generation import GenerationTrigger
from src.domain.profile import FitnessProfile
from src.infrastructure.config import DATABASE_URL

pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL is not set")

TEST_TELEGRAM_ID = "900077"


@pytest.fixture
async def session_factory():
    from src.infrastructure.persistence.postgres.db import get_session_factory

    return get_session_factory()


@pytest.fixture
async def profiles(session_factory):
    from src.infrastructure.persistence.postgres.profile_repository import (
        PostgresProfileRepository,
    )

    return PostgresProfileRepository(session_factory)


@pytest.fixture
async def orchestrator(session_factory):
    from src.infrastructure.persistence.postgres.exercise_repository import (
        ExerciseRepository,
    )
    from src.infrastructure.persistence.postgres.profile_repository import (
        PostgresProfileRepository,
    )
    from src.infrastructure.persistence.postgres.program_repository import (
        PostgresProgramRepository,
    )

    return ProgramGenerationOrchestrator(
        profile_repository=PostgresProfileRepository(session_factory),
        exercise_repository=ExerciseRepository(session_factory),
        program_repository=PostgresProgramRepository(session_factory),
        primary_generator="deterministic",
        fallback_generator="deterministic",
        deterministic_generator=DeterministicProgramGenerator(),
        exercise_filter=ExerciseFilter(),
        safety_engine=SafetyEngine(),
        validator=ProgramValidator(),
    )


@pytest.fixture
async def program_service(session_factory):
    from src.infrastructure.persistence.postgres.program_repository import (
        PostgresProgramRepository,
    )

    return ProgramService(program_repository=PostgresProgramRepository(session_factory))


@pytest.fixture(autouse=True)
async def cleanup_test_data():
    """Удаляет тестовые данные (telegram_user_id 900077) до и после тестов."""
    from src.infrastructure.persistence.postgres.db import get_session_factory
    from src.infrastructure.persistence.postgres.models import (
        ConsentRow,
        GenerationJobRow,
        ProfileRow,
        UserRow,
        WorkoutProgramRow,
    )

    async def _purge() -> None:
        async with get_session_factory()() as session:
            async with session.begin():
                profile_ids = (
                    await session.execute(
                        select(ProfileRow.profile_id).where(
                            ProfileRow.profile_id.like("test-prog-%")
                        )
                    )
                ).scalars().all()
                if profile_ids:
                    await session.execute(
                        GenerationJobRow.__table__.delete().where(
                            GenerationJobRow.profile_id.in_(profile_ids)
                        )
                    )
                    await session.execute(
                        WorkoutProgramRow.__table__.delete().where(
                            WorkoutProgramRow.profile_id.in_(profile_ids)
                        )
                    )
                user_ids = (
                    await session.execute(
                        select(UserRow.id).where(
                            UserRow.telegram_user_id == TEST_TELEGRAM_ID
                        )
                    )
                ).scalars().all()
                if user_ids:
                    await session.execute(
                        ConsentRow.__table__.delete().where(ConsentRow.user_id.in_(user_ids))
                    )
                    await session.execute(
                        ProfileRow.__table__.delete().where(ProfileRow.user_id.in_(user_ids))
                    )
                    await session.execute(
                        UserRow.__table__.delete().where(UserRow.id.in_(user_ids))
                    )

    await _purge()
    yield
    await _purge()
    from src.infrastructure.persistence.postgres.db import dispose_engine

    await dispose_engine()


def _gym_profile(profile_id: str) -> FitnessProfile:
    profile = FitnessProfile(profile_id=profile_id)
    profile.source.bot_user_id = TEST_TELEGRAM_ID
    profile.source.telegram_username = "test_program_user"
    profile.client.name = "Тест Программы"
    profile.client.age_years = 30
    profile.goals.primary = PrimaryGoal.MUSCLE_GAIN
    profile.training_background.experience_level = ExperienceLevel.THREE_TWELVE_MONTHS
    profile.training_location.primary_location = TrainingLocationType.GYM
    profile.training_plan_preferences.sessions_per_week = 3
    return profile


def _request(profile_id: str) -> GenerationRequest:
    return GenerationRequest(
        profile_id=profile_id, trigger=GenerationTrigger.ADMIN_REQUEST
    )


class TestEndToEndPipeline:
    async def test_full_pipeline(self, orchestrator, profiles, program_service):
        profile = _gym_profile("test-prog-e2e-1")
        await profiles.save(profile)

        result = await orchestrator.generate(_request(profile.profile_id))

        program = result.program
        assert program.status is ProgramStatus.VALIDATED
        assert program.profile_id == profile.profile_id
        assert program.version == 1
        assert program.program_id is not None
        assert program.training_days_per_week == 3
        assert len(program.training_days) == 3

        # Программа использует только упражнения из safe-пула.
        allowed_ids = result.safe_pool.allowed_ids()
        for day in program.training_days:
            for item in day.exercises:
                assert item.exercise_external_id in allowed_ids

        # Программа сохранена и читаема.
        loaded = await program_service.get(program.program_id)
        assert loaded is not None
        assert loaded.program_id == program.program_id
        assert loaded.title == program.title
        assert len(loaded.training_days) == 3

    async def test_generate_missing_profile_raises(self, orchestrator):
        from src.errors import ProgramGenerationError

        with pytest.raises(ProgramGenerationError):
            await orchestrator.generate(_request("nonexistent-profile-id"))


class TestVersioning:
    async def test_regeneration_creates_new_version(
        self, orchestrator, profiles, program_service
    ):
        profile = _gym_profile("test-prog-ver-1")
        await profiles.save(profile)

        first = await orchestrator.generate(_request(profile.profile_id))
        second = await orchestrator.generate(_request(profile.profile_id))

        assert first.program.version == 1
        assert second.program.version == 2
        assert first.program.program_id != second.program.program_id

        # Старая версия доступна.
        versions = await program_service.list_versions(first.program.program_id)
        assert len(versions) == 1
        assert versions[0].version == 1

        # Программа профиля — последняя версия каждой программы.
        profile_programs = await program_service.list_for_profile(profile.profile_id)
        assert len(profile_programs) == 2

    async def test_historical_version_not_destroyed(
        self, orchestrator, profiles, program_service
    ):
        profile = _gym_profile("test-prog-hist-1")
        await profiles.save(profile)

        first = await orchestrator.generate(_request(profile.profile_id))
        await orchestrator.generate(_request(profile.profile_id))

        # Первая версия всё ещё читается по program_id + version.
        loaded_v1 = await program_service.get(first.program.program_id, version=1)
        assert loaded_v1 is not None
        assert loaded_v1.version == 1


class TestProgramRepository:
    async def test_list_all_returns_latest_versions(
        self, orchestrator, profiles, program_service
    ):
        profile = _gym_profile("test-prog-list-1")
        await profiles.save(profile)
        await orchestrator.generate(_request(profile.profile_id))

        total, programs = await program_service.list_all(limit=50)
        assert total >= 1
        assert all(p.program_id for p in programs)

    async def test_count(self, orchestrator, profiles, session_factory):
        from src.infrastructure.persistence.postgres.program_repository import (
            PostgresProgramRepository,
        )

        profile = _gym_profile("test-prog-count-1")
        await profiles.save(profile)
        await orchestrator.generate(_request(profile.profile_id))

        count = await PostgresProgramRepository(session_factory).count()
        assert count >= 1
