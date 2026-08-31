"""Проба готовности модели перед тем, как тратить на неё полный запрос.

Зачем. Отказ модели обходится дорого именно потому, что обнаруживается поздно:
на staging сломанная модель рвала соединение через 30–90 секунд, а другая
возвращала структуру без `choices` через 601 секунду. Две такие модели
исчерпывали общий бюджет генерации, и до рабочих моделей в конце цепочки дело не
доходило — программу собирал алгоритм, хотя работающая модель была.

Почему проба — короткий `chat`-запрос, а не `GET /models`. Проверка подключения
в обоих наблюдавшихся отказах отвечала успехом: `GET /models` возвращал ответ за
0.8–1.6 секунды, а неисправна была модель. Проверять нужно тот же путь, который
потом используется, иначе проба даёт зелёный сигнал перед многоминутным
ожиданием мусора.

Когда пробуется. Лениво — перед первым обращением к конкретному кандидату, а не
ко всей цепочке сразу. Рабочая основная модель стоит одну пробу (1–3 секунды), а
до резервных дело доходит только при отказе, где проба уже экономит минуты.
Repair-запрос не пробуется: модель только что ответила.

Что кешируется. Только отказы. Положительный результат не кешируется, потому что
сразу за пробой идёт настоящий запрос — кеш ничего не сэкономил бы, зато мог бы
подтвердить работоспособность модели, которая уже упала. Отказ кешируется
короткое время: в прогоне из 20 анкет иначе пришлось бы пробовать мёртвую модель
двадцать раз.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from src.application.ai.selection import ModelCandidate
from src.domain.ai.errors import AIError

logger = logging.getLogger(__name__)

# Сколько помнить отказ модели. Пять минут — компромисс: достаточно, чтобы один
# прогон не пробовал мёртвую модель повторно, и мало, чтобы восстановившаяся
# модель вернулась в работу без вмешательства администратора.
FAILURE_TTL_SECONDS = 300


@dataclass(frozen=True)
class ProbeVerdict:
    """Результат пробы одной модели."""

    available: bool
    # Заполняется только при отказе: класс ошибки и краткое описание для журнала.
    error_type: str | None = None
    detail: str | None = None
    # True — вердикт взят из кеша, а не получен запросом.
    cached: bool = False


class ModelProbeService:
    """Проверяет готовность модели и помнит недавние отказы.

    Сервис ничего не решает о цепочке моделей: он отвечает на вопрос «стоит ли
    тратить полный запрос на эту модель». Решение о переходе к следующей
    принимает генератор — там же, где оценивается пригодность ответа.
    """

    def __init__(
        self,
        *,
        adapter_registry,
        secret_store,
        failure_ttl_seconds: int = FAILURE_TTL_SECONDS,
        clock=time.monotonic,
    ) -> None:
        self._registry = adapter_registry
        self._secrets = secret_store
        self._ttl = failure_ttl_seconds
        self._clock = clock
        # model pk → (когда истекает, вердикт). Отрицательные вердикты.
        self._failures: dict[int, tuple[float, ProbeVerdict]] = {}

    async def check(self, candidate: ModelCandidate) -> ProbeVerdict:
        """Готова ли модель принять полный запрос."""
        model_pk = candidate.model.id
        cached = self._cached_failure(model_pk)
        if cached is not None:
            logger.info(
                "event=model_probe_skipped_known_failure",
                extra={"model_pk": model_pk, "model_id": candidate.model.model_id},
            )
            return cached

        adapter = self._registry.get(candidate.provider.protocol)
        connection = await self._connection(candidate)
        started = self._clock()
        try:
            await adapter.probe(connection, candidate.model.model_id)
        except AIError as exc:
            verdict = ProbeVerdict(
                available=False,
                error_type=type(exc).__name__,
                detail=str(exc)[:200],
            )
            self._remember_failure(model_pk, verdict)
            logger.warning(
                "event=model_probe_failed",
                extra={
                    "model_pk": model_pk,
                    "model_id": candidate.model.model_id,
                    "error_type": verdict.error_type,
                    "elapsed_ms": int((self._clock() - started) * 1000),
                },
            )
            return verdict
        except Exception as exc:  # noqa: BLE001
            # Непредвиденный сбой пробы не должен отменять генерацию: считаем
            # модель доступной и даём настоящему запросу решить. Иначе ошибка в
            # пробе перекрыла бы работоспособную модель.
            logger.exception(
                "event=model_probe_error model_pk=%s", model_pk, exc_info=exc
            )
            return ProbeVerdict(available=True)

        logger.info(
            "event=model_probe_ok",
            extra={
                "model_pk": model_pk,
                "model_id": candidate.model.model_id,
                "elapsed_ms": int((self._clock() - started) * 1000),
            },
        )
        return ProbeVerdict(available=True)

    def forget(self, model_pk: int) -> None:
        """Убирает модель из списка недавних отказов.

        Нужно администратору: после починки провайдера ждать истечения TTL
        незачем.
        """
        self._failures.pop(model_pk, None)

    # --- Внутреннее ---------------------------------------------------------------

    def _cached_failure(self, model_pk: int | None) -> ProbeVerdict | None:
        if model_pk is None:
            return None
        entry = self._failures.get(model_pk)
        if entry is None:
            return None
        expires_at, verdict = entry
        if self._clock() >= expires_at:
            del self._failures[model_pk]
            return None
        return ProbeVerdict(
            available=False,
            error_type=verdict.error_type,
            detail=verdict.detail,
            cached=True,
        )

    def _remember_failure(self, model_pk: int | None, verdict: ProbeVerdict) -> None:
        if model_pk is None:
            return
        self._failures[model_pk] = (self._clock() + self._ttl, verdict)

    async def _connection(self, candidate: ModelCandidate):
        from src.infrastructure.ai.adapters import EndpointConnection

        api_key = None
        if candidate.endpoint.secret_reference:
            api_key = await self._secrets.get(candidate.endpoint.secret_reference)
        return EndpointConnection(
            base_url=candidate.endpoint.base_url,
            api_key=api_key,
            timeout_seconds=candidate.endpoint.timeout_seconds,
            max_retries=0,
        )
