"""Интеграционные тесты retry/recovery на реальной PostgreSQL (Phase 1.2-D).

Проверяются свойства, которые нельзя доказать на фейках:

- два экземпляра worker'а не берут один job (`FOR UPDATE SKIP LOCKED`);
- захват и перевод в `RUNNING` неделимы: проигравший видит job уже занятым;
- job, оставшийся в `RUNNING` после «падения» процесса, подхватывается по
  просроченной аренде;
- повторная обработка того же job не создаёт вторую программу и второй job;
- повтор идёт через оркестратор, поэтому программа проходит validator и
  сохраняется штатным путём.

Конкурентные тесты используют независимые engine: общий пул соединений мог бы
объяснить результат сам по себе, и доказательства DB-level взаимного исключения
не получилось бы.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.application.programs.filtering import ExerciseFilter
from src.application.programs.generation_jobs import GenerationJobService
from src.application.programs.generator import DeterministicProgramGenerator
from src.application.programs.orchestrator import (
    GenerationRequest,
    ProgramGenerationOrchestrator,
)
from src.application.programs.retry_service import (
    DeliveryRecoveryService,
    GenerationRetryService,
    RetryCoordinator,
)
from src.application.programs.safety import SafetyEngine
from src.application.programs.validator import ProgramValidator
from src.domain.ai.errors import AITimeoutError
from src.domain.enums import (
    ExperienceLevel,
    GenerationJobStatus,
    PrimaryGoal,
    ProgramDeliveryStatus,
    TrainingLocationType,
)
from src.domain.generation import GenerationErrorCode, GenerationTrigger
from src.domain.profile import FitnessProfile
from src.domain.retry import RetryPolicy
from src.errors import (
    ProgramGenerationError,
    ProgramPersistenceError,
    ProgramValidationError,
)
from src.infrastructure.config import DATABASE_URL
from src.infrastructure.persistence.postgres.delivery_repository import (
    ProgramDeliveryRecord,
    ProgramDeliveryRepository,
)
from src.infrastructure.persistence.postgres.generation_job_repository import (
    GenerationJobRepository,
)
from src.infrastructure.persistence.postgres.models import (
    ConsentRow,
    GenerationJobRow,
    ProfileRow,
    ProgramDeliveryRow,
    UserRow,
    WorkoutProgramRow,
)
from src.infrastructure.persistence.postgres.profile_repository import (
    PostgresProfileRepository,
)
from src.infrastructure.persistence.postgres.program_repository import (
    PostgresProgramRepository,
)

pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL is not set")

TEST_TELEGRAM_ID = "900555"
PROFILE_PREFIX = "test-worker-"
OWNER_A = "worker-test-a"
OWNER_B = "worker-test-b"
LEASE = 120.0

POLICY = RetryPolicy(
    max_attempts=3,
    initial_delay_seconds=60,
    multiplier=4,
    max_delay_seconds=900,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@pytest.fixture
async def engine():
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def sessions(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
async def second_sessions():
    """Второй независимый engine для проверки DB-level взаимного исключения."""
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.fixture(autouse=True)
async def cleanup(sessions):
    """Удаляет только собственные записи: база общая с другими тестами."""

    async def _purge() -> None:
        async with sessions() as session:
            async with session.begin():
                profile_ids = (
                    await session.execute(
                        select(ProfileRow.profile_id).where(
                            ProfileRow.profile_id.like(f"{PROFILE_PREFIX}%")
                        )
                    )
                ).scalars().all()
                if profile_ids:
                    await session.execute(
                        delete(GenerationJobRow).where(
                            GenerationJobRow.profile_id.in_(profile_ids)
                        )
                    )
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
                        select(UserRow.id).where(
                            UserRow.telegram_user_id == TEST_TELEGRAM_ID
                        )
                    )
                ).scalars().all()
                if user_ids:
                    await session.execute(
                        delete(ConsentRow).where(ConsentRow.user_id.in_(user_ids))
                    )
                    await session.execute(
                        delete(ProfileRow).where(ProfileRow.user_id.in_(user_ids))
                    )
                    await session.execute(delete(UserRow).where(UserRow.id.in_(user_ids)))

    await _purge()
    yield
    await _purge()


async def _save_profile(sessions, profile_id: str) -> FitnessProfile:
    profile = FitnessProfile(profile_id=profile_id)
    profile.source.bot_user_id = TEST_TELEGRAM_ID
    profile.source.telegram_username = "test_worker"
    profile.client.name = "Тест Worker"
    profile.client.age_years = 30
    profile.goals.primary = PrimaryGoal.MUSCLE_GAIN
    profile.training_background.experience_level = ExperienceLevel.THREE_TWELVE_MONTHS
    profile.training_location.primary_location = TrainingLocationType.GYM
    profile.training_plan_preferences.sessions_per_week = 3
    await PostgresProfileRepository(sessions).save(profile)
    return profile


class _BrokenAIGenerator:
    """Transient-отказ: такой job подлежит повтору."""

    async def generate(self, profile, pool):
        raise AITimeoutError("AI недоступен в тесте")


class _InvalidGenerator:
    """Non-retryable отказ: повтор его не лечит."""

    async def generate(self, profile, pool):
        raise ProgramValidationError("программа не прошла валидацию в тесте")


class _TransientGenerator:
    """Transient-отказ, который не лечится подменой генератора.

    Нужен для проверки исчерпания попыток: отказ самого AI-провайдера повтор
    закрывает алгоритмическим генератором внутри той же попытки, поэтому такой
    job успешно завершается с первого повтора. Недоступность хранилища валит
    операцию целиком и остаётся transient.
    """

    async def generate(self, profile, pool):
        raise ProgramPersistenceError("хранилище недоступно в тесте")


def _orchestrator(
    sessions,
    *,
    primary: str = "ai",
    allow_jobs: bool = True,
    ai_generator=None,
    deterministic=None,
    policy: RetryPolicy | None = POLICY,
) -> ProgramGenerationOrchestrator:
    programs = PostgresProgramRepository(sessions)
    jobs = (
        GenerationJobService(
            repository=GenerationJobRepository(sessions),
            program_repository=programs,
            retry_policy=policy,
        )
        if allow_jobs
        else None
    )
    from src.infrastructure.persistence.postgres.exercise_repository import (
        ExerciseRepository,
    )

    return ProgramGenerationOrchestrator(
        profile_repository=PostgresProfileRepository(sessions),
        exercise_repository=ExerciseRepository(sessions),
        program_repository=programs,
        primary_generator=primary,
        fallback_generator="deterministic",
        ai_generator_factory=(lambda: ai_generator) if ai_generator else None,
        deterministic_generator=deterministic or DeterministicProgramGenerator(),
        exercise_filter=ExerciseFilter(),
        safety_engine=SafetyEngine(),
        validator=ProgramValidator(),
        generation_jobs=jobs,
    )


def _auto_request(profile_id: str) -> GenerationRequest:
    """Автогенерация после finalize. Fallback здесь отключён специально.

    Иначе AI-отказ был бы «вылечен» алгоритмическим генератором внутри той же
    попытки, и job закрылся бы успехом — повторять было бы нечего.
    """
    return GenerationRequest(
        profile_id=profile_id,
        trigger=GenerationTrigger.AUTO_FINALIZATION,
        requested_generator="ai",
        allow_fallback=False,
        reuse_existing=False,
    )


def _retry_service(sessions, orchestrator, *, owner: str = OWNER_A):
    return GenerationRetryService(
        jobs=GenerationJobRepository(sessions),
        orchestrator=orchestrator,
        policy=POLICY,
        owner=owner,
        lease_seconds=LEASE,
    )


async def _count(sessions, model, profile_id: str) -> int:
    async with sessions() as session:
        rows = (
            await session.execute(
                select(model.id).where(model.profile_id == profile_id)
            )
        ).scalars().all()
    return len(rows)


async def _make_due(sessions, job_id: str) -> None:
    """Сдвигает назначенный повтор в прошлое: тест не ждёт минуту."""
    async with sessions() as session:
        async with session.begin():
            await session.execute(
                GenerationJobRow.__table__.update()
                .where(GenerationJobRow.job_id == job_id)
                .values(next_attempt_at=_utcnow() - timedelta(seconds=1))
            )


# --- Планирование повтора ------------------------------------------------------


class TestRetryScheduling:
    async def test_transient_failure_schedules_retry(self, sessions):
        """Сетевой отказ AI: job закрыт, но повтор назначен."""
        profile = await _save_profile(sessions, f"{PROFILE_PREFIX}transient")
        orchestrator = _orchestrator(sessions, ai_generator=_BrokenAIGenerator())

        with pytest.raises(ProgramGenerationError):
            await orchestrator.generate(_auto_request(profile.profile_id))

        jobs = await GenerationJobRepository(sessions).list_for_profile(
            profile.profile_id
        )
        assert jobs[0].status is GenerationJobStatus.FAILED
        assert jobs[0].last_error_code == GenerationErrorCode.AI_TIMEOUT.value
        assert jobs[0].next_attempt_at is not None

    async def test_non_retryable_failure_is_final(self, sessions):
        """Валидация повтором не лечится: повтор не назначается."""
        profile = await _save_profile(sessions, f"{PROFILE_PREFIX}permanent")
        orchestrator = _orchestrator(sessions, ai_generator=_InvalidGenerator())

        with pytest.raises(ProgramGenerationError):
            await orchestrator.generate(_auto_request(profile.profile_id))

        jobs = await GenerationJobRepository(sessions).list_for_profile(
            profile.profile_id
        )
        assert jobs[0].status is GenerationJobStatus.FAILED
        assert jobs[0].next_attempt_at is None

    async def test_retry_is_not_scheduled_without_policy(self, sessions):
        """Без политики повторов поведение прежнее (1.2-B)."""
        profile = await _save_profile(sessions, f"{PROFILE_PREFIX}no-policy")
        orchestrator = _orchestrator(
            sessions, ai_generator=_BrokenAIGenerator(), policy=None
        )

        with pytest.raises(ProgramGenerationError):
            await orchestrator.generate(_auto_request(profile.profile_id))

        jobs = await GenerationJobRepository(sessions).list_for_profile(
            profile.profile_id
        )
        assert jobs[0].next_attempt_at is None

    async def test_attempts_are_exhausted_after_max(self, sessions):
        """Повторы не бесконечны: после исчерпания попыток очередь пуста.

        Отказ выбран такой, который не лечится подменой генератора: иначе
        первый же повтор закрыл бы job успехом через алгоритмический генератор.
        """
        profile = await _save_profile(sessions, f"{PROFILE_PREFIX}exhaust")
        orchestrator = _orchestrator(
            sessions,
            primary="deterministic",
            deterministic=_TransientGenerator(),
        )
        repo = GenerationJobRepository(sessions)
        service = _retry_service(sessions, orchestrator)

        with pytest.raises(ProgramGenerationError):
            await orchestrator.generate(
                GenerationRequest(
                    profile_id=profile.profile_id,
                    trigger=GenerationTrigger.AUTO_FINALIZATION,
                    requested_generator="deterministic",
                    allow_fallback=False,
                    reuse_existing=False,
                )
            )
        job_id = (await repo.list_for_profile(profile.profile_id))[0].job_id

        for _ in range(POLICY.max_attempts + 2):
            job = await repo.get(job_id)
            if job.next_attempt_at is None:
                break
            await _make_due(sessions, job_id)
            await service.process_due()

        job = await repo.get(job_id)
        assert job.attempts == POLICY.max_attempts
        assert job.next_attempt_at is None
        assert job.status is GenerationJobStatus.FAILED
        # Одна логическая генерация: повторы не создают ни второй job, ни
        # вторую программу.
        assert await _count(sessions, GenerationJobRow, profile.profile_id) == 1
        assert await _count(sessions, WorkoutProgramRow, profile.profile_id) == 0


# --- Повтор через оркестратор --------------------------------------------------


class TestRetryExecution:
    async def test_retry_produces_program_through_orchestrator(self, sessions):
        """Повтор с работающим генератором доводит ту же операцию до успеха."""
        profile = await _save_profile(sessions, f"{PROFILE_PREFIX}recover-ok")
        repo = GenerationJobRepository(sessions)

        failing = _orchestrator(sessions, ai_generator=_BrokenAIGenerator())
        with pytest.raises(ProgramGenerationError):
            await failing.generate(_auto_request(profile.profile_id))
        job_id = (await repo.list_for_profile(profile.profile_id))[0].job_id
        await _make_due(sessions, job_id)

        # На повторе AI по-прежнему недоступен, но fallback внутри повтора
        # разрешён: retry относится к автогенерации, где подмена генератора и
        # задумана.
        working = _orchestrator(sessions, ai_generator=_BrokenAIGenerator())
        succeeded, failed = await _retry_service(sessions, working).process_due()

        assert (succeeded, failed) == (1, 0)
        job = await repo.get(job_id)
        assert job.status is GenerationJobStatus.SUCCEEDED
        assert job.attempts == 2
        assert job.program_id is not None
        assert job.next_attempt_at is None
        assert job.lease_owner is None
        # Идемпотентность: одна программа, один job.
        assert await _count(sessions, WorkoutProgramRow, profile.profile_id) == 1
        assert await _count(sessions, GenerationJobRow, profile.profile_id) == 1

    async def test_repeated_processing_of_same_job_creates_no_duplicate(self, sessions):
        """Обязательный тест: повторный запуск обработки не даёт дубликат."""
        profile = await _save_profile(sessions, f"{PROFILE_PREFIX}idempotent")
        repo = GenerationJobRepository(sessions)
        failing = _orchestrator(sessions, ai_generator=_BrokenAIGenerator())
        with pytest.raises(ProgramGenerationError):
            await failing.generate(_auto_request(profile.profile_id))
        job_id = (await repo.list_for_profile(profile.profile_id))[0].job_id
        await _make_due(sessions, job_id)

        service = _retry_service(sessions, _orchestrator(sessions))
        first = await service.process_due()
        # Второй проход по той же очереди: job уже успешен, брать нечего.
        second = await service.process_due()

        assert first == (1, 0)
        assert second == (0, 0)
        assert await _count(sessions, WorkoutProgramRow, profile.profile_id) == 1
        assert await _count(sessions, GenerationJobRow, profile.profile_id) == 1

    async def test_admin_job_is_not_retried(self, sessions):
        """Запрос администратора система сама не повторяет."""
        profile = await _save_profile(sessions, f"{PROFILE_PREFIX}admin-no-retry")
        orchestrator = _orchestrator(sessions, ai_generator=_BrokenAIGenerator())

        with pytest.raises(ProgramGenerationError):
            await orchestrator.generate(
                GenerationRequest(
                    profile_id=profile.profile_id,
                    trigger=GenerationTrigger.ADMIN_REQUEST,
                    requested_generator="ai",
                    allow_fallback=False,
                )
            )

        repo = GenerationJobRepository(sessions)
        job = (await repo.list_for_profile(profile.profile_id))[0]
        # Повтор запланирован сервисом job'ов (он не знает о триггерах), но
        # обработчик его не берёт и закрывает окончательно.
        await _make_due(sessions, job.job_id)
        succeeded, failed = await _retry_service(
            sessions, _orchestrator(sessions)
        ).process_due()

        assert (succeeded, failed) == (0, 1)
        job = await repo.get(job.job_id)
        assert job.status is GenerationJobStatus.FAILED
        assert job.next_attempt_at is None
        assert await _count(sessions, WorkoutProgramRow, profile.profile_id) == 0


# --- Конкуренция ---------------------------------------------------------------


class TestWorkerConcurrency:
    async def test_two_workers_do_not_claim_the_same_job(
        self, sessions, second_sessions
    ):
        """Главное свойство: один job не достаётся двум исполнителям.

        Engine'ы независимы, поэтому взаимное исключение обеспечивает
        PostgreSQL (`FOR UPDATE SKIP LOCKED`), а не общий пул соединений.
        """
        profile = await _save_profile(sessions, f"{PROFILE_PREFIX}race-claim")
        failing = _orchestrator(sessions, ai_generator=_BrokenAIGenerator())
        with pytest.raises(ProgramGenerationError):
            await failing.generate(_auto_request(profile.profile_id))
        job_id = (
            await GenerationJobRepository(sessions).list_for_profile(profile.profile_id)
        )[0].job_id
        await _make_due(sessions, job_id)

        first, second = await asyncio.gather(
            GenerationJobRepository(sessions).claim_due(
                owner=OWNER_A, lease_seconds=LEASE, limit=5
            ),
            GenerationJobRepository(second_sessions).claim_due(
                owner=OWNER_B, lease_seconds=LEASE, limit=5
            ),
        )

        claimed = [j for batch in (first, second) for j in batch]
        assert len(claimed) == 1
        assert claimed[0].job_id == job_id
        assert claimed[0].status is GenerationJobStatus.RUNNING
        assert claimed[0].attempts == 2

    async def test_two_workers_do_not_run_the_same_retry(
        self, sessions, second_sessions
    ):
        """Полный цикл: параллельные worker'ы дают одну программу."""
        profile = await _save_profile(sessions, f"{PROFILE_PREFIX}race-retry")
        failing = _orchestrator(sessions, ai_generator=_BrokenAIGenerator())
        with pytest.raises(ProgramGenerationError):
            await failing.generate(_auto_request(profile.profile_id))
        job_id = (
            await GenerationJobRepository(sessions).list_for_profile(profile.profile_id)
        )[0].job_id
        await _make_due(sessions, job_id)

        results = await asyncio.gather(
            _retry_service(sessions, _orchestrator(sessions), owner=OWNER_A).process_due(),
            _retry_service(
                second_sessions, _orchestrator(second_sessions), owner=OWNER_B
            ).process_due(),
        )

        total_succeeded = sum(r[0] for r in results)
        assert total_succeeded == 1
        assert await _count(sessions, WorkoutProgramRow, profile.profile_id) == 1
        assert await _count(sessions, GenerationJobRow, profile.profile_id) == 1

    async def test_claimed_job_is_not_visible_to_second_claim(self, sessions):
        """После захвата job уходит из очереди: повторный claim пуст."""
        profile = await _save_profile(sessions, f"{PROFILE_PREFIX}claim-once")
        failing = _orchestrator(sessions, ai_generator=_BrokenAIGenerator())
        with pytest.raises(ProgramGenerationError):
            await failing.generate(_auto_request(profile.profile_id))
        repo = GenerationJobRepository(sessions)
        job_id = (await repo.list_for_profile(profile.profile_id))[0].job_id
        await _make_due(sessions, job_id)

        first = await repo.claim_due(owner=OWNER_A, lease_seconds=LEASE)
        second = await repo.claim_due(owner=OWNER_B, lease_seconds=LEASE)

        assert len(first) == 1
        assert second == []


# --- Recovery после падения процесса ------------------------------------------


class TestCrashRecovery:
    async def test_stale_running_job_is_picked_up(self, sessions):
        """Job в RUNNING после «падения» процесса возвращается в очередь."""
        profile = await _save_profile(sessions, f"{PROFILE_PREFIX}stale")
        repo = GenerationJobRepository(sessions)
        failing = _orchestrator(sessions, ai_generator=_BrokenAIGenerator())
        with pytest.raises(ProgramGenerationError):
            await failing.generate(_auto_request(profile.profile_id))
        job_id = (await repo.list_for_profile(profile.profile_id))[0].job_id
        await _make_due(sessions, job_id)

        claimed = await repo.claim_due(owner=OWNER_A, lease_seconds=LEASE)
        assert claimed[0].status is GenerationJobStatus.RUNNING

        # Имитация падения: аренда истекает, результат никто не записал.
        async with sessions() as session:
            async with session.begin():
                await session.execute(
                    GenerationJobRow.__table__.update()
                    .where(GenerationJobRow.job_id == job_id)
                    .values(lease_expires_at=_utcnow() - timedelta(seconds=1))
                )

        released = await _retry_service(sessions, _orchestrator(sessions)).recover_stale()

        assert len(released) == 1
        job = await repo.get(job_id)
        assert job.status is GenerationJobStatus.FAILED
        assert job.lease_owner is None
        assert job.next_attempt_at is not None

    async def test_recovered_job_completes_on_next_cycle(self, sessions):
        """Полный сценарий: падение → recovery → повтор → программа."""
        profile = await _save_profile(sessions, f"{PROFILE_PREFIX}stale-full")
        repo = GenerationJobRepository(sessions)
        failing = _orchestrator(sessions, ai_generator=_BrokenAIGenerator())
        with pytest.raises(ProgramGenerationError):
            await failing.generate(_auto_request(profile.profile_id))
        job_id = (await repo.list_for_profile(profile.profile_id))[0].job_id
        await _make_due(sessions, job_id)
        await repo.claim_due(owner=OWNER_A, lease_seconds=LEASE)
        async with sessions() as session:
            async with session.begin():
                await session.execute(
                    GenerationJobRow.__table__.update()
                    .where(GenerationJobRow.job_id == job_id)
                    .values(lease_expires_at=_utcnow() - timedelta(seconds=1))
                )

        service = _retry_service(sessions, _orchestrator(sessions))
        await service.recover_stale()
        await _make_due(sessions, job_id)
        succeeded, _ = await service.process_due()

        assert succeeded == 1
        job = await repo.get(job_id)
        assert job.status is GenerationJobStatus.SUCCEEDED
        assert await _count(sessions, WorkoutProgramRow, profile.profile_id) == 1

    async def test_running_job_with_live_lease_is_not_stolen(self, sessions):
        """Живая аренда защищает исполнителя: job не отбирают на полпути."""
        profile = await _save_profile(sessions, f"{PROFILE_PREFIX}live-lease")
        repo = GenerationJobRepository(sessions)
        failing = _orchestrator(sessions, ai_generator=_BrokenAIGenerator())
        with pytest.raises(ProgramGenerationError):
            await failing.generate(_auto_request(profile.profile_id))
        job_id = (await repo.list_for_profile(profile.profile_id))[0].job_id
        await _make_due(sessions, job_id)
        await repo.claim_due(owner=OWNER_A, lease_seconds=LEASE)

        released = await _retry_service(sessions, _orchestrator(sessions)).recover_stale()

        assert released == []
        assert (await repo.get(job_id)).status is GenerationJobStatus.RUNNING

    async def test_lease_renewal_requires_ownership(self, sessions):
        """Продление аренды чужим владельцем невозможно."""
        profile = await _save_profile(sessions, f"{PROFILE_PREFIX}lease-owner")
        repo = GenerationJobRepository(sessions)
        failing = _orchestrator(sessions, ai_generator=_BrokenAIGenerator())
        with pytest.raises(ProgramGenerationError):
            await failing.generate(_auto_request(profile.profile_id))
        job_id = (await repo.list_for_profile(profile.profile_id))[0].job_id
        await _make_due(sessions, job_id)
        claimed = (await repo.claim_due(owner=OWNER_A, lease_seconds=LEASE))[0]

        renewed = await repo.renew_lease(claimed, lease_seconds=LEASE)
        assert renewed is not None

        stranger = claimed.model_copy(deep=True)
        stranger.lease_owner = OWNER_B
        assert await repo.renew_lease(stranger, lease_seconds=LEASE) is None


# --- Восстановление доставки ---------------------------------------------------


class TestDeliveryRecovery:
    """Восстановление доставок на реальной базе.

    Отправку выполняет Gateway, поэтому здесь проверяется только возврат
    застрявших записей в очередь: `sending` с просроченной арендой без
    вмешательства осталась бы недостижимой навсегда — Gateway забирает лишь
    `pending` и созревшие `failed`.
    """

    async def _program(self, sessions, profile_id: str):
        orchestrator = _orchestrator(sessions, primary="deterministic")
        result = await orchestrator.generate(
            GenerationRequest(
                profile_id=profile_id,
                trigger=GenerationTrigger.ADMIN_REQUEST,
                requested_generator="deterministic",
                allow_fallback=False,
            )
        )
        return result.program

    async def _stuck_delivery(self, sessions, profile_id: str, program):
        """Запись в `sending` с истёкшей арендой: шлюз умер во время отправки."""
        deliveries = ProgramDeliveryRepository(sessions)
        record = await deliveries.create(
            ProgramDeliveryRecord(
                program_id=program.program_id,
                profile_id=profile_id,
                chat_id="777",
                filename="program.html",
                status=ProgramDeliveryStatus.SENDING,
            )
        )
        async with sessions() as session:
            async with session.begin():
                await session.execute(
                    ProgramDeliveryRow.__table__.update()
                    .where(ProgramDeliveryRow.id == record.id)
                    .values(
                        lease_owner="gateway-dead",
                        lease_expires_at=_utcnow() - timedelta(seconds=1),
                    )
                )
        return deliveries, record

    def _service(self, deliveries) -> DeliveryRecoveryService:
        return DeliveryRecoveryService(deliveries=deliveries, policy=POLICY)

    async def test_stale_sending_delivery_returns_to_queue(self, sessions):
        profile = await _save_profile(sessions, f"{PROFILE_PREFIX}delivery-stale")
        program = await self._program(sessions, profile.profile_id)
        deliveries, _ = await self._stuck_delivery(
            sessions, profile.profile_id, program
        )

        released = await self._service(deliveries).recover_stale()

        assert len(released) == 1
        record = await deliveries.get_for_profile(profile.profile_id)
        assert record.status is ProgramDeliveryStatus.FAILED
        assert record.next_attempt_at is not None
        assert record.lease_owner is None

    async def test_recovered_delivery_is_claimable_by_gateway(self, sessions):
        """Итог восстановления проверяется тем, что задание снова выдаётся."""
        profile = await _save_profile(sessions, f"{PROFILE_PREFIX}delivery-claim")
        program = await self._program(sessions, profile.profile_id)
        deliveries, record = await self._stuck_delivery(
            sessions, profile.profile_id, program
        )

        await self._service(deliveries).recover_stale()
        async with sessions() as session:
            async with session.begin():
                await session.execute(
                    ProgramDeliveryRow.__table__.update()
                    .where(ProgramDeliveryRow.id == record.id)
                    .values(next_attempt_at=_utcnow() - timedelta(seconds=1))
                )

        claimed = await deliveries.claim_for_send(
            owner="gateway-1", lease_seconds=LEASE, limit=5
        )
        assert [r.id for r in claimed] == [record.id]

    async def test_live_lease_is_not_stolen(self, sessions):
        """Живая аренда означает, что шлюз ещё отправляет файл."""
        profile = await _save_profile(sessions, f"{PROFILE_PREFIX}delivery-live")
        program = await self._program(sessions, profile.profile_id)
        deliveries, record = await self._stuck_delivery(
            sessions, profile.profile_id, program
        )
        async with sessions() as session:
            async with session.begin():
                await session.execute(
                    ProgramDeliveryRow.__table__.update()
                    .where(ProgramDeliveryRow.id == record.id)
                    .values(lease_expires_at=_utcnow() + timedelta(minutes=5))
                )

        assert await self._service(deliveries).recover_stale() == []
        record = await deliveries.get_for_profile(profile.profile_id)
        assert record.status is ProgramDeliveryStatus.SENDING

    async def test_recovery_does_not_create_second_program(self, sessions):
        """Доставка и генерация независимы: восстановление не трогает программы."""
        profile = await _save_profile(sessions, f"{PROFILE_PREFIX}delivery-nogen")
        program = await self._program(sessions, profile.profile_id)
        deliveries, _ = await self._stuck_delivery(
            sessions, profile.profile_id, program
        )

        await self._service(deliveries).recover_stale()

        assert await _count(sessions, WorkoutProgramRow, profile.profile_id) == 1
        assert await _count(sessions, GenerationJobRow, profile.profile_id) == 1


# --- Координатор на реальной базе ---------------------------------------------


class TestCoordinatorOnPostgres:
    async def test_cycle_recovers_and_retries(self, sessions):
        profile = await _save_profile(sessions, f"{PROFILE_PREFIX}cycle")
        repo = GenerationJobRepository(sessions)
        failing = _orchestrator(sessions, ai_generator=_BrokenAIGenerator())
        with pytest.raises(ProgramGenerationError):
            await failing.generate(_auto_request(profile.profile_id))
        job_id = (await repo.list_for_profile(profile.profile_id))[0].job_id
        await _make_due(sessions, job_id)

        coordinator = RetryCoordinator(
            generation=_retry_service(sessions, _orchestrator(sessions)),
            delivery=None,
        )
        result = await coordinator.run_once()

        assert result.retried_jobs == 1
        assert (await repo.get(job_id)).status is GenerationJobStatus.SUCCEEDED

    async def test_empty_cycle_is_harmless(self, sessions):
        """Холостой проход ничего не меняет и не падает."""
        result = await RetryCoordinator(
            generation=_retry_service(sessions, _orchestrator(sessions)),
            delivery=None,
        ).run_once()
        assert result.did_work is False
