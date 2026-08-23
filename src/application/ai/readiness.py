"""AIReadinessService: сводная готовность AI-контура и защита включения задачи.

Одна и та же логика отвечает на три вопроса:

1. «Готова ли AI-генерация прямо сейчас и что именно мешает?» — `report()`
   строит чек-лист шагов настройки, эффективную цепочку моделей и
   фактическую стратегию генерации (primary/fallback генератор).
2. «Можно ли включить AI-задачу?» — `validate_enable()` запрещает включение
   заведомо нерабочей конфигурации (нет моделей, все модели недоступны,
   протокол без адаптера, отсутствующая версия промпта).
3. «Стоит ли пытаться вызвать AI для этой генерации?» — `runtime_gate()`
   даёт машиночитаемое решение для ProgramGenerationOrchestrator.

`runtime_gate()` намеренно построен на том же чек-листе, что и `report()`:
администратор в UI и оркестратор в runtime видят одну и ту же причину, а не
две независимые реализации, которые со временем разойдутся.

Сервис не выполняет запросов к провайдеру: он читает конфигурацию и
сохранённый результат последней проверки подключения. Живую проверку
делает `AIGateway.test_endpoint`.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from src.application.ai.program_generator import PROMPTS_DIR
from src.application.ai.selection import ModelCandidate, ModelSelector
from src.domain.ai.config import AIEndpoint, AIModel, AIProvider, AITaskConfig
from src.domain.ai.enums import (
    AIFallbackReason,
    AIProtocol,
    AITaskType,
    AIUsageStatus,
)
from src.domain.ai.errors import AIConfigurationError
from src.infrastructure.ai.adapters import ProviderAdapterRegistry
from src.infrastructure.ai.secrets import SecretStore
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

# Названия генераторов для текстов, которые видит администратор.
_GENERATOR_LABELS = {GENERATOR_AI: "ИИ", "deterministic": "алгоритм"}


def _generator_label(name: str) -> str:
    return _GENERATOR_LABELS.get(name, name)


@dataclass(frozen=True)
class ReadinessCheck:
    """Шаг чек-листа настройки AI.

    blocking=False — шаг важен, но не мешает AI работать (например,
    эндпоинт без ключа: часть self-hosted эндпоинтов ключа не требует).

    reason_code — машиночитаемая причина (значение `AIFallbackReason`).
    Её заполняет сам шаг, потому что только он знает контекст: «провайдера
    нет» и «провайдер отключён» дают одинаковый статус, но разные причины.
    """

    key: str
    title: str
    status: str
    detail: str
    action: str | None = None
    blocking: bool = True
    reason_code: str | None = None


@dataclass(frozen=True)
class RuntimeGateDecision:
    """Решение о том, выполнять ли AI-вызов для конкретной генерации.

    allowed=False означает, что AI-запрос заведомо бесполезен: оркестратор
    сразу берёт детерминированный генератор и сохраняет `reason`.
    """

    allowed: bool
    reason: AIFallbackReason | None = None
    detail: str | None = None


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
        secret_store: SecretStore | None = None,
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
        self._secrets = secret_store
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
            await self._check_api_key(focus_endpoint),
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
            generation={
                "primary_generator": self._primary_generator,
                "fallback_generator": self._fallback_generator,
                "auto_generate_after_finalize": self._auto_generate,
                "ai_in_strategy": GENERATOR_AI
                in (self._primary_generator, self._fallback_generator),
            },
        )

    async def runtime_gate(self, task_type: AITaskType) -> RuntimeGateDecision:
        """Решает, выполнять ли AI-вызов, и объясняет отказ машиночитаемо.

        Используется ProgramGenerationOrchestrator перед попыткой AI: если
        конфигурация заведомо нерабочая, дорогой запрос к провайдеру не
        выполняется вообще.

        Причина берётся из первого блокирующего шага того же чек-листа, что
        показывает админка: один источник истины на настройку и на runtime.
        """
        report = await self.report(task_type)
        if report.ready:
            return RuntimeGateDecision(allowed=True)

        blocking = [
            c for c in report.checks if c.blocking and c.status != STATUS_OK
        ]
        if not blocking:
            # ready=False без блокирующих шагов означать не должно, но падать
            # из-за этого нельзя: генерация продолжится детерминированно.
            return RuntimeGateDecision(
                allowed=False,
                reason=AIFallbackReason.TASK_NOT_READY,
                detail="Настройки ИИ не готовы",
            )
        first = blocking[0]
        reason = (
            AIFallbackReason(first.reason_code)
            if first.reason_code
            else AIFallbackReason.TASK_NOT_READY
        )
        return RuntimeGateDecision(
            allowed=False, reason=reason, detail=f"{first.title}: {first.detail}"
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
                title="Сервис ИИ",
                status=STATUS_OK,
                detail=", ".join(p.name for p in usable),
            )
        if not providers:
            return ReadinessCheck(
                key="provider",
                title="Сервис ИИ",
                status=STATUS_MISSING,
                detail="Сервис ИИ не добавлен",
                action="Добавьте сервис ИИ",
                reason_code=AIFallbackReason.AI_NOT_CONFIGURED.value,
            )
        unsupported = [p for p in providers if p.protocol not in supported]
        if unsupported and not [p for p in providers if p.protocol in supported]:
            return ReadinessCheck(
                key="provider",
                title="Сервис ИИ",
                status=STATUS_FAILED,
                detail="Ни один добавленный сервис не использует поддерживаемый "
                "способ подключения: "
                + ", ".join(sorted({p.name for p in unsupported})),
                action="Добавьте сервис заново — система работает с поставщиками, "
                "совместимыми с OpenAI API",
                reason_code=AIFallbackReason.UNSUPPORTED_PROTOCOL.value,
            )
        return ReadinessCheck(
            key="provider",
            title="Сервис ИИ",
            status=STATUS_MISSING,
            detail="Все подходящие сервисы выключены",
            action="Включите сервис ИИ",
            reason_code=AIFallbackReason.PROVIDER_UNAVAILABLE.value,
        )

    def _check_endpoint(
        self, usable_providers: list[AIProvider], endpoints: list[AIEndpoint]
    ) -> ReadinessCheck:
        if endpoints:
            return ReadinessCheck(
                key="endpoint",
                title="Подключение",
                status=STATUS_OK,
                detail=", ".join(f"{e.name} → {e.base_url}" for e in endpoints),
            )
        if not usable_providers:
            return ReadinessCheck(
                key="endpoint",
                title="Подключение",
                status=STATUS_MISSING,
                detail="Нет сервиса, к которому можно добавить подключение",
                action="Сначала добавьте сервис ИИ",
                reason_code=AIFallbackReason.AI_NOT_CONFIGURED.value,
            )
        return ReadinessCheck(
            key="endpoint",
            title="Подключение",
            status=STATUS_MISSING,
            detail="Нет включённого подключения",
            action="Добавьте адрес подключения из документации поставщика",
            reason_code=AIFallbackReason.ENDPOINT_UNAVAILABLE.value,
        )

    async def _check_api_key(self, endpoint: AIEndpoint | None) -> ReadinessCheck:
        if endpoint is None:
            return ReadinessCheck(
                key="api_key",
                title="Ключ доступа",
                status=STATUS_MISSING,
                detail="Нет подключения, которое можно проверить",
                action="Добавьте подключение",
                blocking=False,
            )
        if endpoint.secret_reference:
            # Ссылка на ключ и сам ключ — разные вещи. Ключ может исчезнуть из
            # хранилища (миграция, очистка, смена ключа шифрования), и тогда
            # запросы получают 401, хотя чек-лист показывал «ключ сохранён».
            if self._secrets is not None and not await self._secrets.exists(
                endpoint.secret_reference
            ):
                return ReadinessCheck(
                    key="api_key",
                    title="Ключ доступа",
                    status=STATUS_FAILED,
                    detail=(
                        f"Для подключения «{endpoint.name}» ключ помечен как "
                        "сохранённый, но в хранилище его нет"
                    ),
                    action="Сохраните ключ доступа заново",
                    blocking=False,
                )
            return ReadinessCheck(
                key="api_key",
                title="Ключ доступа",
                status=STATUS_OK,
                detail=f"Ключ сохранён для подключения «{endpoint.name}»",
                blocking=False,
            )
        return ReadinessCheck(
            key="api_key",
            title="Ключ доступа",
            status=STATUS_WARNING,
            detail=f"Для подключения «{endpoint.name}» ключ не задан",
            action="Сохраните ключ, если поставщик требует авторизацию",
            blocking=False,
        )

    def _check_connection(self, endpoint: AIEndpoint | None) -> ReadinessCheck:
        if endpoint is None:
            return ReadinessCheck(
                key="connection",
                title="Связь с сервисом",
                status=STATUS_MISSING,
                detail="Нет подключения, которое можно проверить",
                action="Добавьте подключение",
                reason_code=AIFallbackReason.ENDPOINT_UNAVAILABLE.value,
            )
        if endpoint.last_test_status == AIUsageStatus.SUCCESS.value:
            when = (
                endpoint.last_test_at.strftime("%d.%m.%Y %H:%M")
                if endpoint.last_test_at
                else "время неизвестно"
            )
            return ReadinessCheck(
                key="connection",
                title="Связь с сервисом",
                status=STATUS_OK,
                detail=f"Связь есть, последняя проверка: {when}",
            )
        if endpoint.last_test_status == AIUsageStatus.ERROR.value:
            return ReadinessCheck(
                key="connection",
                title="Связь с сервисом",
                status=STATUS_FAILED,
                detail=f"Последняя проверка подключения «{endpoint.name}» завершилась "
                f"ошибкой: {endpoint.last_test_error_type or 'неизвестная ошибка'}",
                action="Исправьте адрес, ключ или название модели и проверьте связь снова",
                reason_code=AIFallbackReason.ENDPOINT_UNAVAILABLE.value,
            )
        return ReadinessCheck(
            key="connection",
            title="Связь с сервисом",
            status=STATUS_MISSING,
            detail=f"Связь с подключением «{endpoint.name}» ещё не проверяли",
            action="Нажмите «Проверить связь» до включения задачи",
            reason_code=AIFallbackReason.CONNECTION_NOT_TESTED.value,
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
                detail="Нет подключения, на котором можно добавить модель",
                action="Сначала добавьте подключение",
                reason_code=AIFallbackReason.ENDPOINT_UNAVAILABLE.value,
            )
        return ReadinessCheck(
            key="model",
            title="Модель",
            status=STATUS_MISSING,
            detail="Нет включённых моделей",
            action="Добавьте модель, указав её название у поставщика",
            reason_code=AIFallbackReason.MODEL_UNAVAILABLE.value,
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
                detail="Модели привязаны, но ни одна не доступна: выключена модель, "
                "подключение или сервис, либо способ подключения не поддерживается",
                action="Включите нужную модель, её подключение и сервис",
                reason_code=AIFallbackReason.MODEL_UNAVAILABLE.value,
            )
        return ReadinessCheck(
            key="task_models",
            title="Модели задачи",
            status=STATUS_MISSING,
            detail="Для задачи не выбрана ни одна модель",
            action="Выберите основную модель в настройках задачи",
            reason_code=AIFallbackReason.MODEL_UNAVAILABLE.value,
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
            detail="Задача выключена: к ИИ обращаться не будем",
            action="Включите использование ИИ и сохраните задачу",
            reason_code=AIFallbackReason.TASK_DISABLED.value,
        )

    async def _check_prompt(
        self, task_type: AITaskType, config: AITaskConfig | None
    ) -> ReadinessCheck:
        version = config.prompt_version if config else None
        available, detail = await self._resolve_prompt(task_type, version)
        if available:
            return ReadinessCheck(
                key="prompt",
                title="Инструкция для ИИ",
                status=STATUS_OK,
                detail=detail,
            )
        return ReadinessCheck(
            key="prompt",
            title="Инструкция для ИИ",
            status=STATUS_FAILED,
            detail=detail,
            action="Укажите существующую версию инструкции",
            reason_code=AIFallbackReason.TASK_NOT_READY.value,
        )

    def _check_generation_strategy(self) -> ReadinessCheck:
        primary = self._primary_generator
        fallback = self._fallback_generator
        detail = (
            f"основной генератор — {_generator_label(primary)}, "
            f"резервный — {_generator_label(fallback)}"
        )
        if GENERATOR_AI not in (primary, fallback):
            return ReadinessCheck(
                key="generation_strategy",
                title="Порядок сборки программ",
                status=STATUS_FAILED,
                detail=f"ИИ не участвует в сборке программ ({detail})",
                action="Укажите ИИ основным генератором в настройках сервера",
                reason_code=AIFallbackReason.GENERATOR_NOT_CONFIGURED.value,
            )
        if primary != GENERATOR_AI:
            return ReadinessCheck(
                key="generation_strategy",
                title="Порядок сборки программ",
                status=STATUS_WARNING,
                detail=f"ИИ используется только как резервный генератор ({detail})",
                action="Чтобы программы собирал ИИ, сделайте его основным генератором",
                blocking=False,
            )
        return ReadinessCheck(
            key="generation_strategy",
            title="Порядок сборки программ",
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
            return "выбранная модель не найдена"
        label = f"модель «{model.display_name}»"
        if not model.enabled:
            return f"{label} выключена"
        endpoint = await self._endpoints.get(model.endpoint_id)
        if endpoint is None:
            return f"{label}: подключение не найдено"
        if not endpoint.enabled:
            return f"{label}: подключение «{endpoint.name}» выключено"
        provider = await self._providers.get(endpoint.provider_id)
        if provider is None:
            return f"{label}: сервис не найден"
        if not provider.enabled:
            return f"{label}: сервис «{provider.name}» выключен"
        if provider.protocol not in supported:
            return f"{label}: такой способ подключения система не поддерживает"
        return None

    async def _resolve_prompt(
        self, task_type: AITaskType, version: int | None
    ) -> tuple[bool, str]:
        """Повторяет порядок PromptLoader: сначала БД, затем файлы."""
        if version is not None:
            template = await self._prompts.get(task_type, version)
            if template is not None and template.enabled:
                return True, f"версия №{version} из базы данных"
        file_version = version or 1
        directory = PROMPTS_DIR / f"v{file_version}"
        if (directory / "system.txt").exists() and (
            directory / "user_template.txt"
        ).exists():
            return True, f"версия №{file_version} из файлов проекта"
        db_versions = await self._prompts.list_for_task(task_type)
        available = (
            ", ".join(f"№{t.version}" for t in db_versions if t.enabled) or "нет"
        )
        return False, (
            f"инструкции версии №{file_version} нет ни в базе данных, ни в файлах "
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
