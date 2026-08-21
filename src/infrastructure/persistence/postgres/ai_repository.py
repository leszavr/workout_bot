"""Репозитории AI-конфигурации (PostgreSQL, async SQLAlchemy 2.0).

Маппинг ORM-строк ↔ Pydantic-модели домена. Репозитории не содержат
бизнес-логики и ничего не знают о секретах (secret_reference — просто
строка; сам секрет живёт в SecretStore).
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.domain.ai.config import (
    AIEndpoint,
    AIModel,
    AIProvider,
    AITaskConfig,
    AITaskModelBinding,
    AIUsageRecord,
    PromptTemplate,
)
from src.domain.ai.enums import AITaskType, AIUsageStatus
from src.errors import ProfilePersistenceError
from src.infrastructure.persistence.postgres.models import (
    AIAuditEventRow,
    AIEndpointRow,
    AIModelRow,
    AIProviderRow,
    AITaskConfigRow,
    AITaskModelBindingRow,
    AIUsageRecordRow,
    PromptTemplateRow,
)


def _persistence_error(exc: SQLAlchemyError, what: str) -> ProfilePersistenceError:
    return ProfilePersistenceError(f"{what}: {exc.__class__.__name__}")


# --- Providers -----------------------------------------------------------------


class AIProviderRepository:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._sessions = session_factory

    @staticmethod
    def _to_domain(row: AIProviderRow) -> AIProvider:
        return AIProvider(
            id=row.id,
            name=row.name,
            slug=row.slug,
            protocol=row.protocol,  # type: ignore[arg-type]
            enabled=row.enabled,
            priority=row.priority,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    async def create(self, provider: AIProvider) -> AIProvider:
        try:
            async with self._sessions() as session:
                async with session.begin():
                    row = AIProviderRow(
                        name=provider.name,
                        slug=provider.slug,
                        protocol=provider.protocol.value,
                        enabled=provider.enabled,
                        priority=provider.priority,
                    )
                    session.add(row)
                    await session.flush()
                    await session.refresh(row)
                    return self._to_domain(row)
        except IntegrityError as exc:
            raise ProfilePersistenceError(
                f"Провайдер со slug '{provider.slug}' уже существует"
            ) from exc
        except SQLAlchemyError as exc:
            raise _persistence_error(exc, "Не удалось создать провайдера") from exc

    async def get(self, provider_id: int) -> AIProvider | None:
        async with self._sessions() as session:
            row = (
                await session.execute(
                    select(AIProviderRow).where(AIProviderRow.id == provider_id)
                )
            ).scalar_one_or_none()
        return self._to_domain(row) if row else None

    async def get_by_slug(self, slug: str) -> AIProvider | None:
        async with self._sessions() as session:
            row = (
                await session.execute(
                    select(AIProviderRow).where(AIProviderRow.slug == slug)
                )
            ).scalar_one_or_none()
        return self._to_domain(row) if row else None

    async def list(self) -> list[AIProvider]:
        async with self._sessions() as session:
            rows = (
                await session.execute(
                    select(AIProviderRow).order_by(
                        AIProviderRow.priority, AIProviderRow.id
                    )
                )
            ).scalars().all()
        return [self._to_domain(r) for r in rows]

    async def update(self, provider_id: int, **fields) -> AIProvider | None:
        """Обновляет только переданные поля (PATCH-семантика)."""
        if not fields:
            return await self.get(provider_id)
        try:
            async with self._sessions() as session:
                async with session.begin():
                    row = (
                        await session.execute(
                            select(AIProviderRow)
                            .where(AIProviderRow.id == provider_id)
                            .with_for_update()
                        )
                    ).scalar_one_or_none()
                    if row is None:
                        return None
                    for key, value in fields.items():
                        setattr(row, key, value)
                    await session.flush()
                    await session.refresh(row)
                    return self._to_domain(row)
        except IntegrityError as exc:
            raise ProfilePersistenceError(
                "Нарушение уникальности при обновлении провайдера"
            ) from exc
        except SQLAlchemyError as exc:
            raise _persistence_error(exc, "Не удалось обновить провайдера") from exc

    async def delete(self, provider_id: int) -> bool:
        """Удаляет провайдера вместе с эндпоинтами (CASCADE).

        Модели удаляются каскадно через эндпоинты; если модель привязана
        к задаче — FK RESTRICT не даст удалить (см. сервисный слой).
        """
        try:
            async with self._sessions() as session:
                async with session.begin():
                    result = await session.execute(
                        delete(AIProviderRow).where(AIProviderRow.id == provider_id)
                    )
                    return bool(result.rowcount)
        except IntegrityError as exc:
            raise ProfilePersistenceError(
                "Нельзя удалить провайдера: модели используются в задачах. "
                "Отключите их (enabled=false) вместо удаления."
            ) from exc
        except SQLAlchemyError as exc:
            raise _persistence_error(exc, "Не удалось удалить провайдера") from exc


# --- Endpoints -----------------------------------------------------------------


class AIEndpointRepository:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._sessions = session_factory

    @staticmethod
    def _to_domain(row: AIEndpointRow) -> AIEndpoint:
        return AIEndpoint(
            id=row.id,
            provider_id=row.provider_id,
            name=row.name,
            base_url=row.base_url,
            secret_reference=row.secret_reference,
            timeout_seconds=row.timeout_seconds,
            max_retries=row.max_retries,
            enabled=row.enabled,
            priority=row.priority,
            last_test_at=row.last_test_at,
            last_test_status=row.last_test_status,
            last_test_error_type=row.last_test_error_type,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    async def create(self, endpoint: AIEndpoint) -> AIEndpoint:
        try:
            async with self._sessions() as session:
                async with session.begin():
                    row = AIEndpointRow(
                        provider_id=endpoint.provider_id,
                        name=endpoint.name,
                        base_url=endpoint.base_url,
                        secret_reference=endpoint.secret_reference,
                        timeout_seconds=endpoint.timeout_seconds,
                        max_retries=endpoint.max_retries,
                        enabled=endpoint.enabled,
                        priority=endpoint.priority,
                    )
                    session.add(row)
                    await session.flush()
                    await session.refresh(row)
                    return self._to_domain(row)
        except IntegrityError as exc:
            raise ProfilePersistenceError(
                "Нарушение уникальности при создании эндпоинта"
            ) from exc
        except SQLAlchemyError as exc:
            raise _persistence_error(exc, "Не удалось создать эндпоинт") from exc

    async def get(self, endpoint_id: int) -> AIEndpoint | None:
        async with self._sessions() as session:
            row = (
                await session.execute(
                    select(AIEndpointRow).where(AIEndpointRow.id == endpoint_id)
                )
            ).scalar_one_or_none()
        return self._to_domain(row) if row else None

    async def list_for_provider(self, provider_id: int) -> list[AIEndpoint]:
        async with self._sessions() as session:
            rows = (
                await session.execute(
                    select(AIEndpointRow)
                    .where(AIEndpointRow.provider_id == provider_id)
                    .order_by(AIEndpointRow.priority, AIEndpointRow.id)
                )
            ).scalars().all()
        return [self._to_domain(r) for r in rows]

    async def update(self, endpoint_id: int, **fields) -> AIEndpoint | None:
        if not fields:
            return await self.get(endpoint_id)
        try:
            async with self._sessions() as session:
                async with session.begin():
                    row = (
                        await session.execute(
                            select(AIEndpointRow)
                            .where(AIEndpointRow.id == endpoint_id)
                            .with_for_update()
                        )
                    ).scalar_one_or_none()
                    if row is None:
                        return None
                    for key, value in fields.items():
                        setattr(row, key, value)
                    await session.flush()
                    await session.refresh(row)
                    return self._to_domain(row)
        except IntegrityError as exc:
            raise ProfilePersistenceError(
                "Нарушение уникальности при обновлении эндпоинта"
            ) from exc
        except SQLAlchemyError as exc:
            raise _persistence_error(exc, "Не удалось обновить эндпоинт") from exc

    async def delete(self, endpoint_id: int) -> bool:
        try:
            async with self._sessions() as session:
                async with session.begin():
                    result = await session.execute(
                        delete(AIEndpointRow).where(AIEndpointRow.id == endpoint_id)
                    )
                    return bool(result.rowcount)
        except IntegrityError as exc:
            raise ProfilePersistenceError(
                "Нельзя удалить эндпоинт: модели используются в задачах. "
                "Отключите их (enabled=false) вместо удаления."
            ) from exc
        except SQLAlchemyError as exc:
            raise _persistence_error(exc, "Не удалось удалить эндпоинт") from exc

    async def record_test_result(
        self, endpoint_id: int, *, success: bool, error_type: str | None = None
    ) -> AIEndpoint | None:
        """Сохраняет результат проверки подключения.

        Пишется только технический результат: время, статус и класс ошибки.
        Ни ключ, ни текст ответа провайдера здесь не сохраняются.
        """
        return await self.update(
            endpoint_id,
            last_test_at=datetime.now(timezone.utc),
            last_test_status=(
                AIUsageStatus.SUCCESS.value if success else AIUsageStatus.ERROR.value
            ),
            last_test_error_type=None if success else (error_type or "UnknownError")[:100],
        )


# --- Models --------------------------------------------------------------------


class AIModelRepository:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._sessions = session_factory

    @staticmethod
    def _to_domain(row: AIModelRow) -> AIModel:
        return AIModel(
            id=row.id,
            endpoint_id=row.endpoint_id,
            model_id=row.model_id,
            display_name=row.display_name,
            enabled=row.enabled,
            priority=row.priority,
            context_window=row.context_window,
            max_output_tokens=row.max_output_tokens,
            supports_structured_output=row.supports_structured_output,
            supports_json_schema=row.supports_json_schema,
            supports_streaming=row.supports_streaming,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    async def create(self, model: AIModel) -> AIModel:
        try:
            async with self._sessions() as session:
                async with session.begin():
                    row = AIModelRow(
                        endpoint_id=model.endpoint_id,
                        model_id=model.model_id,
                        display_name=model.display_name,
                        enabled=model.enabled,
                        priority=model.priority,
                        context_window=model.context_window,
                        max_output_tokens=model.max_output_tokens,
                        supports_structured_output=model.supports_structured_output,
                        supports_json_schema=model.supports_json_schema,
                        supports_streaming=model.supports_streaming,
                    )
                    session.add(row)
                    await session.flush()
                    await session.refresh(row)
                    return self._to_domain(row)
        except IntegrityError as exc:
            raise ProfilePersistenceError(
                f"Модель '{model.model_id}' уже зарегистрирована на этом эндпоинте"
            ) from exc
        except SQLAlchemyError as exc:
            raise _persistence_error(exc, "Не удалось создать модель") from exc

    async def get(self, model_pk: int) -> AIModel | None:
        async with self._sessions() as session:
            row = (
                await session.execute(
                    select(AIModelRow).where(AIModelRow.id == model_pk)
                )
            ).scalar_one_or_none()
        return self._to_domain(row) if row else None

    async def list_for_endpoint(self, endpoint_id: int) -> list[AIModel]:
        async with self._sessions() as session:
            rows = (
                await session.execute(
                    select(AIModelRow)
                    .where(AIModelRow.endpoint_id == endpoint_id)
                    .order_by(AIModelRow.priority, AIModelRow.id)
                )
            ).scalars().all()
        return [self._to_domain(r) for r in rows]

    async def list_all(self) -> list[AIModel]:
        async with self._sessions() as session:
            rows = (
                await session.execute(
                    select(AIModelRow).order_by(AIModelRow.priority, AIModelRow.id)
                )
            ).scalars().all()
        return [self._to_domain(r) for r in rows]

    async def update(self, model_pk: int, **fields) -> AIModel | None:
        if not fields:
            return await self.get(model_pk)
        try:
            async with self._sessions() as session:
                async with session.begin():
                    row = (
                        await session.execute(
                            select(AIModelRow)
                            .where(AIModelRow.id == model_pk)
                            .with_for_update()
                        )
                    ).scalar_one_or_none()
                    if row is None:
                        return None
                    for key, value in fields.items():
                        setattr(row, key, value)
                    await session.flush()
                    await session.refresh(row)
                    return self._to_domain(row)
        except IntegrityError as exc:
            raise ProfilePersistenceError(
                "Нарушение уникальности при обновлении модели"
            ) from exc
        except SQLAlchemyError as exc:
            raise _persistence_error(exc, "Не удалось обновить модель") from exc

    async def delete(self, model_pk: int) -> bool:
        """Удаляет модель. FK RESTRICT защищает привязки к задачам."""
        try:
            async with self._sessions() as session:
                async with session.begin():
                    result = await session.execute(
                        delete(AIModelRow).where(AIModelRow.id == model_pk)
                    )
                    return bool(result.rowcount)
        except IntegrityError as exc:
            raise ProfilePersistenceError(
                "Нельзя удалить модель: она используется в конфигурации задачи. "
                "Отключите её (enabled=false) вместо удаления."
            ) from exc
        except SQLAlchemyError as exc:
            raise _persistence_error(exc, "Не удалось удалить модель") from exc

    async def is_bound_to_task(self, model_pk: int) -> bool:
        async with self._sessions() as session:
            count = (
                await session.execute(
                    select(func.count())
                    .select_from(AITaskModelBindingRow)
                    .where(AITaskModelBindingRow.model_id == model_pk)
                )
            ).scalar_one()
        return bool(count)


# --- Task configs + bindings ----------------------------------------------------


class AITaskConfigRepository:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._sessions = session_factory

    @staticmethod
    def _to_domain(row: AITaskConfigRow) -> AITaskConfig:
        return AITaskConfig(
            id=row.id,
            task_type=row.task_type,  # type: ignore[arg-type]
            enabled=row.enabled,
            temperature=row.temperature,
            max_tokens=row.max_tokens,
            timeout_seconds=row.timeout_seconds,
            prompt_version=row.prompt_version,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _binding_to_domain(row: AITaskModelBindingRow) -> AITaskModelBinding:
        return AITaskModelBinding(
            id=row.id,
            task_config_id=row.task_config_id,
            model_id=row.model_id,
            priority=row.priority,
            is_primary=row.is_primary,
        )

    async def upsert(self, config: AITaskConfig) -> AITaskConfig:
        """Создаёт или обновляет конфигурацию задачи (task_type уникален)."""
        try:
            async with self._sessions() as session:
                async with session.begin():
                    row = (
                        await session.execute(
                            select(AITaskConfigRow)
                            .where(AITaskConfigRow.task_type == config.task_type.value)
                            .with_for_update()
                        )
                    ).scalar_one_or_none()
                    if row is None:
                        row = AITaskConfigRow(task_type=config.task_type.value)
                        session.add(row)
                    row.enabled = config.enabled
                    row.temperature = config.temperature
                    row.max_tokens = config.max_tokens
                    row.timeout_seconds = config.timeout_seconds
                    row.prompt_version = config.prompt_version
                    await session.flush()
                    await session.refresh(row)
                    return self._to_domain(row)
        except SQLAlchemyError as exc:
            raise _persistence_error(exc, "Не удалось сохранить конфигурацию задачи") from exc

    async def get(self, task_type: AITaskType) -> AITaskConfig | None:
        async with self._sessions() as session:
            row = (
                await session.execute(
                    select(AITaskConfigRow).where(
                        AITaskConfigRow.task_type == task_type.value
                    )
                )
            ).scalar_one_or_none()
        return self._to_domain(row) if row else None

    async def list(self) -> list[AITaskConfig]:
        async with self._sessions() as session:
            rows = (
                await session.execute(
                    select(AITaskConfigRow).order_by(AITaskConfigRow.task_type)
                )
            ).scalars().all()
        return [self._to_domain(r) for r in rows]

    async def list_bindings(self, task_config_id: int) -> list[AITaskModelBinding]:
        """Привязки задачи, отсортированные по priority (1 = primary)."""
        async with self._sessions() as session:
            rows = (
                await session.execute(
                    select(AITaskModelBindingRow)
                    .where(AITaskModelBindingRow.task_config_id == task_config_id)
                    .order_by(AITaskModelBindingRow.priority)
                )
            ).scalars().all()
        return [self._binding_to_domain(r) for r in rows]

    async def replace_bindings(
        self, task_config_id: int, model_pks: list[int]
    ) -> list[AITaskModelBinding]:
        """Атомарно заменяет привязки: индекс 0 → primary (priority=1), далее fallback."""
        try:
            async with self._sessions() as session:
                async with session.begin():
                    await session.execute(
                        delete(AITaskModelBindingRow).where(
                            AITaskModelBindingRow.task_config_id == task_config_id
                        )
                    )
                    bindings: list[AITaskModelBinding] = []
                    for index, model_pk in enumerate(model_pks):
                        row = AITaskModelBindingRow(
                            task_config_id=task_config_id,
                            model_id=model_pk,
                            priority=index + 1,
                            is_primary=index == 0,
                        )
                        session.add(row)
                        await session.flush()
                        await session.refresh(row)
                        bindings.append(self._binding_to_domain(row))
                    return bindings
        except IntegrityError as exc:
            raise ProfilePersistenceError(
                "Конфликт привязок: проверьте уникальность моделей"
            ) from exc
        except SQLAlchemyError as exc:
            raise _persistence_error(exc, "Не удалось обновить привязки моделей") from exc


# --- Prompt templates -----------------------------------------------------------


class PromptTemplateRepository:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._sessions = session_factory

    @staticmethod
    def _to_domain(row: PromptTemplateRow) -> PromptTemplate:
        return PromptTemplate(
            id=row.id,
            task_type=row.task_type,  # type: ignore[arg-type]
            version=row.version,
            name=row.name,
            system_prompt=row.system_prompt,
            user_template=row.user_template,
            enabled=row.enabled,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    async def next_version(self, task_type: AITaskType) -> int:
        async with self._sessions() as session:
            max_version = (
                await session.execute(
                    select(func.max(PromptTemplateRow.version)).where(
                        PromptTemplateRow.task_type == task_type.value
                    )
                )
            ).scalar_one_or_none()
        return (max_version or 0) + 1

    async def create(self, template: PromptTemplate) -> PromptTemplate:
        """Создаёт НОВУЮ версию. Изменение существующей версии запрещено."""
        try:
            async with self._sessions() as session:
                async with session.begin():
                    row = PromptTemplateRow(
                        task_type=template.task_type.value,
                        version=template.version,
                        name=template.name,
                        system_prompt=template.system_prompt,
                        user_template=template.user_template,
                        enabled=template.enabled,
                    )
                    session.add(row)
                    await session.flush()
                    await session.refresh(row)
                    return self._to_domain(row)
        except IntegrityError as exc:
            raise ProfilePersistenceError(
                f"Версия v{template.version} для задачи '{template.task_type}' уже существует. "
                "Создайте новую версию вместо изменения существующей."
            ) from exc
        except SQLAlchemyError as exc:
            raise _persistence_error(exc, "Не удалось создать шаблон промпта") from exc

    async def get(self, task_type: AITaskType, version: int) -> PromptTemplate | None:
        async with self._sessions() as session:
            row = (
                await session.execute(
                    select(PromptTemplateRow).where(
                        PromptTemplateRow.task_type == task_type.value,
                        PromptTemplateRow.version == version,
                    )
                )
            ).scalar_one_or_none()
        return self._to_domain(row) if row else None

    async def list_for_task(self, task_type: AITaskType) -> list[PromptTemplate]:
        async with self._sessions() as session:
            rows = (
                await session.execute(
                    select(PromptTemplateRow)
                    .where(PromptTemplateRow.task_type == task_type.value)
                    .order_by(PromptTemplateRow.version.desc())
                )
            ).scalars().all()
        return [self._to_domain(r) for r in rows]


# --- Usage records --------------------------------------------------------------


class AIUsageRepository:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._sessions = session_factory

    async def save(self, record: AIUsageRecord) -> None:
        try:
            async with self._sessions() as session:
                async with session.begin():
                    session.add(
                        AIUsageRecordRow(
                            task_type=record.task_type.value,
                            provider_id=record.provider_id,
                            endpoint_id=record.endpoint_id,
                            model_id=record.model_id,
                            profile_id=record.profile_id,
                            program_id=record.program_id,
                            input_tokens=record.input_tokens,
                            output_tokens=record.output_tokens,
                            total_tokens=record.total_tokens,
                            latency_ms=record.latency_ms,
                            status=record.status,
                            error_type=record.error_type,
                        )
                    )
        except SQLAlchemyError as exc:
            raise _persistence_error(exc, "Не удалось сохранить запись usage") from exc

    async def list_recent(self, limit: int = 50) -> list[dict]:
        async with self._sessions() as session:
            rows = (
                await session.execute(
                    select(AIUsageRecordRow)
                    .order_by(AIUsageRecordRow.created_at.desc())
                    .limit(limit)
                )
            ).scalars().all()
        return [
            {
                "id": r.id,
                "task_type": r.task_type,
                "provider_id": r.provider_id,
                "endpoint_id": r.endpoint_id,
                "model_id": r.model_id,
                "input_tokens": r.input_tokens,
                "output_tokens": r.output_tokens,
                "total_tokens": r.total_tokens,
                "latency_ms": r.latency_ms,
                "status": r.status,
                "error_type": r.error_type,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]

    async def latest_by_endpoint(self) -> dict[int, dict]:
        """Последний AI-вызов по каждому эндпоинту.

        Нужен Infrastructure Health: connection test мог пройти успешно, а
        реальные вызовы при этом падать. Такой эндпоинт честнее показывать
        как degraded, и это не требует новых запросов к провайдеру —
        данные уже есть в журнале вызовов.

        DISTINCT ON — PostgreSQL-специфично; проект работает только на
        PostgreSQL (asyncpg + JSONB).
        """
        async with self._sessions() as session:
            rows = (
                await session.execute(
                    select(AIUsageRecordRow)
                    .where(AIUsageRecordRow.endpoint_id.is_not(None))
                    .distinct(AIUsageRecordRow.endpoint_id)
                    .order_by(
                        AIUsageRecordRow.endpoint_id,
                        AIUsageRecordRow.created_at.desc(),
                    )
                )
            ).scalars().all()
        return {
            r.endpoint_id: {
                "status": r.status,
                "error_type": r.error_type,
                "created_at": r.created_at,
            }
            for r in rows
            if r.endpoint_id is not None
        }


# --- Audit events ---------------------------------------------------------------


class AIAuditRepository:
    """Audit-события административных изменений. metadata без секретов."""

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._sessions = session_factory

    async def record(
        self,
        event_type: str,
        *,
        actor: str | None = None,
        entity_type: str | None = None,
        entity_id: str | None = None,
        metadata: dict | None = None,
    ) -> None:
        try:
            async with self._sessions() as session:
                async with session.begin():
                    session.add(
                        AIAuditEventRow(
                            event_type=event_type,
                            actor=actor,
                            entity_type=entity_type,
                            entity_id=entity_id,
                            metadata_json=metadata or {},
                        )
                    )
        except SQLAlchemyError as exc:
            raise _persistence_error(exc, "Не удалось записать audit-событие") from exc

    async def list_recent(self, limit: int = 50) -> list[dict]:
        return await self._list(limit=limit)

    async def list_recent_by_types(
        self, event_types: list[str], limit: int = 50
    ) -> list[dict]:
        """События заданных типов. Используется журналом fallback."""
        if not event_types:
            return []
        return await self._list(limit=limit, event_types=event_types)

    async def _list(
        self, *, limit: int, event_types: list[str] | None = None
    ) -> list[dict]:
        query = select(AIAuditEventRow).order_by(AIAuditEventRow.created_at.desc())
        if event_types is not None:
            query = query.where(AIAuditEventRow.event_type.in_(event_types))
        async with self._sessions() as session:
            rows = (await session.execute(query.limit(limit))).scalars().all()
        return [
            {
                "id": r.id,
                "event_type": r.event_type,
                "actor": r.actor,
                "entity_type": r.entity_type,
                "entity_id": r.entity_id,
                "metadata": r.metadata_json,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
