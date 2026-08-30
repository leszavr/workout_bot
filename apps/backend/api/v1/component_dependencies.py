"""Сборка сервисов реестра компонентов."""
from __future__ import annotations

from src.application.components.registry import (
    ComponentRegistryService,
    ConnectorDirectory,
)
from src.infrastructure.persistence.postgres.component_repository import (
    ComponentRegistryRepository,
)
from src.infrastructure.persistence.postgres.db import get_session_factory
from src.version import APP_VERSION


def build_component_registry() -> ComponentRegistryService:
    return ComponentRegistryService(
        ComponentRegistryRepository(get_session_factory()),
        backend_version=APP_VERSION,
    )


def build_connector_directory() -> ConnectorDirectory:
    return ConnectorDirectory(ComponentRegistryRepository(get_session_factory()))
