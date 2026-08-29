"""Удаление анкет и программ администратором.

Раздел анкет накапливается: каждая пройденная анкета остаётся в списке навсегда,
даже когда программа по ней собрана, отправлена и потеряла актуальность.
Удаления не было вовсе, поэтому список только рос.

Границы удаления выбраны так, чтобы случайная потеря пользовательских данных
была невозможна:

- **анкета с программами не удаляется** — сначала удаляются программы. Анкета
  дороже программы: программу можно собрать заново из анкеты, анкету
  восстановить нельзя, её заполнял человек в боте;
- **программа удаляется целиком**, со всеми версиями и записями доставок.

Внешних ключей на `workout_programs.profile_id` и на `program_deliveries` в базе
нет, поэтому целостность обеспечивает этот сервис, а не СУБД. `generation_jobs`
— исключение: там FK есть (`ON DELETE CASCADE` на профиль, `SET NULL` на
программу), и история операций сохраняется намеренно.
"""
from __future__ import annotations

import logging

from src.application.deletion import DeleteBlockedError, DeleteDependencies
from src.infrastructure.persistence.postgres.delivery_repository import (
    ProgramDeliveryRepository,
)
from src.infrastructure.persistence.profile_repository import ProfileRepository
from src.infrastructure.persistence.program_repository import ProgramRepository

logger = logging.getLogger(__name__)


class ProfileAdminService:
    def __init__(
        self,
        *,
        profiles: ProfileRepository,
        programs: ProgramRepository,
        deliveries: ProgramDeliveryRepository,
    ) -> None:
        self._profiles = profiles
        self._programs = programs
        self._deliveries = deliveries

    # --- Анкеты -------------------------------------------------------------------

    async def profile_dependencies(self, profile_id: str) -> DeleteDependencies:
        """Что мешает удалить анкету.

        Единственный блокер — собранные по ней программы. Такое удаление база не
        остановит (внешнего ключа нет), а программа без анкеты бесполезна: по
        ней уже не видно, кому и почему её собрали.
        """
        dependencies = DeleteDependencies()
        programs = await self._programs.list_for_profile(profile_id)
        if programs:
            dependencies.blockers.append(
                {
                    "type": "workout_program",
                    "count": len(programs),
                    "detail": (
                        f"по анкете собрано программ: {len(programs)}. "
                        "Сначала удалите программы."
                    ),
                }
            )
        return dependencies

    async def delete_profile(self, profile_id: str) -> dict:
        """Удаляет анкету без программ. Возвращает сводку удалённого.

        Записи доставок удаляются вместе с анкетой: без анкеты и программы они
        уже ничего не объясняют. `generation_jobs` удалит база каскадом — там
        внешний ключ есть.
        """
        dependencies = await self.profile_dependencies(profile_id)
        if not dependencies.safe:
            raise DeleteBlockedError(
                f"Невозможно удалить анкету: {dependencies.describe()}",
                dependencies.blockers,
            )
        deliveries = await self._deliveries.delete_for_profile(profile_id)
        await self._profiles.delete(profile_id)
        logger.info(
            "event=profile_deleted",
            extra={"profile_id": profile_id, "deliveries_deleted": deliveries},
        )
        return {"profile_id": profile_id, "deliveries_deleted": deliveries}

    # --- Программы ----------------------------------------------------------------

    async def delete_program(self, program_id: str) -> dict:
        """Удаляет программу со всеми версиями и записями доставок.

        Блокеров нет: программа — производный объект, её всегда можно собрать
        заново из анкеты. Версии не удаляются по одной — `program_id` и есть
        программа, а версии её история.
        """
        versions = await self._programs.delete(program_id)
        deliveries = await self._deliveries.delete_for_program(program_id)
        logger.info(
            "event=program_deleted",
            extra={
                "program_id": program_id,
                "versions_deleted": versions,
                "deliveries_deleted": deliveries,
            },
        )
        return {
            "program_id": program_id,
            "versions_deleted": versions,
            "deliveries_deleted": deliveries,
        }
