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
from src.domain.ai.enums import AIFallbackReason
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
    _FALLBACK_REASON_BY_CODE,
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
    fallback_reason_for_code,
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
    IdempotencyKeyConflictError,
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


class TestUnifiedFallbackClassification:
    """Одна ошибка — одна причина (Phase 1.2-C).

    Раньше рядом с `classify_error` жила вторая таблица, разбиравшая ту же
    иерархию исключений заново: rate limit, сетевой сбой и неподдерживаемый
    протокол получали конкретный `GenerationErrorCode`, но общий
    `ai_runtime_failure` в журнале администратора. Теперь причина выводится из
    кода, поэтому operational-состояние и журнал не могут разойтись.
    """

    @pytest.mark.parametrize(
        "exc, expected_code, expected_reason",
        [
            (
                AITimeoutError("x"),
                GenerationErrorCode.AI_TIMEOUT,
                AIFallbackReason.AI_TIMEOUT,
            ),
            (
                AIRateLimitError("x"),
                GenerationErrorCode.AI_RATE_LIMITED,
                AIFallbackReason.AI_RATE_LIMITED,
            ),
            (
                AIConnectionError("x"),
                GenerationErrorCode.AI_CONNECTION_FAILED,
                AIFallbackReason.AI_CONNECTION_FAILED,
            ),
            (
                AIInvalidResponseError("x"),
                GenerationErrorCode.AI_INVALID_RESPONSE,
                AIFallbackReason.AI_INVALID_RESPONSE,
            ),
            (
                AIUnsupportedProtocolError("x"),
                GenerationErrorCode.AI_UNSUPPORTED_PROTOCOL,
                AIFallbackReason.UNSUPPORTED_PROTOCOL,
            ),
            (
                AIConfigurationError("x"),
                GenerationErrorCode.AI_NOT_CONFIGURED,
                AIFallbackReason.AI_NOT_CONFIGURED,
            ),
            (
                AIProviderError("x", status_code=500),
                GenerationErrorCode.AI_RUNTIME_FAILURE,
                AIFallbackReason.AI_RUNTIME_FAILURE,
            ),
            (
                ProgramValidationError("x"),
                GenerationErrorCode.VALIDATION_FAILED,
                AIFallbackReason.AI_VALIDATION_FAILED,
            ),
            (
                ProgramPersistenceError("x"),
                GenerationErrorCode.PERSISTENCE_FAILED,
                AIFallbackReason.AI_RUNTIME_FAILURE,
            ),
            (
                RuntimeError("x"),
                GenerationErrorCode.UNEXPECTED_ERROR,
                AIFallbackReason.AI_RUNTIME_FAILURE,
            ),
        ],
    )
    def test_exception_maps_to_consistent_pair(
        self, exc, expected_code, expected_reason
    ):
        code = classify_error(exc)
        assert code is expected_code
        assert fallback_reason_for_code(code) is expected_reason

    @pytest.mark.parametrize(
        "exc",
        [
            AITimeoutError("x"),
            AIRateLimitError("x"),
            AIConnectionError("x"),
            AIUnsupportedProtocolError("x"),
        ],
    )
    def test_specific_ai_errors_are_not_generic_runtime_failure(self, exc):
        """Регрессия: конкретная ошибка не деградирует в общий сбой."""
        reason = fallback_reason_for_code(classify_error(exc))
        assert reason is not AIFallbackReason.AI_RUNTIME_FAILURE

    def test_every_error_code_has_explicit_fallback_reason(self):
        """Новый код отказа нельзя добавить, не решив, как он виден админу.

        Тест защищает от молчаливой деградации: без явной записи в таблице новый
        код провалился бы в общий `ai_runtime_failure` через `.get()`.
        """
        missing = [
            code.value
            for code in GenerationErrorCode
            if code not in _FALLBACK_REASON_BY_CODE
        ]
        assert missing == [], (
            "Для этих кодов не задана причина fallback: "
            f"{missing}. Добавьте их в _FALLBACK_REASON_BY_CODE."
        )

    def test_fallback_reasons_are_valid_enum_members(self):
        for reason in _FALLBACK_REASON_BY_CODE.values():
            assert AIFallbackReason(reason.value) is reason


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


class TestIdempotencyKeyParameterConflict:
    """Клиентский ключ — обещание «это тот же запрос» (Phase 1.2-C).

    Если параметры отличаются, обещание неверно: отдать результат прошлого
    запроса нельзя (он собран другим генератором), запустить новую генерацию под
    тем же ключом тоже нельзя (это разрушает идемпотентность). Конфликт
    разрешает клиент.
    """

    async def test_same_key_same_generator_returns_same_job(self):
        jobs = FakeJobRepository()
        program = _program()
        service = _service(jobs, FakeProgramRepository([program]))
        key = "client-conflict-same"

        first = await service.run(
            profile_id=PROFILE_ID,
            trigger=GenerationTrigger.ADMIN_REQUEST,
            requested_generator="deterministic",
            client_idempotency_key=key,
            operation=lambda: _succeed(program),
        )
        second = await service.run(
            profile_id=PROFILE_ID,
            trigger=GenerationTrigger.ADMIN_REQUEST,
            requested_generator="deterministic",
            client_idempotency_key=key,
            operation=_raise_validation,
        )

        assert second.duplicate is True
        assert second.job.job_id == first.job.job_id
        assert second.existing_program is program
        assert len(jobs.rows) == 1

    async def test_same_key_different_generator_conflicts(self):
        """Главный случай: тот же ключ с другим генератором."""
        jobs = FakeJobRepository()
        program = _program()
        service = _service(jobs, FakeProgramRepository([program]))
        key = "client-conflict-diff"

        await service.run(
            profile_id=PROFILE_ID,
            trigger=GenerationTrigger.ADMIN_REQUEST,
            requested_generator="deterministic",
            client_idempotency_key=key,
            operation=lambda: _succeed(program),
        )

        with pytest.raises(IdempotencyKeyConflictError):
            await service.run(
                profile_id=PROFILE_ID,
                trigger=GenerationTrigger.ADMIN_REQUEST,
                requested_generator="ai",
                client_idempotency_key=key,
                operation=_raise_validation,
            )

        # Ни второй job, ни вторая программа не создаются.
        assert len(jobs.rows) == 1
        assert next(iter(jobs.rows.values())).requested_generator == "deterministic"

    async def test_conflict_does_not_return_foreign_generator_result(self):
        """Программа прежнего генератора наружу не отдаётся."""
        jobs = FakeJobRepository()
        program = _program()
        service = _service(jobs, FakeProgramRepository([program]))
        key = "client-conflict-no-leak"

        await service.run(
            profile_id=PROFILE_ID,
            trigger=GenerationTrigger.ADMIN_REQUEST,
            requested_generator="deterministic",
            client_idempotency_key=key,
            operation=lambda: _succeed(program),
        )

        with pytest.raises(IdempotencyKeyConflictError) as exc:
            await service.run(
                profile_id=PROFILE_ID,
                trigger=GenerationTrigger.ADMIN_REQUEST,
                requested_generator="ai",
                client_idempotency_key=key,
                operation=_raise_validation,
            )

        # Конфликт не является отказом генерации: наследование от
        # ProgramGenerationError дало бы 422 вместо 409.
        assert not isinstance(exc.value, ProgramGenerationError)
        assert "deterministic" in str(exc.value)

    async def test_failed_job_same_key_different_generator_conflicts(self):
        """Провалившийся job тоже занимает ключ: конфликт, а не новый запуск."""
        jobs = FakeJobRepository()
        service = _service(jobs)
        key = "client-conflict-failed"

        with pytest.raises(ProgramValidationError):
            await service.run(
                profile_id=PROFILE_ID,
                trigger=GenerationTrigger.ADMIN_REQUEST,
                requested_generator="deterministic",
                client_idempotency_key=key,
                operation=_raise_validation,
            )

        with pytest.raises(IdempotencyKeyConflictError):
            await service.run(
                profile_id=PROFILE_ID,
                trigger=GenerationTrigger.ADMIN_REQUEST,
                requested_generator="ai",
                client_idempotency_key=key,
                operation=_raise_validation,
            )

        assert len(jobs.rows) == 1

    async def test_active_job_same_key_different_generator_conflicts(self):
        """Активная генерация: конфликт параметров важнее сообщения «уже идёт»."""
        jobs = FakeJobRepository()
        service = _service(jobs)
        started = asyncio.Event()
        release = asyncio.Event()
        key = "client-conflict-active"

        async def _slow():
            started.set()
            await release.wait()
            return _Result(_program())

        first = asyncio.create_task(
            service.run(
                profile_id=PROFILE_ID,
                trigger=GenerationTrigger.ADMIN_REQUEST,
                requested_generator="deterministic",
                client_idempotency_key=key,
                operation=_slow,
            )
        )
        await started.wait()

        with pytest.raises(IdempotencyKeyConflictError):
            await service.run(
                profile_id=PROFILE_ID,
                trigger=GenerationTrigger.ADMIN_REQUEST,
                requested_generator="ai",
                client_idempotency_key=key,
                operation=_raise_validation,
            )

        release.set()
        await first
        assert len(jobs.rows) == 1

    async def test_concurrent_same_key_conflicting_generator_is_deterministic(self):
        """Гонка с разными генераторами: победитель один, проигравший — конфликт.

        Исход не зависит от порядка: тот, чья вставка прошла, выполняет
        генерацию; второй получает конфликт параметров, а не чужой результат и
        не второй job.
        """
        jobs = FakeJobRepository()
        service = _service(jobs)
        key = "client-conflict-race"
        ran: list[str] = []

        def _op(name: str):
            async def _run():
                ran.append(name)
                await asyncio.sleep(0)
                return _Result(_program())

            return _run

        results = await asyncio.gather(
            service.run(
                profile_id=PROFILE_ID,
                trigger=GenerationTrigger.ADMIN_REQUEST,
                requested_generator="deterministic",
                client_idempotency_key=key,
                operation=_op("deterministic"),
            ),
            service.run(
                profile_id=PROFILE_ID,
                trigger=GenerationTrigger.ADMIN_REQUEST,
                requested_generator="ai",
                client_idempotency_key=key,
                operation=_op("ai"),
            ),
            return_exceptions=True,
        )

        successes = [r for r in results if not isinstance(r, BaseException)]
        conflicts = [
            r for r in results if isinstance(r, IdempotencyKeyConflictError)
        ]
        assert len(successes) == 1
        assert len(conflicts) == 1
        assert len(ran) == 1
        assert len(jobs.rows) == 1

    async def test_server_attempt_key_ignores_generator_change(self):
        """Серверный ключ попытки конфликтом не считается.

        Ключ `profile:trigger:attempt` вызывающая сторона не выбирала, поэтому
        смена генератора здесь означает изменение конфигурации приложения между
        запусками, а не противоречивый запрос: повторный finalize должен
        получить готовую программу, а не ошибку.
        """
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
            requested_generator="deterministic",
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
