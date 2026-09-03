"""Unit-тесты retry/recovery (Phase 1.2-D).

Без БД: проверяются арифметика backoff, решение «повторять или нет» и поведение
retry-сервисов на фейках репозиториев, повторяющих контракт PostgreSQL-версий
(захват переводит запись в работу, аренда принадлежит владельцу).

Свойства, требующие настоящего PostgreSQL — взаимное исключение двух воркеров и
recovery после «падения» процесса — проверяются в
`tests/integration/test_worker_retry.py`.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.application.programs.retry_service import (
    RETRYABLE_TRIGGERS,
    DeliveryRecoveryService,
    GenerationRetryService,
    RetryCoordinator,
)
from src.domain.enums import (
    GenerationJobStatus,
    GenerationSource,
    ProgramDeliveryStatus,
    ProgramStatus,
)
from src.domain.generation import (
    GenerationErrorCode,
    GenerationErrorKind,
    GenerationJob,
    GenerationTrigger,
    error_kind,
)
from src.domain.program import (
    GenerationInfo,
    ProgramExercise,
    TrainingDay,
    WorkoutProgram,
)
from src.domain.retry import RetryPolicy
from src.errors import ProgramDeliveryError, ProgramGenerationError
from src.infrastructure.persistence.postgres.delivery_repository import (
    ProgramDeliveryRecord,
)

# Фиксированный момент — только для арифметики политики. Фейки и сценарии
# работают в реальном времени: retry-сервисы берут `utcnow()`, и подмена
# времени только в фейках дала бы ложно «наступившие» повторы.
POLICY_NOW = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)


def _now() -> datetime:
    return datetime.now(timezone.utc)
PROFILE_ID = "retry-profile-1"
OWNER = "worker-test-1"
LEASE = 60.0

POLICY = RetryPolicy(
    max_attempts=3,
    initial_delay_seconds=60,
    multiplier=4,
    max_delay_seconds=900,
)


# --- Политика ------------------------------------------------------------------


class TestRetryPolicy:
    def test_backoff_grows_exponentially(self):
        assert POLICY.delay_seconds(1) == 60
        assert POLICY.delay_seconds(2) == 240

    def test_backoff_respects_maximum(self):
        assert POLICY.delay_seconds(10) == 900

    def test_first_failure_uses_initial_delay(self):
        """attempts_made=0 не должен давать задержку меньше начальной."""
        assert POLICY.delay_seconds(0) == 60

    def test_attempts_are_finite(self):
        assert POLICY.has_attempts_left(2) is True
        assert POLICY.has_attempts_left(3) is False
        assert POLICY.next_attempt_at(now=POLICY_NOW, attempts_made=3) is None

    def test_next_attempt_at_uses_delay(self):
        assert POLICY.next_attempt_at(
            now=POLICY_NOW, attempts_made=1
        ) == POLICY_NOW + timedelta(seconds=60)

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"max_attempts": 0},
            {"initial_delay_seconds": 0},
            {"multiplier": 0.5},
            {"max_delay_seconds": 1},
        ],
    )
    def test_nonsense_configuration_rejected(self, kwargs):
        """Конфигурация, которая дала бы бесконечные или убывающие повторы."""
        base = {
            "max_attempts": 3,
            "initial_delay_seconds": 60,
            "multiplier": 4,
            "max_delay_seconds": 900,
        }
        with pytest.raises(ValueError):
            RetryPolicy(**{**base, **kwargs})


# --- Фейки ---------------------------------------------------------------------


def _job(
    *,
    status: GenerationJobStatus = GenerationJobStatus.FAILED,
    attempts: int = 1,
    trigger: GenerationTrigger = GenerationTrigger.AUTO_FINALIZATION,
    next_attempt_at: datetime | None = None,
    lease_owner: str | None = None,
    lease_expires_at: datetime | None = None,
    error_code: str | None = GenerationErrorCode.AI_TIMEOUT.value,
    job_id: str = "job-retry-1",
) -> GenerationJob:
    return GenerationJob(
        id=1,
        job_id=job_id,
        profile_id=PROFILE_ID,
        idempotency_key=f"{trigger.value}:{PROFILE_ID}:1",
        trigger=trigger,
        requested_generator=GenerationSource.AI.value,
        status=status,
        attempts=attempts,
        last_error_code=error_code,
        last_error_message="сбой" if error_code else None,
        next_attempt_at=next_attempt_at,
        lease_owner=lease_owner,
        lease_expires_at=lease_expires_at,
    )


def _program(version: int = 1) -> WorkoutProgram:
    return WorkoutProgram(
        program_id="prog-retry-1",
        profile_id=PROFILE_ID,
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
                        exercise_external_id="Barbell_Full_Squat",
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


class FakeJobRepo:
    """Повторяет контракт PostgreSQL-репозитория в части захвата и аренды."""

    def __init__(self, jobs: list[GenerationJob] | None = None) -> None:
        self.jobs = jobs or []
        self.cleared: list[str] = []
        self.failed: list[tuple[str, datetime | None]] = []

    async def claim_due(self, *, owner, lease_seconds, limit=1, now=None):
        moment = now or _now()
        due = [
            j
            for j in self.jobs
            if j.status is GenerationJobStatus.FAILED
            and j.next_attempt_at is not None
            and j.next_attempt_at <= moment
        ]
        claimed = []
        for job in due[:limit]:
            job.start()
            job.lease_owner = owner
            job.lease_expires_at = moment + timedelta(seconds=lease_seconds)
            claimed.append(job)
        return claimed

    async def release_stale(
        self, *, error_code, message, next_attempt_at=None, limit=50, now=None
    ):
        moment = now or _now()
        # Класс ошибки берётся от кода recovery, а не от кода прошлой попытки:
        # так же поступает PostgreSQL-реализация.
        retryable = error_kind(error_code) is GenerationErrorKind.TRANSIENT
        stale = [
            j
            for j in self.jobs
            if j.status is GenerationJobStatus.RUNNING and j.lease_expired(now=moment)
        ]
        released = []
        for job in stale[:limit]:
            job.fail(
                error_code=error_code,
                message=message,
                next_attempt_at=next_attempt_at if retryable else None,
            )
            released.append(job)
        return released

    async def clear_retry(self, job: GenerationJob) -> None:
        self.cleared.append(job.job_id)
        job.next_attempt_at = None

    async def mark_failed(self, job, *, error_code, message, next_attempt_at=None):
        job.fail(
            error_code=error_code, message=message, next_attempt_at=next_attempt_at
        )
        self.failed.append((job.job_id, next_attempt_at))
        return job


class FakeOrchestrator:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[str] = []

    async def retry(self, job: GenerationJob):
        self.calls.append(job.job_id)
        if self.fail:
            raise ProgramGenerationError("повтор не удался")
        return object()


class FakeDeliveryRepo:
    def __init__(self, records: list[ProgramDeliveryRecord] | None = None) -> None:
        self.records = records or []
        self.updated: list[ProgramDeliveryRecord] = []

    async def claim_due(self, *, owner, lease_seconds, limit=1, now=None):
        moment = now or _now()
        due = [
            r
            for r in self.records
            if r.status is ProgramDeliveryStatus.FAILED
            and r.next_attempt_at is not None
            and r.next_attempt_at <= moment
            and r.chat_id
        ]
        claimed = []
        for record in due[:limit]:
            record.status = ProgramDeliveryStatus.SENDING
            record.next_attempt_at = None
            record.lease_owner = owner
            record.lease_expires_at = moment + timedelta(seconds=lease_seconds)
            claimed.append(record)
        return claimed

    async def release_stale(self, *, message, next_attempt_at=None, limit=50, now=None):
        moment = now or _now()
        stale = [
            r
            for r in self.records
            if r.status is ProgramDeliveryStatus.SENDING
            and r.lease_expires_at is not None
            and r.lease_expires_at <= moment
        ]
        released = []
        for record in stale[:limit]:
            record.status = ProgramDeliveryStatus.FAILED
            record.last_error = message
            record.next_attempt_at = next_attempt_at
            record.lease_owner = None
            record.lease_expires_at = None
            released.append(record)
        return released

    async def update(self, record: ProgramDeliveryRecord) -> None:
        self.updated.append(record)


def _generation_service(repo: FakeJobRepo, orchestrator: FakeOrchestrator):
    return GenerationRetryService(
        jobs=repo,
        orchestrator=orchestrator,
        policy=POLICY,
        owner=OWNER,
        lease_seconds=LEASE,
    )


def _delivery_service(repo: FakeDeliveryRepo) -> DeliveryRecoveryService:
    return DeliveryRecoveryService(deliveries=repo, policy=POLICY)


def _record(
    *,
    status: ProgramDeliveryStatus = ProgramDeliveryStatus.FAILED,
    attempts: int = 1,
    next_attempt_at: datetime | None = None,
    chat_id: str | None = "555",
    lease_expires_at: datetime | None = None,
) -> ProgramDeliveryRecord:
    return ProgramDeliveryRecord(
        id=1,
        program_id="prog-retry-1",
        profile_id=PROFILE_ID,
        chat_id=chat_id,
        filename="program.html",
        status=status,
        attempts=attempts,
        next_attempt_at=next_attempt_at,
        lease_expires_at=lease_expires_at,
        lease_owner=OWNER if lease_expires_at else None,
    )


# --- Повтор генерации ----------------------------------------------------------


class TestGenerationRetry:
    async def test_due_job_is_retried_through_orchestrator(self):
        """Повтор идёт через единственную точку генерации, а не мимо неё."""
        repo = FakeJobRepo([_job(next_attempt_at=_now() - timedelta(seconds=1))])
        orchestrator = FakeOrchestrator()

        succeeded, failed = await _generation_service(repo, orchestrator).process_due()

        assert (succeeded, failed) == (1, 0)
        assert orchestrator.calls == ["job-retry-1"]

    async def test_job_scheduled_for_future_is_not_taken(self):
        repo = FakeJobRepo([_job(next_attempt_at=_now() + timedelta(hours=1))])
        orchestrator = FakeOrchestrator()

        succeeded, failed = await _generation_service(repo, orchestrator).process_due()

        assert (succeeded, failed) == (0, 0)
        assert orchestrator.calls == []

    async def test_job_without_schedule_is_not_taken(self):
        """FAILED без next_attempt_at — окончательный отказ, его не берут."""
        repo = FakeJobRepo([_job(next_attempt_at=None)])
        orchestrator = FakeOrchestrator()

        assert await _generation_service(repo, orchestrator).process_due() == (0, 0)

    async def test_admin_request_is_not_retried(self):
        """Явный запрос администратора повторяется не системой, а человеком.

        Администратор выбрал генератор и запретил fallback; молча собрать
        программу через минуту значило бы отменить это решение.
        """
        repo = FakeJobRepo(
            [
                _job(
                    trigger=GenerationTrigger.ADMIN_REQUEST,
                    next_attempt_at=_now() - timedelta(seconds=1),
                )
            ]
        )
        orchestrator = FakeOrchestrator()

        succeeded, failed = await _generation_service(repo, orchestrator).process_due()

        assert (succeeded, failed) == (0, 1)
        assert orchestrator.calls == []
        # Job закрыт, а не оставлен в RUNNING: иначе он застрял бы навсегда.
        assert repo.jobs[0].status is GenerationJobStatus.FAILED
        assert repo.jobs[0].next_attempt_at is None

    async def test_retryable_triggers_are_explicit(self):
        assert RETRYABLE_TRIGGERS == frozenset({GenerationTrigger.AUTO_FINALIZATION})

    async def test_failed_retry_is_counted_and_job_left_closed(self):
        repo = FakeJobRepo([_job(next_attempt_at=_now() - timedelta(seconds=1))])
        orchestrator = FakeOrchestrator(fail=True)

        succeeded, failed = await _generation_service(repo, orchestrator).process_due()

        assert (succeeded, failed) == (0, 1)
        assert orchestrator.calls == ["job-retry-1"]


class TestGenerationRecovery:
    async def test_stale_running_job_is_released_and_rescheduled(self):
        job = _job(
            status=GenerationJobStatus.RUNNING,
            lease_owner="worker-dead",
            lease_expires_at=_now() - timedelta(seconds=1),
        )
        repo = FakeJobRepo([job])

        released = await _generation_service(repo, FakeOrchestrator()).recover_stale()

        assert len(released) == 1
        assert job.status is GenerationJobStatus.FAILED
        assert job.next_attempt_at is not None
        assert job.lease_owner is None

    async def test_running_job_with_valid_lease_is_untouched(self):
        job = _job(
            status=GenerationJobStatus.RUNNING,
            lease_owner=OWNER,
            lease_expires_at=_now() + timedelta(minutes=30),
        )
        repo = FakeJobRepo([job])

        assert await _generation_service(repo, FakeOrchestrator()).recover_stale() == []
        assert job.status is GenerationJobStatus.RUNNING

    async def test_running_job_without_lease_is_untouched(self):
        """RUNNING без аренды принадлежит синхронному пути в другом процессе."""
        job = _job(status=GenerationJobStatus.RUNNING, lease_expires_at=None)
        repo = FakeJobRepo([job])

        assert await _generation_service(repo, FakeOrchestrator()).recover_stale() == []
        assert job.status is GenerationJobStatus.RUNNING

    async def test_exhausted_job_is_not_rescheduled_after_recovery(self):
        """Исчерпавший попытки job закрывается окончательно, а не крутится."""
        job = _job(
            status=GenerationJobStatus.RUNNING,
            attempts=POLICY.max_attempts,
            lease_owner="worker-dead",
            lease_expires_at=_now() - timedelta(seconds=1),
        )
        repo = FakeJobRepo([job])

        await _generation_service(repo, FakeOrchestrator()).recover_stale()

        assert job.status is GenerationJobStatus.FAILED
        assert job.next_attempt_at is None
        assert repo.cleared == [job.job_id]

    async def test_recovery_classifies_by_its_own_error_code(self):
        """Повтор после recovery назначается по коду recovery, а не прошлого отказа.

        Прежний код относится к предыдущей попытке: job мог упасть на валидации,
        затем быть повторён и умереть вместе с процессом. Смерть процесса —
        transient-событие независимо от того, чем закончилась прошлая попытка.
        """
        job = _job(
            status=GenerationJobStatus.RUNNING,
            error_code=GenerationErrorCode.VALIDATION_FAILED.value,
            lease_owner="worker-dead",
            lease_expires_at=_now() - timedelta(seconds=1),
        )
        repo = FakeJobRepo([job])

        await _generation_service(repo, FakeOrchestrator()).recover_stale()

        assert job.last_error_code == GenerationErrorCode.UNEXPECTED_ERROR.value
        assert job.next_attempt_at is not None

    async def test_admin_job_is_not_rescheduled_after_recovery(self):
        job = _job(
            status=GenerationJobStatus.RUNNING,
            trigger=GenerationTrigger.ADMIN_REQUEST,
            lease_owner="worker-dead",
            lease_expires_at=_now() - timedelta(seconds=1),
        )
        repo = FakeJobRepo([job])

        await _generation_service(repo, FakeOrchestrator()).recover_stale()

        assert job.status is GenerationJobStatus.FAILED
        assert job.next_attempt_at is None


# --- Восстановление доставки -----------------------------------------------------
#
# Отправку выполняет Gateway (только у него есть доступ к Bot API). Worker
# возвращает в очередь записи, чей отправитель исчез, — без этого они остались бы
# в `sending` навсегда: Gateway забирает только `pending` и созревшие `failed`.


class TestDeliveryRecovery:
    async def test_stale_sending_delivery_is_recovered(self):
        record = _record(
            status=ProgramDeliveryStatus.SENDING,
            lease_expires_at=_now() - timedelta(seconds=1),
        )
        repo = FakeDeliveryRepo([record])

        released = await _delivery_service(repo).recover_stale()

        assert len(released) == 1
        assert record.status is ProgramDeliveryStatus.FAILED
        assert record.next_attempt_at is not None

    async def test_exhausted_delivery_is_not_rescheduled(self):
        """Исчерпавшую попытки запись Gateway брать не должен."""
        record = _record(
            status=ProgramDeliveryStatus.SENDING,
            attempts=POLICY.max_attempts,
            lease_expires_at=_now() - timedelta(seconds=1),
        )
        repo = FakeDeliveryRepo([record])

        await _delivery_service(repo).recover_stale()

        assert record.next_attempt_at is None

    async def test_live_lease_is_not_touched(self):
        """Живая аренда означает, что шлюз ещё отправляет."""
        record = _record(
            status=ProgramDeliveryStatus.SENDING,
            lease_expires_at=_now() + timedelta(minutes=5),
        )
        repo = FakeDeliveryRepo([record])

        assert await _delivery_service(repo).recover_stale() == []
        assert record.status is ProgramDeliveryStatus.SENDING

    async def test_worker_does_not_send_anything(self):
        """У сервиса нет ни Bot API, ни рендера: отправить он не может.

        Проверяется составом зависимостей, а не поведением: доступа к
        `api.telegram.org` из RU нет, и попытка отправки гарантированно сожгла бы
        бюджет попыток, конкурируя с очередью Gateway.
        """
        service = _delivery_service(FakeDeliveryRepo())
        assert not hasattr(service, "process_due")
        assert not any("delivery_service" in name for name in vars(service))


# --- Координатор ---------------------------------------------------------------


class TestRetryCoordinator:
    async def test_single_cycle_covers_recovery_and_retry(self):
        stale = _job(
            job_id="job-stale",
            status=GenerationJobStatus.RUNNING,
            lease_owner="worker-dead",
            lease_expires_at=_now() - timedelta(seconds=1),
        )
        due = _job(job_id="job-due", next_attempt_at=_now() - timedelta(seconds=1))
        jobs = FakeJobRepo([stale, due])
        orchestrator = FakeOrchestrator()
        deliveries = FakeDeliveryRepo(
            [
                _record(
                    status=ProgramDeliveryStatus.SENDING,
                    lease_expires_at=_now() - timedelta(seconds=1),
                )
            ]
        )

        result = await RetryCoordinator(
            generation=_generation_service(jobs, orchestrator),
            delivery=_delivery_service(deliveries),
        ).run_once()

        assert result.recovered_jobs == 1
        # Восстановленный job получает повтор в будущем, поэтому в этом же
        # проходе берётся только тот, чьё время пришло.
        assert result.retried_jobs == 1
        assert result.recovered_deliveries == 1
        assert orchestrator.calls == ["job-due"]

    async def test_generation_failure_does_not_stop_delivery(self):
        """Доставка и генерация независимы: сбой одной не блокирует другую."""

        class BrokenJobs(FakeJobRepo):
            async def claim_due(self, **kwargs):
                raise RuntimeError("БД недоступна")

            async def release_stale(self, **kwargs):
                raise RuntimeError("БД недоступна")

        deliveries = FakeDeliveryRepo(
            [
                _record(
                    status=ProgramDeliveryStatus.SENDING,
                    lease_expires_at=_now() - timedelta(seconds=1),
                )
            ]
        )

        result = await RetryCoordinator(
            generation=_generation_service(BrokenJobs(), FakeOrchestrator()),
            delivery=_delivery_service(deliveries),
        ).run_once()

        assert result.retried_jobs == 0
        assert result.recovered_deliveries == 1

    async def test_empty_cycle_reports_no_work(self):
        result = await RetryCoordinator(
            generation=_generation_service(FakeJobRepo(), FakeOrchestrator()),
            delivery=_delivery_service(FakeDeliveryRepo()),
        ).run_once()

        assert result.did_work is False
