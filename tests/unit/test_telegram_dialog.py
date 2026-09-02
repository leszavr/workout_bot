"""Диалог Telegram-анкеты на стороне Backend.

Проверяется поведение, которое раньше жило в хендлерах Gateway и не имело
собственных тестов: идемпотентность шага, переходы по анкете, правка из сводки,
финализация с уведомлением администратора и приём фотографии.

Без БД: репозиторий сессий и профилей — фейки, повторяющие контракт. Свойства,
требующие PostgreSQL (уникальность сессии, конкурентные обновления), проверяются
в integration-тестах.
"""
from __future__ import annotations

import pytest

from src.application.profiles.finalization import ProfileFinalizationService
from src.application.questionnaire.service import QuestionnaireService
from src.application.telegram.dialog import (
    ACTION_FINAL_CONFIRM,
    ACTION_RESTART,
    ACTION_RESUME,
    ACTION_REVIEW_CONFIRM,
    ACTION_REVIEW_EDIT,
    ACTION_SKIP,
    ACTION_START,
    POSITION_CONFIRM,
    POSITION_REVIEW,
    PREFIX_EDIT_QUESTION,
    TelegramDialogService,
)
from src.domain.enums import CompletionStatus
from src.domain.profile import FitnessProfile
from src.domain.telegram_contract import (
    TelegramMessageKind,
    TelegramUpdateKind,
    TelegramUpdateRequest,
)
from src.errors import ProfilePersistenceError
from src.infrastructure.persistence.postgres.telegram_session_repository import (
    TelegramSession,
)

USER = "555"


class FakeSessions:
    def __init__(self) -> None:
        self.state: TelegramSession | None = None
        self.deletes = 0

    async def get(self, telegram_user_id: str):
        return self.state

    async def save(self, session: TelegramSession) -> None:
        self.state = session

    async def delete(self, telegram_user_id: str) -> None:
        self.deletes += 1
        self.state = None


class FakeProfiles:
    def __init__(self, *, fail: bool = False) -> None:
        self.saved: list[FitnessProfile] = []
        self.fail = fail

    async def exists(self, profile_id: str) -> bool:
        return any(p.profile_id == profile_id for p in self.saved)

    async def next_display_number(self) -> str:
        return "REQ-20260902-00001"

    async def save(self, profile: FitnessProfile) -> FitnessProfile:
        if self.fail:
            raise ProfilePersistenceError("db down")
        self.saved.append(profile)
        return profile


class FakePhotoStorage:
    def __init__(self) -> None:
        self.saved: list[tuple[str, int]] = []

    def save_photo(self, profile_id, file_id, content, extension):
        self.saved.append((file_id, len(content)))
        return f"equipment/{profile_id}/{file_id}{extension}"

    def count_photos(self, profile_id):  # pragma: no cover — не используется
        raise NotImplementedError

    def delete_profile_files(self, profile_id):  # pragma: no cover
        raise NotImplementedError


@pytest.fixture
def dialog():
    sessions = FakeSessions()
    profiles = FakeProfiles()
    storage = FakePhotoStorage()
    service = TelegramDialogService(
        sessions=sessions,
        questionnaire=QuestionnaireService(storage),
        finalization=ProfileFinalizationService(profiles),
        profiles=profiles,
        admin_chat_id="999",
    )
    return service, sessions, profiles, storage


def _request(kind: TelegramUpdateKind, payload: str, update_id: int = 1):
    return TelegramUpdateRequest(
        update_id=update_id,
        telegram_user_id=USER,
        chat_id=USER,
        username="ivan",
        kind=kind,
        payload=payload,
    )


async def _start(service, update_id: int = 1):
    await service.handle(_request(TelegramUpdateKind.COMMAND, "/start", update_id))
    return await service.handle(
        _request(TelegramUpdateKind.CALLBACK, ACTION_START, update_id + 1)
    )


class TestEntryPoints:
    async def test_start_offers_questionnaire(self, dialog):
        service, _, _, _ = dialog
        response = await service.handle(
            _request(TelegramUpdateKind.COMMAND, "/start")
        )
        actions = [
            button.action
            for row in response.view.messages[0].buttons
            for button in row
        ]
        assert ACTION_START in actions

    async def test_start_with_unfinished_questionnaire_offers_resume(self, dialog):
        service, _, _, _ = dialog
        await _start(service)
        response = await service.handle(
            _request(TelegramUpdateKind.COMMAND, "/start", 10)
        )
        actions = [
            button.action
            for row in response.view.messages[0].buttons
            for button in row
        ]
        assert ACTION_RESUME in actions
        assert ACTION_RESTART in actions

    async def test_first_question_replaces_entry_screen(self, dialog):
        """Входной экран заменяется вопросом: иначе кнопка «Начать» остаётся."""
        service, _, _, _ = dialog
        response = await _start(service)
        assert response.view.messages[0].delete_current is True

    async def test_cancel_clears_session(self, dialog):
        service, sessions, _, _ = dialog
        await _start(service)
        await service.handle(_request(TelegramUpdateKind.COMMAND, "/cancel", 20))
        assert sessions.deletes == 1

    async def test_restart_discards_previous_draft(self, dialog):
        """Ответы двух анкет не должны смешиваться."""
        service, sessions, _, _ = dialog
        await _start(service)
        await service.handle(_request(TelegramUpdateKind.TEXT, "Иван", 3))
        before = sessions.state.draft["client"]["name"]

        await service.handle(_request(TelegramUpdateKind.CALLBACK, ACTION_RESTART, 4))

        assert before == "Иван"
        assert sessions.state.draft["client"]["name"] is None


class TestQuestionnaireFlow:
    async def test_answer_advances_position(self, dialog):
        service, sessions, _, _ = dialog
        await _start(service)
        assert sessions.state.position == "q01_name"

        await service.handle(_request(TelegramUpdateKind.TEXT, "Иван", 3))

        assert sessions.state.position == "q02_age"
        assert sessions.state.draft["client"]["name"] == "Иван"

    async def test_validation_error_keeps_position(self, dialog):
        service, sessions, _, _ = dialog
        await _start(service)
        response = await service.handle(_request(TelegramUpdateKind.TEXT, "И", 3))

        assert sessions.state.position == "q01_name"
        assert "2 до 50" in response.view.messages[0].text

    async def test_draft_exists_from_first_answer(self, dialog):
        """Прерванная анкета не теряется: черновик пишется сразу."""
        service, sessions, _, _ = dialog
        await _start(service)
        assert sessions.state.draft is not None

    async def test_skip_is_rejected_for_required_question(self, dialog):
        service, sessions, _, _ = dialog
        await _start(service)
        response = await service.handle(
            _request(TelegramUpdateKind.CALLBACK, ACTION_SKIP, 3)
        )
        assert sessions.state.position == "q01_name"
        assert response.view.toast is not None

    async def test_unknown_action_gets_an_answer(self, dialog):
        """Кнопка старого сообщения: молчание оставило бы её с индикатором."""
        service, _, _, _ = dialog
        await _start(service)
        response = await service.handle(
            _request(TelegramUpdateKind.CALLBACK, "obsolete_action", 3)
        )
        assert response.view.toast is not None

    async def test_text_before_start_shows_entry_screen(self, dialog):
        service, _, _, _ = dialog
        response = await service.handle(_request(TelegramUpdateKind.TEXT, "привет"))
        actions = [
            button.action
            for row in response.view.messages[0].buttons
            for button in row
        ]
        assert ACTION_START in actions


class TestIdempotency:
    async def test_duplicate_update_returns_same_view(self, dialog):
        service, sessions, _, _ = dialog
        await _start(service)
        first = await service.handle(_request(TelegramUpdateKind.TEXT, "Иван", 3))
        position_after_first = sessions.state.position

        second = await service.handle(_request(TelegramUpdateKind.TEXT, "Иван", 3))

        assert second.duplicate is True
        assert second.view.model_dump() == first.view.model_dump()
        # Главное: анкета не продвинулась на второй шаг от одного ответа.
        assert sessions.state.position == position_after_first

    async def test_different_update_id_advances(self, dialog):
        service, sessions, _, _ = dialog
        await _start(service)
        await service.handle(_request(TelegramUpdateKind.TEXT, "Иван", 3))
        await service.handle(_request(TelegramUpdateKind.TEXT, "30", 4))
        assert sessions.state.position == "q03_sex"


class TestEditing:
    async def _reach_review(self, service, sessions):
        await _start(service)
        profile = FitnessProfile(profile_id="p-1")
        profile.client.name = "Иван"
        sessions.state.draft = profile.model_dump(mode="json")
        sessions.state.position = POSITION_REVIEW

    async def test_review_confirm_moves_to_confirmation(self, dialog):
        service, sessions, _, _ = dialog
        await self._reach_review(service, sessions)
        response = await service.handle(
            _request(TelegramUpdateKind.CALLBACK, ACTION_REVIEW_CONFIRM, 30)
        )
        assert sessions.state.position == POSITION_CONFIRM
        assert response.view.messages[0].kind is TelegramMessageKind.EDIT

    async def test_edit_returns_to_review_not_next_question(self, dialog):
        """Правка одного ответа не должна прогонять анкету заново."""
        service, sessions, _, _ = dialog
        await self._reach_review(service, sessions)
        await service.handle(
            _request(
                TelegramUpdateKind.CALLBACK, f"{PREFIX_EDIT_QUESTION}q01_name", 31
            )
        )
        assert sessions.state.editing_question == "q01_name"
        assert sessions.state.position == "q01_name"

        await service.handle(_request(TelegramUpdateKind.TEXT, "Пётр", 32))

        assert sessions.state.position == POSITION_REVIEW
        assert sessions.state.editing_question is None
        assert sessions.state.draft["client"]["name"] == "Пётр"

    async def test_edit_sections_are_offered(self, dialog):
        service, sessions, _, _ = dialog
        await self._reach_review(service, sessions)
        response = await service.handle(
            _request(TelegramUpdateKind.CALLBACK, ACTION_REVIEW_EDIT, 33)
        )
        assert response.view.messages[0].buttons


class TestFinalization:
    async def _prepare_confirm(self, sessions):
        profile = FitnessProfile(profile_id="p-1")
        profile.source.bot_user_id = USER
        profile.client.name = "Иван"
        sessions.state = TelegramSession(
            telegram_user_id=USER,
            chat_id=USER,
            draft=profile.model_dump(mode="json"),
            position=POSITION_CONFIRM,
        )

    async def test_finalize_saves_profile(self, dialog):
        service, sessions, profiles, _ = dialog
        await self._prepare_confirm(sessions)

        response = await service.handle(
            _request(TelegramUpdateKind.CALLBACK, ACTION_FINAL_CONFIRM, 40)
        )

        assert response.finished is True
        assert response.profile_id == "p-1"
        assert profiles.saved
        assert (
            profiles.saved[0].questionnaire.completion_status
            is CompletionStatus.CONFIRMED
        )

    async def test_admin_notification_goes_through_gateway(self, dialog):
        """Backend в RU не имеет доступа к Bot API: сообщение отдаётся шлюзу."""
        service, sessions, _, _ = dialog
        await self._prepare_confirm(sessions)

        response = await service.handle(
            _request(TelegramUpdateKind.CALLBACK, ACTION_FINAL_CONFIRM, 40)
        )

        admin_messages = [m for m in response.view.messages if m.chat_id == "999"]
        assert len(admin_messages) == 2
        assert any(m.document is not None for m in admin_messages)

    async def test_repeated_confirm_does_not_notify_twice(self, dialog):
        service, sessions, _, _ = dialog
        await self._prepare_confirm(sessions)
        await service.handle(
            _request(TelegramUpdateKind.CALLBACK, ACTION_FINAL_CONFIRM, 40)
        )

        response = await service.handle(
            _request(TelegramUpdateKind.CALLBACK, ACTION_FINAL_CONFIRM, 41)
        )

        assert [m for m in response.view.messages if m.chat_id == "999"] == []

    async def test_persistence_failure_is_user_visible(self, dialog):
        service, sessions, _, storage = dialog
        broken = FakeProfiles(fail=True)
        service = TelegramDialogService(
            sessions=sessions,
            questionnaire=QuestionnaireService(storage),
            finalization=ProfileFinalizationService(broken),
            profiles=broken,
            admin_chat_id="999",
        )
        await self._prepare_confirm(sessions)

        response = await service.handle(
            _request(TelegramUpdateKind.CALLBACK, ACTION_FINAL_CONFIRM, 40)
        )

        assert response.finished is False
        assert response.view.toast is not None


class TestPhoto:
    async def test_photo_is_stored_and_advances(self, dialog):
        service, sessions, _, storage = dialog
        await _start(service)
        sessions.state.position = "q19_equipment_photos"

        response = await service.handle_photo(
            update_id=50,
            telegram_user_id=USER,
            chat_id=USER,
            file_id="AgACAgI",
            content=b"\xff\xd8\xffjpeg",
            extension=".jpg",
        )

        assert storage.saved == [("AgACAgI", 7)]
        assert response.view.messages

    async def test_photo_on_wrong_step_is_answered(self, dialog):
        """Пользователь отправил файл — реакция обязательна."""
        service, sessions, _, storage = dialog
        await _start(service)

        response = await service.handle_photo(
            update_id=51,
            telegram_user_id=USER,
            chat_id=USER,
            file_id="AgACAgI",
            content=b"\xff\xd8\xff",
            extension=".jpg",
        )

        assert storage.saved == []
        assert response.view.messages[0].text

    async def test_duplicate_photo_is_not_stored_twice(self, dialog):
        service, sessions, _, storage = dialog
        await _start(service)
        sessions.state.position = "q19_equipment_photos"

        for _ in range(2):
            await service.handle_photo(
                update_id=52,
                telegram_user_id=USER,
                chat_id=USER,
                file_id="AgACAgI",
                content=b"\xff\xd8\xff",
                extension=".jpg",
            )

        assert len(storage.saved) == 1


class TestNoPersonalDataLeavesRu:
    async def test_view_contains_no_raw_draft(self, dialog):
        """Наружу уходит отрендеренный текст, а не структура ответов."""
        service, sessions, _, _ = dialog
        await _start(service)
        response = await service.handle(_request(TelegramUpdateKind.TEXT, "Иван", 3))

        payload = response.model_dump(mode="json")
        assert "draft" not in payload
        assert "profile" not in payload

    async def test_incompatible_draft_does_not_crash(self, dialog):
        """Черновик от старой схемы анкеты не должен ронять диалог."""
        service, sessions, _, _ = dialog
        await _start(service)
        sessions.state.draft = {"unexpected": "shape"}

        response = await service.handle(_request(TelegramUpdateKind.TEXT, "Иван", 60))

        assert response.view.messages
