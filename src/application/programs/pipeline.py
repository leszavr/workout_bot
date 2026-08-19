"""End-to-end pipeline Stage 5: генерация + рендер + доставка.

Pipeline:
    FinalizeProfile
      → ProgramGenerationOrchestrator (AI → deterministic fallback)
      → ProgramRepository
      → ProgramHtmlService (renderer)
      → ProgramDeliveryService (Telegram document)

Pipeline-сервис возвращает безопасный user-facing результат; технические
детали уходят в логи и алерты администратору. Telegram handler не содержит
бизнес-логики и лишь транслирует результат пользователю.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum

from src.application.notifications.program_alerts import (
    ProgramAlert,
    ProgramAlertService,
)
from src.application.programs.orchestrator import ProgramGenerationOrchestrator
from src.application.programs.telegram_delivery import ProgramDeliveryService
from src.domain.enums import ProgramDeliveryStatus
from src.domain.program import WorkoutProgram
from src.errors import (
    HtmlRenderError,
    ProgramDeliveryError,
    ProgramGenerationError,
    ProgramPersistenceError,
    WorkoutBotError,
)

logger = logging.getLogger(__name__)


class PipelineOutcome(StrEnum):
    DELIVERED = "delivered"
    GENERATION_FAILED = "generation_failed"
    RENDER_FAILED = "render_failed"
    DELIVERY_FAILED = "delivery_failed"


@dataclass
class PipelineResult:
    outcome: PipelineOutcome
    program: WorkoutProgram | None = None
    fallback_used: bool = False
    reused_existing: bool = False
    user_message: str = ""


class ProgramPipelineService:
    def __init__(
        self,
        *,
        orchestrator: ProgramGenerationOrchestrator,
        delivery_service: ProgramDeliveryService | None,
        alert_service: ProgramAlertService | None = None,
    ) -> None:
        self._orchestrator = orchestrator
        self._delivery = delivery_service
        self._alerts = alert_service

    async def run_for_user(
        self, *, profile_id: str, chat_id: str | None, reuse_existing: bool = True
    ) -> PipelineResult:
        """Полный e2e pipeline. Ошибки не пробрасываются наружу — только результат."""
        # --- Generation --------------------------------------------------------
        try:
            result = await self._orchestrator.generate(
                profile_id, reuse_existing=reuse_existing
            )
        except (ProgramGenerationError, ProgramPersistenceError) as exc:
            logger.error(
                "event=pipeline_generation_failed",
                extra={"profile_id": profile_id, "error_type": exc.__class__.__name__},
            )
            await self._notify(
                stage="generation",
                profile_id=profile_id,
                program_id=None,
                exception_type=exc.__class__.__name__,
                message=str(exc),
            )
            return PipelineResult(
                outcome=PipelineOutcome.GENERATION_FAILED,
                user_message=(
                    "Не удалось автоматически сформировать программу. "
                    "Мы получили уведомление об ошибке."
                ),
            )
        except WorkoutBotError as exc:
            logger.error(
                "event=pipeline_generation_failed",
                extra={"profile_id": profile_id, "error_type": exc.__class__.__name__},
            )
            await self._notify(
                stage="generation",
                profile_id=profile_id,
                program_id=None,
                exception_type=exc.__class__.__name__,
                message=str(exc),
            )
            return PipelineResult(
                outcome=PipelineOutcome.GENERATION_FAILED,
                user_message=(
                    "Не удалось автоматически сформировать программу. "
                    "Мы получили уведомление об ошибке."
                ),
            )

        program = result.program

        # --- Delivery (render inside) ------------------------------------------
        if self._delivery is None or chat_id is None:
            return PipelineResult(
                outcome=PipelineOutcome.DELIVERED,
                program=program,
                fallback_used=result.fallback_used,
                reused_existing=result.reused_existing,
                user_message="Ваша программа тренировок сформирована.",
            )

        try:
            await self._delivery.deliver(program=program, chat_id=chat_id)
            return PipelineResult(
                outcome=PipelineOutcome.DELIVERED,
                program=program,
                fallback_used=result.fallback_used,
                reused_existing=result.reused_existing,
                user_message="Ваша персональная программа тренировок готова.",
            )
        except HtmlRenderError:
            return PipelineResult(
                outcome=PipelineOutcome.RENDER_FAILED,
                program=program,
                fallback_used=result.fallback_used,
                reused_existing=result.reused_existing,
                user_message=(
                    "Программа сформирована, но возникла проблема с подготовкой файла. "
                    "Мы получили уведомление об ошибке."
                ),
            )
        except ProgramDeliveryError:
            return PipelineResult(
                outcome=PipelineOutcome.DELIVERY_FAILED,
                program=program,
                fallback_used=result.fallback_used,
                reused_existing=result.reused_existing,
                user_message=(
                    "Программа сформирована, но возникла проблема с отправкой файла. "
                    "Мы попробуем отправить её повторно."
                ),
            )

    async def _notify(
        self,
        *,
        stage: str,
        profile_id: str | None,
        program_id: str | None,
        exception_type: str,
        message: str,
    ) -> None:
        if self._alerts is None:
            return
        await self._alerts.notify(
            ProgramAlert(
                stage=stage,
                profile_id=profile_id,
                program_id=program_id,
                exception_type=exception_type,
                message=message,
            )
        )


def delivery_status_text(status: ProgramDeliveryStatus) -> str:
    return {
        ProgramDeliveryStatus.PENDING: "ожидает отправки",
        ProgramDeliveryStatus.SENDING: "отправляется",
        ProgramDeliveryStatus.SENT: "отправлена",
        ProgramDeliveryStatus.FAILED: "ошибка отправки",
    }[status]
