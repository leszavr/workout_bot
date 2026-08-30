"""Analytics-слой админки: сводка качества генерации и сравнение инструкций.

Репозиторий отдаёт числа, сервис отвечает на вопрос «можно ли на них
опираться». Разделение существенное: 2 генерации с успехом 100% и 200
генераций с успехом 96% — разные утверждения, и показывать их одинаково
значило бы обманывать.

Сервис ничего не пишет и не меняет конфигурацию. Выбор рабочей версии
инструкции остаётся действием администратора: автоматическое переключение по
статистике здесь не делается, потому что «лучше по проценту» и «лучше по сути»
— не одно и то же, а цена ошибки — качество программ у живых пользователей.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.infrastructure.persistence.postgres.analytics_repository import (
    AnalyticsFilter,
    GenerationAnalyticsRepository,
)

# Минимальный объём выборки, при котором доли имеют смысл. Значение выбрано как
# порог, ниже которого одна генерация меняет процент больше, чем на 10 пунктов:
# такой показатель описывает случайность, а не поведение модели.
MIN_CONFIDENT_SAMPLE = 10

# Разница долей, ниже которой версии инструкций считаются неразличимыми.
# Меньшая разница на выборках такого размера не отличима от шума, и объявлять
# победителя по ней — значит выдавать случайность за вывод.
MIN_MEANINGFUL_DIFFERENCE_PP = 5.0


@dataclass(frozen=True)
class PromptComparison:
    """Сравнение двух версий инструкции по одному показателю."""

    metric: str
    left_version: int
    right_version: int
    left_value: float | None
    right_value: float | None
    difference_pp: float | None
    # None — сравнение недостоверно (мало данных или нет значения).
    better_version: int | None
    confident: bool
    note: str


class GenerationAnalyticsService:
    """Read-only аналитика генерации для админки."""

    def __init__(self, repository: GenerationAnalyticsRepository) -> None:
        self._repository = repository

    async def overview(self, spec: AnalyticsFilter) -> dict:
        """Сводка с явной пометкой достоверности выборки."""
        data = await self._repository.overview(spec)
        total = data["generations"]["total"]
        data["sample"] = {
            "generations": total,
            "confident": total >= MIN_CONFIDENT_SAMPLE,
            "min_confident": MIN_CONFIDENT_SAMPLE,
        }
        return data

    async def timeseries(self, spec: AnalyticsFilter, *, bucket: str) -> list[dict]:
        return await self._repository.timeseries(spec, bucket=bucket)

    async def models(
        self, spec: AnalyticsFilter, *, sort_by: str, descending: bool
    ) -> list[dict]:
        """Модели с пометкой, достаточно ли данных по каждой.

        Пометка ставится на строку, а не на таблицу: у основной модели может
        быть 200 попыток, у резервной — 3, и доверять их процентам одинаково
        нельзя.
        """
        items = await self._repository.models(
            spec, sort_by=sort_by, descending=descending
        )
        for item in items:
            item["confident"] = item["usage"] >= MIN_CONFIDENT_SAMPLE
        return items

    async def prompts(
        self, spec: AnalyticsFilter, *, sort_by: str, descending: bool
    ) -> list[dict]:
        items = await self._repository.prompts(
            spec, sort_by=sort_by, descending=descending
        )
        for item in items:
            item["confident"] = item["usage"] >= MIN_CONFIDENT_SAMPLE
        return items

    async def compare_prompts(
        self, spec: AnalyticsFilter, *, left: int, right: int
    ) -> dict:
        """Сравнение двух версий инструкции по ключевым показателям.

        Возвращает и сами показатели, и вывод по каждому. Вывод не делается,
        если данных мало или разница в пределах шума: «версия 2 лучше на 0.4
        процентных пункта при 6 генерациях» — не факт, а совпадение.
        """
        items = await self._repository.prompts(
            spec, sort_by="prompt_version", descending=False
        )
        by_version = {item["prompt_version"]: item for item in items}
        left_row = by_version.get(left)
        right_row = by_version.get(right)
        if left_row is None or right_row is None:
            missing = [v for v, row in ((left, left_row), (right, right_row)) if row is None]
            return {
                "left": left_row,
                "right": right_row,
                "metrics": [],
                "missing_versions": missing,
            }

        comparisons = [
            self._compare(left_row, right_row, "success_rate", higher_is_better=True),
            self._compare(
                left_row, right_row, "validation_failure_rate", higher_is_better=False
            ),
            self._compare(left_row, right_row, "fallback_rate", higher_is_better=False),
            self._compare(left_row, right_row, "failure_rate", higher_is_better=False),
        ]
        return {
            "left": left_row,
            "right": right_row,
            "metrics": [comparison.__dict__ for comparison in comparisons],
            "missing_versions": [],
        }

    @staticmethod
    def _compare(
        left: dict, right: dict, metric: str, *, higher_is_better: bool
    ) -> PromptComparison:
        left_value = left.get(metric)
        right_value = right.get(metric)
        confident = (
            left["usage"] >= MIN_CONFIDENT_SAMPLE
            and right["usage"] >= MIN_CONFIDENT_SAMPLE
        )

        if left_value is None or right_value is None:
            return PromptComparison(
                metric=metric,
                left_version=left["prompt_version"],
                right_version=right["prompt_version"],
                left_value=left_value,
                right_value=right_value,
                difference_pp=None,
                better_version=None,
                confident=False,
                note="нет данных для сравнения",
            )

        difference = round(right_value - left_value, 1)
        if not confident:
            note = (
                f"мало данных: нужно не менее {MIN_CONFIDENT_SAMPLE} генераций "
                "на каждую версию"
            )
            better = None
        elif abs(difference) < MIN_MEANINGFUL_DIFFERENCE_PP:
            note = "разница в пределах погрешности"
            better = None
        else:
            note = "разница значима"
            improved = difference > 0 if higher_is_better else difference < 0
            better = (
                right["prompt_version"] if improved else left["prompt_version"]
            )
        return PromptComparison(
            metric=metric,
            left_version=left["prompt_version"],
            right_version=right["prompt_version"],
            left_value=left_value,
            right_value=right_value,
            difference_pp=difference,
            better_version=better,
            confident=confident,
            note=note,
        )

    async def generations(
        self,
        spec: AnalyticsFilter,
        *,
        limit: int,
        offset: int,
        sort_by: str,
        descending: bool,
    ) -> tuple[int, list[dict]]:
        return await self._repository.generations(
            spec,
            limit=limit,
            offset=offset,
            sort_by=sort_by,
            descending=descending,
        )

    async def generation(self, job_id: str) -> dict | None:
        return await self._repository.generation(job_id)

    async def filter_options(self) -> dict:
        return await self._repository.filter_options()
