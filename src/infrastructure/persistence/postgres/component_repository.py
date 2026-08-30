"""Реестр экземпляров компонентов (PostgreSQL, async SQLAlchemy 2.0).

Регистрация и heartbeat — одна и та же операция upsert по `component_id`:
компонент не обязан помнить, регистрировался ли он раньше, и повторный вызов
после его перезапуска обновляет запись, а не создаёт дубль. Это делает
heartbeat идемпотентным без дополнительной логики на стороне компонента.

Дублирующая запись возможна при одновременной первой регистрации двух
экземпляров с одним `component_id`. Обрабатывается через `IntegrityError` с
повторным чтением: уникальный индекс — источник истины, а не предварительный
SELECT.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.domain.components import (
    Capability,
    ComponentInstance,
    ComponentMetadata,
    ComponentStatus,
    ComponentType,
)
from src.errors import ProfilePersistenceError
from src.infrastructure.persistence.postgres.models import ComponentInstanceRow


class ComponentRegistryRepository:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._sessions = session_factory

    @staticmethod
    def _to_domain(row: ComponentInstanceRow) -> ComponentInstance:
        # Неизвестные значения не роняют чтение: строка могла быть записана
        # более новой версией Backend, и реестр обязан её показать.
        capabilities: list[Capability] = []
        for raw in row.capabilities or []:
            try:
                capabilities.append(Capability(raw))
            except ValueError:
                continue
        return ComponentInstance(
            metadata=ComponentMetadata(
                component_id=row.component_id,
                component_type=ComponentType(row.component_type),
                name=row.name,
                region=row.region,
                version=row.version,
                build_sha=row.build_sha,
                contract_version=row.contract_version,
                capabilities=capabilities,
                status=ComponentStatus(row.status),
            ),
            last_heartbeat_at=row.last_heartbeat_at,
            registered_at=row.registered_at,
            updated_at=row.updated_at,
        )

    async def upsert(
        self, metadata: ComponentMetadata, *, seen_at: datetime | None = None
    ) -> ComponentInstance:
        """Регистрация и heartbeat: одна идемпотентная операция."""
        seen_at = seen_at or datetime.now(timezone.utc)
        try:
            return await self._upsert(metadata, seen_at)
        except IntegrityError:
            # Гонка первой регистрации: запись уже создал параллельный запрос.
            return await self._upsert(metadata, seen_at)
        except SQLAlchemyError as exc:
            raise ProfilePersistenceError(
                f"Не удалось записать компонент в реестр: {exc.__class__.__name__}"
            ) from exc

    async def _upsert(
        self, metadata: ComponentMetadata, seen_at: datetime
    ) -> ComponentInstance:
        async with self._sessions() as session:
            async with session.begin():
                row = (
                    await session.execute(
                        select(ComponentInstanceRow)
                        .where(ComponentInstanceRow.component_id == metadata.component_id)
                        .with_for_update()
                    )
                ).scalar_one_or_none()

                if row is None:
                    row = ComponentInstanceRow(
                        component_id=metadata.component_id,
                        registered_at=seen_at,
                    )
                    session.add(row)

                row.component_type = metadata.component_type.value
                row.name = metadata.name
                row.region = metadata.region
                row.version = metadata.version
                row.build_sha = metadata.build_sha
                row.contract_version = metadata.contract_version
                row.capabilities = [c.value for c in metadata.capabilities]
                row.status = metadata.status.value
                row.last_heartbeat_at = seen_at

                await session.flush()
                await session.refresh(row)
                return self._to_domain(row)

    async def get(self, component_id: str) -> ComponentInstance | None:
        async with self._sessions() as session:
            row = (
                await session.execute(
                    select(ComponentInstanceRow).where(
                        ComponentInstanceRow.component_id == component_id
                    )
                )
            ).scalar_one_or_none()
        return self._to_domain(row) if row else None

    async def list(
        self, *, component_type: ComponentType | None = None
    ) -> list[ComponentInstance]:
        query = select(ComponentInstanceRow).order_by(
            ComponentInstanceRow.component_type, ComponentInstanceRow.component_id
        )
        if component_type is not None:
            query = query.where(
                ComponentInstanceRow.component_type == component_type.value
            )
        async with self._sessions() as session:
            rows = (await session.execute(query)).scalars().all()
        return [self._to_domain(r) for r in rows]

    async def delete(self, component_id: str) -> bool:
        """Убирает экземпляр из реестра.

        Нужно при выводе экземпляра из эксплуатации: иначе он навсегда
        останется в списке как OFFLINE и будет мешать читать состояние.
        """
        try:
            async with self._sessions() as session:
                async with session.begin():
                    result = await session.execute(
                        delete(ComponentInstanceRow).where(
                            ComponentInstanceRow.component_id == component_id
                        )
                    )
                    return bool(result.rowcount)
        except SQLAlchemyError as exc:
            raise ProfilePersistenceError(
                f"Не удалось удалить компонент из реестра: {exc.__class__.__name__}"
            ) from exc
