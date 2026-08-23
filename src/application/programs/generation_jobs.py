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
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Awaitable, Callable, Generic, Protocol, TypeVar

from src.domain.enums import GenerationJobStatus
from src.domain.generation import (
    GenerationJob,
    GenerationTrigger,
    build_client_idempotency_key,
    build_idempotency_key,
    classify_error,
    safe_error_message,
)
from src.domain.program import WorkoutProgram
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
    ) -> None:
        self._jobs = repository
        self._programs = program_repository

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
            result = await operation()
        except BaseException as exc:  # noqa: BLE001 — любой отказ обязан закрыть job
            code = classify_error(exc)
            job = await self._jobs.mark_failed(
                job, error_code=code, message=safe_error_message(exc)
            )
            logger.warning(
                "event=generation_job_failed",
                extra={
                    "profile_id": profile_id,
                    "job_id": job.job_id,
                    "error_code": code.value,
                },
            )
            raise

        program = result.program
        if not program.program_id:
            # Успешная генерация обязана иметь сохранённую версию программы:
            # job без ссылки на результат успешным считать нельзя.
            job = await self._jobs.mark_failed(
                job,
                error_code=classify_error(RuntimeError()),
                message="Генерация не вернула сохранённую программу",
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
