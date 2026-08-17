"""Финализация анкеты: идемпотентное сохранение профиля.

Повторное нажатие кнопки подтверждения не создаёт дубликат:
если профиль уже подтверждён и сохранён — возвращается существующий результат.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from src.domain.consents import ConsentRecord
from src.domain.enums import CompletionStatus, ConsentScope
from src.domain.profile import FitnessProfile
from src.infrastructure.persistence.profile_repository import ProfileRepository


@dataclass
class FinalizationResult:
    profile: FitnessProfile
    already_finalized: bool


class ProfileFinalizationService:
    def __init__(self, repository: ProfileRepository) -> None:
        self._repository = repository

    async def finalize(self, profile: FitnessProfile) -> FinalizationResult:
        # Идемпотентность: уже подтверждён и сохранён → вернуть существующий.
        if (
            profile.profile_id
            and profile.questionnaire.completion_status is CompletionStatus.CONFIRMED
            and await self._repository.exists(profile.profile_id)
        ):
            return FinalizationResult(profile=profile, already_finalized=True)

        if not profile.profile_id:
            profile.profile_id = uuid.uuid4().hex
        if not profile.display_number:
            profile.display_number = await self._repository.next_display_number()

        profile.questionnaire.completed = True
        profile.questionnaire.completion_status = CompletionStatus.CONFIRMED
        profile.review.client_summary_confirmed = True

        # Согласия фиксируются явно: scope + timestamp + версия документа + источник.
        granted_scopes = {c.scope for c in profile.consents}
        for scope in (
            ConsentScope.DATA_PROCESSING,
            ConsentScope.HEALTH_INFORMATION,
            ConsentScope.ACCURACY,
        ):
            if scope not in granted_scopes:
                profile.consents.append(
                    ConsentRecord(scope=scope, source="telegram_review_confirm")
                )

        profile.touch()
        await self._repository.save(profile)
        return FinalizationResult(profile=profile, already_finalized=False)
