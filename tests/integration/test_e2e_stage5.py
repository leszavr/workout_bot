"""End-to-end integration test Stage 5 (реальная PostgreSQL, без Telegram).

Сценарий:
    Profile → Finalize → GenerationOrchestrator (AI → fallback при необходимости)
    → ProgramRepository → HTML Renderer → Delivery mock.

AI-зависимость имитируется (без реальной модели), deterministic — реальный,
каталог упражнений — из БД.
"""
from __future__ import annotations

import pytest

from src.application.profiles.finalization import ProfileFinalizationService
from src.application.programs.filtering import ExerciseFilter
from src.application.programs.generator import DeterministicProgramGenerator
from src.application.programs.html_service import ProgramHtmlService
from src.application.programs.orchestrator import (
    GenerationRequest,
    ProgramGenerationOrchestrator,
)
from src.application.programs.safety import SafetyEngine
from src.application.programs.telegram_delivery import ProgramDeliveryService
from src.application.programs.validator import ProgramValidator
from src.domain.enums import (
    ExperienceLevel,
    GenerationSource,
    PrimaryGoal,
    ProgramDeliveryStatus,
    ProgramStatus,
    TrainingLocationType,
)
from src.domain.generation import GenerationTrigger
from src.domain.profile import FitnessProfile
from src.errors import ProgramDeliveryError
from src.infrastructure.config import DATABASE_URL
from src.infrastructure.persistence.postgres.program_repository import (
    PostgresProgramRepository,
)

pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL is not set")

TEST_TELEGRAM_ID = "900888"
CHAT_ID = "424242"


@pytest.fixture
async def session_factory():
    from src.infrastructure.persistence.postgres.db import get_session_factory

    return get_session_factory()


class FailingAIGenerator:
    async def generate(self, profile, pool):
        raise RuntimeError("AI provider unavailable in tests")


class StubAIGenerator:
    """Имитация успешного AI: строит программу из реального safe-пула."""

    def __init__(self, base_generator: DeterministicProgramGenerator) -> None:
        self._base = base_generator

    async def generate(self, profile, pool):
        program = await self._base.generate(profile, pool)
        program.generation.source = GenerationSource.AI
        program.generation.provider = "test-provider"
        program.generation.model = "test-model"
        program.generation.prompt_version = 1
        return program


class FakeMediaService:
    """Медиа для e2e: в БД нет тестовых медиа → пустые списки."""

    async def bulk_list(self, pairs, limit_per_exercise=None):
        return {}

    async def list_for_exercise(self, external_id, source="leszavr/workout", limit=None):
        return []

    async def get_bytes(self, asset):
        raise AssertionError("unexpected media read in e2e test")

    def public_url(self, asset, base_url):
        return ""


class FakeDeliverySender:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.sent: list[tuple[str, str, int]] = []

    async def __call__(self, chat_id: str, document) -> int:
        if self.fail:
            raise RuntimeError("telegram down")
        self.sent.append((chat_id, document.filename, len(document.bytes_content)))
        return 777


@pytest.fixture(autouse=True)
async def cleanup():
    from sqlalchemy import delete, select

    from src.infrastructure.persistence.postgres.db import dispose_engine, get_session_factory
    from src.infrastructure.persistence.postgres.models import (
        ConsentRow,
        ProfileRow,
        ProgramDeliveryRow,
        UserRow,
        WorkoutProgramRow,
    )

    async def _purge() -> None:
        async with get_session_factory()() as session:
            async with session.begin():
                profile_ids = (
                    await session.execute(
                        select(ProfileRow.profile_id).where(
                            ProfileRow.profile_id.like("test-stage5-%")
                        )
                    )
                ).scalars().all()
                if profile_ids:
                    await session.execute(
                        delete(ProgramDeliveryRow).where(
                            ProgramDeliveryRow.profile_id.in_(profile_ids)
                        )
                    )
                    await session.execute(
                        delete(WorkoutProgramRow).where(
                            WorkoutProgramRow.profile_id.in_(profile_ids)
                        )
                    )
                user_ids = (
                    await session.execute(
                        select(UserRow.id).where(UserRow.telegram_user_id == TEST_TELEGRAM_ID)
                    )
                ).scalars().all()
                if user_ids:
                    await session.execute(delete(ConsentRow).where(ConsentRow.user_id.in_(user_ids)))
                    await session.execute(
                        delete(ProfileRow).where(ProfileRow.user_id.in_(user_ids))
                    )
                    await session.execute(delete(UserRow).where(UserRow.id.in_(user_ids)))

    await _purge()
    yield
    await _purge()
    from src.infrastructure.persistence.postgres.db import dispose_engine

    await dispose_engine()


def _profile(profile_id: str) -> FitnessProfile:
    profile = FitnessProfile(profile_id=profile_id)
    profile.source.bot_user_id = TEST_TELEGRAM_ID
    profile.source.telegram_username = "test_stage5"
    profile.client.name = "Тест Stage5"
    profile.client.age_years = 28
    profile.goals.primary = PrimaryGoal.MUSCLE_GAIN
    profile.training_background.experience_level = ExperienceLevel.THREE_TWELVE_MONTHS
    profile.training_location.primary_location = TrainingLocationType.GYM
    profile.training_plan_preferences.sessions_per_week = 3
    return profile


def _build_contour(session_factory, primary_generator, fallback_generator, sender):
    """Оркестратор и доставка по отдельности.

    Общего pipeline-сервиса больше нет: после выноса Gateway за сетевую границу
    генерацию запускает Backend, а отправку выполняет Gateway по заданию из
    очереди. Сквозной путь проверяется здесь теми же двумя шагами, что и в
    продакшене, — генерация, затем доставка.
    """
    from src.infrastructure.persistence.postgres.delivery_repository import (
        ProgramDeliveryRepository,
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

    deterministic = DeterministicProgramGenerator()

    def ai_factory():
        return StubAIGenerator(deterministic)

    orchestrator = ProgramGenerationOrchestrator(
        profile_repository=PostgresProfileRepository(session_factory),
        exercise_repository=ExerciseRepository(session_factory),
        program_repository=PostgresProgramRepository(session_factory),
        primary_generator=primary_generator,
        fallback_generator=fallback_generator,
        ai_generator_factory=ai_factory,
        deterministic_generator=deterministic,
        exercise_filter=ExerciseFilter(),
        safety_engine=SafetyEngine(),
        validator=ProgramValidator(),
    )

    html_service = ProgramHtmlService(
        exercise_repository=ExerciseRepository(session_factory),
        media_service=FakeMediaService(),
        media_mode="html",
        max_media_per_exercise=5,
    )
    delivery = ProgramDeliveryService(
        html_service=html_service,
        delivery_repository=ProgramDeliveryRepository(session_factory),
        sender=sender,
        max_attempts=2,
    )
    return orchestrator, delivery


class TestEndToEndStage5:
    async def _generate_and_deliver(self, orchestrator, delivery, profile_id, *, reuse=False):
        """Тот же порядок, что в продакшене: сначала генерация, потом отправка."""
        result = await orchestrator.generate(
            GenerationRequest(
                profile_id=profile_id,
                trigger=GenerationTrigger.AUTO_FINALIZATION,
                reuse_existing=reuse,
            )
        )
        await delivery.deliver(program=result.program, chat_id=CHAT_ID)
        return result

    async def test_e2e_ai_success_flow(self, session_factory, monkeypatch):
        import src.application.programs.telegram_delivery as dm

        monkeypatch.setattr(dm, "RETRY_BASE_DELAY", 0.0)

        profile = _profile("test-stage5-e2e-ai")
        from src.infrastructure.persistence.postgres.profile_repository import (
            PostgresProfileRepository,
        )

        finalization = ProfileFinalizationService(PostgresProfileRepository(session_factory))
        result = await finalization.finalize(profile)
        assert result.already_finalized is False
        assert result.profile.profile_id == profile.profile_id

        sender = FakeDeliverySender()
        orchestrator, delivery = _build_contour(
            session_factory, "ai", "deterministic", sender
        )
        outcome = await self._generate_and_deliver(
            orchestrator, delivery, profile.profile_id
        )

        program = outcome.program
        assert program.status is ProgramStatus.VALIDATED
        assert program.generation.source is GenerationSource.AI
        assert program.generation.requested_generator is GenerationSource.AI
        assert program.generation.actual_generator is GenerationSource.AI
        assert program.generation.fallback_used is False
        assert program.generation.prompt_version == 1
        assert len(sender.sent) == 1

    async def test_e2e_ai_failure_falls_back(self, session_factory):
        from src.infrastructure.persistence.postgres.exercise_repository import (
            ExerciseRepository,
        )
        from src.infrastructure.persistence.postgres.profile_repository import (
            PostgresProfileRepository,
        )
        from src.infrastructure.persistence.postgres.program_repository import (
            PostgresProgramRepository,
        )

        profile = _profile("test-stage5-e2e-fallback")
        await PostgresProfileRepository(session_factory).save(profile)

        orchestrator = ProgramGenerationOrchestrator(
            profile_repository=PostgresProfileRepository(session_factory),
            exercise_repository=ExerciseRepository(session_factory),
            program_repository=PostgresProgramRepository(session_factory),
            primary_generator="ai",
            fallback_generator="deterministic",
            ai_generator_factory=FailingAIGenerator,
            deterministic_generator=DeterministicProgramGenerator(),
        )

        result = await orchestrator.generate(
            GenerationRequest(
                profile_id=profile.profile_id,
                trigger=GenerationTrigger.AUTO_FINALIZATION,
            )
        )

        assert result.fallback_used is True
        program = result.program
        assert program.generation.requested_generator is GenerationSource.AI
        assert program.generation.actual_generator is GenerationSource.DETERMINISTIC

    async def test_e2e_delivery_failure_does_not_regenerate(self, session_factory, monkeypatch):
        import src.application.programs.telegram_delivery as dm

        monkeypatch.setattr(dm, "RETRY_BASE_DELAY", 0.0)

        profile = _profile("test-stage5-e2e-delivery")
        from src.infrastructure.persistence.postgres.profile_repository import (
            PostgresProfileRepository,
        )

        await PostgresProfileRepository(session_factory).save(profile)

        broken_sender = FakeDeliverySender(fail=True)
        orchestrator, broken_delivery = _build_contour(
            session_factory, "deterministic", "ai", broken_sender
        )

        with pytest.raises(ProgramDeliveryError):
            await self._generate_and_deliver(
                orchestrator, broken_delivery, profile.profile_id
            )

        first_versions = await PostgresProgramRepository(
            session_factory
        ).list_for_profile(profile.profile_id)
        assert len(first_versions) == 1

        # Повторная отправка с reuse_existing: программа переиспользуется,
        # генератор не вызывается заново. Это и есть требование «delivery retry
        # не запускает generation retry».
        ok_sender = FakeDeliverySender()
        orchestrator_ok, delivery_ok = _build_contour(
            session_factory, "deterministic", "ai", ok_sender
        )
        second = await self._generate_and_deliver(
            orchestrator_ok, delivery_ok, profile.profile_id, reuse=True
        )
        assert second.reused_existing is True
        assert second.program.program_id == first_versions[0].program_id
        assert len(ok_sender.sent) == 1

    async def test_e2e_html_and_delivery_records(self, session_factory, monkeypatch):
        import src.application.programs.telegram_delivery as dm

        monkeypatch.setattr(dm, "RETRY_BASE_DELAY", 0.0)

        profile = _profile("test-stage5-e2e-html")
        from src.infrastructure.persistence.postgres.profile_repository import (
            PostgresProfileRepository,
        )
        from src.infrastructure.persistence.postgres.delivery_repository import (
            ProgramDeliveryRepository,
        )

        await PostgresProfileRepository(session_factory).save(profile)

        sender = FakeDeliverySender()
        orchestrator, delivery = _build_contour(
            session_factory, "deterministic", "ai", sender
        )
        await self._generate_and_deliver(orchestrator, delivery, profile.profile_id)

        chat_id, filename, size = sender.sent[0]
        assert chat_id == CHAT_ID
        assert filename == f"workout_program_{profile.profile_id}_v1.html"
        assert size > 0

        repo = ProgramDeliveryRepository(session_factory)
        record = await repo.get_for_profile(profile.profile_id)
        assert record is not None
        assert record.status is ProgramDeliveryStatus.SENT
        assert record.attempts == 1
        assert record.source_media_mode == "html"

    async def test_e2e_reuse_existing_skips_generation(self, session_factory):
        from src.infrastructure.persistence.postgres.profile_repository import (
            PostgresProfileRepository,
        )
        from src.infrastructure.persistence.postgres.program_repository import (
            PostgresProgramRepository,
        )

        profile = _profile("test-stage5-e2e-reuse")
        await PostgresProfileRepository(session_factory).save(profile)

        sender = FakeDeliverySender()
        orchestrator, delivery = _build_contour(
            session_factory, "deterministic", "ai", sender
        )
        await self._generate_and_deliver(orchestrator, delivery, profile.profile_id)

        program_repo = PostgresProgramRepository(session_factory)
        count_before = len(await program_repo.list_for_profile(profile.profile_id))

        outcome = await self._generate_and_deliver(
            orchestrator, delivery, profile.profile_id, reuse=True
        )
        count_after = len(await program_repo.list_for_profile(profile.profile_id))

        assert count_before == count_after == 1
        assert outcome.reused_existing is True