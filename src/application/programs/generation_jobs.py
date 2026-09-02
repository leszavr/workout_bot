"""Application-слой persistent состояния генерации (Phase 1.2-B).

Сервис оборачивает существующую генерацию в operational-запись `GenerationJob`
и обеспечивает серверную идемпотентность. Он сознательно НЕ является
orchestrator'ом: генератор, readiness gate, safety и validator остаются за
`ProgramGenerationOrchestrator` (единая точка генерации — Phase 1.2-C).

Границы транзакций (длительный AI-вызов ни в одну из них не попадает):

    tx: определить номер попытки
    tx: создать job (INSERT ... ON CONFLICT DO NOTHING) → PENDING
    tx: PENDING → RUNNING
    -- вне транзакции: генерация, включая внешний AI-вызов и сохранение программы
    tx: RUNNING → SUCCEEDED | FAILED

Повторный запрос той же логической генерации второй генерации не запускает:
успешный job отдаёт уже созданную версию программы, активный — сообщает, что
генерация ещё идёт.

Phase 1.2-D: отказ может быть не окончательным. Если код ошибки transient и
попытки не исчерпаны, сервис назначает `next_attempt_at`, и job попадает в
очередь повторов воркера. `run_claimed` выполняет такую повторную попытку по
уже захваченному job — второй логической генерации при этом не возникает,
потому что ключ идемпотентности остаётся прежним.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable, Generic, Protocol, TypeVar

from src.application.programs.generation_context import generation_job_context
from src.domain.enums import GenerationJobStatus
from src.domain.generation import (
    GenerationErrorCode,
    GenerationErrorKind,
    GenerationJob,
    GenerationTrigger,
    build_client_idempotency_key,
    build_idempotency_key,
    classify_error,
    error_kind,
    safe_error_message,
)
from src.domain.program import WorkoutProgram
from src.domain.retry import RetryPolicy
from src.errors import (
    GenerationAlreadyRunningError,
    IdempotencyKeyConflictError,
    ProgramGenerationError,
)
from src.infrastructure.persistence.postgres.generation_job_repository import (
    GenerationJobRepository,
)
from src.infrastructure.persistence.program_repository import ProgramRepository

logger = logging.getLogger(__name__)


class HasProgram(Protocol):
    """Результат генерации: сервису нужна только сама программа."""

    program: WorkoutProgram


TResult = TypeVar("TResult", bound=HasProgram)


@dataclass
class GenerationRun(Generic[TResult]):
    """Итог запуска под контролем job.

    `result` заполнен, только если генерация действительно выполнялась.
    duplicate=True означает, что та же логическая генерация уже завершилась
    успешно, и `existing_program` содержит её результат.
    """

    job: GenerationJob
    result: TResult | None = None
    duplicate: bool = False
    existing_program: WorkoutProgram | None = None


class GenerationJobService:
    def __init__(
        self,
        *,
        repository: GenerationJobRepository,
        program_repository: ProgramRepository,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        self._jobs = repository
        self._programs = program_repository
        # Политика повторов принадлежит тому, кто закрывает job: момент отказа —
        # единственное место, где известны и код ошибки, и номер попытки.
        # None означает «повторы не назначаются»: так работают вызовы без
        # retry-контура (тесты, путь без worker'а).
        self._retry = retry_policy

    async def run(
        self,
        *,
        profile_id: str,
        trigger: GenerationTrigger,
        requested_generator: str,
        operation: Callable[[], Awaitable[TResult]],
        client_idempotency_key: str | None = None,
    ) -> GenerationRun[TResult]:
        """Выполняет генерацию ровно один раз на логическую генерацию."""
        key = await self._resolve_key(
            profile_id=profile_id,
            trigger=trigger,
            client_idempotency_key=client_idempotency_key,
        )
        job, created = await self._jobs.create_or_get(
            GenerationJob(
                job_id=uuid.uuid4().hex,
                profile_id=profile_id,
                idempotency_key=key,
                trigger=trigger,
                requested_generator=requested_generator,
            )
        )
        if not created:
            return await self._handle_duplicate(
                job,
                requested_generator=requested_generator,
                # Конфликт параметров проверяется только для клиентского ключа:
                # серверный ключ попытки вызывающая сторона не выбирала и
                # ничего им не обещала.
                check_parameters=bool(client_idempotency_key),
            )

        job = await self._jobs.mark_running(job)
        logger.info(
            "event=generation_job_running",
            extra={
                "profile_id": profile_id,
                "job_id": job.job_id,
                "attempt": job.attempts,
            },
        )

        try:
            # Телеметрия AI-контура пишется внутри `operation` и должна знать,
            # к какой операции генерации относится. Ссылка передаётся ambient-
            # контекстом, а не аргументом: контракт `ProgramGenerator.generate`
            # общий для AI и алгоритмического генератора, и последнему job не нужен.
            with generation_job_context(job.job_id):
                result = await operation()
        except BaseException as exc:  # noqa: BLE001 — любой отказ обязан закрыть job
            job = await self._fail(job, exc)
            raise

        program = result.program
        if not program.program_id:
            # Успешная генерация обязана иметь сохранённую версию программы:
            # job без ссылки на результат успешным считать нельзя.
            job = await self._fail(
                job,
                RuntimeError("Генерация не вернула сохранённую программу"),
            )
            logger.error(
                "event=generation_job_missing_program",
                extra={"profile_id": profile_id, "job_id": job.job_id},
            )
            raise ProgramGenerationError(
                "Генерация завершилась без сохранённой программы"
            )

        job = await self._jobs.mark_succeeded(
            job, program_id=program.program_id, program_version=program.version
        )
        logger.info(
            "event=generation_job_succeeded",
            extra={
                "profile_id": profile_id,
                "job_id": job.job_id,
                "program_id": program.program_id,
                "version": program.version,
            },
        )
        return GenerationRun(job=job, result=result)

    async def run_claimed(
        self,
        job: GenerationJob,
        *,
        operation: Callable[[], Awaitable[TResult]],
    ) -> GenerationRun[TResult]:
        """Выполняет повторную попытку уже захваченного job (Phase 1.2-D).

        Job приходит из `claim_due`: он уже переведён в `RUNNING`, аренда уже
        принадлежит воркеру. Здесь остаётся ровно то, что делает `run` после
        `mark_running`, — выполнить операцию и закрыть job.

        Ключ идемпотентности не пересчитывается и не меняется: повтор — это та
        же логическая генерация, поэтому вторая программа появиться не может.
        Проверять дубликаты тоже не нужно: захват уже исключил параллельного
        исполнителя на уровне PostgreSQL.
        """
        logger.info(
            "event=generation_job_retry_started",
            extra={
                "profile_id": job.profile_id,
                "job_id": job.job_id,
                "attempt": job.attempts,
            },
        )
        try:
            with generation_job_context(job.job_id):
                result = await operation()
        except BaseException as exc:  # noqa: BLE001 — повтор обязан закрыть job
            await self._fail(job, exc)
            raise

        program = result.program
        if not program.program_id:
            await self._fail(
                job, RuntimeError("Генерация не вернула сохранённую программу")
            )
            raise ProgramGenerationError(
                "Генерация завершилась без сохранённой программы"
            )

        job = await self._jobs.mark_succeeded(
            job, program_id=program.program_id, program_version=program.version
        )
        logger.info(
            "event=generation_job_retry_succeeded",
            extra={
                "profile_id": job.profile_id,
                "job_id": job.job_id,
                "program_id": program.program_id,
                "attempts": job.attempts,
            },
        )
        return GenerationRun(job=job, result=result)

    async def _fail(self, job: GenerationJob, exc: BaseException) -> GenerationJob:
        """Закрывает job отказом и, если это уместно, назначает повтор.

        Повтор планируется здесь, а не в worker'е, потому что здесь известны
        оба слагаемых решения: класс ошибки (по стабильному коду) и номер
        попытки. Worker, увидев только `FAILED`, не смог бы отличить «ещё не
        пробовали повторять» от «попытки исчерпаны», не пересчитывая политику
        задним числом.

        Non-retryable отказ и исчерпание попыток дают одно и то же наружное
        состояние: `FAILED` без `next_attempt_at`. Это осознанно — для
        администратора это одинаковый факт «система больше не пробует», а
        отличие видно по коду ошибки и числу попыток.
        """
        code = classify_error(exc)
        next_attempt_at = self._plan_retry(job, code)
        job = await self._jobs.mark_failed(
            job,
            error_code=code,
            message=safe_error_message(exc),
            next_attempt_at=next_attempt_at,
        )
        logger.warning(
            "event=generation_job_failed",
            extra={
                "profile_id": job.profile_id,
                "job_id": job.job_id,
                "error_code": code.value,
                "attempts": job.attempts,
                "retry_scheduled": next_attempt_at is not None,
            },
        )
        return job

    def _plan_retry(
        self, job: GenerationJob, code: GenerationErrorCode
    ) -> datetime | None:
        if self._retry is None:
            return None
        if error_kind(code) is not GenerationErrorKind.TRANSIENT:
            return None
        # `job.attempts` — попытки до этого отказа: `mark_failed` номер не
        # меняет, его увеличил `start()`.
        return self._retry.next_attempt_at(
            now=datetime.now(timezone.utc), attempts_made=job.attempts
        )

    async def _resolve_key(
        self,
        *,
        profile_id: str,
        trigger: GenerationTrigger,
        client_idempotency_key: str | None,
    ) -> str:
        if client_idempotency_key:
            return build_client_idempotency_key(
                profile_id=profile_id, client_key=client_idempotency_key
            )
        attempt = await self._jobs.next_attempt(profile_id, trigger)
        return build_idempotency_key(
            profile_id=profile_id, trigger=trigger, attempt=attempt
        )

    async def _handle_duplicate(
        self,
        job: GenerationJob,
        *,
        requested_generator: str,
        check_parameters: bool,
    ) -> GenerationRun:
        """Дубликат логической генерации: второй раз ничего не запускаем.

        Для клиентского ключа сначала проверяется совместимость параметров.
        Idempotency key — это утверждение вызывающей стороны «это повтор того же
        запроса»; если `requested_generator` отличается, утверждение неверно.
        Отдать результат победителя нельзя (он собран другим генератором — это
        молчаливая подмена явного выбора), запустить новую генерацию под тем же
        ключом тоже нельзя (это разрушает идемпотентность). Поэтому конфликт
        возвращается клиенту.

        Проверка идёт до разбора статуса: конфликт параметров не зависит от того,
        чем закончилась предыдущая генерация, и одинаково применим к активному,
        успешному и провалившемуся job.

        Серверный ключ попытки (`profile:trigger:attempt`) не проверяется:
        вызывающая сторона его не выбирала и ничего им не обещала. Там смена
        генератора означает изменение конфигурации приложения между запусками, а
        не противоречивый запрос, и повторный finalize должен по-прежнему
        получать готовую программу, а не ошибку.
        """
        if check_parameters and job.requested_generator != requested_generator:
            logger.warning(
                "event=generation_job_key_conflict",
                extra={
                    "profile_id": job.profile_id,
                    "job_id": job.job_id,
                    "existing_generator": job.requested_generator,
                    "requested_generator": requested_generator,
                },
            )
            raise IdempotencyKeyConflictError(
                "Этот idempotency key уже использован с другим генератором "
                f"({job.requested_generator}). Используйте новый ключ или "
                "повторите запрос с тем же генератором."
            )
        logger.info(
            "event=generation_job_duplicate",
            extra={
                "profile_id": job.profile_id,
                "job_id": job.job_id,
                "job_status": job.status.value,
            },
        )
        if job.status is GenerationJobStatus.SUCCEEDED and job.program_id:
            program = await self._programs.get(job.program_id, job.program_version)
            if program is not None:
                return GenerationRun(job=job, duplicate=True, existing_program=program)
            # Версия программы удалена (FK обнулил ссылку в другой транзакции):
            # выдавать успех без результата нельзя.
            raise ProgramGenerationError(
                "Программа предыдущей успешной генерации недоступна"
            )
        if job.is_active:
            raise GenerationAlreadyRunningError(
                "Генерация программы для этой анкеты уже выполняется"
            )
        raise ProgramGenerationError(
            job.last_error_message or "Предыдущая генерация завершилась неудачно"
        )
