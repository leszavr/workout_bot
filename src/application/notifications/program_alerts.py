"""Технические уведомления администратору об ошибках pipeline (Stage 5).

При окончательной ошибке генерации/рендера/доставки администратор получает:
profile/program ID, stage, generator, fallback-статус, тип исключения,
безопасное сообщение и timestamp. Секреты и API-ключи сюда не попадают.

Форматирование — в application-слое; транспорт (Telegram) подключается
через callable, как в AdminNotificationService.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)


@dataclass
class ProgramAlert:
    stage: str  # generation | html_render | delivery
    profile_id: str | None = None
    program_id: str | None = None
    generator: str | None = None
    fallback_status: str | None = None
    exception_type: str | None = None
    message: str = ""
    request_id: str | None = None
    timestamp: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc).replace(microsecond=0)
    )


def format_alert(alert: ProgramAlert) -> str:
    stage_labels = {
        "generation": "генерация программы",
        "html_render": "рендеринг HTML",
        "delivery": "доставка программы",
    }
    lines = [
        "⚠️ Ошибка pipeline программы тренировок",
        "",
        f"Этап: {stage_labels.get(alert.stage, alert.stage)}",
        f"Профиль: {alert.profile_id or '—'}",
        f"Программа: {alert.program_id or '—'}",
        f"Генератор: {alert.generator or '—'}",
        f"Fallback: {alert.fallback_status or '—'}",
        f"Исключение: {alert.exception_type or '—'}",
    ]
    if alert.message:
        lines.append(f"Сообщение: {alert.message[:300]}")
    if alert.request_id:
        lines.append(f"Request ID: {alert.request_id}")
    lines.append(f"Время: {alert.timestamp.isoformat()}")
    return "\n".join(lines)


AlertSender = Callable[[ProgramAlert], Awaitable[None]]


class ProgramAlertService:
    def __init__(self, sender: AlertSender | None = None) -> None:
        self._sender = sender

    async def notify(self, alert: ProgramAlert) -> bool:
        """Отправляет алерт администратору. Возвращает True при успехе (или без sender)."""
        text = format_alert(alert)
        logger.error(
            "event=pipeline_alert stage=%s profile_id=%s error_type=%s message=%s",
            alert.stage,
            alert.profile_id,
            alert.exception_type,
            alert.message[:200],
        )
        if self._sender is None:
            return True
        try:
            await self._sender(alert)
            return True
        except Exception:  # noqa: BLE001 — алерт не должен ронять основной сценарий
            logger.exception(
                "event=pipeline_alert_delivery_failed",
                extra={"stage": alert.stage, "profile_id": alert.profile_id},
            )
            return False
