"""Unit-тесты analytics-слоя админки.

Проверяется то, что решает сервис, а не база: когда выборка достаточна, чтобы
опираться на проценты, и когда разница между версиями инструкции — вывод, а не
шум. Именно здесь легко «улучшить» аналитику до состояния, в котором она
уверенно врёт: 100% успеха на двух генерациях выглядит убедительнее, чем 96% на
двухсот, хотя означает меньше.

Запросы к PostgreSQL проверяются интеграционными тестами: в SQL-выражениях
подменять нечего, а фейк базы доказывал бы только то, что фейк согласован сам с
собой.
"""
from __future__ import annotations

import pytest

from src.application.ai.analytics_service import (
    MIN_CONFIDENT_SAMPLE,
    MIN_MEANINGFUL_DIFFERENCE_PP,
    GenerationAnalyticsService,
)
from src.infrastructure.persistence.postgres.analytics_repository import (
    AnalyticsFilter,
)


class FakeRepository:
    """In-memory заглушка репозитория: возвращает заранее заданные агрегаты."""

    def __init__(self, *, overview: dict | None = None, prompts: list | None = None):
        self._overview = overview or {"generations": {"total": 0}, "calls": {}}
        self._prompts = prompts or []
        self.prompt_calls: list[tuple] = []

    async def overview(self, spec):
        return dict(self._overview)

    async def prompts(self, spec, *, sort_by, descending):
        self.prompt_calls.append((sort_by, descending))
        return [dict(item) for item in self._prompts]

    async def models(self, spec, *, sort_by, descending):
        return [dict(item) for item in self._models]

    _models: list = []


def _prompt(version: int, *, usage: int, **metrics) -> dict:
    row = {
        "prompt_version": version,
        "usage": usage,
        "success_rate": None,
        "failure_rate": None,
        "validation_failure_rate": None,
        "fallback_rate": None,
    }
    row.update(metrics)
    return row


def _service(**kwargs) -> GenerationAnalyticsService:
    return GenerationAnalyticsService(FakeRepository(**kwargs))


class TestSampleConfidence:
    """Малая выборка обязана быть помечена, а не выглядеть как результат."""

    @pytest.mark.asyncio
    async def test_small_sample_is_marked_unreliable(self):
        service = _service(overview={"generations": {"total": 3}, "calls": {}})
        result = await service.overview(AnalyticsFilter())
        assert result["sample"]["confident"] is False
        assert result["sample"]["generations"] == 3
        assert result["sample"]["min_confident"] == MIN_CONFIDENT_SAMPLE

    @pytest.mark.asyncio
    async def test_sufficient_sample_is_marked_confident(self):
        service = _service(
            overview={"generations": {"total": MIN_CONFIDENT_SAMPLE}, "calls": {}}
        )
        result = await service.overview(AnalyticsFilter())
        assert result["sample"]["confident"] is True

    @pytest.mark.asyncio
    async def test_confidence_is_per_prompt_row(self):
        """Пометка на строке, а не на таблице: у версий разный объём данных."""
        service = _service(
            prompts=[
                _prompt(1, usage=50, success_rate=90.0),
                _prompt(2, usage=2, success_rate=100.0),
            ]
        )
        items = await service.prompts(
            AnalyticsFilter(), sort_by="prompt_version", descending=False
        )
        assert [item["confident"] for item in items] == [True, False]


class TestPromptComparison:
    async def _compare(self, prompts: list[dict], left: int = 1, right: int = 2):
        service = _service(prompts=prompts)
        return await service.compare_prompts(AnalyticsFilter(), left=left, right=right)

    @pytest.mark.asyncio
    async def test_significant_difference_names_better_version(self):
        result = await self._compare(
            [
                _prompt(1, usage=40, success_rate=70.0),
                _prompt(2, usage=40, success_rate=90.0),
            ]
        )
        success = next(m for m in result["metrics"] if m["metric"] == "success_rate")
        assert success["difference_pp"] == 20.0
        assert success["better_version"] == 2
        assert success["confident"] is True

    @pytest.mark.asyncio
    async def test_lower_is_better_for_failure_metrics(self):
        """У доли отказов «лучше» — это меньше, а не больше."""
        result = await self._compare(
            [
                _prompt(1, usage=40, validation_failure_rate=30.0),
                _prompt(2, usage=40, validation_failure_rate=10.0),
            ]
        )
        metric = next(
            m for m in result["metrics"] if m["metric"] == "validation_failure_rate"
        )
        assert metric["difference_pp"] == -20.0
        assert metric["better_version"] == 2

    @pytest.mark.asyncio
    async def test_small_difference_is_not_declared_a_winner(self):
        difference = MIN_MEANINGFUL_DIFFERENCE_PP - 1
        result = await self._compare(
            [
                _prompt(1, usage=40, success_rate=90.0),
                _prompt(2, usage=40, success_rate=90.0 + difference),
            ]
        )
        metric = next(m for m in result["metrics"] if m["metric"] == "success_rate")
        assert metric["better_version"] is None
        assert "погрешности" in metric["note"]

    @pytest.mark.asyncio
    async def test_small_sample_blocks_conclusion_even_on_large_difference(self):
        """40 процентных пунктов на трёх генерациях — не вывод."""
        result = await self._compare(
            [
                _prompt(1, usage=3, success_rate=100.0),
                _prompt(2, usage=3, success_rate=60.0),
            ]
        )
        metric = next(m for m in result["metrics"] if m["metric"] == "success_rate")
        assert metric["better_version"] is None
        assert metric["confident"] is False
        assert "мало данных" in metric["note"]

    @pytest.mark.asyncio
    async def test_missing_metric_value_yields_no_conclusion(self):
        result = await self._compare(
            [
                _prompt(1, usage=40, success_rate=None),
                _prompt(2, usage=40, success_rate=90.0),
            ]
        )
        metric = next(m for m in result["metrics"] if m["metric"] == "success_rate")
        assert metric["better_version"] is None
        assert metric["difference_pp"] is None

    @pytest.mark.asyncio
    async def test_missing_version_is_reported_not_invented(self):
        result = await self._compare([_prompt(1, usage=40, success_rate=90.0)])
        assert result["missing_versions"] == [2]
        assert result["metrics"] == []
        assert result["right"] is None

    @pytest.mark.asyncio
    async def test_comparison_reads_all_versions_not_a_page(self):
        """Сравнение не зависит от сортировки таблицы: берутся обе версии."""
        repository = FakeRepository(
            prompts=[
                _prompt(1, usage=40, success_rate=70.0),
                _prompt(2, usage=40, success_rate=90.0),
            ]
        )
        service = GenerationAnalyticsService(repository)
        await service.compare_prompts(AnalyticsFilter(), left=1, right=2)
        assert repository.prompt_calls == [("prompt_version", False)]
