"""Абстракция хранилища программ тренировок.

Версионность: каждая генерация создаёт новую строку (profile_id, version),
исторические версии не перезаписываются. Application-слой не знает,
где физически лежат данные.
"""
from __future__ import annotations

import abc

from src.domain.program import WorkoutProgram


class ProgramRepository(abc.ABC):
    """Асинхронный интерфейс хранилища программ."""

    @abc.abstractmethod
    async def save(self, program: WorkoutProgram) -> WorkoutProgram:
        """Сохраняет версию программы. Бросает ProgramPersistenceError при ошибке."""

    @abc.abstractmethod
    async def get(self, program_id: str, version: int | None = None) -> WorkoutProgram | None:
        """Возвращает программу. version=None → последняя версия."""

    @abc.abstractmethod
    async def list_versions(self, program_id: str) -> list[WorkoutProgram]:
        """Все версии программы, по возрастанию номера версии."""

    @abc.abstractmethod
    async def list_for_profile(self, profile_id: str) -> list[WorkoutProgram]:
        """Программы профиля (последние версии каждой программы)."""

    @abc.abstractmethod
    async def list_all(self, limit: int = 50, offset: int = 0) -> tuple[int, list[WorkoutProgram]]:
        """Все программы (последние версии), total + страница."""

    @abc.abstractmethod
    async def next_version(self, profile_id: str) -> int:
        """Следующий номер версии для профиля (1, если программ ещё нет)."""

    @abc.abstractmethod
    async def count(self) -> int:
        """Количество программ (последних версий)."""
