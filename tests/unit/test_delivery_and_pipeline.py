"""Unit-тесты delivery и e2e pipeline сервисов (Stage 5).

Доставка через фейковый sender; проверяются:
- успешная отправка с записью статуса;
- retry при ошибках Telegram (ограниченное число попыток);
- отправка после повторного вызова для уже сохранённой программы
  НЕ запускает новую генерацию;
- pipeline-сервис корректно обрабатывает все стадии ошибок.
"""
from __future__ import annotations

import pytest

from src.application.notifications.program_alerts import ProgramAlert, ProgramAlertService
from src.application.programs.html_renderer import render_program_html
from src.application.programs.html_service import ProgramHtmlService
from src.application.programs import telegram_delivery as delivery_module
from src.application.programs.pipeline import (
    PipelineOutcome,
    ProgramPipelineService,
)
from src.application.programs.telegram_delivery import (
    ProgramDeliveryService,
    ProgramDocument,
    build_filename,
)
from src.domain.enums import GenerationSource, ProgramDeliveryStatus
from src.domain.program import (
    GenerationInfo,
    ProgramExercise,
    TrainingDay,
    WorkoutProgram,
)
from src.errors import HtmlRenderError, ProgramDeliveryError

EX_ID = "Push_Up"


def _program() -> WorkoutProgram:
    program = WorkoutProgram(
        profile_id="p1",
        title="Тест",
        duration_weeks=4,
        training_days_per_week=1,
        training_days=[
            TrainingDay(
                day_number=1,
                title="День 1",
                focus="Full body",
                exercises=[
                    ProgramExercise(
                        exercise_external_id=EX_ID,
                        order=1,
                        sets=3,
                        repetitions_min=10,
                        repetitions_max=10,
                        rest_seconds=60,
                    )
                ],
            )
        ],
        generation=GenerationInfo(source=GenerationSource.DETERMINISTIC),
    )
    program.program_id = "prog-1"
    return program


class FakeHtmlService:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    @property
    def media_mode(self) -> str:
        return "html"

    async def render(self, program: WorkoutProgram) -> bytes:
        self.calls += 1
        if self.fail:
            raise HtmlRenderError("render boom")
        return render_program_html(program).encode("utf-8")


class FakeDeliveryRepository:
    def __init__(self) -> None:
        self.records = []

    async def create(self, record):
        record.id = len(self.records) + 1
        self.records.append(record)
        return record

    async def update(self, record) -> None: ...

    async def get_for_profile(self, profile_id: str):
        return None

    async def list_failed(self, limit: int = 50):
        return [r for r in self.records if r.status is ProgramDeliveryStatus.FAILED]


class FakeSender:
    def __init__(self, fail_times: int = 0) -> None:
        self.fail_times = fail_times
        self.calls: list[tuple[str, ProgramDocument]] = []

    async def __call__(self, chat_id: str, document: ProgramDocument) -> int:
        self.calls.append((chat_id, document))
        if len(self.calls) <= self.fail_times:
            raise RuntimeError("telegram api error")
        return 42


def _delivery_service(
    *,
    html_fail: bool = False,
    sender_fail_times: int = 0,
    max_attempts: int = 3,
):
    repo = FakeDeliveryRepository()
    sender = FakeSender(fail_times=sender_fail_times)
    html = FakeHtmlService(fail=html_fail)
    alerts_sent: list[ProgramAlert] = []

    async def alert_sender(alert: ProgramAlert) -> None:
        alerts_sent.append(alert)

    service = ProgramDeliveryService(
        html_service=html,
        delivery_repository=repo,
        sender=sender,
        alert_service=ProgramAlertService(alert_sender),
        max_attempts=max_attempts,
    )
    return service, repo, sender, html, alerts_sent


@pytest.fixture(autouse=True)
def fast_retry():
    original = delivery_module.RETRY_BASE_DELAY
    delivery_module.RETRY_BASE_DELAY = 0.0
    yield
    delivery_module.RETRY_BASE_DELAY = original


class TestDelivery:
    async def test_successful_delivery(self):
        service, repo, sender, _, _ = _delivery_service()
        record = await service.deliver(program=_program(), chat_id="12345")

        assert record.status is ProgramDeliveryStatus.SENT
        assert record.attempts == 1
        assert len(sender.calls) == 1
        chat_id, document = sender.calls[0]
        assert chat_id == "12345"
        assert document.filename == build_filename("p1", 1)
        assert document.bytes_content.startswith(b"<!DOCTYPE html>")
        assert repo.records[0].status is ProgramDeliveryStatus.SENT
        assert repo.records[0].sent_message_id == 42

    async def test_retry_then_success(self):
        service, _, sender, _, _ = _delivery_service(sender_fail_times=2)

        record = await service.deliver(program=_program(), chat_id="1")

        assert record.status is ProgramDeliveryStatus.SENT
        assert record.attempts == 3
        assert len(sender.calls) == 3

    async def test_all_attempts_failed(self):
        service, repo, _, _, alerts = _delivery_service(sender_fail_times=10, max_attempts=2)

        with pytest.raises(ProgramDeliveryError):
            await service.deliver(program=_program(), chat_id="1")

        assert repo.records[0].status is ProgramDeliveryStatus.FAILED
        assert repo.records[0].attempts == 2
        assert alerts and alerts[-1].stage == "delivery"

    async def test_render_failure_marks_failed_and_no_attempts(self):
        service, repo, sender, _, alerts = _delivery_service(html_fail=True)

        with pytest.raises(HtmlRenderError):
            await service.deliver(program=_program(), chat_id="1")

        assert repo.records[0].status is ProgramDeliveryStatus.FAILED
        assert "html_render" in (repo.records[0].last_error or "")
        assert len(sender.calls) == 0
        assert alerts and alerts[-1].stage == "html_render"

    async def test_redeliver_does_not_regenerate(self):
        service, repo, sender, html, _ = _delivery_service()
        program = _program()

        record = await service.deliver(program=program, chat_id="1")
        # Новая доставка той же программы: html-сервис вызывается снова (рендер),
        # но orchestrator/генераторы НЕ вызываются — sender получает тот же документ.
        record2 = await service.redeliver(record, program)

        assert record2.status is ProgramDeliveryStatus.SENT
        assert html.calls == 2
        assert len(sender.calls) == 2


class FakeOrchestrator:
    def __init__(
        self,
        program: WorkoutProgram | None,
        fail: bool = False,
        already_running: bool = False,
    ) -> None:
        self.program = program
        self.fail = fail
        self.already_running = already_running
        self.calls = 0

    async def generate(self, profile_id: str, *, reuse_existing: bool = False):
        from src.application.programs.orchestrator import OrchestratorResult
        from src.domain.pools import ExerciseCandidatePool, SafeExercisePool

        self.calls += 1
        if self.already_running:
            from src.errors import GenerationAlreadyRunningError

            raise GenerationAlreadyRunningError("уже выполняется")
        if self.fail:
            from src.errors import ProgramGenerationError

            raise ProgramGenerationError("generation boom")
        return OrchestratorResult(
            program=self.program,
            candidate_pool=ExerciseCandidatePool(profile_id=profile_id, total_exercises=10),
            safe_pool=SafeExercisePool(profile_id=profile_id),
        )


class TestPipelineService:
    def _pipeline(self, *, generation_fail=False, delivery=None, already_running=False):
        program = _program()
        alerts: list[ProgramAlert] = []

        async def alert_sender(alert: ProgramAlert) -> None:
            alerts.append(alert)

        pipeline = ProgramPipelineService(
            orchestrator=FakeOrchestrator(
                program, fail=generation_fail, already_running=already_running
            ),
            delivery_service=delivery,
            alert_service=ProgramAlertService(alert_sender),
        )
        return pipeline, program, alerts

    async def test_delivered_on_success(self):
        service, _, _, _, _ = _delivery_service()
        pipeline, program, _ = self._pipeline(delivery=service)

        result = await pipeline.run_for_user(profile_id="p1", chat_id="1")

        assert result.outcome is PipelineOutcome.DELIVERED
        assert result.program is not None
        assert result.user_message

    async def test_generation_failure_user_message(self):
        pipeline, _, alerts = self._pipeline(generation_fail=True)

        result = await pipeline.run_for_user(profile_id="p1", chat_id="1")

        assert result.outcome is PipelineOutcome.GENERATION_FAILED
        assert "Не удалось автоматически сформировать" in result.user_message
        assert alerts and alerts[-1].stage == "generation"

    async def test_generation_failure_is_not_raised(self):
        pipeline, _, _ = self._pipeline(generation_fail=True)
        result = await pipeline.run_for_user(profile_id="p1", chat_id="1")
        assert result.program is None

    async def test_render_failure_keeps_program(self):
        service, _, _, _, _ = _delivery_service(html_fail=True)
        pipeline, program, _ = self._pipeline(delivery=service)

        result = await pipeline.run_for_user(profile_id="p1", chat_id="1")

        assert result.outcome is PipelineOutcome.RENDER_FAILED
        assert result.program is program

    async def test_no_delivery_service_returns_program(self):
        pipeline, program, _ = self._pipeline(delivery=None)
        result = await pipeline.run_for_user(profile_id="p1", chat_id=None)
        assert result.outcome is PipelineOutcome.DELIVERED
        assert result.program is program

    async def test_duplicate_run_does_not_alert_admin(self):
        """Дубликат запуска — не сбой: администратора не будим."""
        pipeline, _, alerts = self._pipeline(already_running=True)

        result = await pipeline.run_for_user(profile_id="p1", chat_id="1")

        assert result.outcome is PipelineOutcome.GENERATION_IN_PROGRESS
        assert result.program is None
        assert "уже формируется" in result.user_message
        assert alerts == []
