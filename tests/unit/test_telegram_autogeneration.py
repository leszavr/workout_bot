"""Автогенерация программы после финализации анкеты.

Проверяется путь, который нельзя увидеть в тестах `ProgramPipelineService`:
Telegram-хендлер сам собирает pipeline и отправляет пользователю результат.

Регрессия, из-за которой появился этот файл: `build_program_pipeline`
импортировался внутри `final_confirm`, то есть в локальную область одной
функции, а использовался в другой — `run_program_pipeline`. На module level
имени не было, вызов падал с `NameError`, широкий `except Exception` превращал
это в сообщение «Не удалось автоматически сформировать программу», и
автогенерация не работала ни разу. Ошибка видна только в рантайме: импорт
модуля проходил, тесты с подставленным pipeline — тоже.
"""
from __future__ import annotations

import pytest

from apps.telegram_gateway import pipeline as pipeline_module
from apps.telegram_gateway.handlers import review
from src.application.programs.pipeline import PipelineOutcome, PipelineResult


class FakeBot:
    def __init__(self, *, fail: bool = False) -> None:
        self.messages: list[tuple[str, str]] = []
        self._fail = fail

    async def send_message(self, chat_id: str, text: str, **kwargs) -> None:
        if self._fail:
            raise RuntimeError("telegram unavailable")
        self.messages.append((chat_id, text))


class FakePipeline:
    def __init__(self, result: PipelineResult) -> None:
        self._result = result
        self.calls: list[dict] = []

    async def run_for_user(self, **kwargs) -> PipelineResult:
        self.calls.append(kwargs)
        return self._result


def _result(message: str = "Ваша программа готова.") -> PipelineResult:
    return PipelineResult(outcome=PipelineOutcome.DELIVERED, user_message=message)


class TestPipelineBuilderIsResolvable:
    """Имена, которые хендлер вызывает в рантайме, должны быть ему доступны."""

    def test_builder_is_bound_in_handler_module(self):
        assert review.build_program_pipeline is pipeline_module.build_program_pipeline

    def test_auto_generation_flag_is_bound_in_handler_module(self):
        assert (
            review.is_auto_generation_enabled
            is pipeline_module.is_auto_generation_enabled
        )


class TestRunProgramPipeline:
    async def test_user_gets_progress_notice_and_result(self, monkeypatch):
        bot = FakeBot()
        fake = FakePipeline(_result("Программа готова."))
        monkeypatch.setattr(review, "build_program_pipeline", lambda _bot: fake)

        await review.run_program_pipeline(
            bot=bot, chat_id="42", profile_id="p1", already_finalized=False
        )

        assert [text for _, text in bot.messages] == [
            "⏳ Формируем вашу персональную программу...",
            "Программа готова.",
        ]
        assert fake.calls == [
            {"profile_id": "p1", "chat_id": "42", "reuse_existing": True}
        ]

    async def test_repeated_finalize_skips_progress_notice(self, monkeypatch):
        """Повторное подтверждение не должно обещать новую генерацию."""
        bot = FakeBot()
        fake = FakePipeline(_result("Программа уже готова."))
        monkeypatch.setattr(review, "build_program_pipeline", lambda _bot: fake)

        await review.run_program_pipeline(
            bot=bot, chat_id="42", profile_id="p1", already_finalized=True
        )

        assert [text for _, text in bot.messages] == ["Программа уже готова."]

    async def test_builder_failure_is_reported_to_user(self, monkeypatch):
        def broken(_bot):
            raise RuntimeError("нет соединения с БД")

        bot = FakeBot()
        monkeypatch.setattr(review, "build_program_pipeline", broken)

        await review.run_program_pipeline(
            bot=bot, chat_id="42", profile_id="p1", already_finalized=False
        )

        assert len(bot.messages) == 1
        _, text = bot.messages[0]
        assert "Не удалось автоматически сформировать программу" in text
        assert "БД" not in text

    async def test_pipeline_failure_does_not_raise(self, monkeypatch):
        """Сбой генерации не должен ронять фоновую задачу хендлера."""

        class BrokenPipeline:
            async def run_for_user(self, **kwargs):
                raise RuntimeError("generation exploded")

        bot = FakeBot()
        monkeypatch.setattr(
            review, "build_program_pipeline", lambda _bot: BrokenPipeline()
        )

        await review.run_program_pipeline(
            bot=bot, chat_id="42", profile_id="p1", already_finalized=False
        )

        assert [text for _, text in bot.messages] == [
            "⏳ Формируем вашу персональную программу..."
        ]

    async def test_unreachable_telegram_does_not_raise(self, monkeypatch):
        bot = FakeBot(fail=True)
        fake = FakePipeline(_result())
        monkeypatch.setattr(review, "build_program_pipeline", lambda _bot: fake)

        await review.run_program_pipeline(
            bot=bot, chat_id="42", profile_id="p1", already_finalized=False
        )

        assert bot.messages == []


class TestAutoGenerationFlag:
    @pytest.mark.parametrize(
        ("enabled", "database_url", "expected"),
        [
            (True, "postgresql+asyncpg://host/db", True),
            (True, "", False),
            (False, "postgresql+asyncpg://host/db", False),
        ],
    )
    def test_requires_both_flag_and_database(
        self, monkeypatch, enabled, database_url, expected
    ):
        """Без Postgres автогенерации нет: pipeline не на чем работать."""
        monkeypatch.setattr(
            pipeline_module, "AUTO_GENERATE_PROGRAM_AFTER_FINALIZE", enabled
        )
        monkeypatch.setattr(pipeline_module, "DATABASE_URL", database_url)

        assert pipeline_module.is_auto_generation_enabled() is expected
