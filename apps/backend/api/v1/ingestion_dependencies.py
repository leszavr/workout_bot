"""Сборка репозитория ingestion для Admin API.

Отдельный модуль по той же причине, что `equipment_dependencies.py`: ingestion
внешних источников — самостоятельная подсистема, и смешивать её сборку с
генерацией программ значило бы связать два независимых контура.
"""
from __future__ import annotations

from src.infrastructure.persistence.postgres.db import get_session_factory
from src.infrastructure.persistence.postgres.ingestion_repository import (
    IngestionRepository,
)


def build_ingestion_repository() -> IngestionRepository:
    return IngestionRepository(get_session_factory())
