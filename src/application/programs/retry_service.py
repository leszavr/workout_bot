"""Retry и recovery фоновых операций (Phase 1.2-D).

Здесь живёт вся логика «что можно повторить и когда», а сам процесс-исполнитель
(`apps/worker`) отвечает только за расписание и жизненный цикл. Разделение
нужно, чтобы поведение повторов можно было проверить тестами без запуска
процесса и без таймеров.

Две независимые операции, как требует Phase 1.2:

- генерация — повтор идёт через `ProgramGenerationOrchestrator.retry`, то есть
  через ту же единственную точку генерации;
- доставка — повтор идёт через существующий `ProgramDeliveryService.redeliver`,
  генерацию не трогает.

Что сервис сознательно НЕ делает:

- не создаёт job'ы и программы: он повторяет уже существующие операции;
- не повторяет `admin_request`-генерации: администратор нажал кнопку и получил
  ответ с причиной отказа. Молча пересобрать программу через минуту значило бы
  подменить его решение, а `allow_fallback=False` (сознательное требование
  1.2-C) при повторе всё равно пришлось бы нарушить;
- не решает, повторяема ли ошибка: класс ошибки уже зафиксирован в job её
  стабильным кодом.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from src.application.programs.orchestrator import ProgramGenerationOrchestrator
from src.application.programs.service import ProgramService
from src.application.programs.telegram_delivery import ProgramDeliveryService
from src.domain.enums import ProgramDeliveryStatus
from src.domain.generation import (
    GenerationErrorCode,
    GenerationJob,
    GenerationTrigger,
)
from src.domain.retry import RetryPolicy
from src.infrastructure.persistence.postgres.delivery_repository import (
    ProgramDeliveryRecord,
    ProgramDeliveryRepository,
)
from src.infrastructure.persistence.postgres.generation_job_repository import (
    GenerationJobRepository,
)

logger = logging.getLogger(__name__)

# Триггеры, повтор которых допустим. Явный запрос администратора исключён:
# он синхронный, ответ уже отдан, и повторная сборка программы «сама собой»
# отменила бы его выбор генератора.
RETRYABLE_TRIGGERS = frozenset({GenerationTrigger.AUTO_FINALIZATION})

STALE_LEASE_ERROR = "Исполнитель операции исчез: аренда истекла"
STALE_DELIVERY_ERROR = "Исполнитель доставки исчез: аренда истекла"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class RetryCycleResult:
    """Итог одного прохода. Нужен тестам и логам, а не бизнес-логике."""

    recovered_jobs: int = 0
    retried_jobs: int = 0
    failed_jobs: int = 0
    recovered_deliveries: int = 0
    retried_deliveries: int = 0
    failed_deliveries: int = 0

    @property
    def did_work(self) -> bool:
        return any(
            (
                self.recovered_jobs,
                self.retried_jobs,
                self.failed_jobs,
                self.recovered_deliveries,
                self.retried_deliveries,
                self.failed_deliveries,
            )
        )


class GenerationRetryService:
    """Повтор генерации и recovery зависших job."""

    def __init__(
        self,
        *,
        jobs: GenerationJobRepository,
        orchestrator: ProgramGenerationOrchestrator,
        policy: RetryPolicy,
        owner: str,
        lease_seconds: float,
        batch_size: int = 5,
    ) -> None:
        self._jobs = jobs
        self._orchestrator = orchestrator
        self._policy = policy
        self._owner = owner
        self._lease_seconds = lease_seconds
        self._batch = batch_size

    async def recover_stale(self) -> list[GenerationJob]:
        """Закрывает job'ы, чей исполнитель исчез, и планирует их повтор.

        Повтор назначается здесь же, а не отдельным проходом: job, оставшийся
        `FAILED` без `next_attempt_at`, никто больше не подхватит — именно этой
        ловушки требовал избежать design baseline.
        """
        released = await self._jobs.release_stale(
            error_code=GenerationErrorCode.UNEXPECTED_ERROR,
            message=STALE_LEASE_ERROR,
            next_attempt_at=self._policy.next_attempt_at(
                now=_utcnow(), attempts_made=0
            ),
            limit=self._batch,
        )
        for job in released:
            # Попытки могли быть уже исчерпаны: тогда план повтора, выставленный
            # репозиторием оптимистично, надо снять.
            if not self._policy.has_attempts_left(job.attempts):
                await self._jobs.clear_retry(job)
                job.next_attempt_at = None
            if job.trigger not in RETRYABLE_TRIGGERS and job.next_attempt_at:
                await self._jobs.clear_retry(job)
                job.next_attempt_at = None
            logger.warning(
                "event=generation_job_recovered",
                extra={
                    "job_id": job.job_id,
                    "profile_id": job.profile_id,
                    "attempts": job.attempts,
                    "retry_scheduled": job.next_attempt_at is not None,
                },
            )
        return released

    async def process_due(self) -> tuple[int, int]:
        """Повторяет job'ы, которым назначен повтор. Возвращает (успехи, отказы)."""
        claimed = await self._jobs.claim_due(
            owner=self._owner,
            lease_seconds=self._lease_seconds,
            limit=self._batch,
        )
        succeeded = 0
        failed = 0
        for job in claimed:
            if job.trigger not in RETRYABLE_TRIGGERS:
                # Такой job не должен был попасть в очередь; закрываем его как
                # окончательный отказ, а не оставляем в RUNNING.
                await self._jobs.mark_failed(
                    job,
                    error_code=job.last_error_code
                    or GenerationErrorCode.GENERATION_FAILED,
                    message="Повтор этого типа генерации не выполняется",
                    next_attempt_at=None,
                )
                failed += 1
                continue
            try:
                await self._orchestrator.retry(job)
                succeeded += 1
                logger.info(
                    "event=generation_retry_succeeded",
                    extra={"job_id": job.job_id, "attempt": job.attempts},
                )
            except Exception:  # noqa: BLE001 — job уже закрыт сервисом job'ов
                failed += 1
                logger.warning(
                    "event=generation_retry_failed",
                    extra={"job_id": job.job_id, "attempt": job.attempts},
                )
        return succeeded, failed


class DeliveryRetryService:
    """Повтор доставки. Генерацию не запускает ни при каких условиях.

    Программы читаются через `ProgramService` — read-only фасад. Это не
    формальность: повтор доставки не имеет права создать версию программы, и
    отсутствие у него пишущего репозитория делает это невозможным по
    конструкции, а не по договорённости.
    """

    def __init__(
        self,
        *,
        deliveries: ProgramDeliveryRepository,
        programs: ProgramService,
        delivery_service: ProgramDeliveryService,
        policy: RetryPolicy,
        owner: str,
        lease_seconds: float,
        batch_size: int = 5,
    ) -> None:
        self._deliveries = deliveries
        self._programs = programs
        self._delivery = delivery_service
        self._policy = policy
        self._owner = owner
        self._lease_seconds = lease_seconds
        self._batch = batch_size

    async def recover_stale(self) -> list[ProgramDeliveryRecord]:
        released = await self._deliveries.release_stale(
            message=STALE_DELIVERY_ERROR,
            next_attempt_at=self._policy.next_attempt_at(
                now=_utcnow(), attempts_made=0
            ),
            limit=self._batch,
        )
        for record in released:
            if not self._policy.has_attempts_left(record.attempts):
                record.next_attempt_at = None
                await self._deliveries.update(record)
            logger.warning(
                "event=delivery_recovered",
                extra={
                    "profile_id": record.profile_id,
                    "program_id": record.program_id,
                    "attempts": record.attempts,
                    "retry_scheduled": record.next_attempt_at is not None,
                },
            )
        return released

    async def process_due(self) -> tuple[int, int]:
        """Повторяет доставки. Возвращает (успехи, отказы)."""
        claimed = await self._deliveries.claim_due(
            owner=self._owner,
            lease_seconds=self._lease_seconds,
            limit=self._batch,
        )
        succeeded = 0
        failed = 0
        for record in claimed:
            program = await self._programs.get(record.program_id)
            if program is None:
                # Программу удалили: повторять нечего, и держать запись в
                # SENDING нельзя.
                record.status = ProgramDeliveryStatus.FAILED
                record.last_error = "Программа удалена: доставка невозможна"
                record.next_attempt_at = None
                record.lease_owner = None
                record.lease_expires_at = None
                await self._deliveries.update(record)
                failed += 1
                continue
            try:
                await self._delivery.redeliver(record, program)
                succeeded += 1
                logger.info(
                    "event=delivery_retry_succeeded",
                    extra={
                        "profile_id": record.profile_id,
                        "attempts": record.attempts,
                    },
                )
            except Exception:  # noqa: BLE001 — статус уже записан сервисом доставки
                failed += 1
                logger.warning(
                    "event=delivery_retry_failed",
                    extra={
                        "profile_id": record.profile_id,
                        "attempts": record.attempts,
                    },
                )
        return succeeded, failed


class RetryCoordinator:
    """Один проход обработки: recovery, затем повторы.

    Порядок важен: сначала возвращаем в очередь операции исчезнувших
    исполнителей, потом берём очередь. Иначе job, застрявший в `RUNNING`,
    ждал бы следующего цикла без причины.

    Сбой одного контура не должен останавливать другой: доставка не зависит от
    генерации, и наоборот. Поэтому исключения каждого шага перехватываются.
    """

    def __init__(
        self,
        *,
        generation: GenerationRetryService,
        delivery: DeliveryRetryService | None,
    ) -> None:
        self._generation = generation
        self._delivery = delivery

    async def run_once(self) -> RetryCycleResult:
        result = RetryCycleResult()

        try:
            result.recovered_jobs = len(await self._generation.recover_stale())
        except Exception:  # noqa: BLE001 — цикл не должен умирать от одной ошибки
            logger.exception("event=generation_recovery_error")

        try:
            result.retried_jobs, result.failed_jobs = (
                await self._generation.process_due()
            )
        except Exception:  # noqa: BLE001
            logger.exception("event=generation_retry_error")

        if self._delivery is not None:
            try:
                result.recovered_deliveries = len(
                    await self._delivery.recover_stale()
                )
            except Exception:  # noqa: BLE001
                logger.exception("event=delivery_recovery_error")

            try:
                result.retried_deliveries, result.failed_deliveries = (
                    await self._delivery.process_due()
                )
            except Exception:  # noqa: BLE001
                logger.exception("event=delivery_retry_error")

        if result.did_work:
            logger.info(
                "event=retry_cycle_finished",
                extra={
                    "recovered_jobs": result.recovered_jobs,
                    "retried_jobs": result.retried_jobs,
                    "failed_jobs": result.failed_jobs,
                    "recovered_deliveries": result.recovered_deliveries,
                    "retried_deliveries": result.retried_deliveries,
                    "failed_deliveries": result.failed_deliveries,
                },
            )
        return result
