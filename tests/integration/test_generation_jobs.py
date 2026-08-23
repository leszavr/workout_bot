"""Интеграционные тесты persistent состояния генерации (Phase 1.2-B).

Проверяются свойства, которые нельзя доказать без PostgreSQL:

- идемпотентность обеспечивает UNIQUE constraint, а не проверка в Python;
- два параллельных запроса одной логической генерации создают один job;
- переход состояния выполняется условно, поэтому его нельзя провести дважды;
- rollback транзакции не оставляет phantom job;
- job переживает новую сессию/новый engine («перезапуск приложения»).

Concurrency-тесты используют независимые сессии на отдельном engine: asyncio
lock ничего бы здесь не доказал, потому что не защищает несколько процессов.
"""
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.application.programs.filtering import ExerciseFilter
from src.application.programs.generation_jobs import GenerationJobService
from src.application.programs.generator import DeterministicProgramGenerator
from src.application.programs.orchestrator import (
    GenerationRequest,
    ProgramGenerationOrchestrator,
)
from src.application.programs.safety import SafetyEngine
from src.application.programs.validator import ProgramValidator
from src.domain.ai.errors import AITimeoutError
from src.domain.enums import (
    ExperienceLevel,
    GenerationJobStatus,
    GenerationSource,
    PrimaryGoal,
    ProgramStatus,
    TrainingLocationType,
)
from src.domain.generation import (
    GenerationErrorCode,
    GenerationJob,
    GenerationJobTransitionError,
    GenerationTrigger,
)
from src.domain.profile import FitnessProfile
from src.errors import (
    GenerationAlreadyRunningError,
    GenerationFailedError,
    IdempotencyKeyConflictError,
    ProgramGenerationError,
)
from src.infrastructure.config import DATABASE_URL
from src.infrastructure.persistence.postgres.generation_job_repository import (
    GenerationJobRepository,
)
from src.infrastructure.persistence.postgres.models import (
    ConsentRow,
    GenerationJobRow,
    ProfileRow,
    UserRow,
    WorkoutProgramRow,
)

pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL is not set")

TEST_TELEGRAM_ID = "900123"
PROFILE_PREFIX = "test-genjob-"


@pytest.fixture
async def engine():
    """Собственный engine: тесты работают с независимыми сессиями."""
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
    """Второй независимый engine.

    Нужен там, где проверяется именно DB-level взаимное исключение: с общим
    engine два запроса делят пул соединений, и результат мог бы объясняться
    поведением пула, а не гарантией PostgreSQL.
    """
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
                    await session.execute(
                        delete(UserRow).where(UserRow.id.in_(user_ids))
                    )

    await _purge()
    yield
    await _purge()


def _profile(profile_id: str) -> FitnessProfile:
    profile = FitnessProfile(profile_id=profile_id)
    profile.source.bot_user_id = TEST_TELEGRAM_ID
    profile.source.telegram_username = "test_genjob"
    profile.client.name = "Тест Job"
    profile.client.age_years = 30
    profile.goals.primary = PrimaryGoal.MUSCLE_GAIN
    profile.training_background.experience_level = ExperienceLevel.THREE_TWELVE_MONTHS
    profile.training_location.primary_location = TrainingLocationType.GYM
    profile.training_plan_preferences.sessions_per_week = 3
    return profile


async def _save_profile(sessions, profile_id: str) -> FitnessProfile:
    from src.infrastructure.persistence.postgres.profile_repository import (
        PostgresProfileRepository,
    )

    profile = _profile(profile_id)
    await PostgresProfileRepository(sessions).save(profile)
    return profile


def _job(profile_id: str, *, job_id: str, key: str) -> GenerationJob:
    return GenerationJob(
        job_id=job_id,
        profile_id=profile_id,
        idempotency_key=key,
        trigger=GenerationTrigger.ADMIN_REQUEST,
        requested_generator="deterministic",
    )


def _orchestrator(
    sessions,
    *,
    primary: str = "deterministic",
    fallback: str = "deterministic",
    ai_generator=None,
    deterministic=None,
    with_jobs: bool = True,
) -> ProgramGenerationOrchestrator:
    """Оркестратор — единственная точка генерации (Phase 1.2-C)."""
    from src.infrastructure.persistence.postgres.exercise_repository import (
        ExerciseRepository,
    )
    from src.infrastructure.persistence.postgres.profile_repository import (
        PostgresProfileRepository,
    )
    from src.infrastructure.persistence.postgres.program_repository import (
        PostgresProgramRepository,
    )

    programs = PostgresProgramRepository(sessions)
    jobs = (
        GenerationJobService(
            repository=GenerationJobRepository(sessions), program_repository=programs
        )
        if with_jobs
        else None
    )
    return ProgramGenerationOrchestrator(
        profile_repository=PostgresProfileRepository(sessions),
        exercise_repository=ExerciseRepository(sessions),
        program_repository=programs,
        primary_generator=primary,
        fallback_generator=fallback,
        ai_generator_factory=(lambda: ai_generator) if ai_generator else None,
        deterministic_generator=deterministic or DeterministicProgramGenerator(),
        exercise_filter=ExerciseFilter(),
        safety_engine=SafetyEngine(),
        validator=ProgramValidator(),
        generation_jobs=jobs,
    )


def _admin_request(profile_id: str, *, key: str | None = None) -> GenerationRequest:
    """Запрос администратора: генератор выбран явно, fallback запрещён."""
    return GenerationRequest(
        profile_id=profile_id,
        trigger=GenerationTrigger.ADMIN_REQUEST,
        requested_generator="deterministic",
        allow_fallback=False,
        client_idempotency_key=key,
    )


def _auto_request(profile_id: str, *, reuse_existing: bool = True) -> GenerationRequest:
    """Автогенерация после finalize: стратегия из конфигурации, fallback разрешён."""
    return GenerationRequest(
        profile_id=profile_id,
        trigger=GenerationTrigger.AUTO_FINALIZATION,
        reuse_existing=reuse_existing,
    )


# --- Репозиторий: создание и переходы -----------------------------------------


class TestGenerationJobPersistence:
    async def test_create_job(self, sessions):
        profile = await _save_profile(sessions, f"{PROFILE_PREFIX}create")
        repo = GenerationJobRepository(sessions)

        job, created = await repo.create_or_get(
            _job(profile.profile_id, job_id="j-create", key="k-create")
        )

        assert created is True
        assert job.id is not None
        assert job.status is GenerationJobStatus.PENDING
        assert job.attempts == 0
        assert job.program_id is None
        assert job.created_at is not None

    async def test_pending_to_running(self, sessions):
        profile = await _save_profile(sessions, f"{PROFILE_PREFIX}run")
        repo = GenerationJobRepository(sessions)
        job, _ = await repo.create_or_get(
            _job(profile.profile_id, job_id="j-run", key="k-run")
        )

        running = await repo.mark_running(job)

        assert running.status is GenerationJobStatus.RUNNING
        assert running.attempts == 1
        assert running.started_at is not None

    async def test_running_to_succeeded_links_program_version(self, sessions):
        orchestrator = _orchestrator(sessions)
        profile = await _save_profile(sessions, f"{PROFILE_PREFIX}success")

        result = await orchestrator.generate(_admin_request(profile.profile_id))

        assert result.job is not None
        assert result.job.status is GenerationJobStatus.SUCCEEDED
        assert result.job.program_id == result.program.program_id
        assert result.job.program_version == result.program.version
        assert result.job.completed_at is not None

    async def test_running_to_failed(self, sessions):
        profile = await _save_profile(sessions, f"{PROFILE_PREFIX}fail")
        repo = GenerationJobRepository(sessions)
        job, _ = await repo.create_or_get(
            _job(profile.profile_id, job_id="j-fail", key="k-fail")
        )
        job = await repo.mark_running(job)

        failed = await repo.mark_failed(
            job,
            error_code=GenerationErrorCode.AI_TIMEOUT,
            message="Authorization: Bearer sk-secret timeout",
        )

        assert failed.status is GenerationJobStatus.FAILED
        assert failed.last_error_code == GenerationErrorCode.AI_TIMEOUT.value
        assert "sk-secret" not in (failed.last_error_message or "")
        assert failed.program_id is None

    async def test_forbidden_transition_rejected_by_database(self, sessions):
        """Второй mark_succeeded не проходит: условие ставит сам UPDATE."""
        profile = await _save_profile(sessions, f"{PROFILE_PREFIX}forbidden")
        repo = GenerationJobRepository(sessions)
        job, _ = await repo.create_or_get(
            _job(profile.profile_id, job_id="j-forbidden", key="k-forbidden")
        )
        running = await repo.mark_running(job)
        await repo.mark_failed(
            running,
            error_code=GenerationErrorCode.GENERATION_FAILED,
            message="failed",
        )

        # Ссылка на job осталась в состоянии RUNNING: другой процесс уже
        # закрыл запись, поэтому повторный переход обязан быть отклонён.
        with pytest.raises(GenerationJobTransitionError):
            await repo.mark_succeeded(running, program_id="x", program_version=1)

    async def test_start_twice_rejected(self, sessions):
        profile = await _save_profile(sessions, f"{PROFILE_PREFIX}start2")
        repo = GenerationJobRepository(sessions)
        job, _ = await repo.create_or_get(
            _job(profile.profile_id, job_id="j-start2", key="k-start2")
        )
        await repo.mark_running(job)

        with pytest.raises(GenerationJobTransitionError):
            await repo.mark_running(job)

    async def test_job_survives_new_session_and_engine(self, sessions):
        """Состояние живёт в PostgreSQL, а не в процессе приложения."""
        profile = await _save_profile(sessions, f"{PROFILE_PREFIX}restart")
        repo = GenerationJobRepository(sessions)
        job, _ = await repo.create_or_get(
            _job(profile.profile_id, job_id="j-restart", key="k-restart")
        )
        await repo.mark_running(job)

        fresh_engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
        try:
            fresh_repo = GenerationJobRepository(
                async_sessionmaker(fresh_engine, expire_on_commit=False)
            )
            loaded = await fresh_repo.get("j-restart")
        finally:
            await fresh_engine.dispose()

        assert loaded is not None
        assert loaded.status is GenerationJobStatus.RUNNING
        assert loaded.attempts == 1
        assert loaded.profile_id == profile.profile_id


# --- Идемпотентность и конкурентность -----------------------------------------


class TestIdempotencyBoundary:
    async def test_unique_constraint_enforced_by_database(self, sessions):
        """Дубликат ключа отклоняет БД, а не проверка в Python."""
        profile = await _save_profile(sessions, f"{PROFILE_PREFIX}unique")
        async with sessions() as session:
            async with session.begin():
                session.add(
                    GenerationJobRow(
                        job_id="j-unique-1",
                        profile_id=profile.profile_id,
                        idempotency_key="k-unique",
                        trigger=GenerationTrigger.ADMIN_REQUEST.value,
                        requested_generator="deterministic",
                        status=GenerationJobStatus.PENDING.value,
                    )
                )

        with pytest.raises(IntegrityError):
            async with sessions() as session:
                async with session.begin():
                    session.add(
                        GenerationJobRow(
                            job_id="j-unique-2",
                            profile_id=profile.profile_id,
                            idempotency_key="k-unique",
                            trigger=GenerationTrigger.ADMIN_REQUEST.value,
                            requested_generator="deterministic",
                            status=GenerationJobStatus.PENDING.value,
                        )
                    )

    async def test_sequential_same_key_returns_single_job(self, sessions):
        profile = await _save_profile(sessions, f"{PROFILE_PREFIX}seq")
        repo = GenerationJobRepository(sessions)

        first, created_first = await repo.create_or_get(
            _job(profile.profile_id, job_id="j-seq-1", key="k-seq")
        )
        second, created_second = await repo.create_or_get(
            _job(profile.profile_id, job_id="j-seq-2", key="k-seq")
        )

        assert created_first is True
        assert created_second is False
        assert first.id == second.id
        assert first.job_id == second.job_id == "j-seq-1"
        assert await _count_jobs(sessions, profile.profile_id) == 1

    async def test_concurrent_same_key_creates_exactly_one_job(self, sessions):
        """Две независимые сессии, один ключ → ровно один job."""
        profile = await _save_profile(sessions, f"{PROFILE_PREFIX}race")
        repo_a = GenerationJobRepository(sessions)
        repo_b = GenerationJobRepository(sessions)

        results = await asyncio.gather(
            repo_a.create_or_get(
                _job(profile.profile_id, job_id="j-race-a", key="k-race")
            ),
            repo_b.create_or_get(
                _job(profile.profile_id, job_id="j-race-b", key="k-race")
            ),
        )

        created = [created for _, created in results]
        assert sorted(created) == [False, True]
        assert results[0][0].id == results[1][0].id
        assert await _count_jobs(sessions, profile.profile_id) == 1

    async def test_concurrent_generation_requests_create_one_program(self, sessions):
        """Параллельные запросы генерации: один job, одна программа."""
        profile = await _save_profile(sessions, f"{PROFILE_PREFIX}race-gen")
        orchestrator_a = _orchestrator(sessions)
        orchestrator_b = _orchestrator(sessions)
        key = "client-race"

        results = await asyncio.gather(
            orchestrator_a.generate(_admin_request(profile.profile_id, key=key)),
            orchestrator_b.generate(_admin_request(profile.profile_id, key=key)),
            return_exceptions=True,
        )

        successes = [r for r in results if not isinstance(r, BaseException)]
        rejected = [
            r for r in results if isinstance(r, GenerationAlreadyRunningError)
        ]
        # Проигравший запрос либо получил отказ (генерация ещё идёт), либо
        # дождался результата победителя. В обоих случаях второй программы нет.
        assert len(successes) + len(rejected) == 2
        # Иных исходов быть не должно: любое другое исключение означало бы, что
        # гонку разрешил не контур идемпотентности.
        assert len(successes) >= 1
        assert await _count_jobs(sessions, profile.profile_id) == 1
        assert await _count_programs(sessions, profile.profile_id) == 1
        # Все успешные ответы указывают на одну и ту же версию программы.
        program_ids = {r.program.program_id for r in successes}
        assert len(program_ids) == 1

    async def test_different_keys_create_independent_jobs(self, sessions):
        profile = await _save_profile(sessions, f"{PROFILE_PREFIX}two-keys")
        repo = GenerationJobRepository(sessions)

        await repo.create_or_get(
            _job(profile.profile_id, job_id="j-key-1", key="k-key-1")
        )
        await repo.create_or_get(
            _job(profile.profile_id, job_id="j-key-2", key="k-key-2")
        )

        assert await _count_jobs(sessions, profile.profile_id) == 2


class TestIdempotencyKeyParameterConflict:
    """Ключ с несовместимыми параметрами (Phase 1.2-C).

    Клиентский ключ означает «это тот же запрос». Если генератор другой,
    утверждение неверно: отдать программу победителя нельзя (она собрана другим
    генератором — это отмена явного выбора администратора), создать второй job
    под тем же ключом тоже нельзя (это разрушает DB-enforced идемпотентность).
    """

    async def test_same_key_different_generator_conflicts(self, sessions):
        profile = await _save_profile(sessions, f"{PROFILE_PREFIX}key-conflict")
        key = "client-conflict"
        deterministic = _orchestrator(sessions)
        ai = _orchestrator(
            sessions,
            primary="ai",
            ai_generator=_StubAIGenerator(DeterministicProgramGenerator()),
        )

        first = await deterministic.generate(
            _admin_request(profile.profile_id, key=key)
        )

        with pytest.raises(IdempotencyKeyConflictError):
            await ai.generate(
                GenerationRequest(
                    profile_id=profile.profile_id,
                    trigger=GenerationTrigger.ADMIN_REQUEST,
                    requested_generator="ai",
                    allow_fallback=False,
                    client_idempotency_key=key,
                )
            )

        # Ни второй job, ни вторая программа; программа победителя не тронута.
        assert await _count_jobs(sessions, profile.profile_id) == 1
        assert await _count_programs(sessions, profile.profile_id) == 1
        jobs = await GenerationJobRepository(sessions).list_for_profile(
            profile.profile_id
        )
        assert jobs[0].requested_generator == "deterministic"
        assert jobs[0].program_id == first.program.program_id

    async def test_failed_job_same_key_different_generator_conflicts(self, sessions):
        """Провалившийся job тоже занимает ключ."""
        profile = await _save_profile(sessions, f"{PROFILE_PREFIX}key-conflict-failed")
        key = "client-conflict-failed"
        failing = _orchestrator(sessions, deterministic=_FailingGenerator())

        with pytest.raises(ProgramGenerationError):
            await failing.generate(_admin_request(profile.profile_id, key=key))

        with pytest.raises(IdempotencyKeyConflictError):
            await _orchestrator(
                sessions,
                primary="ai",
                ai_generator=_StubAIGenerator(DeterministicProgramGenerator()),
            ).generate(
                GenerationRequest(
                    profile_id=profile.profile_id,
                    trigger=GenerationTrigger.ADMIN_REQUEST,
                    requested_generator="ai",
                    allow_fallback=False,
                    client_idempotency_key=key,
                )
            )

        assert await _count_jobs(sessions, profile.profile_id) == 1
        assert await _count_programs(sessions, profile.profile_id) == 0

    async def test_concurrent_conflicting_generators_on_independent_engines(
        self, sessions, second_sessions
    ):
        """Гонка на двух независимых engine: победитель один, второй не получает чужого.

        Сессии берутся из разных engine, поэтому взаимное исключение обеспечивает
        именно PostgreSQL (UNIQUE по ключу), а не общий пул или Python-примитив.
        """
        profile = await _save_profile(sessions, f"{PROFILE_PREFIX}key-conflict-race")
        key = "client-conflict-race"

        results = await asyncio.gather(
            _orchestrator(sessions).generate(
                _admin_request(profile.profile_id, key=key)
            ),
            _orchestrator(
                second_sessions,
                primary="ai",
                ai_generator=_StubAIGenerator(DeterministicProgramGenerator()),
            ).generate(
                GenerationRequest(
                    profile_id=profile.profile_id,
                    trigger=GenerationTrigger.ADMIN_REQUEST,
                    requested_generator="ai",
                    allow_fallback=False,
                    client_idempotency_key=key,
                )
            ),
            return_exceptions=True,
        )

        successes = [r for r in results if not isinstance(r, BaseException)]
        conflicts = [
            r for r in results if isinstance(r, IdempotencyKeyConflictError)
        ]
        # Проигравший мог застать победителя ещё активным: тогда он получает
        # отказ «уже выполняется». Оба варианта не создают вторую генерацию.
        running = [
            r for r in results if isinstance(r, GenerationAlreadyRunningError)
        ]
        assert len(successes) == 1
        assert len(conflicts) + len(running) == 1
        assert await _count_jobs(sessions, profile.profile_id) == 1
        assert await _count_programs(sessions, profile.profile_id) == 1
        # Главное: никто не получил программу, собранную не тем генератором,
        # который он запросил. Именно это ломалось до проверки параметров ключа.
        for result in successes:
            assert result.actual_generator == result.requested_generator

    async def test_same_key_same_generator_still_reuses(self, sessions):
        """Regression: совместимый повтор по-прежнему отдаёт готовую программу."""
        profile = await _save_profile(sessions, f"{PROFILE_PREFIX}key-same-gen")
        key = "client-conflict-ok"
        orchestrator = _orchestrator(sessions)

        first = await orchestrator.generate(_admin_request(profile.profile_id, key=key))
        second = await orchestrator.generate(
            _admin_request(profile.profile_id, key=key)
        )

        assert second.reused_existing is True
        assert second.program.program_id == first.program.program_id
        assert await _count_jobs(sessions, profile.profile_id) == 1
        assert await _count_programs(sessions, profile.profile_id) == 1

    async def test_auto_generation_ignores_generator_change(self, sessions):
        """Серверный ключ попытки конфликтом не считается.

        Ключ `profile:trigger:attempt` вызывающая сторона не выбирала: смена
        генератора здесь означает изменение конфигурации приложения между
        запусками, а не противоречивый запрос.
        """
        profile = await _save_profile(sessions, f"{PROFILE_PREFIX}auto-gen-change")

        first = await _orchestrator(sessions).generate(
            _auto_request(profile.profile_id, reuse_existing=False)
        )
        second = await _orchestrator(
            sessions,
            primary="ai",
            ai_generator=_StubAIGenerator(DeterministicProgramGenerator()),
        ).generate(_auto_request(profile.profile_id, reuse_existing=False))

        assert second.reused_existing is True
        assert second.program.program_id == first.program.program_id
        assert await _count_jobs(sessions, profile.profile_id) == 1
        assert await _count_programs(sessions, profile.profile_id) == 1

    async def test_repeated_successful_request_reuses_program(self, sessions):
        """Повтор успешного запроса не создаёт вторую программу."""
        profile = await _save_profile(sessions, f"{PROFILE_PREFIX}reuse")
        orchestrator = _orchestrator(sessions)
        key = "client-reuse"

        first = await orchestrator.generate(_admin_request(profile.profile_id, key=key))
        second = await orchestrator.generate(_admin_request(profile.profile_id, key=key))

        assert second.reused_existing is True
        assert second.program.program_id == first.program.program_id
        assert second.program.version == first.program.version
        assert await _count_programs(sessions, profile.profile_id) == 1
        assert await _count_jobs(sessions, profile.profile_id) == 1

    async def test_admin_request_after_success_creates_new_version(self, sessions):
        """Явный повторный запрос администратора — законная новая генерация."""
        profile = await _save_profile(sessions, f"{PROFILE_PREFIX}new-version")
        orchestrator = _orchestrator(sessions)

        first = await orchestrator.generate(_admin_request(profile.profile_id))
        second = await orchestrator.generate(_admin_request(profile.profile_id))

        assert second.reused_existing is False
        assert second.program.version == first.program.version + 1
        assert await _count_jobs(sessions, profile.profile_id) == 2
        assert await _count_programs(sessions, profile.profile_id) == 2

    async def test_auto_generation_repeat_does_not_regenerate(self, sessions):
        """Повторная автогенерация после finalize возвращает готовую программу."""
        profile = await _save_profile(sessions, f"{PROFILE_PREFIX}auto")
        orchestrator = _orchestrator(sessions)

        first = await orchestrator.generate(
            _auto_request(profile.profile_id, reuse_existing=False)
        )
        second = await orchestrator.generate(
            _auto_request(profile.profile_id, reuse_existing=False)
        )

        assert second.reused_existing is True
        assert second.program.program_id == first.program.program_id
        assert await _count_programs(sessions, profile.profile_id) == 1


# --- Транзакционность и связь с программой ------------------------------------


class TestTransactionSafety:
    async def test_rollback_leaves_no_phantom_job(self, sessions):
        """Откат транзакции не оставляет ложный PENDING job."""
        profile = await _save_profile(sessions, f"{PROFILE_PREFIX}rollback")

        with pytest.raises(RuntimeError):
            async with sessions() as session:
                async with session.begin():
                    session.add(
                        GenerationJobRow(
                            job_id="j-rollback",
                            profile_id=profile.profile_id,
                            idempotency_key="k-rollback",
                            trigger=GenerationTrigger.ADMIN_REQUEST.value,
                            requested_generator="deterministic",
                            status=GenerationJobStatus.PENDING.value,
                        )
                    )
                    await session.flush()
                    raise RuntimeError("сбой посреди транзакции")

        repo = GenerationJobRepository(sessions)
        assert await repo.get("j-rollback") is None
        assert await _count_jobs(sessions, profile.profile_id) == 0

    async def test_failed_generation_creates_no_program(self, sessions):
        """При отказе программа не создаётся даже фиктивно."""
        profile = await _save_profile(sessions, f"{PROFILE_PREFIX}no-program")
        orchestrator = _orchestrator(sessions, deterministic=_FailingGenerator())

        with pytest.raises(ProgramGenerationError):
            await orchestrator.generate(_admin_request(profile.profile_id))

        assert await _count_programs(sessions, profile.profile_id) == 0
        jobs = await GenerationJobRepository(sessions).list_for_profile(
            profile.profile_id
        )
        assert len(jobs) == 1
        assert jobs[0].status is GenerationJobStatus.FAILED
        assert jobs[0].program_id is None
        assert jobs[0].last_error_code == GenerationErrorCode.GENERATION_FAILED.value

    async def test_validation_failure_is_recorded_as_non_retryable(self, sessions):
        profile = await _save_profile(sessions, f"{PROFILE_PREFIX}invalid")
        orchestrator = _orchestrator(sessions)
        orchestrator._validator = _RejectingValidator()  # noqa: SLF001 — подмена в тесте

        with pytest.raises(ProgramGenerationError):
            await orchestrator.generate(_admin_request(profile.profile_id))

        jobs = await GenerationJobRepository(sessions).list_for_profile(
            profile.profile_id
        )
        assert jobs[0].last_error_code == GenerationErrorCode.VALIDATION_FAILED.value
        assert await _count_programs(sessions, profile.profile_id) == 0

    async def test_missing_profile_creates_no_job(self, sessions):
        """У несуществующего профиля не должно остаться operational-записи."""
        orchestrator = _orchestrator(sessions)

        with pytest.raises(ProgramGenerationError):
            await orchestrator.generate(
                _admin_request(f"{PROFILE_PREFIX}nonexistent")
            )

        async with sessions() as session:
            total = (
                await session.execute(
                    select(GenerationJobRow.id).where(
                        GenerationJobRow.profile_id
                        == f"{PROFILE_PREFIX}nonexistent"
                    )
                )
            ).scalars().all()
        assert total == []

    async def test_generation_without_jobs_still_works(self, sessions):
        """Существующий путь без job-контура не сломан."""
        profile = await _save_profile(sessions, f"{PROFILE_PREFIX}no-jobs")
        orchestrator = _orchestrator(sessions, with_jobs=False)

        result = await orchestrator.generate(_admin_request(profile.profile_id))

        assert result.job is None
        assert result.program.status is ProgramStatus.VALIDATED
        assert await _count_jobs(sessions, profile.profile_id) == 0


class _FailingGenerator:
    async def generate(self, profile, pool):
        raise ProgramGenerationError("генератор недоступен в тесте")


class _StubAIGenerator:
    """Имитация успешного AI на базе реального детерминированного генератора."""

    def __init__(self, base: DeterministicProgramGenerator) -> None:
        self._base = base

    async def generate(self, profile, pool):
        program = await self._base.generate(profile, pool)
        program.generation.source = GenerationSource.AI
        program.generation.provider = "test-provider"
        program.generation.model = "test-model"
        return program


class _BrokenAIGenerator:
    async def generate(self, profile, pool):
        raise AITimeoutError("AI недоступен в тесте")


class TestOrchestratorUnderJobControl:
    async def test_ai_generation_still_works(self, sessions):
        """Существующая AI-генерация не сломана job-контуром."""
        profile = await _save_profile(sessions, f"{PROFILE_PREFIX}orch-ai")
        orchestrator = _orchestrator(
            sessions,
            primary="ai",
            ai_generator=_StubAIGenerator(DeterministicProgramGenerator()),
        )

        result = await orchestrator.generate(_auto_request(profile.profile_id))

        assert result.program.generation.source is GenerationSource.AI
        assert result.fallback_used is False
        assert result.job is not None
        assert result.job.status is GenerationJobStatus.SUCCEEDED
        assert result.job.requested_generator == "ai"
        assert result.job.program_id == result.program.program_id

    async def test_deterministic_fallback_still_works(self, sessions):
        """Отказ AI приводит к fallback, а job закрывается успехом."""
        profile = await _save_profile(sessions, f"{PROFILE_PREFIX}orch-fallback")
        orchestrator = _orchestrator(
            sessions, primary="ai", ai_generator=_BrokenAIGenerator()
        )

        result = await orchestrator.generate(_auto_request(profile.profile_id))

        assert result.fallback_used is True
        assert result.program.generation.actual_generator is (
            GenerationSource.DETERMINISTIC
        )
        assert result.actual_generator == "deterministic"
        assert result.requested_generator == "ai"
        assert result.job is not None
        assert result.job.status is GenerationJobStatus.SUCCEEDED
        # Fallback выполняется внутри одного job: второй записи нет.
        assert await _count_jobs(sessions, profile.profile_id) == 1
        assert await _count_programs(sessions, profile.profile_id) == 1

    async def test_admin_request_does_not_fall_back(self, sessions):
        """Явно выбранный генератор не подменяется молча (Phase 1.2-C)."""
        profile = await _save_profile(sessions, f"{PROFILE_PREFIX}orch-no-fallback")
        orchestrator = _orchestrator(
            sessions, primary="ai", ai_generator=_BrokenAIGenerator()
        )

        with pytest.raises(GenerationFailedError) as exc:
            await orchestrator.generate(
                GenerationRequest(
                    profile_id=profile.profile_id,
                    trigger=GenerationTrigger.ADMIN_REQUEST,
                    requested_generator="ai",
                    allow_fallback=False,
                )
            )

        assert exc.value.generation_error_code == GenerationErrorCode.AI_TIMEOUT.value
        assert await _count_programs(sessions, profile.profile_id) == 0
        jobs = await GenerationJobRepository(sessions).list_for_profile(
            profile.profile_id
        )
        assert jobs[0].status is GenerationJobStatus.FAILED
        assert jobs[0].last_error_code == GenerationErrorCode.AI_TIMEOUT.value

    async def test_repeated_auto_generation_reuses_program(self, sessions):
        """Повторный finalize не создаёт вторую программу."""
        profile = await _save_profile(sessions, f"{PROFILE_PREFIX}orch-repeat")
        orchestrator = _orchestrator(
            sessions,
            primary="deterministic",
            ai_generator=_StubAIGenerator(DeterministicProgramGenerator()),
        )

        first = await orchestrator.generate(_auto_request(profile.profile_id))
        second = await orchestrator.generate(_auto_request(profile.profile_id))

        assert second.reused_existing is True
        assert second.program.program_id == first.program.program_id
        assert await _count_programs(sessions, profile.profile_id) == 1
        assert await _count_jobs(sessions, profile.profile_id) == 1

    async def test_concurrent_auto_generation_creates_one_program(self, sessions):
        """Два параллельных автозапуска: один job, одна программа."""
        profile = await _save_profile(sessions, f"{PROFILE_PREFIX}orch-race")
        orchestrator_a = _orchestrator(
            sessions,
            primary="deterministic",
            ai_generator=_StubAIGenerator(DeterministicProgramGenerator()),
        )
        orchestrator_b = _orchestrator(
            sessions,
            primary="deterministic",
            ai_generator=_StubAIGenerator(DeterministicProgramGenerator()),
        )

        results = await asyncio.gather(
            orchestrator_a.generate(_auto_request(profile.profile_id)),
            orchestrator_b.generate(_auto_request(profile.profile_id)),
            return_exceptions=True,
        )

        failures = [r for r in results if isinstance(r, BaseException)]
        assert all(
            isinstance(f, GenerationAlreadyRunningError) for f in failures
        ), failures
        assert await _count_jobs(sessions, profile.profile_id) == 1
        assert await _count_programs(sessions, profile.profile_id) == 1

    async def test_telegram_and_admin_paths_use_same_orchestrator(self, sessions):
        """Оба пути приходят в одну точку и дают эквивалентную программу."""
        auto_profile = await _save_profile(sessions, f"{PROFILE_PREFIX}orch-auto-path")
        admin_profile = await _save_profile(
            sessions, f"{PROFILE_PREFIX}orch-admin-path"
        )
        orchestrator = _orchestrator(sessions)

        auto = await orchestrator.generate(_auto_request(auto_profile.profile_id))
        admin = await orchestrator.generate(_admin_request(admin_profile.profile_id))

        assert auto.job is not None and admin.job is not None
        assert auto.job.trigger is GenerationTrigger.AUTO_FINALIZATION
        assert admin.job.trigger is GenerationTrigger.ADMIN_REQUEST
        assert auto.program.status is admin.program.status is ProgramStatus.VALIDATED
        assert auto.actual_generator == admin.actual_generator == "deterministic"
        assert len(auto.program.training_days) == len(admin.program.training_days)

    async def test_failed_generation_records_error_code(self, sessions):
        profile = await _save_profile(sessions, f"{PROFILE_PREFIX}orch-failed")
        orchestrator = _orchestrator(
            sessions,
            primary="deterministic",
            ai_generator=_BrokenAIGenerator(),
            deterministic=_FailingGenerator(),
        )

        with pytest.raises(ProgramGenerationError):
            await orchestrator.generate(_auto_request(profile.profile_id))

        jobs = await GenerationJobRepository(sessions).list_for_profile(
            profile.profile_id
        )
        assert len(jobs) == 1
        assert jobs[0].status is GenerationJobStatus.FAILED
        assert jobs[0].program_id is None
        assert await _count_programs(sessions, profile.profile_id) == 0


class _RejectingValidator:
    def validate(self, program, pool, profile, catalog_ids):
        class _Issue:
            code = "TEST"
            message = "отклонено тестом"

        class _Result:
            valid = False
            issues = [_Issue()]

        return _Result()


async def _count_jobs(sessions, profile_id: str) -> int:
    async with sessions() as session:
        rows = (
            await session.execute(
                select(GenerationJobRow.id).where(
                    GenerationJobRow.profile_id == profile_id
                )
            )
        ).scalars().all()
    return len(rows)


async def _count_programs(sessions, profile_id: str) -> int:
    async with sessions() as session:
        rows = (
            await session.execute(
                select(WorkoutProgramRow.id).where(
                    WorkoutProgramRow.profile_id == profile_id
                )
            )
        ).scalars().all()
    return len(rows)
