"""Текущая операция генерации: ambient-контекст для телеметрии.

Задача: связать записи телеметрии AI-контура с operational-записью генерации
(`generation_jobs.job_id`). Без такой связи журнал попыток моделей, журнал
fallback и журнал вызовов невозможно свести к конкретной генерации: аналитика
может показать «сколько было отказов», но не «что произошло в этой генерации».

Почему contextvar, а не параметр. Идентификатор нужен в трёх местах, лежащих
на разной глубине от места, где job создаётся: журнал попыток моделей, журнал
fallback и запись вызова AI. Провести его аргументом означало бы изменить
контракт `ProgramGenerator.generate`, общий для AI- и алгоритмического
генератора, ради поля, которое алгоритмическому генератору не нужно вовсе.

Границы намеренно узкие: контекст устанавливает только `GenerationJobService`
на время одной генерации, и читают его только телеметрические записи. Бизнес-
решения по нему не принимаются: отсутствие контекста означает лишь, что
генерация выполняется без job-контура, и телеметрия остаётся без ссылки.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

_current_job_id: ContextVar[str | None] = ContextVar(
    "workout_current_generation_job_id", default=None
)


def current_generation_job_id() -> str | None:
    """`job_id` текущей генерации или None вне job-контура."""
    return _current_job_id.get()


@contextmanager
def generation_job_context(job_id: str) -> Iterator[None]:
    """Помечает текущую операцию идентификатором job.

    Значение восстанавливается по токену, а не сбрасывается в None: вложенный
    вызов не должен стирать контекст внешнего.
    """
    token = _current_job_id.set(job_id)
    try:
        yield
    finally:
        _current_job_id.reset(token)
