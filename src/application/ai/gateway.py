"""AIGateway: единая точка обращения application-слоя к AI.

Gateway:
1. берёт конфигурацию задачи (AITaskConfig);
2. получает кандидатов через ModelSelector (primary → fallbacks);
3. для каждого кандидата безопасно получает секрет из SecretStore,
   берёт адаптер по протоколу провайдера из реестра и выполняет запрос;
4. при ошибке переходит к следующему кандидату;
5. сохраняет AIUsageRecord (успех или ошибка) — без prompt/ответа/ключей.

Gateway работает только с внутренними DTO (AIRequest/AIResponse);
специфика протоколов изолирована в адаптерах.
"""
from __future__ import annotations

import logging
import time

from src.application.ai.selection import ModelCandidate, ModelSelector
from src.domain.ai.config import AIUsageRecord
from src.domain.ai.enums import AIProtocol, AIUsageStatus
from src.domain.ai.errors import (
    AIConfigurationError,
    AIError,
)
from src.domain.ai.gateway import AIMessage, AIRequest, AIResponse
from src.infrastructure.ai.adapters import (
    AdapterRequest,
    DiscoveredModel,
    EndpointConnection,
    ProviderAdapterRegistry,
)
from src.infrastructure.ai.secrets import SecretStore
from src.infrastructure.persistence.postgres.ai_repository import (
    AIEndpointRepository,
    AIModelRepository,
    AIProviderRepository,
    AITaskConfigRepository,
    AIUsageRepository,
)

logger = logging.getLogger(__name__)


class AIGateway:
    def __init__(
        self,
        *,
        selector: ModelSelector,
        adapter_registry: ProviderAdapterRegistry,
        secret_store: SecretStore,
        task_repository: AITaskConfigRepository,
        usage_repository: AIUsageRepository,
        endpoint_repository: AIEndpointRepository,
        provider_repository: AIProviderRepository,
        model_repository: AIModelRepository,
    ) -> None:
        self._selector = selector
        self._registry = adapter_registry
        self._secrets = secret_store
        self._tasks = task_repository
        self._usage = usage_repository
        self._endpoints = endpoint_repository
        self._providers = provider_repository
        self._models = model_repository

    async def generate(self, request: AIRequest) -> AIResponse:
        """Выполняет AI-запрос с fallback по кандидатам конфигурации задачи."""
        config = await self._tasks.get(request.task_type)
        if config is None or not config.enabled:
            raise AIConfigurationError(
                f"Задача '{request.task_type.value}' не настроена или отключена"
            )

        candidates = await self._selector.select_candidates(
            request.task_type, request.requirements
        )
        if not candidates:
            raise AIConfigurationError(
                f"Для задачи '{request.task_type.value}' нет доступных моделей "
                "(включены и соответствуют требованиям)"
            )

        adapter_request = AdapterRequest(
            messages=request.messages,
            temperature=request.temperature
            if request.temperature is not None
            else config.temperature,
            max_tokens=request.max_tokens
            if request.max_tokens is not None
            else config.max_tokens,
            response_format=request.response_format,
        )

        last_error: AIError | None = None
        for candidate in candidates:
            try:
                return await self._execute(
                    candidate, adapter_request, request, config.timeout_seconds
                )
            except AIError as exc:
                last_error = exc
                logger.warning(
                    "AI-вызов не удался (модель pk=%s, приоритет=%s): %s — переход к следующему кандидату",
                    candidate.model.id,
                    candidate.priority,
                    exc.__class__.__name__,
                )
        assert last_error is not None
        raise last_error

    async def _execute(
        self,
        candidate: ModelCandidate,
        adapter_request: AdapterRequest,
        request: AIRequest,
        timeout_seconds: int,
    ) -> AIResponse:
        adapter = self._registry.get(candidate.provider.protocol)
        api_key = None
        if candidate.endpoint.secret_reference:
            api_key = await self._secrets.get(candidate.endpoint.secret_reference)

        connection = EndpointConnection(
            base_url=candidate.endpoint.base_url,
            api_key=api_key,
            # Сколько ждать ответа, задаёт настройка задачи: именно её видит и
            # меняет администратор. Таймаут подключения в интерфейс не выведен,
            # поэтому опираться на него значило бы обрывать запрос раньше
            # выставленного срока.
            timeout_seconds=timeout_seconds,
            max_retries=candidate.endpoint.max_retries,
        )

        started = time.monotonic()
        try:
            result = await adapter.generate(
                adapter_request, connection, candidate.model.model_id
            )
        except AIError as exc:
            latency_ms = int((time.monotonic() - started) * 1000)
            await self._record_usage(
                request,
                candidate,
                status=AIUsageStatus.ERROR,
                latency_ms=latency_ms,
                error_type=exc.__class__.__name__,
            )
            raise

        await self._record_usage(
            request,
            candidate,
            status=AIUsageStatus.SUCCESS,
            latency_ms=result.latency_ms,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            total_tokens=result.total_tokens,
        )
        return AIResponse(
            content=result.content,
            model=result.model,
            provider=candidate.provider.slug,
            endpoint=candidate.endpoint.name,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            total_tokens=result.total_tokens,
            latency_ms=result.latency_ms,
            raw_metadata=result.raw_metadata,
        )

    async def _record_usage(
        self,
        request: AIRequest,
        candidate: ModelCandidate,
        *,
        status: AIUsageStatus,
        latency_ms: int | None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        total_tokens: int | None = None,
        error_type: str | None = None,
    ) -> None:
        try:
            await self._usage.save(
                AIUsageRecord(
                    task_type=request.task_type,
                    provider_id=candidate.provider.id,
                    endpoint_id=candidate.endpoint.id,
                    model_id=candidate.model.id,
                    profile_id=request.profile_id,
                    program_id=request.program_id,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    total_tokens=total_tokens,
                    latency_ms=latency_ms,
                    status=status.value,
                    error_type=error_type,
                )
            )
        except Exception:  # noqa: BLE001 — usage не должен ломать основной поток
            logger.exception("Не удалось сохранить AI usage record")

    async def discover_models(self, endpoint_id: int) -> list[DiscoveredModel]:
        """Модели, о которых сообщает сам сервис.

        Нужна, чтобы администратор выбирал модель из списка, а не переписывал
        идентификатор из документации руками. Ничего не сохраняет: это
        справочный запрос, решение о добавлении принимает администратор.
        """
        endpoint = await self._endpoints.get(endpoint_id)
        if endpoint is None:
            raise AIConfigurationError("Подключение не найдено")
        provider = await self._providers.get(endpoint.provider_id)
        if provider is None:
            raise AIConfigurationError("Сервис для этого подключения не найден")

        api_key = None
        if endpoint.secret_reference:
            api_key = await self._secrets.get(endpoint.secret_reference)
        return await self.probe_models(
            protocol=provider.protocol,
            base_url=endpoint.base_url,
            api_key=api_key,
            timeout_seconds=endpoint.timeout_seconds,
        )

    async def probe_models(
        self,
        *,
        protocol: AIProtocol,
        base_url: str,
        api_key: str | None,
        timeout_seconds: int = 30,
    ) -> list[DiscoveredModel]:
        """Список моделей по ещё не сохранённым параметрам подключения.

        Нужна на этапе первичной настройки: администратор должен выбрать
        модель из списка до того, как в системе появится подключение.
        Переданный ключ здесь не сохраняется и не логируется.
        """
        adapter = self._registry.get(protocol)
        connection = EndpointConnection(
            base_url=base_url,
            api_key=api_key,
            timeout_seconds=min(timeout_seconds, 30),
            # Справочный GET дешёвый, а случайные отказы контура перед сервисом
            # встречаются регулярно. Пара повторов надёжнее, чем предлагать
            # администратору нажимать кнопку заново.
            max_retries=2,
        )
        return await adapter.list_models(connection)

    async def test_endpoint(self, endpoint_id: int) -> dict:
        """Connection test: минимальный нейтральный запрос без персональных данных.

        Результат сохраняется на эндпоинте: отчёт готовности AI должен
        отличать «подключение не проверялось» от «проверка провалилась».
        """
        endpoint = await self._endpoints.get(endpoint_id)
        if endpoint is None:
            raise AIConfigurationError("Подключение не найдено")
        provider = await self._providers.get(endpoint.provider_id)
        if provider is None:
            raise AIConfigurationError("Сервис для этого подключения не найден")

        models = await self._models.list_for_endpoint(endpoint_id)
        model_id = models[0].model_id if models else "ping"
        base = {"provider": provider.slug, "endpoint": endpoint.name, "model": model_id}

        started = time.monotonic()
        try:
            adapter = self._registry.get(provider.protocol)
            api_key = None
            if endpoint.secret_reference:
                api_key = await self._secrets.get(endpoint.secret_reference)
            connection = EndpointConnection(
                base_url=endpoint.base_url,
                api_key=api_key,
                timeout_seconds=min(endpoint.timeout_seconds, 30),
                # Пара повторов: случайный отказ контура перед сервисом не
                # должен показываться администратору как «связи нет».
                max_retries=2,
            )
            ping = AdapterRequest(
                messages=[AIMessage(role="user", content="ping")],
                temperature=0.0,
                max_tokens=1,
            )
            await adapter.generate(ping, connection, model_id)
        except AIError as exc:
            await self._record_test_result(
                endpoint_id, success=False, error_type=exc.__class__.__name__
            )
            return {
                **base,
                "success": False,
                "latency_ms": int((time.monotonic() - started) * 1000),
                "error_type": exc.__class__.__name__,
                "message": str(exc),
            }
        await self._record_test_result(endpoint_id, success=True)
        return {
            **base,
            "success": True,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "message": "Связь установлена, сервис отвечает",
        }

    async def _record_test_result(
        self, endpoint_id: int, *, success: bool, error_type: str | None = None
    ) -> None:
        """Сохранение результата не должно ломать сам тест."""
        try:
            await self._endpoints.record_test_result(
                endpoint_id, success=success, error_type=error_type
            )
        except Exception:  # noqa: BLE001
            logger.exception("Не удалось сохранить результат проверки подключения")
