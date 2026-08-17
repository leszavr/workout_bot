"""Общие зависимости и хелперы Telegram gateway.

Handlers получают готовые application-сервисы и не содержат бизнес-логики.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src.application.notifications.admin_notifier import AdminNotificationService
from src.application.profiles.finalization import ProfileFinalizationService
from src.application.questionnaire.questions import QUESTIONS_BY_ID
from src.application.questionnaire.service import QuestionPrompt, QuestionnaireService
from src.domain.profile import FitnessProfile
from src.infrastructure.config import (
    DATABASE_URL,
    DATA_DIR,
    MAX_PHOTOS,
    MAX_PHOTO_SIZE_MB,
    PHOTOS_DIR,
    PROFILES_DIR,
)
from src.infrastructure.files.storage import LocalFileStorage
from src.infrastructure.persistence.profile_repository import (
    FileProfileRepository,
    ProfileRepository,
)


def create_profile_repository() -> ProfileRepository:
    """PostgreSQL, если задан DATABASE_URL; иначе файловое хранилище (dev/test)."""
    if DATABASE_URL:
        from src.infrastructure.persistence.postgres.db import get_session_factory
        from src.infrastructure.persistence.postgres.profile_repository import (
            PostgresProfileRepository,
        )

        return PostgresProfileRepository(get_session_factory())
    return FileProfileRepository(PROFILES_DIR, DATA_DIR / "counter.json")


@dataclass(frozen=True)
class Services:
    questionnaire: QuestionnaireService
    finalization: ProfileFinalizationService
    repository: ProfileRepository


@lru_cache
def get_services() -> Services:
    repository = create_profile_repository()
    file_storage = LocalFileStorage(PHOTOS_DIR, max_files=MAX_PHOTOS, max_size_mb=MAX_PHOTO_SIZE_MB)
    return Services(
        questionnaire=QuestionnaireService(file_storage),
        finalization=ProfileFinalizationService(repository),
        repository=repository,
    )


def get_admin_notification_service(repository: ProfileRepository, sender) -> AdminNotificationService:
    return AdminNotificationService(repository, sender)


# --- Работа с профилем в FSM ---------------------------------------------------

async def load_profile(state: FSMContext) -> FitnessProfile | None:
    data = await state.get_data()
    raw = data.get("profile")
    if raw is None:
        return None
    if isinstance(raw, FitnessProfile):
        return raw
    return FitnessProfile.model_validate(raw)


async def store_profile(state: FSMContext, profile: FitnessProfile) -> None:
    await state.update_data(profile=profile.model_dump(mode="json"))


def state_to_question_id(current_state: object | None) -> str | None:
    if current_state is None:
        return None
    if hasattr(current_state, "state"):
        current_state = current_state.state
    current_state = str(current_state)
    question_id = current_state.rsplit(":", 1)[-1]
    return question_id if question_id in QUESTIONS_BY_ID else None


async def show_question(
    target: Message,
    service: QuestionnaireService,
    profile: FitnessProfile,
    question_id: str,
) -> None:
    """Отправляет вопрос с клавиатурой. Вся логика — в сервисе."""
    from apps.telegram_gateway.keyboards.inline import preferred_days_kb, question_kb
    from src.application.questionnaire.questions import QuestionKind

    prompt: QuestionPrompt = service.build_prompt(profile, question_id)
    if prompt.kind is QuestionKind.MULTISELECT:
        reply_markup = preferred_days_kb(prompt.selected_days)
    else:
        reply_markup = question_kb(QUESTIONS_BY_ID[question_id], prompt.skippable)
    await target.answer(prompt.text, reply_markup=reply_markup)
