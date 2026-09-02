"""Integration-тесты сетевой границы Gateway (реальная PostgreSQL).

Проверяются свойства, которые нельзя доказать на фейках:

- анкета проходится целиком через HTTP-контракт, без прямого доступа Gateway к БД;
- ответы и позиция диалога лежат в PostgreSQL (RU), а не в состоянии Gateway;
- идемпотентность шага на уровне базы: повтор `update_id` не даёт второго шага;
- у пользователя одна сессия: параллельные обновления не раздваивают анкету;
- очередь доставки идемпотентна, а два экземпляра Gateway не отправят файл дважды;
- обрыв связи не создаёт дубликатов профиля, генерации и доставки.

Клиент HTTP — `httpx.ASGITransport` поверх приложения Backend: сеть не нужна, но
проходит весь путь через реальные роутер, аутентификацию и сервисы.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.backend.main import create_app
from src.application.questionnaire.questions import QUESTIONS_BY_ID, QuestionKind
from src.domain.enums import ProgramDeliveryStatus
from src.domain.telegram_contract import (
    TELEGRAM_CONTRACT_VERSION,
    TelegramUpdateKind,
)
from src.infrastructure.components.heartbeat_client import SERVICE_TOKEN_HEADER
from src.infrastructure.config import DATABASE_URL, INTERNAL_SERVICE_TOKEN
from src.infrastructure.persistence.postgres.delivery_repository import (
    ProgramDeliveryRecord,
    ProgramDeliveryRepository,
)
from src.infrastructure.persistence.postgres.models import (
    ConsentRow,
    GenerationJobRow,
    ProfileRow,
    ProgramDeliveryRow,
    TelegramSessionRow,
    UserRow,
    WorkoutProgramRow,
)
from src.infrastructure.persistence.postgres.telegram_session_repository import (
    TelegramSessionRepository,
)

pytestmark = pytest.mark.skipif(
    not DATABASE_URL or not INTERNAL_SERVICE_TOKEN,
    reason="DATABASE_URL and INTERNAL_SERVICE_TOKEN are required",
)

TELEGRAM_USER = "990901"
CHAT = "990901"


@pytest.fixture
async def sessions():
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.fixture
async def second_sessions():
    """Независимый engine: DB-level взаимное исключение, а не общий пул."""
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.fixture(autouse=True)
async def cleanup(sessions):
    """Удаляет только свои записи: база общая с другими тестами."""

    async def _purge() -> None:
        async with sessions() as session:
            async with session.begin():
                await session.execute(
                    delete(TelegramSessionRow).where(
                        TelegramSessionRow.telegram_user_id == TELEGRAM_USER
                    )
                )
                user_ids = (
                    await session.execute(
                        select(UserRow.id).where(
                            UserRow.telegram_user_id == TELEGRAM_USER
                        )
                    )
                ).scalars().all()
                if not user_ids:
                    return
                profile_ids = (
                    await session.execute(
                        select(ProfileRow.profile_id).where(
                            ProfileRow.user_id.in_(user_ids)
                        )
                    )
                ).scalars().all()
                if profile_ids:
                    await session.execute(
                        delete(GenerationJobRow).where(
                            GenerationJobRow.profile_id.in_(profile_ids)
                        )
                    )
                    await session.execute(
                        delete(ProgramDeliveryRow).where(
                            ProgramDeliveryRow.profile_id.in_(profile_ids)
                        )
                    )
                    await session.execute(
                        delete(WorkoutProgramRow).where(
                            WorkoutProgramRow.profile_id.in_(profile_ids)
                        )
                    )
                await session.execute(
                    delete(ConsentRow).where(ConsentRow.user_id.in_(user_ids))
                )
                await session.execute(
                    delete(ProfileRow).where(ProfileRow.user_id.in_(user_ids))
                )
                await session.execute(delete(UserRow).where(UserRow.id.in_(user_ids)))

    await _purge()
    yield
    await _purge()


@pytest.fixture(autouse=True)
async def fresh_backend_engine():
    """Сбрасывает глобальный engine Backend перед каждым тестом.

    `get_session_factory` кэширует engine на процесс, а pytest-asyncio даёт
    каждому тесту свой event loop. Соединение, открытое в прошлом loop, во
    втором тесте падает с «Event loop is closed» — это артефакт кэша, а не
    поведение приложения.
    """
    from src.infrastructure.persistence.postgres.db import dispose_engine

    await dispose_engine()
    yield
    await dispose_engine()


@pytest.fixture
async def client(monkeypatch):
    """HTTP-клиент к Backend. Автогенерация отключена: её проверяют отдельно."""
    import apps.backend.api.v1.telegram_dependencies as deps

    monkeypatch.setattr(deps, "AUTO_GENERATE_PROGRAM_AFTER_FINALIZE", False)
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://backend.test",
        headers={SERVICE_TOKEN_HEADER: INTERNAL_SERVICE_TOKEN},
        timeout=30,
    ) as http:
        yield http


async def _update(
    client: httpx.AsyncClient,
    *,
    update_id: int,
    kind: TelegramUpdateKind,
    payload: str,
) -> dict:
    response = await client.post(
        "/internal/v1/telegram/updates",
        json={
            "update_id": update_id,
            "telegram_user_id": TELEGRAM_USER,
            "chat_id": CHAT,
            "username": "qa_boundary",
            "kind": kind.value,
            "payload": payload,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


ANSWERS = {
    "q01_name": "Иван",
    "q02_age": "30",
    "q04_height": "180",
    "q05_weight": "80",
    "q09_desired_result": "Набрать массу",
    "q25_limitation_categories": "нет",
    "q27_movements_to_avoid": "прыжки",
    "q28_doctor_recommendations": "нет",
}


async def _run_questionnaire(client: httpx.AsyncClient, sessions) -> str:
    """Проходит анкету до сводки, возвращая позицию диалога."""
    repository = TelegramSessionRepository(sessions)
    update_id = 1
    await _update(
        client, update_id=update_id, kind=TelegramUpdateKind.COMMAND, payload="/start"
    )
    update_id += 1
    await _update(
        client, update_id=update_id, kind=TelegramUpdateKind.CALLBACK, payload="start_qa"
    )

    for _ in range(80):
        session = await repository.get(TELEGRAM_USER)
        assert session is not None
        position = session.position
        if position not in QUESTIONS_BY_ID:
            return position

        question = QUESTIONS_BY_ID[position]
        update_id += 1
        if question.kind is QuestionKind.CHOICE:
            await _update(
                client,
                update_id=update_id,
                kind=TelegramUpdateKind.CALLBACK,
                payload=question.options[0].callback_data,
            )
        elif question.kind is QuestionKind.MULTISELECT:
            await _update(
                client,
                update_id=update_id,
                kind=TelegramUpdateKind.CALLBACK,
                payload="day_mon",
            )
            update_id += 1
            await _update(
                client,
                update_id=update_id,
                kind=TelegramUpdateKind.CALLBACK,
                payload="days_done",
            )
        elif question.kind is QuestionKind.PHOTOS:
            await _update(
                client,
                update_id=update_id,
                kind=TelegramUpdateKind.CALLBACK,
                payload="skip_question",
            )
        else:
            answer = ANSWERS.get(position)
            if answer is None:
                await _update(
                    client,
                    update_id=update_id,
                    kind=TelegramUpdateKind.CALLBACK,
                    payload="skip_question",
                )
            else:
                await _update(
                    client,
                    update_id=update_id,
                    kind=TelegramUpdateKind.TEXT,
                    payload=answer,
                )
    raise AssertionError("анкета не дошла до сводки за 80 шагов")


class TestContractEndpoint:
    async def test_version_is_exposed(self, client):
        response = await client.get("/internal/v1/telegram/contract")
        assert response.status_code == 200
        assert response.json()["contract_version"] == TELEGRAM_CONTRACT_VERSION

    async def test_unauthenticated_request_is_rejected(self, client):
        response = await client.get(
            "/internal/v1/telegram/contract",
            headers={SERVICE_TOKEN_HEADER: "wrong-token"},
        )
        assert response.status_code == 401

    async def test_missing_token_is_rejected(self, client):
        response = await client.post(
            "/internal/v1/telegram/updates",
            headers={SERVICE_TOKEN_HEADER: ""},
            json={
                "update_id": 1,
                "telegram_user_id": TELEGRAM_USER,
                "chat_id": CHAT,
                "kind": "text",
                "payload": "Иван",
            },
        )
        assert response.status_code == 401


class TestQuestionnaireOverHttp:
    async def test_full_questionnaire_reaches_review(self, client, sessions):
        position = await _run_questionnaire(client, sessions)
        assert position == "review"

    async def test_answers_are_stored_in_postgres(self, client, sessions):
        """Ответы живут в RU: их читает репозиторий сессий, а не Gateway."""
        await _update(
            client, update_id=1, kind=TelegramUpdateKind.COMMAND, payload="/start"
        )
        await _update(
            client, update_id=2, kind=TelegramUpdateKind.CALLBACK, payload="start_qa"
        )
        await _update(
            client, update_id=3, kind=TelegramUpdateKind.TEXT, payload="Иван"
        )

        session = await TelegramSessionRepository(sessions).get(TELEGRAM_USER)
        assert session is not None
        assert session.draft["client"]["name"] == "Иван"
        assert session.position == "q02_age"

    async def test_draft_survives_between_requests(self, client, sessions):
        """Каждое событие — отдельный HTTP-запрос: состояние обязано быть в БД."""
        await _update(
            client, update_id=1, kind=TelegramUpdateKind.COMMAND, payload="/start"
        )
        await _update(
            client, update_id=2, kind=TelegramUpdateKind.CALLBACK, payload="start_qa"
        )
        await _update(client, update_id=3, kind=TelegramUpdateKind.TEXT, payload="Иван")
        await _update(client, update_id=4, kind=TelegramUpdateKind.TEXT, payload="30")

        session = await TelegramSessionRepository(sessions).get(TELEGRAM_USER)
        assert session.draft["client"]["name"] == "Иван"
        assert session.draft["client"]["age_years"] == 30


class TestIdempotencyOnPostgres:
    async def test_duplicate_update_does_not_advance(self, client, sessions):
        await _update(
            client, update_id=1, kind=TelegramUpdateKind.COMMAND, payload="/start"
        )
        await _update(
            client, update_id=2, kind=TelegramUpdateKind.CALLBACK, payload="start_qa"
        )
        first = await _update(
            client, update_id=3, kind=TelegramUpdateKind.TEXT, payload="Иван"
        )
        repository = TelegramSessionRepository(sessions)
        position = (await repository.get(TELEGRAM_USER)).position

        second = await _update(
            client, update_id=3, kind=TelegramUpdateKind.TEXT, payload="Иван"
        )

        assert second["duplicate"] is True
        assert second["view"] == first["view"]
        assert (await repository.get(TELEGRAM_USER)).position == position

    async def test_concurrent_updates_keep_single_session(self, client, sessions):
        """У пользователя один диалог: уникальность выражена ограничением БД."""
        await _update(
            client, update_id=1, kind=TelegramUpdateKind.COMMAND, payload="/start"
        )
        await _update(
            client, update_id=2, kind=TelegramUpdateKind.CALLBACK, payload="start_qa"
        )

        await asyncio.gather(
            _update(client, update_id=10, kind=TelegramUpdateKind.TEXT, payload="Иван"),
            _update(client, update_id=11, kind=TelegramUpdateKind.TEXT, payload="Пётр"),
        )

        async with sessions() as session:
            rows = (
                await session.execute(
                    select(TelegramSessionRow).where(
                        TelegramSessionRow.telegram_user_id == TELEGRAM_USER
                    )
                )
            ).scalars().all()
        assert len(rows) == 1


class TestDeliveryQueue:
    async def _record(self, sessions, program_id: str = "prog-boundary") -> int:
        repository = ProgramDeliveryRepository(sessions)
        record = await repository.create(
            ProgramDeliveryRecord(
                program_id=program_id,
                profile_id="profile-boundary",
                chat_id=CHAT,
                filename="program.html",
                status=ProgramDeliveryStatus.PENDING,
            )
        )
        return record.id

    async def test_claim_returns_pending_task(self, client, sessions):
        delivery_id = await self._record(sessions)
        try:
            response = await client.post(
                "/internal/v1/telegram/deliveries/claim",
                params={"owner": "gw-1", "limit": 5},
            )
            assert response.status_code == 200
            ids = [task["delivery_id"] for task in response.json()]
            assert delivery_id in ids
        finally:
            await self._cleanup_delivery(sessions, delivery_id)

    async def test_two_gateways_do_not_claim_the_same_task(
        self, client, sessions, second_sessions
    ):
        """Файл не должен уйти пользователю дважды."""
        delivery_id = await self._record(sessions)
        try:
            first, second = await asyncio.gather(
                ProgramDeliveryRepository(sessions).claim_for_send(
                    owner="gw-1", lease_seconds=60, limit=5
                ),
                ProgramDeliveryRepository(second_sessions).claim_for_send(
                    owner="gw-2", lease_seconds=60, limit=5
                ),
            )
            claimed = [
                record
                for batch in (first, second)
                for record in batch
                if record.id == delivery_id
            ]
            assert len(claimed) == 1
        finally:
            await self._cleanup_delivery(sessions, delivery_id)

    async def test_result_marks_delivery_sent(self, client, sessions):
        delivery_id = await self._record(sessions)
        try:
            await client.post(
                "/internal/v1/telegram/deliveries/claim",
                params={"owner": "gw-1", "limit": 5},
            )
            response = await client.post(
                f"/internal/v1/telegram/deliveries/{delivery_id}/result",
                json={"delivered": True, "message_id": 4242},
            )
            assert response.status_code == 200
            assert response.json()["status"] == "sent"

            record = await ProgramDeliveryRepository(sessions).get(delivery_id)
            assert record.status is ProgramDeliveryStatus.SENT
            assert record.sent_message_id == 4242
            assert record.lease_owner is None
        finally:
            await self._cleanup_delivery(sessions, delivery_id)

    async def test_failed_result_schedules_retry(self, client, sessions):
        delivery_id = await self._record(sessions)
        try:
            await client.post(
                "/internal/v1/telegram/deliveries/claim",
                params={"owner": "gw-1", "limit": 5},
            )
            response = await client.post(
                f"/internal/v1/telegram/deliveries/{delivery_id}/result",
                json={"delivered": False, "error": "telegram unavailable"},
            )
            body = response.json()
            assert body["status"] == "failed"
            assert body["retry_scheduled"] is True
            assert body["attempts"] == 1
        finally:
            await self._cleanup_delivery(sessions, delivery_id)

    async def test_document_requires_claimed_delivery(self, client, sessions):
        """Программа не отдаётся по незахваченной доставке."""
        delivery_id = await self._record(sessions)
        try:
            response = await client.get(
                f"/internal/v1/telegram/deliveries/{delivery_id}/document"
            )
            assert response.status_code == 409
        finally:
            await self._cleanup_delivery(sessions, delivery_id)

    async def test_unknown_delivery_result_is_404(self, client):
        response = await client.post(
            "/internal/v1/telegram/deliveries/99999999/result",
            json={"delivered": True, "message_id": 1},
        )
        assert response.status_code == 404

    @staticmethod
    async def _cleanup_delivery(sessions, delivery_id: int) -> None:
        async with sessions() as session:
            async with session.begin():
                await session.execute(
                    delete(ProgramDeliveryRow).where(
                        ProgramDeliveryRow.id == delivery_id
                    )
                )


class TestNoDuplicatesOnFailure:
    async def test_repeated_finalize_creates_one_profile(self, client, sessions):
        """Обрыв связи после финализации не создаёт вторую анкету."""
        await _run_questionnaire(client, sessions)
        await _update(
            client, update_id=500, kind=TelegramUpdateKind.CALLBACK, payload="review_confirm"
        )
        first = await _update(
            client, update_id=501, kind=TelegramUpdateKind.CALLBACK, payload="final_confirm"
        )
        # Тот же update_id — Telegram переотправил обновление.
        duplicate = await _update(
            client, update_id=501, kind=TelegramUpdateKind.CALLBACK, payload="final_confirm"
        )
        # Другой update_id — пользователь нажал кнопку второй раз.
        repeated = await _update(
            client, update_id=502, kind=TelegramUpdateKind.CALLBACK, payload="final_confirm"
        )

        assert first["profile_id"] == duplicate["profile_id"] == repeated["profile_id"]
        assert duplicate["duplicate"] is True

        async with sessions() as session:
            user_ids = (
                await session.execute(
                    select(UserRow.id).where(
                        UserRow.telegram_user_id == TELEGRAM_USER
                    )
                )
            ).scalars().all()
            profiles = (
                await session.execute(
                    select(ProfileRow.profile_id).where(
                        ProfileRow.user_id.in_(user_ids)
                    )
                )
            ).scalars().all()
        assert len(profiles) == 1

    async def test_enqueue_is_idempotent(self, client, sessions):
        """Повторная постановка того же файла не даёт второй отправки."""
        from apps.backend.api.v1.telegram_dependencies import (
            build_delivery_queue_service,
        )

        queue = build_delivery_queue_service()
        first = await queue.enqueue(
            program_id="prog-idem",
            profile_id="profile-boundary",
            version=1,
            chat_id=CHAT,
        )
        second = await queue.enqueue(
            program_id="prog-idem",
            profile_id="profile-boundary",
            version=1,
            chat_id=CHAT,
        )
        try:
            assert first.id == second.id
            async with sessions() as session:
                rows = (
                    await session.execute(
                        select(ProgramDeliveryRow.id).where(
                            ProgramDeliveryRow.program_id == "prog-idem"
                        )
                    )
                ).scalars().all()
            assert len(rows) == 1
        finally:
            async with sessions() as session:
                async with session.begin():
                    await session.execute(
                        delete(ProgramDeliveryRow).where(
                            ProgramDeliveryRow.program_id == "prog-idem"
                        )
                    )
