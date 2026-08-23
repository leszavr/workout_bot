"""Unit-тесты persistent состояния генерации (Phase 1.2-B).

Без БД: проверяются доменная state machine, классификация ошибок, санитизация
сообщений и поведение `GenerationJobService` на in-memory фейке репозитория,
который повторяет контракт PostgreSQL-реализации (идемпотентность по ключу и
условный переход состояния).
"""
from __future__ import annotations

import asyncio

import pytest

from src.application.programs.generation_jobs import GenerationJobService
from src.domain.ai.errors import (
    AIConfigurationError,
    AIConnectionError,
    AIInvalidResponseError,
    AIProviderError,
    AIRateLimitError,
    AITimeoutError,
    AIUnsupportedProtocolError,
)
from src.domain.enums import GenerationJobStatus, GenerationSource, ProgramStatus
from src.domain.generation import (
    ALLOWED_TRANSITIONS,
    GenerationErrorCode,
    GenerationErrorKind,
    GenerationJob,
    GenerationJobTransitionError,
    GenerationTrigger,
    build_client_idempotency_key,
    build_idempotency_key,
    can_transition,
    classify_error,
    error_kind,
    safe_error_message,
)
from src.domain.program import (
    GenerationInfo,
    ProgramExercise,
    TrainingDay,
    WorkoutProgram,
)
from src.errors import (
    GenerationAlreadyRunningError,
    ProgramGenerationError,
    ProgramPersistenceError,
    ProgramValidationError,
)

EX_ID = "Barbell_Full_Squat"
PROFILE_ID = "unit-profile-1"


def _job(status: GenerationJobStatus = GenerationJobStatus.PENDING) -> GenerationJob:
    return GenerationJob(
        id=1,
        job_id="job-1",
        profile_id=PROFILE_ID,
        idempotency_key="admin_request:unit-profile-1:1",
        trigger=GenerationTrigger.ADMIN_REQUEST,
        requested_generator=GenerationSource.DETERMINISTIC.value,
        status=status,
    )


def _program(profile_id: str = PROFILE_ID, version: int = 1) -> WorkoutProgram:
    return WorkoutProgram(
        program_id="prog-1",
        profile_id=profile_id,
        version=version,
        status=ProgramStatus.VALIDATED,
        title="Программа",
        duration_weeks=4,
        training_days_per_week=1,
        training_days=[
            TrainingDay(
                day_number=1,
                title="День 1",
                focus="Full body",
                exercises=[
                    ProgramExercise(
                        exercise_external_id=EX_ID,
                        order=1,
                        sets=3,
                        repetitions_min=10,
                        repetitions_max=12,
                        rest_seconds=60,
                    )
                ],
            )
        ],
        generation=GenerationInfo(),
    )


class _Result:
    def __init__(self, program: WorkoutProgram) -> None:
        self.program = program
        self.job: GenerationJob | None = None


class FakeJobRepository:
    """In-memory аналог PostgreSQL-репозитория.

    Повторяет два свойства, на которые опирается сервис: уникальность
    idempotency key при вставке и условный переход состояния.
    """

    def __init__(self) -> None:
        self.rows: dict[str, GenerationJob] = {}
        self._next_id = 1

    async def next_attempt(self, profile_id: str, trigger: GenerationTrigger) -> int:
        consumed = [
            j
            for j in self.rows.values()
            if j.profile_id == profile_id
            and j.trigger is trigger
            and (
                j.status is GenerationJobStatus.FAILED
                or (
                    j.status is GenerationJobStatus.SUCCEEDED
                    and (
                        trigger is GenerationTrigger.ADMIN_REQUEST
                        or j.program_id is None
                    )
                )
            )
        ]
        return len(consumed) + 1

    async def create_or_get(self, job: GenerationJob) -> tuple[GenerationJob, bool]:
        existing = self.rows.get(job.idempotency_key)
        if existing is not None:
            return existing.model_copy(deep=True), False
        stored = job.model_copy(deep=True)
        stored.id = self._next_id
        self._next_id += 1
        self.rows[stored.idempotency_key] = stored
        return stored.model_copy(deep=True), True

    def _apply(self, updated: GenerationJob, expected: GenerationJobStatus) -> GenerationJob:
        stored = self.rows[updated.idempotency_key]
        if stored.status is not expected:
            raise GenerationJobTransitionError(stored.status, updated.status)
        self.rows[updated.idempotency_key] = updated.model_copy(deep=True)
        return updated.model_copy(deep=True)

    async def mark_running(self, job: GenerationJob) -> GenerationJob:
        updated = job.model_copy(deep=True)
        updated.start()
        return self._apply(updated, GenerationJobStatus.PENDING)

    async def mark_succeeded(
        self, job: GenerationJob, *, program_id: str, program_version: int
    ) -> GenerationJob:
        updated = job.model_copy(deep=True)
        updated.succeed(program_id=program_id, program_version=program_version)
        return self._apply(updated, GenerationJobStatus.RUNNING)

    async def mark_failed(
        self, job: GenerationJob, *, error_code, message: str
    ) -> GenerationJob:
        updated = job.model_copy(deep=True)
        updated.fail(error_code=error_code, message=message)
        return self._apply(updated, GenerationJobStatus.RUNNING)


class FakeProgramRepository:
    def __init__(self, programs: list[WorkoutProgram] | None = None) -> None:
        self.programs = programs or []

    async def get(self, program_id: str, version: int | None = None):
        for p in self.programs:
            if p.program_id == program_id and (version is None or p.version == version):
                return p
        return None


def _service(
    jobs: FakeJobRepository, programs: FakeProgramRepository | None = None
) -> GenerationJobService:
    return GenerationJobService(
        repository=jobs, program_repository=programs or FakeProgramRepository()
    )


# --- State machine ------------------------------------------------------------


class TestStateMachine:
    def test_allowed_transitions_table(self):
        assert ALLOWED_TRANSITIONS[GenerationJobStatus.PENDING] == frozenset(
            {GenerationJobStatus.RUNNING}
        )
        assert ALLOWED_TRANSITIONS[GenerationJobStatus.RUNNING] == frozenset(
            {GenerationJobStatus.SUCCEEDED, GenerationJobStatus.FAILED}
        )
        assert ALLOWED_TRANSITIONS[GenerationJobStatus.SUCCEEDED] == frozenset()
        assert ALLOWED_TRANSITIONS[GenerationJobStatus.FAILED] == frozenset()

    def test_pending_to_running(self):
        job = _job()
        job.start()
        assert job.status is GenerationJobStatus.RUNNING
        assert job.attempts == 1
        assert job.started_at is not None
        assert job.completed_at is None

    def test_running_to_succeeded_links_program(self):
        job = _job(GenerationJobStatus.RUNNING)
        job.succeed(program_id="prog-1", program_version=2)
        assert job.status is GenerationJobStatus.SUCCEEDED
        assert job.program_id == "prog-1"
        assert job.program_version == 2
        assert job.last_error_code is None
        assert job.completed_at is not None

    def test_running_to_failed_keeps_error_code(self):
        job = _job(GenerationJobStatus.RUNNING)
        job.fail(error_code=GenerationErrorCode.AI_TIMEOUT, message="timeout")
        assert job.status is GenerationJobStatus.FAILED
        assert job.last_error_code == GenerationErrorCode.AI_TIMEOUT.value
        assert job.program_id is None
        assert job.error_kind() is GenerationErrorKind.TRANSIENT

    @pytest.mark.parametrize(
        "status, action",
        [
            (GenerationJobStatus.PENDING, "succeed"),
            (GenerationJobStatus.PENDING, "fail"),
            (GenerationJobStatus.RUNNING, "start"),
            (GenerationJobStatus.SUCCEEDED, "start"),
            (GenerationJobStatus.SUCCEEDED, "fail"),
            (GenerationJobStatus.FAILED, "start"),
            (GenerationJobStatus.FAILED, "succeed"),
        ],
    )
    def test_forbidden_transitions_rejected(self, status, action):
        job = _job(status)
        with pytest.raises(GenerationJobTransitionError):
            if action == "start":
                job.start()
            elif action == "succeed":
                job.succeed(program_id="prog-1", program_version=1)
            else:
                job.fail(error_code=GenerationErrorCode.GENERATION_FAILED, message="x")

    def test_retry_transition_reserved_for_later_phase(self):
        """FAILED → PENDING/RUNNING появится вместе с retry-контуром (1.2-D)."""
        assert not can_transition(
            GenerationJobStatus.FAILED, GenerationJobStatus.PENDING
        )
        assert not can_transition(
            GenerationJobStatus.FAILED, GenerationJobStatus.RUNNING
        )


# --- Классификация ошибок ------------------------------------------------------


class TestErrorClassification:
    @pytest.mark.parametrize(
        "exc, expected",
        [
            (ProgramValidationError("x"), GenerationErrorCode.VALIDATION_FAILED),
            (ProgramPersistenceError("x"), GenerationErrorCode.PERSISTENCE_FAILED),
            (AITimeoutError("x"), GenerationErrorCode.AI_TIMEOUT),
            (AIRateLimitError("x"), GenerationErrorCode.AI_RATE_LIMITED),
            (AIConnectionError("x"), GenerationErrorCode.AI_CONNECTION_FAILED),
            (AIInvalidResponseError("x"), GenerationErrorCode.AI_INVALID_RESPONSE),
            (
                AIUnsupportedProtocolError("x"),
                GenerationErrorCode.AI_UNSUPPORTED_PROTOCOL,
            ),
            (AIConfigurationError("x"), GenerationErrorCode.AI_NOT_CONFIGURED),
            (AIProviderError("x", status_code=500), GenerationErrorCode.AI_RUNTIME_FAILURE),
            (ProgramGenerationError("x"), GenerationErrorCode.GENERATION_FAILED),
            (RuntimeError("x"), GenerationErrorCode.UNEXPECTED_ERROR),
        ],
    )
    def test_classify(self, exc, expected):
        assert classify_error(exc) is expected

    def test_configuration_and_validation_are_non_retryable(self):
        for code in (
            GenerationErrorCode.VALIDATION_FAILED,
            GenerationErrorCode.AI_NOT_CONFIGURED,
            GenerationErrorCode.PROFILE_NOT_FOUND,
            GenerationErrorCode.AI_INVALID_RESPONSE,
        ):
            assert error_kind(code) is GenerationErrorKind.NON_RETRYABLE

    def test_network_failures_are_transient(self):
        for code in (
            GenerationErrorCode.AI_TIMEOUT,
            GenerationErrorCode.AI_CONNECTION_FAILED,
            GenerationErrorCode.AI_RATE_LIMITED,
            GenerationErrorCode.PERSISTENCE_FAILED,
        ):
            assert error_kind(code) is GenerationErrorKind.TRANSIENT

    def test_unknown_code_is_transient(self):
        assert error_kind("code-from-another-version") is GenerationErrorKind.TRANSIENT


class TestSafeErrorMessage:
    def test_secrets_are_redacted(self):
        message = safe_error_message(
            'HTTP 401 {"error": "invalid"} Authorization: Bearer sk-abcdef1234567890'
        )
        assert "sk-abcdef1234567890" not in message
        assert "Bearer" not in message
        assert "[redacted]" in message

    def test_api_key_assignment_redacted(self):
        assert "supersecret" not in safe_error_message("api_key=supersecret failed")

    def test_message_is_truncated(self):
        assert len(safe_error_message("x" * 5000)) == 500

    def test_whitespace_collapsed(self):
        assert safe_error_message("a\n\n  b") == "a b"


class TestIdempotencyKey:
    def test_key_is_stable(self):
        first = build_idempotency_key(
            profile_id=PROFILE_ID, trigger=GenerationTrigger.ADMIN_REQUEST, attempt=1
        )
        second = build_idempotency_key(
            profile_id=PROFILE_ID, trigger=GenerationTrigger.ADMIN_REQUEST, attempt=1
        )
        assert first == second

    def test_trigger_is_part_of_identity(self):
        """Автогенерация и явный запрос администратора — разные операции."""
        auto = build_idempotency_key(
            profile_id=PROFILE_ID,
            trigger=GenerationTrigger.AUTO_FINALIZATION,
            attempt=1,
        )
        admin = build_idempotency_key(
            profile_id=PROFILE_ID, trigger=GenerationTrigger.ADMIN_REQUEST, attempt=1
        )
        assert auto != admin

    def test_attempt_must_be_positive(self):
        with pytest.raises(ValueError):
            build_idempotency_key(
                profile_id=PROFILE_ID,
                trigger=GenerationTrigger.ADMIN_REQUEST,
                attempt=0,
            )

    def test_client_key_is_scoped_to_profile(self):
        first = build_client_idempotency_key(profile_id="p1", client_key="same")
        second = build_client_idempotency_key(profile_id="p2", client_key="same")
        assert first != second

    def test_empty_client_key_rejected(self):
        with pytest.raises(ValueError):
            build_client_idempotency_key(profile_id="p1", client_key="   ")

    def test_too_long_client_key_rejected(self):
        with pytest.raises(ValueError):
            build_client_idempotency_key(profile_id="p1", client_key="x" * 200)


# --- GenerationJobService ------------------------------------------------------


class TestGenerationJobService:
    async def test_successful_run_links_program(self):
        jobs = FakeJobRepository()
        program = _program()
        run = await _service(jobs).run(
            profile_id=PROFILE_ID,
            trigger=GenerationTrigger.ADMIN_REQUEST,
            requested_generator="deterministic",
            operation=lambda: _succeed(program),
        )
        assert run.duplicate is False
        assert run.job.status is GenerationJobStatus.SUCCEEDED
        assert run.job.program_id == program.program_id
        assert run.job.program_version == program.version
        assert run.job.attempts == 1

    async def test_failed_run_marks_job_and_reraises(self):
        jobs = FakeJobRepository()
        with pytest.raises(ProgramValidationError):
            await _service(jobs).run(
                profile_id=PROFILE_ID,
                trigger=GenerationTrigger.ADMIN_REQUEST,
                requested_generator="deterministic",
                operation=_raise_validation,
            )
        stored = next(iter(jobs.rows.values()))
        assert stored.status is GenerationJobStatus.FAILED
        assert stored.last_error_code == GenerationErrorCode.VALIDATION_FAILED.value
        assert stored.program_id is None

    async def test_repeated_successful_request_returns_existing_program(self):
        """Повтор успешной генерации не создаёт вторую программу."""
        jobs = FakeJobRepository()
        program = _program()
        programs = FakeProgramRepository([program])
        service = _service(jobs, programs)
        key = "client-key-1"

        first = await service.run(
            profile_id=PROFILE_ID,
            trigger=GenerationTrigger.ADMIN_REQUEST,
            requested_generator="deterministic",
            client_idempotency_key=key,
            operation=lambda: _succeed(program),
        )
        calls: list[int] = []

        async def _should_not_run():
            calls.append(1)
            raise AssertionError("вторая генерация не должна запускаться")

        second = await service.run(
            profile_id=PROFILE_ID,
            trigger=GenerationTrigger.ADMIN_REQUEST,
            requested_generator="deterministic",
            client_idempotency_key=key,
            operation=_should_not_run,
        )

        assert calls == []
        assert second.duplicate is True
        assert second.existing_program is program
        assert second.job.job_id == first.job.job_id
        assert len(jobs.rows) == 1

    async def test_concurrent_duplicate_requests_create_single_job(self):
        """Второй параллельный запрос не запускает вторую генерацию."""
        jobs = FakeJobRepository()
        service = _service(jobs)
        started = asyncio.Event()
        release = asyncio.Event()
        runs = 0

        async def _slow():
            nonlocal runs
            runs += 1
            started.set()
            await release.wait()
            return _Result(_program())

        first = asyncio.create_task(
            service.run(
                profile_id=PROFILE_ID,
                trigger=GenerationTrigger.ADMIN_REQUEST,
                requested_generator="deterministic",
                operation=_slow,
            )
        )
        await started.wait()

        with pytest.raises(GenerationAlreadyRunningError):
            await service.run(
                profile_id=PROFILE_ID,
                trigger=GenerationTrigger.ADMIN_REQUEST,
                requested_generator="deterministic",
                operation=_slow,
            )

        release.set()
        await first
        assert runs == 1
        assert len(jobs.rows) == 1

    async def test_different_triggers_create_independent_jobs(self):
        jobs = FakeJobRepository()
        service = _service(jobs)
        program = _program()

        await service.run(
            profile_id=PROFILE_ID,
            trigger=GenerationTrigger.AUTO_FINALIZATION,
            requested_generator="ai",
            operation=lambda: _succeed(program),
        )
        await service.run(
            profile_id=PROFILE_ID,
            trigger=GenerationTrigger.ADMIN_REQUEST,
            requested_generator="deterministic",
            operation=lambda: _succeed(program),
        )

        assert len(jobs.rows) == 2

    async def test_admin_can_request_new_generation_after_success(self):
        """Явный запрос администратора — законная новая логическая генерация."""
        jobs = FakeJobRepository()
        service = _service(jobs)
        program = _program()

        await service.run(
            profile_id=PROFILE_ID,
            trigger=GenerationTrigger.ADMIN_REQUEST,
            requested_generator="deterministic",
            operation=lambda: _succeed(program),
        )
        second = await service.run(
            profile_id=PROFILE_ID,
            trigger=GenerationTrigger.ADMIN_REQUEST,
            requested_generator="deterministic",
            operation=lambda: _succeed(_program(version=2)),
        )

        assert second.duplicate is False
        assert len(jobs.rows) == 2

    async def test_auto_generation_does_not_repeat_after_success(self):
        """Повторный finalize не запускает генерацию заново."""
        jobs = FakeJobRepository()
        program = _program()
        service = _service(jobs, FakeProgramRepository([program]))

        await service.run(
            profile_id=PROFILE_ID,
            trigger=GenerationTrigger.AUTO_FINALIZATION,
            requested_generator="ai",
            operation=lambda: _succeed(program),
        )
        second = await service.run(
            profile_id=PROFILE_ID,
            trigger=GenerationTrigger.AUTO_FINALIZATION,
            requested_generator="ai",
            operation=_raise_validation,
        )

        assert second.duplicate is True
        assert second.existing_program is program
        assert len(jobs.rows) == 1

    async def test_generation_without_saved_program_fails_job(self):
        jobs = FakeJobRepository()
        program = _program()
        program.program_id = None

        with pytest.raises(ProgramGenerationError):
            await _service(jobs).run(
                profile_id=PROFILE_ID,
                trigger=GenerationTrigger.ADMIN_REQUEST,
                requested_generator="deterministic",
                operation=lambda: _succeed(program),
            )
        stored = next(iter(jobs.rows.values()))
        assert stored.status is GenerationJobStatus.FAILED

    async def test_missing_program_of_successful_job_is_not_reported_as_success(self):
        jobs = FakeJobRepository()
        program = _program()
        service = _service(jobs, FakeProgramRepository([]))
        await service.run(
            profile_id=PROFILE_ID,
            trigger=GenerationTrigger.AUTO_FINALIZATION,
            requested_generator="ai",
            operation=lambda: _succeed(program),
        )

        with pytest.raises(ProgramGenerationError):
            await service.run(
                profile_id=PROFILE_ID,
                trigger=GenerationTrigger.AUTO_FINALIZATION,
                requested_generator="ai",
                operation=lambda: _succeed(program),
            )


async def _succeed(program: WorkoutProgram) -> _Result:
    return _Result(program)


async def _raise_validation() -> _Result:
    raise ProgramValidationError("Программа не прошла валидацию: TEST")
