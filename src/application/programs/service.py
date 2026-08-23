"""ProgramService: чтение сохранённых программ.

Генерация здесь НЕ выполняется. Единственная точка генерации —
`ProgramGenerationOrchestrator` (Phase 1.2-C): раньше `ProgramService` имел
собственный конвейер (фильтр → генератор → валидатор → репозиторий), из-за
чего Admin API шёл мимо readiness gate и fallback, а Telegram — через
оркестратор. Две параллельные orchestration-логики устранены, и сервис
отвечает только за выдачу уже созданных версий программ.
"""
from __future__ import annotations

from src.domain.program import WorkoutProgram
from src.infrastructure.persistence.program_repository import ProgramRepository


class ProgramService:
    def __init__(self, *, program_repository: ProgramRepository) -> None:
        self._programs = program_repository

    async def get(self, program_id: str, version: int | None = None) -> WorkoutProgram | None:
        return await self._programs.get(program_id, version)

    async def list_versions(self, program_id: str) -> list[WorkoutProgram]:
        return await self._programs.list_versions(program_id)

    async def list_for_profile(self, profile_id: str) -> list[WorkoutProgram]:
        return await self._programs.list_for_profile(profile_id)

    async def list_all(self, limit: int = 50, offset: int = 0) -> tuple[int, list[WorkoutProgram]]:
        return await self._programs.list_all(limit=limit, offset=offset)
