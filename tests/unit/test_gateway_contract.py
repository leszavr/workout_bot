"""Контракт Backend ↔ Telegram Gateway (сетевая граница).

Проверяется то, что делает Gateway независимой единицей развёртывания, а не
косметически отделённой: клиент, аутентификация, идемпотентность, поведение при
недоступном Backend и совместимость версий контракта.

Gateway здесь не поднимается как процесс: тестируются его составные части
(клиент, рендер вида, поллер доставки) на фейках Bot API и Backend.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from apps.telegram_gateway import delivery_poller, view_renderer
from apps.telegram_gateway.backend_client import (
    BackendClient,
    BackendRejectedError,
    BackendUnavailableError,
)
from src.domain.components import BACKEND_SUPPORTED_CONTRACTS
from src.domain.telegram_contract import (
    TELEGRAM_CONTRACT_VERSION,
    TelegramButton,
    TelegramDeliveryResult,
    TelegramDeliveryTask,
    TelegramDocument,
    TelegramMessage,
    TelegramMessageKind,
    TelegramUpdateKind,
    TelegramUpdateRequest,
    TelegramUpdateResponse,
    TelegramView,
)
from src.infrastructure.components.heartbeat_client import SERVICE_TOKEN_HEADER

TOKEN = "test-service-token"


def _client(handler, *, retries: int = 3, delay: float = 0.0) -> BackendClient:
    """Клиент с подменённым транспортом: сеть не задействована."""
    client = BackendClient(
        base_url="http://backend.test",
        service_token=TOKEN,
        timeout_seconds=1,
        retries=retries,
        retry_delay_seconds=delay,
    )
    client._client = httpx.AsyncClient(
        base_url="http://backend.test",
        transport=httpx.MockTransport(handler),
        headers={SERVICE_TOKEN_HEADER: TOKEN},
    )
    return client


def _update(update_id: int = 1) -> TelegramUpdateRequest:
    return TelegramUpdateRequest(
        update_id=update_id,
        telegram_user_id="555",
        chat_id="555",
        kind=TelegramUpdateKind.TEXT,
        payload="Иван",
    )


def _view_payload(text: str = "ok") -> dict:
    return TelegramUpdateResponse(
        view=TelegramView(messages=[TelegramMessage(text=text)])
    ).model_dump(mode="json")


class TestAuthentication:
    async def test_service_token_is_sent(self):
        seen: list[str | None] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.headers.get(SERVICE_TOKEN_HEADER))
            return httpx.Response(200, json=_view_payload())

        client = _client(handler)
        try:
            await client.handle_update(_update())
        finally:
            await client.close()

        assert seen == [TOKEN]

    async def test_admin_jwt_is_not_used(self):
        """У Gateway нет пользователя и роли: Authorization здесь не место."""
        seen: list[str | None] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.headers.get("Authorization"))
            return httpx.Response(200, json=_view_payload())

        client = _client(handler)
        try:
            await client.handle_update(_update())
        finally:
            await client.close()

        assert seen == [None]

    def test_token_is_required_at_startup(self, monkeypatch):
        """Без токена клиент не создаётся: internal API отклонит все запросы."""
        import apps.telegram_gateway.runtime as runtime

        monkeypatch.setattr(runtime, "INTERNAL_SERVICE_TOKEN", "")
        monkeypatch.setattr(runtime, "BACKEND_INTERNAL_URL", "http://backend.test")
        with pytest.raises(RuntimeError, match="INTERNAL_SERVICE_TOKEN"):
            runtime.build_backend_client()

    def test_backend_url_is_required_at_startup(self, monkeypatch):
        """Другого доступа к данным у Gateway нет: PostgreSQL ему недоступен."""
        import apps.telegram_gateway.runtime as runtime

        monkeypatch.setattr(runtime, "INTERNAL_SERVICE_TOKEN", TOKEN)
        monkeypatch.setattr(runtime, "BACKEND_INTERNAL_URL", "")
        with pytest.raises(RuntimeError, match="BACKEND_INTERNAL_URL"):
            runtime.build_backend_client()


class TestRetriesAndFailures:
    async def test_transient_failure_is_retried(self):
        """Заминка туннеля RU↔EU лечится повтором, пока пользователь ждёт."""
        attempts = {"count": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            attempts["count"] += 1
            if attempts["count"] < 3:
                raise httpx.ConnectError("tunnel down")
            return httpx.Response(200, json=_view_payload())

        client = _client(handler)
        try:
            response = await client.handle_update(_update())
        finally:
            await client.close()

        assert attempts["count"] == 3
        assert response.view.messages[0].text == "ok"

    async def test_server_error_is_retried(self):
        attempts = {"count": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            attempts["count"] += 1
            if attempts["count"] == 1:
                return httpx.Response(503)
            return httpx.Response(200, json=_view_payload())

        client = _client(handler)
        try:
            await client.handle_update(_update())
        finally:
            await client.close()

        assert attempts["count"] == 2

    async def test_client_error_is_not_retried(self):
        """4xx повтором не лечится: второй такой же запрос вернёт то же самое."""
        attempts = {"count": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            attempts["count"] += 1
            return httpx.Response(422, json={"detail": "bad payload"})

        client = _client(handler)
        try:
            with pytest.raises(BackendRejectedError) as failure:
                await client.handle_update(_update())
        finally:
            await client.close()

        assert attempts["count"] == 1
        assert failure.value.status_code == 422

    async def test_exhausted_retries_raise_unavailable(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("down")

        client = _client(handler, retries=2)
        try:
            with pytest.raises(BackendUnavailableError):
                await client.handle_update(_update())
        finally:
            await client.close()

    async def test_error_detail_does_not_leak_request_body(self):
        """Тело ошибки не копируется целиком: в нём бывает ответ пользователя."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, text="ответ пользователя: вес 82 кг")

        client = _client(handler)
        try:
            with pytest.raises(BackendRejectedError) as failure:
                await client.handle_update(_update())
        finally:
            await client.close()

        assert "82" not in failure.value.detail


class TestIdempotency:
    async def test_update_id_is_transmitted(self):
        """Ключ идемпотентности обязан доходить до Backend."""
        seen: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            import json

            seen.append(json.loads(request.content)["update_id"])
            return httpx.Response(200, json=_view_payload())

        client = _client(handler)
        try:
            await client.handle_update(_update(update_id=77))
            await client.handle_update(_update(update_id=77))
        finally:
            await client.close()

        assert seen == [77, 77]

    async def test_retry_reuses_the_same_update_id(self):
        """Повтор при таймауте не должен продвигать анкету на второй шаг."""
        seen: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            import json

            seen.append(json.loads(request.content)["update_id"])
            if len(seen) == 1:
                raise httpx.ReadTimeout("slow")
            return httpx.Response(200, json=_view_payload())

        client = _client(handler)
        try:
            await client.handle_update(_update(update_id=99))
        finally:
            await client.close()

        assert seen == [99, 99]


class TestPhotoTransfer:
    async def test_photo_bytes_go_to_backend(self):
        """Байты уходят в RU: на диск EU фотография не попадает."""
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["content"] = request.content
            captured["params"] = dict(request.url.params)
            return httpx.Response(200, json=_view_payload())

        client = _client(handler)
        try:
            await client.send_photo(
                update_id=5,
                telegram_user_id="555",
                chat_id="555",
                file_id="AgACAgI",
                extension=".jpg",
                content=b"\xff\xd8\xffbinary",
            )
        finally:
            await client.close()

        assert captured["content"] == b"\xff\xd8\xffbinary"
        assert captured["params"]["file_id"] == "AgACAgI"


class TestContractVersion:
    def test_gateway_and_backend_agree(self):
        assert TELEGRAM_CONTRACT_VERSION in BACKEND_SUPPORTED_CONTRACTS

    async def test_version_is_readable(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json={"contract_version": 1, "backend_version": "2.2.0"}
            )

        client = _client(handler)
        try:
            assert await client.contract_version() == 1
        finally:
            await client.close()

    def test_view_is_forward_compatible(self):
        """Незнакомые поля в ответе не должны ронять Gateway.

        Expand/contract требует, чтобы Backend мог начать отдавать новое поле до
        обновления Gateway. Pydantic по умолчанию игнорирует лишние ключи —
        тест фиксирует это как свойство контракта, а не как случайность.
        """
        payload = _view_payload()
        payload["view"]["messages"][0]["unknown_future_field"] = "value"
        payload["unknown_top_level"] = 1

        response = TelegramUpdateResponse.model_validate(payload)
        assert response.view.messages[0].text == "ok"


# --- Рендер вида ----------------------------------------------------------------


class FakeMessage:
    def __init__(self, chat_id: int = 555, message_id: int = 10) -> None:
        self.message_id = message_id
        self.edited: list[str] = []
        self.markup_edits = 0
        self.deleted = False

    async def edit_text(self, text, reply_markup=None, parse_mode=None):
        self.edited.append(text)

    async def edit_reply_markup(self, reply_markup=None):
        self.markup_edits += 1

    async def delete(self):
        self.deleted = True


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, object]] = []
        self.documents: list[tuple[str, str]] = []

    async def send_message(self, chat_id, text, reply_markup=None, parse_mode=None):
        self.sent.append((str(chat_id), text, reply_markup))

        class _Sent:
            message_id = 4242

        return _Sent()

    async def send_document(self, chat_id, document, caption=None):
        self.documents.append((str(chat_id), document.filename))

        class _Sent:
            message_id = 4343

        return _Sent()


class TestViewRendering:
    async def test_send_message_with_buttons(self):
        bot = FakeBot()
        view = TelegramView(
            messages=[
                TelegramMessage(
                    text="Вопрос",
                    buttons=[[TelegramButton(label="Да", action="answer_yes")]],
                )
            ]
        )

        await view_renderer.render(view, bot=bot, chat_id="555")

        chat_id, text, markup = bot.sent[0]
        assert (chat_id, text) == ("555", "Вопрос")
        assert markup.inline_keyboard[0][0].callback_data == "answer_yes"

    async def test_edit_falls_back_to_new_message(self):
        """Правка невозможна — человек всё равно должен увидеть ответ."""
        from aiogram.exceptions import TelegramBadRequest

        class Broken(FakeMessage):
            async def edit_text(self, text, reply_markup=None, parse_mode=None):
                raise TelegramBadRequest(method=None, message="message is not modified")

        bot = FakeBot()
        current = Broken()
        view = TelegramView(
            messages=[TelegramMessage(kind=TelegramMessageKind.EDIT, text="Готово")]
        )

        await view_renderer.render(view, bot=bot, chat_id="555", source=current)

        assert bot.sent and bot.sent[0][1] == "Готово"

    async def test_empty_edit_changes_only_keyboard(self):
        """Переключение дней недели не перерисовывает текст вопроса."""
        bot = FakeBot()
        current = FakeMessage()
        view = TelegramView(
            messages=[
                TelegramMessage(
                    kind=TelegramMessageKind.EDIT,
                    text="",
                    buttons=[[TelegramButton(label="Пн", action="day_mon")]],
                )
            ]
        )

        await view_renderer.render(view, bot=bot, chat_id="555", source=current)

        assert current.markup_edits == 1
        assert bot.sent == []

    async def test_document_is_sent_from_memory(self):
        bot = FakeBot()
        view = TelegramView(
            messages=[
                TelegramMessage(
                    text="",
                    chat_id="999",
                    document=TelegramDocument(
                        filename="REQ-1.json", text_content="{}", caption="профиль"
                    ),
                )
            ]
        )

        await view_renderer.render(view, bot=bot, chat_id="555")

        assert bot.documents == [("999", "REQ-1.json")]

    async def test_message_to_other_chat_is_not_an_edit(self):
        """Уведомление администратору — новое сообщение, а не правка чужого."""
        bot = FakeBot()
        current = FakeMessage()
        view = TelegramView(
            messages=[
                TelegramMessage(
                    kind=TelegramMessageKind.EDIT, text="Новая анкета", chat_id="999"
                )
            ]
        )

        await view_renderer.render(view, bot=bot, chat_id="555", source=current)

        assert current.edited == []
        assert bot.sent[0][0] == "999"


# --- Поллер доставки -------------------------------------------------------------


class FakeBackend:
    def __init__(
        self,
        *,
        tasks: list[TelegramDeliveryTask] | None = None,
        document: bytes = b"<html></html>",
        document_error: Exception | None = None,
    ) -> None:
        self.tasks = tasks or []
        self.document = document
        self.document_error = document_error
        self.reports: list[tuple[int, TelegramDeliveryResult]] = []
        self.claims = 0

    async def claim_deliveries(self, *, owner: str, limit: int):
        self.claims += 1
        tasks, self.tasks = self.tasks, []
        return tasks

    async def fetch_document(self, delivery_id: int) -> bytes:
        if self.document_error is not None:
            raise self.document_error
        return self.document

    async def report_delivery(self, delivery_id: int, result: TelegramDeliveryResult):
        self.reports.append((delivery_id, result))


def _task(delivery_id: int = 1) -> TelegramDeliveryTask:
    return TelegramDeliveryTask(
        delivery_id=delivery_id,
        chat_id="555",
        filename="program.html",
        caption="готово",
    )


class TestDeliveryPoller:
    async def test_successful_send_is_reported(self):
        bot = FakeBot()
        backend = FakeBackend()

        assert await delivery_poller.deliver_one(_task(), bot=bot, client=backend)

        assert bot.documents == [("555", "program.html")]
        delivery_id, result = backend.reports[0]
        assert (delivery_id, result.delivered, result.message_id) == (1, True, 4343)

    async def test_send_failure_is_reported(self):
        """Отчёт обязателен: без него Backend не расходует бюджет попыток."""

        class BrokenBot(FakeBot):
            async def send_document(self, chat_id, document, caption=None):
                raise RuntimeError("telegram unavailable")

        backend = FakeBackend()

        assert not await delivery_poller.deliver_one(
            _task(), bot=BrokenBot(), client=backend
        )

        _, result = backend.reports[0]
        assert result.delivered is False
        assert "RuntimeError" in result.error

    async def test_missing_document_is_reported_without_send(self):
        bot = FakeBot()
        backend = FakeBackend(
            document_error=BackendRejectedError(409, "программа удалена")
        )

        assert not await delivery_poller.deliver_one(_task(), bot=bot, client=backend)

        assert bot.documents == []
        _, result = backend.reports[0]
        assert result.delivered is False

    async def test_unavailable_backend_leaves_task_for_lease_expiry(self):
        """Отчитаться нечем: аренда истечёт, задание вернётся в очередь."""
        bot = FakeBot()
        backend = FakeBackend(document_error=BackendUnavailableError("down"))

        assert not await delivery_poller.deliver_one(_task(), bot=bot, client=backend)

        assert bot.documents == []
        assert backend.reports == []

    async def test_poll_processes_batch(self):
        bot = FakeBot()
        backend = FakeBackend(tasks=[_task(1), _task(2)])

        sent = await delivery_poller.poll_once(
            bot=bot, client=backend, owner="gw-1", limit=5
        )

        assert sent == 2
        assert len(backend.reports) == 2

    async def test_poll_survives_unavailable_backend(self):
        class BrokenBackend(FakeBackend):
            async def claim_deliveries(self, *, owner: str, limit: int):
                raise BackendUnavailableError("down")

        assert (
            await delivery_poller.poll_once(
                bot=FakeBot(), client=BrokenBackend(), owner="gw-1", limit=5
            )
            == 0
        )

    async def test_loop_stops_on_signal(self):
        """Остановка не ждёт интервал: иначе SIGTERM висел бы до таймаута."""
        stop = asyncio.Event()
        backend = FakeBackend()

        async def _signal_later():
            await asyncio.sleep(0.05)
            stop.set()

        cycles, _ = await asyncio.wait_for(
            asyncio.gather(
                delivery_poller.run_delivery_poller(
                    bot=FakeBot(),
                    client=backend,
                    owner="gw-1",
                    interval_seconds=3600,
                    limit=5,
                    stop=stop,
                ),
                _signal_later(),
            ),
            timeout=2,
        )

        assert cycles == 1
