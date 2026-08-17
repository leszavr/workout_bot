"""Уведомление администратора о новой анкете.

Статус доставки хранится явно в профиле (``admin_notification_status``):
pending → sent | failed. Ошибка отправки не приводит к ложному сообщению
об успехе пользователю.
"""
from __future__ import annotations

import logging
from typing import Awaitable, Callable

from src.domain.profile import FitnessProfile
from src.errors import NotificationError
from src.infrastructure.persistence.profile_repository import ProfileRepository

logger = logging.getLogger(__name__)

# Транспортно-независимый отправитель: принимает профиль, бросает NotificationError при сбое.
AdminSender = Callable[[FitnessProfile], Awaitable[None]]


class AdminNotificationService:
    def __init__(self, repository: ProfileRepository, sender: AdminSender | None) -> None:
        self._repository = repository
        self._sender = sender

    async def notify(self, profile: FitnessProfile) -> bool:
        """Возвращает True, если уведомление доставлено (или отправитель не настроен)."""
        if self._sender is None:
            return True
        try:
            await self._sender(profile)
        except NotificationError:
            logger.warning(
                "admin_notification_failed",
                extra={"profile_id": profile.profile_id, "event": "admin_notification"},
            )
            profile.admin_notification_status = "failed"
            self._persist_status(profile)
            return False
        profile.admin_notification_status = "sent"
        self._persist_status(profile)
        return True

    def _persist_status(self, profile: FitnessProfile) -> None:
        try:
            self._repository.save(profile)
        except Exception:  # noqa: BLE001 — статус доставки не должен ронять сценарий
            logger.error(
                "admin_notification_status_persist_failed",
                extra={"profile_id": profile.profile_id},
            )
