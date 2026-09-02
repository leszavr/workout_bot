"""Политика повторов фоновых операций (Phase 1.2-D).

Одна политика на два потребителя — повтор генерации и повтор доставки. Общий
тип нужен не ради абстракции: правила «сколько попыток» и «через сколько» у них
одинаковы по форме и разные по значениям, а два независимых механизма backoff
разошлись бы при первом же изменении.

Бесконечных повторов нет: `max_attempts` — жёсткая граница, после которой
операция остаётся в окончательном отказе и требует решения человека.

Значения задаются конфигурацией (`src/infrastructure/config.py`), а не этим
модулем: здесь только арифметика и проверка её осмысленности.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class RetryPolicy:
    """Экспоненциальный backoff с ограничением сверху.

    `max_attempts` считает *все* попытки, включая первую: политика из трёх
    попыток означает исходную и два повтора. Иначе значение приходилось бы
    читать по-разному для job (у него уже есть счётчик `attempts`) и для
    доставки.
    """

    max_attempts: int
    initial_delay_seconds: float
    multiplier: float
    max_delay_seconds: float

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts должен быть >= 1")
        if self.initial_delay_seconds <= 0:
            raise ValueError("initial_delay_seconds должен быть > 0")
        if self.multiplier < 1:
            raise ValueError("multiplier должен быть >= 1: задержка не может убывать")
        if self.max_delay_seconds < self.initial_delay_seconds:
            raise ValueError("max_delay_seconds не может быть меньше начальной задержки")

    def has_attempts_left(self, attempts_made: int) -> bool:
        """Остались ли попытки после `attempts_made` выполненных."""
        return attempts_made < self.max_attempts

    def delay_seconds(self, attempts_made: int) -> float:
        """Пауза перед попыткой номер `attempts_made + 1`.

        После первой неудачи ждём `initial_delay_seconds`, дальше умножаем на
        `multiplier` и упираемся в `max_delay_seconds`. `attempts_made <= 0`
        трактуется как первая неудача: отрицательная степень дала бы задержку
        меньше начальной.
        """
        exponent = max(0, attempts_made - 1)
        delay = self.initial_delay_seconds * (self.multiplier**exponent)
        return min(delay, self.max_delay_seconds)

    def next_attempt_at(
        self, *, now: datetime, attempts_made: int
    ) -> datetime | None:
        """Момент следующей попытки либо None, если попытки исчерпаны."""
        if not self.has_attempts_left(attempts_made):
            return None
        return now + timedelta(seconds=self.delay_seconds(attempts_made))
