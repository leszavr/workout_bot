"""Сборка сервисов базы знаний об оборудовании для Admin API.

Отдельный модуль, а не строки в `dependencies.py`: база знаний об оборудовании —
самостоятельная подсистема со своим набором репозиториев, и смешивать её сборку
с генерацией программ значило бы связать два независимых контура.
"""
from __future__ import annotations

from src.application.equipment.service import EquipmentKnowledgeService
from src.infrastructure.persistence.postgres.db import get_session_factory
from src.infrastructure.persistence.postgres.equipment_repository import (
    EquipmentProfileRepository,
    EquipmentRepository,
)
from src.infrastructure.persistence.postgres.exercise_knowledge_repository import (
    ExerciseKnowledgeRepository,
)
from src.infrastructure.persistence.postgres.exercise_repository import (
    ExerciseRepository,
)


def build_equipment_knowledge_service() -> EquipmentKnowledgeService:
    session_factory = get_session_factory()
    return EquipmentKnowledgeService(
        equipment=EquipmentRepository(session_factory),
        knowledge=ExerciseKnowledgeRepository(session_factory),
        exercises=ExerciseRepository(session_factory),
    )


def build_equipment_profile_repository() -> EquipmentProfileRepository:
    return EquipmentProfileRepository(get_session_factory())


def build_exercise_knowledge_repository() -> ExerciseKnowledgeRepository:
    return ExerciseKnowledgeRepository(get_session_factory())
