"""AIReadinessService: сводная готовность AI-контура и защита включения задачи.

Одна и та же логика отвечает на два вопроса:

1. «Готова ли AI-генерация прямо сейчас и что именно мешает?» — `report()`
   строит чек-лист шагов настройки, эффективную цепочку моделей и
   фактическую стратегию генерации (primary/fallback генератор).
2. «Можно ли включить AI-задачу?» — `validate_enable()` запрещает включение
   заведомо нерабочей конфигурации (нет моделей, все модели недоступны,
   протокол без адаптера, отсутствующая версия промпта).

Сервис не выполняет запросов к провайдеру: он читает конфигурацию и
сохранённый результат последней проверки подключения. Живую проверку
делает `AIGateway.test_endpoint`.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from src.application.ai.program_generator import PROMPTS_DIR
from src.application.ai.selection import ModelCandidate, ModelSelector
from src.domain.ai.config import AIEndpoint, AIModel, AIProvider, AITaskConfig
from src.domain.ai.enums import AIProtocol, AITaskType, AIUsageStatus
from src.domain.ai.errors import AIConfigurationError
from src.infrastructure.ai.adapters import ProviderAdapterRegistry
from src.infrastructure.persistence.postgres.ai_repository import (
    AIEndpointRepository,
    AIModelRepository,
    AIProviderRepository,
    AITaskConfigRepository,
    PromptTemplateRepository,
)

# Статусы шага чек-листа.
STATUS_OK = "ok"
STATUS_WARNING = "warning"
STATUS_MISSING = "missing"
STATUS_FAILED = "failed"

GENERATOR_AI = "ai"


@dataclass(frozen=True)
class ReadinessCheck:
    """Шаг чек-листа настройки AI.

    blocking=False — шаг важен, но не мешает AI работать (например,
    эндпоинт без ключа: часть self-hosted эндпоинтов ключа не требует).
    """

    key: str
    title: str
    status: str
    detail: str
    action: str | None = None
    blocking: bool = True


@dataclass(frozen=True)
class ChainEntry:
    """Звено эффективной цепочки: что реально будет вызвано."""

    priority: int
    is_primary: bool
    provider: str
    endpoint: str
    model_id: str
    model_display_name: str
    model_pk: int | None


@dataclass
class AIReadinessReport:
    task_type: str
    ready: bool
    checks: list[ReadinessCheck] = field(default_factory=list)
    chain: list[ChainEntry] = field(default_factory=list)
    protocols: list[dict] = field(default_factory=list)
    generation: dict = field(default_factory=dict)


class AIReadinessService:
    def __init__(
        self,
        *,
        providers: AIProviderRepository,
        endpoints: AIEndpointRepository,
        models: AIModelRepository,
        tasks: AITaskConfigRepository,
        prompts: PromptTemplateRepository,
        selector: ModelSelector,
        adapter_registry: ProviderAdapterRegistry,
        primary_generator: str = GENERATOR_AI,
        fallback_generator: str = "deterministic",
        auto_generate_after_finalize: bool = True,
    ) -> None:
        self._providers = providers
        self._endpoints = endpoints
        self._models = models
        self._tasks = tasks
        self._prompts = prompts
        self._selector = selector
        self._registry = adapter_registry
        self._primary_generator = primary_generator
        self._fallback_generator = fallback_generator
        self._auto_generate = auto_generate_after_finalize

    # --- Публичный API ---------------------------------------------------------

    async def report(self, task_type: AITaskType) -> AIReadinessReport:
        """Чек-лист настройки, эффективная цепочка и стратегия генерации."""
        supported = self._supported_protocols()
        providers = await self._providers.list()
        usable_providers = [
            p for p in providers if p.enabled and p.protocol in supported
        ]

        endpoints = await self._enabled_endpoints(usable_providers)
        models = await self._enabled_models(endpoints)
        # ModelSelector отбирает по enabled/capabilities и ничего не знает о
        # реестре адаптеров: кандидата с протоколом без адаптера вызвать
        # нельзя, поэтому в эффективную цепочку он не попадает.
        candidates = [
            c
            for c in await self._selector.select_candidates(task_type)
            if c.provider.protocol in supported
        ]
        config = await self._tasks.get(task_type)
        focus_endpoint = candidates[0].endpoint if candidates else (
            endpoints[0] if endpoints else None
        )

        checks = [
            self._check_provider(providers, usable_providers, supported),
            self._check_endpoint(usable_providers, endpoints),
            self._check_api_key(focus_endpoint),
            self._check_connection(focus_endpoint),
            self._check_models(endpoints, models),
            await self._check_task_models(task_type, config, candidates),
            self._check_task_enabled(config),
            await self._check_prompt(task_type, config),
        ]
        if task_type is AITaskType.WORKOUT_GENERATION:
            checks.append(self._check_generation_strategy())

        ready = all(
            c.status == STATUS_OK for c in checks if c.blocking
        )
        return AIReadinessReport(
            task_type=task_type.value,
            ready=ready,
            checks=checks,
            chain=[self._chain_entry(c) for c in candidates],
            protocols=[
                {"value": p.value, "supported": p in supported} for p in AIProtocol
            ],
            generation={
                "primary_generator": self._primary_generator,
                "fallback_generator": self._fallback_generator,
                "auto_generate_after_finalize": self._auto_generate,
                "ai_in_strategy": GENERATOR_AI
                in (self._primary_generator, self._fallback_generator),
            },
        )

    async def validate_enable(
        self, config: AITaskConfig, model_pks: list[int] | None
    ) -> None:
        """Запрещает включение задачи в заведомо нерабочем состоянии.

        Дублирует UI-ограничения серверной проверкой. Требование минимально
        достаточное: хотя бы одна из привязанных моделей должна быть
        реально вызываемой (именно так работает выбор кандидатов в
        AIGateway) и версия промпта должна существовать.
        """
        pks = model_pks
        if pks is None:
            stored = await self._tasks.get(config.task_type)
            existing = (
                await self._tasks.list_bindings(stored.id)
                if stored is not None and stored.id is not None
                else []
            )
            pks = [b.model_id for b in existing]
        if not pks:
            raise AIConfigurationError(
                "Нельзя включить задачу: не выбрана ни одна модель. "
                "Добавьте основную модель и сохраните задачу снова."
            )

        supported = self._supported_protocols()
        problems: list[str] = []
        usable = False
        for pk in pks:
            reason = await self._model_unusable_reason(pk, supported)
            if reason is None:
                usable = True
                break
            problems.append(reason)
        if not usable:
            raise AIConfigurationError(
                "Нельзя включить задачу: ни одна из выбранных моделей не может "
                "использоваться. " + "; ".join(problems) + "."
            )

        available, detail = await self._resolve_prompt(config.task_type, config.prompt_version)
        if not available:
            raise AIConfigurationError(f"Нельзя включить задачу: {detail}")

    # --- Шаги чек-листа ---------------------------------------------------------

    def _check_provider(
        self,
        providers: list[AIProvider],
        usable: list[AIProvider],
        supported: set[AIProtocol],
    ) -> ReadinessCheck:
        if usable:
            return ReadinessCheck(
                key="provider",
                title="Провайдер",
                status=STATUS_OK,
                detail=", ".join(f"{p.name} ({p.protocol.value})" for p in usable),
            )
        if not providers:
            return ReadinessCheck(
                key="provider",
                title="Провайдер",
                status=STATUS_MISSING,
                detail="Провайдер не создан",
                action="Создайте провайдера с поддерживаемым протоколом",
            )
        unsupported = [p for p in providers if p.protocol not in supported]
        if unsupported and not [p for p in providers if p.protocol in supported]:
            return ReadinessCheck(
                key="provider",
                title="Провайдер",
                status=STATUS_FAILED,
                detail="Все провайдеры используют протокол без адаптера: "
                + ", ".join(sorted({p.protocol.value for p in unsupported})),
                action="Создайте провайдера с протоколом "
                + ", ".join(sorted(p.value for p in supported)),
            )
        return ReadinessCheck(
            key="provider",
            title="Провайдер",
            status=STATUS_MISSING,
            detail="Все подходящие провайдеры отключены",
            action="Включите провайдера",
        )

    def _check_endpoint(
        self, usable_providers: list[AIProvider], endpoints: list[AIEndpoint]
    ) -> ReadinessCheck:
        if endpoints:
            return ReadinessCheck(
                key="endpoint",
                title="Эндпоинт",
                status=STATUS_OK,
                detail=", ".join(f"{e.name} → {e.base_url}" for e in endpoints),
            )
        if not usable_providers:
            return ReadinessCheck(
                key="endpoint",
                title="Эндпоинт",
                status=STATUS_MISSING,
                detail="Нет провайдера, к которому можно добавить эндпоинт",
                action="Сначала создайте провайдера",
            )
        return ReadinessCheck(
            key="endpoint",
            title="Эндпоинт",
            status=STATUS_MISSING,
            detail="Включённого эндпоинта нет",
            action="Создайте или включите эндпоинт с базовым URL провайдера",
        )

    def _check_api_key(self, endpoint: AIEndpoint | None) -> ReadinessCheck:
        if endpoint is None:
            return ReadinessCheck(
                key="api_key",
                title="API-ключ",
                status=STATUS_MISSING,
                detail="Нет эндпоинта для проверки",
                action="Создайте эндпоинт",
                blocking=False,
            )
        if endpoint.secret_reference:
            return ReadinessCheck(
                key="api_key",
                title="API-ключ",
                status=STATUS_OK,
                detail=f"Ключ сохранён для эндпоинта «{endpoint.name}»",
                blocking=False,
            )
        return ReadinessCheck(
            key="api_key",
            title="API-ключ",
            status=STATUS_WARNING,
            detail=f"Для эндпоинта «{endpoint.name}» ключ не задан",
            action="Сохраните ключ, если провайдер требует авторизацию",
            blocking=False,
        )

    def _check_connection(self, endpoint: AIEndpoint | None) -> ReadinessCheck:
        if endpoint is None:
            return ReadinessCheck(
                key="connection",
                title="Проверка подключения",
                status=STATUS_MISSING,
                detail="Нет эндпоинта для проверки",
                action="Создайте эндпоинт",
            )
        if endpoint.last_test_status == AIUsageStatus.SUCCESS.value:
            when = endpoint.last_test_at.isoformat() if endpoint.last_test_at else "—"
            return ReadinessCheck(
                key="connection",
                title="Проверка подключения",
                status=STATUS_OK,
                detail=f"Успешно: {when}",
            )
        if endpoint.last_test_status == AIUsageStatus.ERROR.value:
            return ReadinessCheck(
                key="connection",
                title="Проверка подключения",
                status=STATUS_FAILED,
                detail=f"Последняя проверка эндпоинта «{endpoint.name}» завершилась "
                f"ошибкой: {endpoint.last_test_error_type or 'неизвестная ошибка'}",
                action="Исправьте URL/ключ/модель и выполните проверку снова",
            )
        return ReadinessCheck(
            key="connection",
            title="Проверка подключения",
            status=STATUS_MISSING,
            detail=f"Подключение эндпоинта «{endpoint.name}» ни разу не проверялось",
            action="Нажмите «Проверить подключение» до включения задачи",
        )

    def _check_models(
        self, endpoints: list[AIEndpoint], models: list[AIModel]
    ) -> ReadinessCheck:
        if models:
            return ReadinessCheck(
                key="model",
                title="Модель",
                status=STATUS_OK,
                detail=", ".join(f"{m.display_name} ({m.model_id})" for m in models),
            )
        if not endpoints:
            return ReadinessCheck(
                key="model",
                title="Модель",
                status=STATUS_MISSING,
                detail="Нет эндпоинта, на котором можно объявить модель",
                action="Сначала создайте эндпоинт",
            )
        return ReadinessCheck(
            key="model",
            title="Модель",
            status=STATUS_MISSING,
            detail="Включённых моделей нет",
            action="Создайте или включите модель с идентификатором провайдера",
        )

    async def _check_task_models(
        self,
        task_type: AITaskType,
        config: AITaskConfig | None,
        candidates: list[ModelCandidate],
    ) -> ReadinessCheck:
        if candidates:
            primary = candidates[0]
            return ReadinessCheck(
                key="task_models",
                title="Модели задачи",
                status=STATUS_OK,
                detail=f"Основная: {primary.model.display_name}; "
                f"резервных: {len(candidates) - 1}",
            )
        bindings = (
            await self._tasks.list_bindings(config.id)
            if config and config.id
            else []
        )
        if bindings:
            return ReadinessCheck(
                key="task_models",
                title="Модели задачи",
                status=STATUS_FAILED,
                detail="Модели привязаны, но ни одна не доступна: отключена модель, "
                "эндпоинт или провайдер, либо протокол без адаптера",
                action="Включите нужную модель и её эндпоинт/провайдера",
            )
        return ReadinessCheck(
            key="task_models",
            title="Модели задачи",
            status=STATUS_MISSING,
            detail=f"Для задачи «{task_type.value}» не выбрана ни одна модель",
            action="Выберите основную модель в карточке задачи",
        )

    def _check_task_enabled(self, config: AITaskConfig | None) -> ReadinessCheck:
        if config is not None and config.enabled:
            return ReadinessCheck(
                key="task_enabled",
                title="Задача включена",
                status=STATUS_OK,
                detail="Задача включена",
            )
        return ReadinessCheck(
            key="task_enabled",
            title="Задача включена",
            status=STATUS_MISSING,
            detail="Задача выключена: AI не будет вызван",
            action="Отметьте «включена» и сохраните задачу",
        )

    async def _check_prompt(
        self, task_type: AITaskType, config: AITaskConfig | None
    ) -> ReadinessCheck:
        version = config.prompt_version if config else None
        available, detail = await self._resolve_prompt(task_type, version)
        if available:
            return ReadinessCheck(
                key="prompt",
                title="Промпт",
                status=STATUS_OK,
                detail=detail,
            )
        return ReadinessCheck(
            key="prompt",
            title="Промпт",
            status=STATUS_FAILED,
            detail=detail,
            action="Укажите существующую версию промпта или создайте новую",
        )

    def _check_generation_strategy(self) -> ReadinessCheck:
        primary = self._primary_generator
        fallback = self._fallback_generator
        detail = f"primary: {primary}, fallback: {fallback}"
        if GENERATOR_AI not in (primary, fallback):
            return ReadinessCheck(
                key="generation_strategy",
                title="Стратегия генерации",
                status=STATUS_FAILED,
                detail=f"AI не участвует в генерации программ ({detail})",
                action="Задайте PROGRAM_PRIMARY_GENERATOR=ai в конфигурации сервера",
            )
        if primary != GENERATOR_AI:
            return ReadinessCheck(
                key="generation_strategy",
                title="Стратегия генерации",
                status=STATUS_WARNING,
                detail=f"AI используется только как резервный генератор ({detail})",
                action="Для генерации через AI задайте PROGRAM_PRIMARY_GENERATOR=ai",
                blocking=False,
            )
        return ReadinessCheck(
            key="generation_strategy",
            title="Стратегия генерации",
            status=STATUS_OK,
            detail=detail,
        )

    # --- Вспомогательное --------------------------------------------------------

    def _supported_protocols(self) -> set[AIProtocol]:
        """Протоколы, для которых реально зарегистрирован адаптер."""
        registered = set(self._registry.protocols())
        return {p for p in AIProtocol if p.value in registered}

    async def _enabled_endpoints(
        self, providers: list[AIProvider]
    ) -> list[AIEndpoint]:
        result: list[AIEndpoint] = []
        for provider in providers:
            if provider.id is None:
                continue
            endpoints = await self._endpoints.list_for_provider(provider.id)
            result.extend(e for e in endpoints if e.enabled)
        return result

    async def _enabled_models(self, endpoints: list[AIEndpoint]) -> list[AIModel]:
        result: list[AIModel] = []
        for endpoint in endpoints:
            if endpoint.id is None:
                continue
            models = await self._models.list_for_endpoint(endpoint.id)
            result.extend(m for m in models if m.enabled)
        return result

    async def _model_unusable_reason(
        self, model_pk: int, supported: set[AIProtocol]
    ) -> str | None:
        """None — модель пригодна; иначе человекочитаемая причина."""
        model = await self._models.get(model_pk)
        if model is None:
            return f"модель pk={model_pk} не найдена"
        label = f"модель «{model.display_name}»"
        if not model.enabled:
            return f"{label} отключена"
        endpoint = await self._endpoints.get(model.endpoint_id)
        if endpoint is None:
            return f"{label}: эндпоинт не найден"
        if not endpoint.enabled:
            return f"{label}: эндпоинт «{endpoint.name}» отключён"
        provider = await self._providers.get(endpoint.provider_id)
        if provider is None:
            return f"{label}: провайдер не найден"
        if not provider.enabled:
            return f"{label}: провайдер «{provider.name}» отключён"
        if provider.protocol not in supported:
            return (
                f"{label}: протокол «{provider.protocol.value}» не поддерживается "
                "(адаптер не зарегистрирован)"
            )
        return None

    async def _resolve_prompt(
        self, task_type: AITaskType, version: int | None
    ) -> tuple[bool, str]:
        """Повторяет порядок PromptLoader: сначала БД, затем файлы."""
        if version is not None:
            template = await self._prompts.get(task_type, version)
            if template is not None and template.enabled:
                return True, f"версия v{version} из базы данных"
        file_version = version or 1
        directory = PROMPTS_DIR / f"v{file_version}"
        if (directory / "system.txt").exists() and (
            directory / "user_template.txt"
        ).exists():
            return True, f"версия v{file_version} из файлов промптов"
        db_versions = await self._prompts.list_for_task(task_type)
        available = ", ".join(f"v{t.version}" for t in db_versions if t.enabled) or "нет"
        return False, (
            f"промпт версии v{file_version} не найден ни в базе данных, ни в файлах "
            f"(версии в базе: {available})"
        )

    @staticmethod
    def _chain_entry(candidate: ModelCandidate) -> ChainEntry:
        return ChainEntry(
            priority=candidate.priority,
            is_primary=candidate.is_primary,
            provider=candidate.provider.name,
            endpoint=candidate.endpoint.name,
            model_id=candidate.model.model_id,
            model_display_name=candidate.model.display_name,
            model_pk=candidate.model.id,
        )
