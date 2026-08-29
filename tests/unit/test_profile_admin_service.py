"""Unit-тесты ProfileAdminService: границы удаления анкет и программ.

Проверяется то, чего база гарантировать не может: внешних ключей на
`workout_programs.profile_id` и `program_deliveries` в схеме нет, поэтому
`DELETE` анкеты прошёл бы успешно и оставил программы без анкеты.

Fake-репозитории, без PostgreSQL.
"""
from __future__ import annotations

import pytest

from src.application.deletion import DeleteBlockedError
from src.application.profiles.admin_service import ProfileAdminService
from src.domain.program import ProgramExercise, TrainingDay, WorkoutProgram

pytestmark = pytest.mark.asyncio


def _program(profile_id: str, program_id: str, version: int = 1) -> WorkoutProgram:
    return WorkoutProgram(
        program_id=program_id,
        profile_id=profile_id,
        version=version,
        title="Программа",
        duration_weeks=8,
        training_days_per_week=1,
        training_days=[
            TrainingDay(
                day_number=1,
                title="День 1",
                focus="full_body",
                exercises=[
                    ProgramExercise(
                        exercise_external_id="ex_1",
                        order=1,
                        sets=3,
                        repetitions_min=10,
                        repetitions_max=12,
                        rest_seconds=60,
                    )
                ],
            )
        ],
    )


class FakeProfiles:
    def __init__(self, profile_ids: list[str]) -> None:
        self.profile_ids = list(profile_ids)
        self.deleted: list[str] = []

    async def delete(self, profile_id: str) -> None:
        self.deleted.append(profile_id)
        if profile_id in self.profile_ids:
            self.profile_ids.remove(profile_id)


class FakePrograms:
    def __init__(self, programs: list[WorkoutProgram] | None = None) -> None:
        self.programs = list(programs or [])
        self.deleted: list[str] = []

    async def list_for_profile(self, profile_id: str) -> list[WorkoutProgram]:
        return [p for p in self.programs if p.profile_id == profile_id]

    async def delete(self, program_id: str) -> int:
        removed = [p for p in self.programs if p.program_id == program_id]
        self.programs = [p for p in self.programs if p.program_id != program_id]
        self.deleted.append(program_id)
        return len(removed)

    async def delete_for_profile(self, profile_id: str) -> int:
        removed = [p for p in self.programs if p.profile_id == profile_id]
        self.programs = [p for p in self.programs if p.profile_id != profile_id]
        return len(removed)


class FakeDeliveries:
    def __init__(self, *, per_profile: int = 0, per_program: int = 0) -> None:
        self.per_profile = per_profile
        self.per_program = per_program
        self.deleted_profiles: list[str] = []
        self.deleted_programs: list[str] = []

    async def delete_for_profile(self, profile_id: str) -> int:
        self.deleted_profiles.append(profile_id)
        return self.per_profile

    async def delete_for_program(self, program_id: str) -> int:
        self.deleted_programs.append(program_id)
        return self.per_program


def _service(
    *,
    profiles: FakeProfiles | None = None,
    programs: FakePrograms | None = None,
    deliveries: FakeDeliveries | None = None,
) -> tuple[ProfileAdminService, FakeProfiles, FakePrograms, FakeDeliveries]:
    profiles = profiles or FakeProfiles(["p-1"])
    programs = programs or FakePrograms()
    deliveries = deliveries or FakeDeliveries()
    service = ProfileAdminService(
        profiles=profiles, programs=programs, deliveries=deliveries
    )
    return service, profiles, programs, deliveries


# --- Анкеты ---------------------------------------------------------------------


async def test_profile_without_programs_is_deletable():
    service, profiles, _, deliveries = _service(
        deliveries=FakeDeliveries(per_profile=2)
    )

    result = await service.delete_profile("p-1")

    assert profiles.deleted == ["p-1"]
    # Записи доставок уходят вместе с анкетой: без неё они ничего не объясняют.
    assert deliveries.deleted_profiles == ["p-1"]
    assert result["deliveries_deleted"] == 2


async def test_profile_with_programs_is_blocked():
    """Анкету заполнял человек: потерять её нельзя, программу — можно собрать."""
    service, profiles, _, deliveries = _service(
        programs=FakePrograms([_program("p-1", "prog-1"), _program("p-1", "prog-2")])
    )

    with pytest.raises(DeleteBlockedError) as exc_info:
        await service.delete_profile("p-1")

    blockers = exc_info.value.blockers
    assert len(blockers) == 1
    assert blockers[0]["type"] == "workout_program"
    assert blockers[0]["count"] == 2
    assert "удалите программы" in blockers[0]["detail"].lower()
    # Ни анкета, ни доставки не тронуты: отказ произошёл до записи.
    assert profiles.deleted == []
    assert deliveries.deleted_profiles == []


async def test_blocker_counts_only_programs_of_this_profile():
    service, profiles, _, _ = _service(
        profiles=FakeProfiles(["p-1", "p-2"]),
        programs=FakePrograms([_program("p-2", "prog-other")]),
    )

    dependencies = await service.profile_dependencies("p-1")
    assert dependencies.safe is True

    await service.delete_profile("p-1")
    assert profiles.deleted == ["p-1"]


async def test_profile_becomes_deletable_after_programs_are_removed():
    """Заявленный порядок работает: сначала программы, потом анкета."""
    service, profiles, programs, _ = _service(
        programs=FakePrograms([_program("p-1", "prog-1")])
    )

    with pytest.raises(DeleteBlockedError):
        await service.delete_profile("p-1")

    await service.delete_program("prog-1")
    await service.delete_profile("p-1")

    assert programs.deleted == ["prog-1"]
    assert profiles.deleted == ["p-1"]


# --- Программы ------------------------------------------------------------------


async def test_program_delete_removes_all_versions_and_deliveries():
    service, _, programs, deliveries = _service(
        programs=FakePrograms(
            [
                _program("p-1", "prog-1", version=1),
                _program("p-1", "prog-1", version=2),
                _program("p-1", "prog-1", version=3),
            ]
        ),
        deliveries=FakeDeliveries(per_program=1),
    )

    result = await service.delete_program("prog-1")

    # Программа выводится целиком: версии — её история, а не отдельные объекты.
    assert result["versions_deleted"] == 3
    assert result["deliveries_deleted"] == 1
    assert programs.programs == []
    assert deliveries.deleted_programs == ["prog-1"]


async def test_program_delete_does_not_touch_other_programs():
    service, _, programs, _ = _service(
        programs=FakePrograms([_program("p-1", "prog-1"), _program("p-1", "prog-2")])
    )

    await service.delete_program("prog-1")

    assert [p.program_id for p in programs.programs] == ["prog-2"]


async def test_program_delete_has_no_blockers():
    """Программа производна от анкеты: её удаление ничем не блокируется."""
    service, _, _, _ = _service(programs=FakePrograms([_program("p-1", "prog-1")]))

    result = await service.delete_program("prog-1")
    assert result["program_id"] == "prog-1"


async def test_deleting_missing_program_reports_zero():
    service, _, _, _ = _service()

    result = await service.delete_program("prog-absent")
    assert result["versions_deleted"] == 0
