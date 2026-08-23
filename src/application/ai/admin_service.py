"""AIConfigurationService: администрирование AI-конфигурации.

Единственное место, где секреты принимаются от API и передаются в
SecretStore. Гарантии:
- секреты никогда не возвращаются наружу (только masked/has_api_key);
- audit-события не содержат секретов;
- новая версия промпта вместо изменения существующей.

Safe delete (Phase 1.1.1). Удаление конфигурации не должно оставлять
битые ссылки. Перед удалением провайдера/эндпоинта/модели сервис сам
собирает зависимости и объясняет, что именно мешает, вместо того чтобы
показать администратору общую ошибку целостности из базы. Стратегия:
объект с зависимостями сначала отключается, hard delete разрешён только
когда зависимостей нет. Исторические usage/audit-записи при этом не
удаляются никогда: они нужны для разбора инцидентов.
"""
from __future__ import annotations

import secrets as pysecrets
from dataclasses import dataclass, field

from src.domain.ai.config import (
    AIEndpoint,
    AIModel,
    AIProvider,
    AITaskConfig,
    AITaskModelBinding,
    PromptTemplate,
)
from src.domain.ai.enums import AITaskType
from src.domain.ai.errors import AIConfigurationError
from src.errors import ProfilePersistenceError, WorkoutBotError
from src.infrastructure.ai.secrets import SecretStore, mask_secret
from src.infrastructure.persistence.postgres.ai_repository import (
    AIAuditRepository,
    AIEndpointRepository,
    AIModelRepository,
    AIProviderRepository,
    AITaskConfigRepository,
    AIUsageRepository,
    PromptTemplateRepository,
)

# Тип audit-события для fallback генерации. Отдельной таблицы нет намеренно:
# журнал событий AI-контура должен быть один.
FALLBACK_EVENT_TYPE = "ai_generation_fallback"


class AIDependencyError(WorkoutBotError):
    """Удаление заблокировано зависимостями.

    Несёт машиночитаемый список блокеров, чтобы UI мог объяснить причину,
    а не показывать текст ошибки базы данных.
    """

    def __init__(self, message: str, blockers: list[dict]) -> None:
        super().__init__(message)
        self.blockers = blockers


@dataclass
class DeleteDependencies:
    """Что мешает удалить объект конфигурации."""

    blockers: list[dict] = field(default_factory=list)

    @property
    def safe(self) -> bool:
        return not self.blockers

    def describe(self) -> str:
        return "; ".join(b["detail"] for b in self.blockers)


class AIConfigurationService:
    def __init__(
        self,
        *,
        providers: AIProviderRepository,
        endpoints: AIEndpointRepository,
        models: AIModelRepository,
        tasks: AITaskConfigRepository,
        prompts: PromptTemplateRepository,
        usage: AIUsageRepository,
        audit: AIAuditRepository,
        secret_store: SecretStore,
    ) -> None:
        self._providers = providers
        self._endpoints = endpoints
        self._models = models
        self._tasks = tasks
        self._prompts = prompts
        self._usage = usage
        self._audit = audit
        self._secrets = secret_store

    # --- Providers -------------------------------------------------------------

    async def create_provider(self, provider: AIProvider, actor: str | None = None) -> AIProvider:
        created = await self._providers.create(provider)
        await self._audit.record(
            "ai_provider_created",
            actor=actor,
            entity_type="ai_provider",
            entity_id=str(created.id),
            metadata={"slug": created.slug, "protocol": created.protocol.value},
        )
        return created

    async def update_provider(
        self, provider_id: int, actor: str | None = None, **fields
    ) -> AIProvider | None:
        updated = await self._providers.update(provider_id, **fields)
        if updated is not None:
            await self._audit.record(
                "ai_provider_updated",
                actor=actor,
                entity_type="ai_provider",
                entity_id=str(provider_id),
                metadata={"fields": sorted(fields.keys())},
            )
        return updated

    async def delete_provider(self, provider_id: int, actor: str | None = None) -> bool:
        """Hard delete только при отсутствии зависимостей.

        Провайдер удаляется каскадом вместе с эндпоинтами и моделями,
        поэтому проверяется всё поддерево: модель, привязанная к задаче,
        блокирует удаление всего провайдера.
        """
        dependencies = await self.provider_dependencies(provider_id)
        if not dependencies.safe:
            raise AIDependencyError(
                f"Невозможно удалить сервис: {dependencies.describe()}. "
                "Сначала выключите его или уберите модели из настроек задач "
                "в конфигурации задач.",
                dependencies.blockers,
            )
        # Каскад удалит эндпоинты, но не секреты в SecretStore: их нужно
        # удалить явно, иначе останутся «осиротевшие» шифрованные записи.
        endpoints = await self._endpoints.list_for_provider(provider_id)
        deleted = await self._providers.delete(provider_id)
        if deleted:
            for endpoint in endpoints:
                if endpoint.secret_reference:
                    await self._secrets.delete(endpoint.secret_reference)
            await self._audit.record(
                "ai_provider_deleted",
                actor=actor,
                entity_type="ai_provider",
                entity_id=str(provider_id),
                metadata={"deleted_endpoints": len(endpoints)},
            )
        return deleted

    async def provider_dependencies(self, provider_id: int) -> DeleteDependencies:
        """Модели провайдера, привязанные к задачам, — блокеры удаления."""
        dependencies = DeleteDependencies()
        for endpoint in await self._endpoints.list_for_provider(provider_id):
            if endpoint.id is None:
                continue
            nested = await self.endpoint_dependencies(endpoint.id)
            dependencies.blockers.extend(nested.blockers)
        return dependencies

    # --- Endpoints -------------------------------------------------------------

    async def create_endpoint(
        self,
        endpoint: AIEndpoint,
        api_key: str | None = None,
        actor: str | None = None,
    ) -> AIEndpoint:
        if api_key:
            reference = f"ai-endpoint-{pysecrets.token_hex(8)}"
            await self._secrets.put(reference, api_key)
            endpoint = endpoint.model_copy(update={"secret_reference": reference})
        created = await self._endpoints.create(endpoint)
        await self._audit.record(
            "ai_endpoint_created",
            actor=actor,
            entity_type="ai_endpoint",
            entity_id=str(created.id),
            metadata={"provider_id": created.provider_id, "has_api_key": bool(api_key)},
        )
        return created

    async def update_endpoint(
        self, endpoint_id: int, actor: str | None = None, **fields
    ) -> AIEndpoint | None:
        # secret_reference нельзя менять через обычный PATCH.
        fields.pop("secret_reference", None)
        updated = await self._endpoints.update(endpoint_id, **fields)
        if updated is not None:
            await self._audit.record(
                "ai_endpoint_updated",
                actor=actor,
                entity_type="ai_endpoint",
                entity_id=str(endpoint_id),
                metadata={"fields": sorted(fields.keys())},
            )
        return updated

    async def rotate_endpoint_secret(
        self, endpoint_id: int, api_key: str, actor: str | None = None
    ) -> AIEndpoint:
        """Атомарная ротация ключа: старое значение заменяется по той же ссылке."""
        endpoint = await self._endpoints.get(endpoint_id)
        if endpoint is None:
            raise AIConfigurationError("Эндпоинт не найден")
        reference = endpoint.secret_reference or f"ai-endpoint-{pysecrets.token_hex(8)}"
        await self._secrets.put(reference, api_key)
        updated = await self._endpoints.update(endpoint_id, secret_reference=reference)
        assert updated is not None
        await self._audit.record(
            "ai_endpoint_secret_rotated",
            actor=actor,
            entity_type="ai_endpoint",
            entity_id=str(endpoint_id),
            metadata={},  # никаких данных о секрете
        )
        return updated

    async def delete_endpoint(self, endpoint_id: int, actor: str | None = None) -> bool:
        """Hard delete только при отсутствии зависимостей у его моделей."""
        dependencies = await self.endpoint_dependencies(endpoint_id)
        if not dependencies.safe:
            raise AIDependencyError(
                f"Невозможно удалить подключение: {dependencies.describe()}. "
                "Сначала выключите его или уберите модели из настроек задач "
                "в конфигурации задач.",
                dependencies.blockers,
            )
        endpoint = await self._endpoints.get(endpoint_id)
        deleted = await self._endpoints.delete(endpoint_id)
        if deleted:
            if endpoint and endpoint.secret_reference:
                await self._secrets.delete(endpoint.secret_reference)
            await self._audit.record(
                "ai_endpoint_deleted",
                actor=actor,
                entity_type="ai_endpoint",
                entity_id=str(endpoint_id),
            )
        return deleted

    async def endpoint_dependencies(self, endpoint_id: int) -> DeleteDependencies:
        """Модели эндпоинта, привязанные к задачам, — блокеры удаления."""
        dependencies = DeleteDependencies()
        for model in await self._models.list_for_endpoint(endpoint_id):
            if model.id is None:
                continue
            nested = await self.model_dependencies(model.id)
            dependencies.blockers.extend(nested.blockers)
        return dependencies

    async def endpoint_secret_view(self, endpoint_id: int) -> dict:
        """Только masked-представление; сам секрет никогда не возвращается."""
        endpoint = await self._endpoints.get(endpoint_id)
        if endpoint is None:
            raise AIConfigurationError("Эндпоинт не найден")
        if not endpoint.secret_reference:
            return {"has_api_key": False, "masked_api_key": None}
        secret = await self._secrets.get(endpoint.secret_reference)
        if secret is None:
            return {"has_api_key": False, "masked_api_key": None}
        return {"has_api_key": True, "masked_api_key": mask_secret(secret)}

    # --- Models ----------------------------------------------------------------

    async def create_model(self, model: AIModel, actor: str | None = None) -> AIModel:
        created = await self._models.create(model)
        await self._audit.record(
            "ai_model_created",
            actor=actor,
            entity_type="ai_model",
            entity_id=str(created.id),
            metadata={"model_id": created.model_id, "endpoint_id": created.endpoint_id},
        )
        return created

    async def add_models(
        self,
        endpoint_id: int,
        models: list[tuple[str, str]],
        actor: str | None = None,
    ) -> tuple[list[AIModel], list[str]]:
        """Добавляет выбранные модели, пропуская уже зарегистрированные.

        Возвращает (созданные, пропущенные model_id). Выбор нескольких моделей
        из списка сервиса не должен обрываться из-за того, что одна из них уже
        есть: это ожидаемая ситуация, а не ошибка.
        """
        existing = {
            m.model_id for m in await self._models.list_for_endpoint(endpoint_id)
        }
        created: list[AIModel] = []
        skipped: list[str] = []
        for model_id, display_name in models:
            if model_id in existing:
                skipped.append(model_id)
                continue
            existing.add(model_id)
            created.append(
                await self._models.create(
                    AIModel(
                        endpoint_id=endpoint_id,
                        model_id=model_id,
                        display_name=display_name or model_id,
                    )
                )
            )
        if created:
            await self._audit.record(
                "ai_models_added",
                actor=actor,
                entity_type="ai_endpoint",
                entity_id=str(endpoint_id),
                metadata={
                    "added": [m.model_id for m in created],
                    "skipped": skipped,
                },
            )
        return created, skipped

    async def update_model(
        self, model_pk: int, actor: str | None = None, **fields
    ) -> AIModel | None:
        updated = await self._models.update(model_pk, **fields)
        if updated is not None:
            await self._audit.record(
                "ai_model_updated",
                actor=actor,
                entity_type="ai_model",
                entity_id=str(model_pk),
                metadata={"fields": sorted(fields.keys())},
            )
        return updated

    async def delete_model(self, model_pk: int, actor: str | None = None) -> bool:
        """Hard delete только если модель не привязана ни к одной задаче."""
        dependencies = await self.model_dependencies(model_pk)
        if not dependencies.safe:
            raise AIDependencyError(
                f"Невозможно удалить модель: {dependencies.describe()}. "
                "Сначала выключите её или уберите из настроек задачи.",
                dependencies.blockers,
            )
        deleted = await self._models.delete(model_pk)
        if deleted:
            await self._audit.record(
                "ai_model_deleted",
                actor=actor,
                entity_type="ai_model",
                entity_id=str(model_pk),
            )
        return deleted

    async def model_dependencies(self, model_pk: int) -> DeleteDependencies:
        """Задачи, использующие модель. Только они блокируют удаление.

        Историю вызовов (usage) намеренно не считаем блокером: удалять
        конфигурацию из-за наличия истории нельзя, а историю удалять ради
        конфигурации — тем более.
        """
        dependencies = DeleteDependencies()
        model = await self._models.get(model_pk)
        label = f"«{model.display_name}»" if model else f"pk={model_pk}"
        for config in await self._tasks.list():
            if config.id is None:
                continue
            for binding in await self._tasks.list_bindings(config.id):
                if binding.model_id != model_pk:
                    continue
                role = "основная" if binding.is_primary else "резервная"
                dependencies.blockers.append(
                    {
                        "type": "ai_task_config",
                        "task_type": config.task_type.value,
                        "task_enabled": config.enabled,
                        "model_id": model_pk,
                        "detail": (
                            f"модель {label} используется задачей "
                            f"«{config.task_type.value}» ({role})"
                        ),
                    }
                )
        return dependencies

    # --- Task configs ------------------------------------------------------------

    async def configure_task(
        self,
        config: AITaskConfig,
        model_pks: list[int] | None = None,
        actor: str | None = None,
    ) -> tuple[AITaskConfig, list[AITaskModelBinding]]:
        """Сохраняет конфигурацию задачи и атомарно заменяет привязки моделей."""
        if model_pks is not None:
            for pk in model_pks:
                model = await self._models.get(pk)
                if model is None:
                    raise AIConfigurationError(f"Модель pk={pk} не найдена")
        saved = await self._tasks.upsert(config)
        bindings: list[AITaskModelBinding] = []
        if saved.id is not None and model_pks is not None:
            bindings = await self._tasks.replace_bindings(saved.id, model_pks)
        await self._audit.record(
            "ai_task_updated",
            actor=actor,
            entity_type="ai_task_config",
            entity_id=str(saved.id),
            metadata={
                "task_type": config.task_type.value,
                "enabled": config.enabled,
                "models": model_pks if model_pks is not None else "unchanged",
            },
        )
        return saved, bindings

    async def get_task(self, task_type: AITaskType):
        config = await self._tasks.get(task_type)
        bindings = await self._tasks.list_bindings(config.id) if config and config.id else []
        return config, bindings

    # --- Prompts -----------------------------------------------------------------

    async def create_prompt_version(
        self, template: PromptTemplate, actor: str | None = None
    ) -> PromptTemplate:
        """Создаёт новую версию; существующие версии неизменяемы."""
        existing = await self._prompts.get(template.task_type, template.version)
        if existing is not None:
            raise WorkoutBotError(
                f"Версия v{template.version} уже существует и неизменяема. "
                "Создайте новую версию."
            )
        created = await self._prompts.create(template)
        await self._audit.record(
            "ai_prompt_created",
            actor=actor,
            entity_type="prompt_template",
            entity_id=str(created.id),
            metadata={"task_type": created.task_type.value, "version": created.version},
        )
        return created

    async def next_prompt_version(self, task_type: AITaskType) -> int:
        return await self._prompts.next_version(task_type)

    # --- Observability -------------------------------------------------------------

    async def recent_usage(self, limit: int = 50) -> list[dict]:
        return await self._usage.list_recent(limit=limit)

    async def recent_audit(self, limit: int = 50) -> list[dict]:
        return await self._audit.list_recent(limit=limit)

    async def record_generation_fallback(
        self,
        *,
        requested_generator: str,
        actual_generator: str,
        reason_code: str,
        detail: str,
        ai_attempted: bool,
    ) -> None:
        """Пишет fallback генерации в существующий журнал событий.

        Отдельной таблицы под это намеренно нет: администратору нужен один
        журнал событий AI-контура, а не параллельная подсистема.

        В metadata не попадают profile_id, содержимое программы и любые
        персональные данные — только технические поля.
        """
        await self._audit.record(
            FALLBACK_EVENT_TYPE,
            actor=None,  # системное событие, не действие администратора
            entity_type="program_generation",
            entity_id=None,
            metadata={
                "requested_generator": requested_generator,
                "actual_generator": actual_generator,
                "reason_code": reason_code,
                "detail": detail[:500],
                "ai_attempted": ai_attempted,
            },
        )

    async def recent_fallback_events(self, limit: int = 50) -> list[dict]:
        """Журнал fallback: почему программа не была сгенерирована AI."""
        return await self._audit.list_recent_by_types(
            [FALLBACK_EVENT_TYPE], limit=limit
        )
