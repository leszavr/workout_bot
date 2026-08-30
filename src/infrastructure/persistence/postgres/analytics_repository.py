"""Чтение агрегатов генерации для админ-аналитики (read-only).

Единица анализа — `generation_jobs`: одна логическая генерация. Программа
существует только при успехе, поэтому строить аналитику на `workout_programs`
значило бы не видеть отказов вовсе.

Откуда берётся каждый показатель (ни один не выдумывается и не досчитывается):

- статус, число попыток, код отказа, длительность — `generation_jobs`;
- фактический генератор, модель, провайдер, версия инструкции, признак и
  причина fallback — `workout_programs.data->'generation'` созданной версии;
- что происходило с моделями внутри AI-попытки (прошёл ли первый ответ,
  сколько было исправлений, почему модель оставлена) — событие
  `ai_model_attempts` журнала AI-контура, связанное с генерацией через
  `ai_audit_events.entity_id = generation_jobs.job_id`;
- задержка вызова, токены и класс ошибки провайдера — `ai_usage_records`.

Обращения к ИИ считаются отдельно от генераций и намеренно не сводятся в один
показатель: одна генерация делает от нуля до нескольких вызовов (перебор
моделей и repair-попытки), поэтому «успешность вызовов» и «успешность
генераций» — разные величины, и складывать их значило бы придумать метрику.

Фильтрация, сортировка и пагинация выполняются в SQL. Причина не в
производительности: аналитика, отсортированная по первой странице, показывает
неверный ответ на вопрос «какая модель худшая», а не медленный.

Репозиторий ничего не пишет и не решает: пороги достоверности выборки и
сравнение инструкций живут в application-слое.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import (
    Integer,
    and_,
    case,
    cast,
    column,
    desc,
    func,
    literal,
    nulls_last,
    or_,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.domain.enums import GenerationJobStatus, GenerationSource
from src.errors import ProfilePersistenceError
from src.infrastructure.persistence.postgres.models import (
    AIAuditEventRow,
    AIEndpointRow,
    AIModelRow,
    AIProviderRow,
    AIUsageRecordRow,
    GenerationJobRow,
    PromptTemplateRow,
    WorkoutProgramRow,
)

# Тип события журнала AI-контура с историей попыток моделей. Значение совпадает
# с `src.application.ai.admin_service.MODEL_ATTEMPTS_EVENT_TYPE`; импортировать
# application-слой из infrastructure нельзя, поэтому константа продублирована,
# а расхождение поймано тестом.
MODEL_ATTEMPTS_EVENT_TYPE = "ai_model_attempts"

# Исходы попытки одной модели (`AIProgramGenerator`). Хранятся в журнале,
# поэтому переименование — breaking change, а не косметика.
ATTEMPT_SUCCESS = "success"
ATTEMPT_INVALID_OUTPUT = "invalid_output"

# Код отказа генерации «результат не прошёл проверку».
VALIDATION_FAILED_CODE = "validation_failed"

_SUCCEEDED = GenerationJobStatus.SUCCEEDED.value
_FAILED = GenerationJobStatus.FAILED.value
_AI = GenerationSource.AI.value
_DETERMINISTIC = GenerationSource.DETERMINISTIC.value

# Состояния проверки результата, доступные фильтру.
VALIDATION_VALID = "valid"
VALIDATION_FAILED = "failed"
VALIDATION_REPAIRED = "repaired"

# Шаг временного ряда.
BUCKET_HOUR = "hour"
BUCKET_DAY = "day"


@dataclass(frozen=True)
class AnalyticsFilter:
    """Условия выборки. Все поля необязательны и комбинируются через AND.

    Значения приходят из query-параметров, но именами колонок не являются:
    в SQL-выражения их переводит репозиторий, поэтому произвольная строка из
    запроса в `WHERE`/`ORDER BY` не попадает.
    """

    date_from: datetime | None = None
    date_to: datetime | None = None
    provider: str | None = None
    model: str | None = None
    prompt_version: int | None = None
    # Фактически сработавший генератор: ai | deterministic.
    generator: str | None = None
    # Итог операции: succeeded | failed | pending | running.
    result: str | None = None
    fallback: bool | None = None
    # valid | failed | repaired (см. `_validation_condition`).
    validation: str | None = None
    trigger: str | None = None


def _persistence_error(exc: SQLAlchemyError, what: str) -> ProfilePersistenceError:
    return ProfilePersistenceError(f"{what}: {exc}")


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _ratio(part: int, whole: int) -> float | None:
    """Доля в процентах. None, когда делить не на что.

    Ноль здесь был бы утверждением «отказов не было», хотя на самом деле не
    было ни одной генерации.
    """
    if not whole:
        return None
    return round(part * 100 / whole, 1)


def _sorted(
    items: list[dict], sort_by: str, descending: bool, *, allowed: set[str]
) -> list[dict]:
    """Сортировка готовых агрегатов по разрешённому полю.

    Здесь сортируются уже посчитанные группы, а не строки таблицы: их столько,
    сколько моделей или версий инструкций, то есть единицы. Пагинации у этих
    таблиц нет, поэтому порядок в Python применяется ко всей выборке, а не к
    странице, и «сортировка только видимого» здесь невозможна.

    Поле принимается из белого списка: неизвестное значение молча заменяется
    на первое допустимое, а не подставляется в выражение.

    Отсутствующее значение (None) всегда уходит в конец независимо от
    направления: у модели без вызовов средняя задержка неизвестна, и показывать
    её первой при сортировке «самые быстрые» было бы неверно.
    """
    key = sort_by if sort_by in allowed else sorted(allowed)[0]
    known = [item for item in items if item.get(key) is not None]
    unknown = [item for item in items if item.get(key) is None]
    known.sort(key=lambda item: item[key], reverse=descending)
    return known + unknown


class GenerationAnalyticsRepository:
    """Агрегаты по генерациям, моделям, инструкциям и вызовам ИИ."""

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._sessions = session_factory

    # --- Выражения над одной генерацией -------------------------------------------
    #
    # Собраны в одном месте: иначе определение «сработал fallback» разошлось бы
    # между сводкой, списком и карточкой генерации.

    @staticmethod
    def _generation_info():
        """JSONB-объект `generation` созданной версии программы."""
        return WorkoutProgramRow.data["generation"]

    @classmethod
    def _fallback_used(cls):
        """Признак подмены генератора — из метаданных программы.

        Событие журнала для этого не используется: оно необязательно и может
        отсутствовать, а метаданные сохраняются вместе с самой программой.
        """
        return cls._generation_info()["fallback_used"].as_boolean()

    @classmethod
    def _program_model(cls):
        return cls._generation_info()["model"].as_string()

    @classmethod
    def _program_provider(cls):
        return cls._generation_info()["provider"].as_string()

    @classmethod
    def _program_prompt_version(cls):
        return cls._generation_info()["prompt_version"].as_integer()

    @staticmethod
    def _duration_ms():
        """Длительность генерации: от начала работы до её завершения.

        Отсчёт от `started_at`, а не от `created_at`: запись создаётся в другой
        транзакции, и на быстрых генерациях разница давала отрицательные
        значения. `created_at` остаётся, когда `started_at` нет — у job,
        не дошедшего до запуска.

        Значение null у незавершённой генерации: показать «0 мс» у ещё
        выполняющейся операции значило бы утверждать, что она уже мгновенно
        завершилась.
        """
        started = func.coalesce(
            GenerationJobRow.started_at, GenerationJobRow.created_at
        )
        return case(
            (
                GenerationJobRow.completed_at.is_not(None),
                func.greatest(
                    cast(
                        func.round(
                            func.extract(
                                "epoch", GenerationJobRow.completed_at - started
                            )
                            * 1000
                        ),
                        Integer,
                    ),
                    0,
                ),
            ),
            else_=literal(None),
        )

    @classmethod
    def _attempt_stats(cls):
        """Итоги попыток моделей по каждой генерации.

        Массив попыток разворачивается в строки (`jsonb_array_elements`) и
        сворачивается в числа: сколько моделей пробовали, сколько было запросов
        на исправление, сколько ответов отвергнуто проверкой и был ли результат
        принят только после исправления.

        `DISTINCT ON` берёт свежее событие: одна генерация записывает попытки
        более одного раза (после успеха и после исчерпания цепочки), и без него
        числа удваивались бы.

        Считать это в Python нельзя: тогда фильтр и сортировка по этим
        величинам перестали бы быть серверными.
        """
        event = (
            select(
                AIAuditEventRow.entity_id.label("job_id"),
                AIAuditEventRow.metadata_json["prompt_version"]
                .as_integer()
                .label("prompt_version"),
                func.coalesce(
                    AIAuditEventRow.metadata_json["attempts"],
                    cast(literal("[]"), JSONB),
                ).label("attempts"),
            )
            .where(
                AIAuditEventRow.event_type == MODEL_ATTEMPTS_EVENT_TYPE,
                AIAuditEventRow.entity_id.is_not(None),
            )
            .distinct(AIAuditEventRow.entity_id)
            .order_by(AIAuditEventRow.entity_id, AIAuditEventRow.created_at.desc())
            .subquery("attempts_event")
        )
        attempt = (
            func.jsonb_array_elements(event.c.attempts)
            .table_valued(column("value", JSONB))
            .lateral("attempt")
        )
        outcome = attempt.c.value["outcome"].as_string()
        repairs = attempt.c.value["repair_attempts"].as_integer()
        return (
            select(
                event.c.job_id.label("job_id"),
                event.c.prompt_version.label("prompt_version"),
                event.c.attempts.label("attempts"),
                func.count(attempt.c.value).label("models_tried"),
                func.coalesce(func.sum(repairs), 0).label("repair_attempts"),
                func.count()
                .filter(outcome == ATTEMPT_INVALID_OUTPUT)
                .label("invalid_outputs"),
                func.coalesce(
                    func.bool_or(and_(outcome == ATTEMPT_SUCCESS, repairs > 0)),
                    literal(False),
                ).label("repaired"),
            )
            # OUTER JOIN: событие с пустым массивом попыток тоже должно
            # остаться строкой, иначе генерация исчезнет из выборки.
            .select_from(event.outerjoin(attempt, literal(True)))
            .group_by(event.c.job_id, event.c.prompt_version, event.c.attempts)
            .subquery("attempt_stats")
        )

    def _base(self):
        """FROM для всех запросов: генерация + её программа + итоги попыток."""
        stats = self._attempt_stats()
        source = (
            GenerationJobRow.__table__.outerjoin(
                WorkoutProgramRow.__table__,
                and_(
                    WorkoutProgramRow.program_id == GenerationJobRow.program_id,
                    WorkoutProgramRow.version == GenerationJobRow.program_version,
                ),
            )
            .outerjoin(stats, stats.c.job_id == GenerationJobRow.job_id)
        )
        return source, stats

    @classmethod
    def _prompt_version_expr(cls, stats):
        """Версия инструкции генерации.

        Метаданные программы — первичный источник (есть у каждой успешной
        AI-программы); журнал попыток нужен там, где программы нет: неудачная
        AI-попытка тоже относится к конкретной инструкции.
        """
        return func.coalesce(cls._program_prompt_version(), stats.c.prompt_version)

    @classmethod
    def _invalid_outputs(cls, stats):
        return func.coalesce(stats.c.invalid_outputs, 0)

    @classmethod
    def _repair_attempts(cls, stats):
        return func.coalesce(stats.c.repair_attempts, 0)

    @classmethod
    def _models_tried(cls, stats):
        return func.coalesce(stats.c.models_tried, 0)

    @classmethod
    def _repaired(cls, stats):
        return func.coalesce(stats.c.repaired, literal(False))

    # --- Условия фильтра -----------------------------------------------------------

    def _conditions(self, spec: AnalyticsFilter, stats) -> list:
        conditions: list = []
        if spec.date_from is not None:
            conditions.append(GenerationJobRow.created_at >= spec.date_from)
        if spec.date_to is not None:
            conditions.append(GenerationJobRow.created_at <= spec.date_to)
        if spec.trigger is not None:
            conditions.append(GenerationJobRow.trigger == spec.trigger)
        if spec.result is not None:
            conditions.append(GenerationJobRow.status == spec.result)
        if spec.generator is not None:
            conditions.append(WorkoutProgramRow.generation_source == spec.generator)
        if spec.fallback is not None:
            # NULL (программы нет) — это не «fallback не было»: у упавшей
            # генерации признака подмены генератора не существует вовсе.
            conditions.append(
                self._fallback_used().is_(True)
                if spec.fallback
                else self._fallback_used().is_not(True)
            )
        if spec.prompt_version is not None:
            conditions.append(self._prompt_version_expr(stats) == spec.prompt_version)
        if spec.model is not None:
            # Модель ищется и среди попыток, и в метаданных программы: попытка
            # могла закончиться отказом, и тогда в программе её нет.
            conditions.append(
                or_(
                    stats.c.attempts.contains([{"model_id": spec.model}]),
                    self._program_model() == spec.model,
                )
            )
        if spec.provider is not None:
            conditions.append(
                or_(
                    stats.c.attempts.contains([{"provider": spec.provider}]),
                    self._program_provider() == spec.provider,
                )
            )
        if spec.validation is not None:
            conditions.append(self._validation_condition(spec.validation, stats))
        return conditions

    def _validation_condition(self, validation: str, stats):
        """Состояние проверки результата генерации.

        - `failed` — результат отвергнут проверкой;
        - `repaired` — принят только после запроса исправления у модели;
        - `valid` — принят с первого ответа, без исправлений и отказов.

        `valid` требует и отсутствия отвергнутых ответов, и отсутствия
        исправлений: у успешной попытки с `repair_attempts > 0` счётчик
        отвергнутых ответов равен нулю (модель не была оставлена), и без второго
        условия исправленная программа попадала бы в «принято сразу».
        """
        if validation == VALIDATION_FAILED:
            return or_(
                GenerationJobRow.last_error_code == VALIDATION_FAILED_CODE,
                and_(
                    GenerationJobRow.status == _FAILED,
                    self._invalid_outputs(stats) > 0,
                ),
            )
        if validation == VALIDATION_REPAIRED:
            return and_(
                GenerationJobRow.status == _SUCCEEDED,
                self._repaired(stats).is_(True),
            )
        return and_(
            GenerationJobRow.status == _SUCCEEDED,
            self._invalid_outputs(stats) == 0,
            self._repaired(stats).is_(False),
        )

    # --- Сводка --------------------------------------------------------------------

    async def overview(self, spec: AnalyticsFilter) -> dict:
        """Ключевые числа по генерациям одним запросом.

        Все показатели — счётчики фактических записей. Производные доли
        считаются в `_ratio` и равны None при нулевой выборке: 0% означало бы
        «отказов не было», хотя генераций не было вовсе.
        """
        source, stats = self._base()
        conditions = self._conditions(spec, stats)

        succeeded = GenerationJobRow.status == _SUCCEEDED
        failed = GenerationJobRow.status == _FAILED
        by_ai = WorkoutProgramRow.generation_source == _AI
        by_deterministic = WorkoutProgramRow.generation_source == _DETERMINISTIC

        stmt = (
            select(
                func.count().label("total"),
                func.count().filter(succeeded).label("succeeded"),
                func.count().filter(failed).label("failed"),
                func.count()
                .filter(GenerationJobRow.status.notin_([_SUCCEEDED, _FAILED]))
                .label("active"),
                func.count().filter(by_ai).label("by_ai"),
                func.count().filter(by_deterministic).label("by_deterministic"),
                func.count()
                .filter(self._fallback_used().is_(True))
                .label("fallback"),
                # Deterministic fallback: запрошенным был ИИ, программу собрал
                # алгоритм. Именно этот случай означает «ИИ не сработал».
                func.count()
                .filter(and_(self._fallback_used().is_(True), by_deterministic))
                .label("deterministic_fallback"),
                func.count()
                .filter(
                    or_(
                        GenerationJobRow.last_error_code == VALIDATION_FAILED_CODE,
                        self._invalid_outputs(stats) > 0,
                    )
                )
                .label("validation_failures"),
                # sum() над integer в PostgreSQL даёт bigint/numeric, и без
                # приведения наружу уходил бы Decimal вместо числа.
                cast(
                    func.coalesce(func.sum(self._repair_attempts(stats)), 0), Integer
                ).label("repair_attempts"),
                func.count()
                .filter(self._repaired(stats).is_(True))
                .label("repaired"),
                cast(
                    func.coalesce(func.sum(GenerationJobRow.attempts), 0), Integer
                ).label("job_attempts"),
                cast(func.round(func.avg(self._duration_ms())), Integer).label(
                    "avg_duration_ms"
                ),
                cast(
                    func.percentile_cont(0.95).within_group(
                        self._duration_ms().asc()
                    ),
                    Integer,
                ).label("p95_duration_ms"),
            )
            .select_from(source)
            .where(*conditions)
        )

        try:
            async with self._sessions() as session:
                row = (await session.execute(stmt)).one()
                calls = await self._call_totals(session, spec)
        except SQLAlchemyError as exc:
            raise _persistence_error(exc, "Ошибка сводки генераций") from exc

        total = row.total
        return {
            "generations": {
                "total": total,
                "succeeded": row.succeeded,
                "failed": row.failed,
                "active": row.active,
                "by_ai": row.by_ai,
                "by_deterministic": row.by_deterministic,
                "fallback": row.fallback,
                "deterministic_fallback": row.deterministic_fallback,
                "validation_failures": row.validation_failures,
                "repaired": row.repaired,
                "repair_attempts": row.repair_attempts,
                "job_attempts": row.job_attempts,
                "success_rate": _ratio(row.succeeded, total),
                "failure_rate": _ratio(row.failed, total),
                "fallback_rate": _ratio(row.fallback, total),
                "ai_share": _ratio(row.by_ai, total),
                "avg_duration_ms": row.avg_duration_ms,
                "p95_duration_ms": row.p95_duration_ms,
            },
            "calls": calls,
        }

    async def _call_totals(self, session, spec: AnalyticsFilter) -> dict:
        """Обращения к ИИ за тот же период.

        Считаются по журналу вызовов, а не по генерациям: одна генерация делает
        несколько вызовов (перебор моделей, repair), и смешивать эти величины
        нельзя. Фильтры по генерации здесь не применяются, кроме периода:
        у отдельного вызова нет ни признака fallback, ни итога проверки.
        """
        conditions = []
        if spec.date_from is not None:
            conditions.append(AIUsageRecordRow.created_at >= spec.date_from)
        if spec.date_to is not None:
            conditions.append(AIUsageRecordRow.created_at <= spec.date_to)
        if spec.model is not None:
            conditions.append(AIModelRow.model_id == spec.model)
        if spec.provider is not None:
            conditions.append(AIProviderRow.slug == spec.provider)

        stmt = (
            select(
                func.count().label("total"),
                func.count()
                .filter(AIUsageRecordRow.status == "success")
                .label("succeeded"),
                func.count()
                .filter(AIUsageRecordRow.status != "success")
                .label("failed"),
                cast(func.round(func.avg(AIUsageRecordRow.latency_ms)), Integer).label(
                    "avg_latency_ms"
                ),
                cast(
                    func.percentile_cont(0.95).within_group(
                        AIUsageRecordRow.latency_ms.asc()
                    ),
                    Integer,
                ).label("p95_latency_ms"),
                cast(
                    func.coalesce(func.sum(AIUsageRecordRow.total_tokens), 0), Integer
                ).label("total_tokens"),
            )
            .select_from(
                AIUsageRecordRow.__table__.outerjoin(
                    AIModelRow.__table__,
                    AIModelRow.id == AIUsageRecordRow.model_id,
                ).outerjoin(
                    AIProviderRow.__table__,
                    AIProviderRow.id == AIUsageRecordRow.provider_id,
                )
            )
            .where(*conditions)
        )
        row = (await session.execute(stmt)).one()
        return {
            "total": row.total,
            "succeeded": row.succeeded,
            "failed": row.failed,
            "success_rate": _ratio(row.succeeded, row.total),
            "avg_latency_ms": row.avg_latency_ms,
            "p95_latency_ms": row.p95_latency_ms,
            "total_tokens": row.total_tokens,
        }

    # --- Временной ряд --------------------------------------------------------------

    async def timeseries(self, spec: AnalyticsFilter, *, bucket: str) -> list[dict]:
        """Генерации по интервалам: сколько всего, успешно, отказов, fallback.

        Пустые интервалы не досоздаются: пропуск в ряду означает «генераций не
        было», и подставлять туда нули значило бы утверждать больше, чем
        известно. График рисует ряд как есть.
        """
        source, stats = self._base()
        conditions = self._conditions(spec, stats)
        step = BUCKET_HOUR if bucket == BUCKET_HOUR else BUCKET_DAY
        bucket_at = func.date_trunc(step, GenerationJobRow.created_at).label("bucket")

        stmt = (
            select(
                bucket_at,
                func.count().label("total"),
                func.count()
                .filter(GenerationJobRow.status == _SUCCEEDED)
                .label("succeeded"),
                func.count()
                .filter(GenerationJobRow.status == _FAILED)
                .label("failed"),
                func.count()
                .filter(WorkoutProgramRow.generation_source == _AI)
                .label("by_ai"),
                func.count()
                .filter(self._fallback_used().is_(True))
                .label("fallback"),
                cast(func.round(func.avg(self._duration_ms())), Integer).label(
                    "avg_duration_ms"
                ),
            )
            .select_from(source)
            .where(*conditions)
            .group_by(bucket_at)
            .order_by(bucket_at)
        )

        try:
            async with self._sessions() as session:
                rows = (await session.execute(stmt)).all()
        except SQLAlchemyError as exc:
            raise _persistence_error(exc, "Ошибка временного ряда генераций") from exc

        return [
            {
                "bucket": _iso(row.bucket),
                "total": row.total,
                "succeeded": row.succeeded,
                "failed": row.failed,
                "by_ai": row.by_ai,
                "fallback": row.fallback,
                "avg_duration_ms": row.avg_duration_ms,
                "success_rate": _ratio(row.succeeded, row.total),
            }
            for row in rows
        ]

    # --- Модели ---------------------------------------------------------------------

    # Поля сортировки таблицы моделей. Белый список, а не имя колонки из
    # запроса: подстановка произвольного поля в ORDER BY — это и SQL-инъекция,
    # и утечка внутренней схемы в публичный контракт.
    MODEL_SORTS = {
        "usage",
        "success_rate",
        "failure_rate",
        "fallback_rate",
        "avg_latency_ms",
        "repair_attempts",
        "model",
    }

    async def models(
        self,
        spec: AnalyticsFilter,
        *,
        sort_by: str = "usage",
        descending: bool = True,
    ) -> list[dict]:
        """Показатели по каждой модели, участвовавшей в генерациях.

        Единица подсчёта — попытка модели, а не генерация: одна генерация
        обращается к нескольким моделям, и «использований модели» столько,
        сколько раз она отвечала. Поэтому здесь нельзя переиспользовать
        `overview`: у него другая единица.

        Задержка берётся из журнала вызовов отдельным запросом: она относится к
        вызову, а не к попытке, и внутри одной попытки вызовов может быть
        несколько (исправления).
        """
        source, stats = self._base()
        conditions = self._conditions(spec, stats)

        attempt = (
            func.jsonb_array_elements(stats.c.attempts)
            .table_valued(column("value", JSONB))
            .lateral("model_attempt")
        )
        model_id = attempt.c.value["model_id"].as_string()
        provider = attempt.c.value["provider"].as_string()
        outcome = attempt.c.value["outcome"].as_string()
        repairs = attempt.c.value["repair_attempts"].as_integer()
        is_primary = attempt.c.value["is_primary"].as_boolean()
        initial_valid = attempt.c.value["initial_valid"].as_boolean()

        stmt = (
            select(
                model_id.label("model"),
                provider.label("provider"),
                func.count().label("usage"),
                func.count().filter(outcome == ATTEMPT_SUCCESS).label("succeeded"),
                func.count().filter(outcome != ATTEMPT_SUCCESS).label("failed"),
                func.count()
                .filter(outcome == ATTEMPT_INVALID_OUTPUT)
                .label("invalid_outputs"),
                func.count()
                .filter(outcome == "provider_error")
                .label("provider_errors"),
                func.count()
                .filter(outcome == "budget_exhausted")
                .label("budget_exhausted"),
                cast(func.coalesce(func.sum(repairs), 0), Integer).label(
                    "repair_attempts"
                ),
                func.count().filter(initial_valid.is_(True)).label("initial_valid"),
                func.count().filter(is_primary.is_(True)).label("as_primary"),
                func.count().filter(is_primary.is_(False)).label("as_fallback"),
                # Генерации, где эта модель отвечала и программу всё равно
                # собрал алгоритм.
                func.count()
                .filter(self._fallback_used().is_(True))
                .label("generation_fallbacks"),
            )
            .select_from(source.join(attempt, literal(True)))
            .where(*conditions)
            .group_by(model_id, provider)
        )

        try:
            async with self._sessions() as session:
                rows = (await session.execute(stmt)).all()
                latency = await self._latency_by_model(session, spec)
        except SQLAlchemyError as exc:
            raise _persistence_error(exc, "Ошибка статистики моделей") from exc

        items = [
            {
                "model": row.model,
                "provider": row.provider,
                "usage": row.usage,
                "succeeded": row.succeeded,
                "failed": row.failed,
                "invalid_outputs": row.invalid_outputs,
                "provider_errors": row.provider_errors,
                "budget_exhausted": row.budget_exhausted,
                "repair_attempts": row.repair_attempts,
                "initial_valid": row.initial_valid,
                "as_primary": row.as_primary,
                "as_fallback": row.as_fallback,
                "generation_fallbacks": row.generation_fallbacks,
                "success_rate": _ratio(row.succeeded, row.usage),
                "failure_rate": _ratio(row.failed, row.usage),
                "fallback_rate": _ratio(row.generation_fallbacks, row.usage),
                "first_answer_rate": _ratio(row.initial_valid, row.usage),
                **latency.get(row.model, {"avg_latency_ms": None, "calls": 0}),
            }
            for row in rows
            if row.model is not None
        ]
        return _sorted(items, sort_by, descending, allowed=self.MODEL_SORTS)

    async def _latency_by_model(self, session, spec: AnalyticsFilter) -> dict:
        """Средняя задержка вызова и число вызовов по каждой модели."""
        conditions = []
        if spec.date_from is not None:
            conditions.append(AIUsageRecordRow.created_at >= spec.date_from)
        if spec.date_to is not None:
            conditions.append(AIUsageRecordRow.created_at <= spec.date_to)
        stmt = (
            select(
                AIModelRow.model_id.label("model"),
                func.count().label("calls"),
                cast(func.round(func.avg(AIUsageRecordRow.latency_ms)), Integer).label(
                    "avg_latency_ms"
                ),
            )
            .select_from(
                AIUsageRecordRow.__table__.join(
                    AIModelRow.__table__, AIModelRow.id == AIUsageRecordRow.model_id
                )
            )
            .where(*conditions)
            .group_by(AIModelRow.model_id)
        )
        rows = (await session.execute(stmt)).all()
        return {
            row.model: {"calls": row.calls, "avg_latency_ms": row.avg_latency_ms}
            for row in rows
        }

    # --- Инструкции -----------------------------------------------------------------

    PROMPT_SORTS = {
        "prompt_version",
        "usage",
        "success_rate",
        "failure_rate",
        "validation_failures",
        "fallback_rate",
        "avg_duration_ms",
        "repair_attempts",
    }

    async def prompts(
        self,
        spec: AnalyticsFilter,
        *,
        sort_by: str = "prompt_version",
        descending: bool = True,
    ) -> list[dict]:
        """Показатели по версиям инструкции.

        Единица подсчёта — генерация: инструкция участвует в генерации целиком,
        независимо от того, сколько моделей та перебрала. Поэтому числа здесь
        сопоставимы со сводкой и не сопоставимы с таблицей моделей.

        Строки без версии (алгоритмическая генерация, где инструкции нет вовсе)
        в таблицу не попадают: у них нечего сравнивать.
        """
        source, stats = self._base()
        conditions = self._conditions(spec, stats)
        version = self._prompt_version_expr(stats).label("prompt_version")

        stmt = (
            select(
                version,
                func.count().label("usage"),
                func.count()
                .filter(GenerationJobRow.status == _SUCCEEDED)
                .label("succeeded"),
                func.count()
                .filter(GenerationJobRow.status == _FAILED)
                .label("failed"),
                func.count()
                .filter(
                    or_(
                        GenerationJobRow.last_error_code == VALIDATION_FAILED_CODE,
                        self._invalid_outputs(stats) > 0,
                    )
                )
                .label("validation_failures"),
                func.count()
                .filter(self._fallback_used().is_(True))
                .label("fallback"),
                func.count()
                .filter(self._repaired(stats).is_(True))
                .label("repaired"),
                cast(
                    func.coalesce(func.sum(self._repair_attempts(stats)), 0), Integer
                ).label("repair_attempts"),
                cast(func.round(func.avg(self._duration_ms())), Integer).label(
                    "avg_duration_ms"
                ),
                func.min(GenerationJobRow.created_at).label("first_used_at"),
                func.max(GenerationJobRow.created_at).label("last_used_at"),
            )
            .select_from(source)
            .where(*conditions, version.is_not(None))
            .group_by(version)
        )

        try:
            async with self._sessions() as session:
                rows = (await session.execute(stmt)).all()
                names = await self._prompt_names(session)
        except SQLAlchemyError as exc:
            raise _persistence_error(exc, "Ошибка статистики инструкций") from exc

        items = [
            {
                "prompt_version": row.prompt_version,
                "name": names.get(row.prompt_version, {}).get("name"),
                "enabled": names.get(row.prompt_version, {}).get("enabled"),
                "usage": row.usage,
                "succeeded": row.succeeded,
                "failed": row.failed,
                "validation_failures": row.validation_failures,
                "fallback": row.fallback,
                "repaired": row.repaired,
                "repair_attempts": row.repair_attempts,
                "avg_duration_ms": row.avg_duration_ms,
                "success_rate": _ratio(row.succeeded, row.usage),
                "failure_rate": _ratio(row.failed, row.usage),
                "validation_failure_rate": _ratio(row.validation_failures, row.usage),
                "fallback_rate": _ratio(row.fallback, row.usage),
                "first_used_at": _iso(row.first_used_at),
                "last_used_at": _iso(row.last_used_at),
            }
            for row in rows
        ]
        return _sorted(items, sort_by, descending, allowed=self.PROMPT_SORTS)

    async def _prompt_names(self, session) -> dict[int, dict]:
        """Название и состояние версии инструкции.

        Инструкция могла быть удалена: тогда статистика по её версии остаётся,
        а названия нет. Показывать её как «нет данных» честнее, чем скрывать
        генерации, которые действительно были.
        """
        stmt = select(
            PromptTemplateRow.version,
            PromptTemplateRow.name,
            PromptTemplateRow.enabled,
        )
        rows = (await session.execute(stmt)).all()
        return {
            row.version: {"name": row.name, "enabled": row.enabled} for row in rows
        }

    # --- Справочники фильтров --------------------------------------------------------

    async def filter_options(self) -> dict:
        """Значения, которые реально встречаются в данных.

        Фильтр не должен предлагать модель, которой в генерациях не было: пустой
        результат выглядел бы как поломка. Список строится из журнала попыток и
        метаданных программ, а не из текущей конфигурации ИИ: удалённая модель
        остаётся в истории.
        """
        stats = self._attempt_stats()
        attempt = (
            func.jsonb_array_elements(stats.c.attempts)
            .table_valued(column("value", JSONB))
            .lateral("option_attempt")
        )
        attempt_models = select(
            attempt.c.value["model_id"].as_string().label("model"),
            attempt.c.value["provider"].as_string().label("provider"),
        ).select_from(stats.join(attempt, literal(True)))
        program_models = select(
            self._program_model().label("model"),
            self._program_provider().label("provider"),
        ).where(self._program_model().is_not(None))

        combined = attempt_models.union(program_models).subquery("options")
        # Версии берутся из того же FROM, что и остальная аналитика: собственный
        # экземпляр подзапроса попыток дал бы декартово произведение.
        version_source, version_stats = self._base()
        version_expr = self._prompt_version_expr(version_stats)
        versions = (
            select(func.distinct(version_expr).label("version"))
            .select_from(version_source)
            .where(version_expr.is_not(None))
        )

        try:
            async with self._sessions() as session:
                rows = (
                    await session.execute(
                        select(combined.c.model, combined.c.provider)
                        .where(combined.c.model.is_not(None))
                        .order_by(combined.c.model)
                    )
                ).all()
                version_rows = (await session.execute(versions)).scalars().all()
                triggers = (
                    (
                        await session.execute(
                            select(func.distinct(GenerationJobRow.trigger))
                        )
                    )
                    .scalars()
                    .all()
                )
        except SQLAlchemyError as exc:
            raise _persistence_error(exc, "Ошибка справочника фильтров") from exc

        providers = sorted({row.provider for row in rows if row.provider})
        return {
            "models": [
                {"model": row.model, "provider": row.provider} for row in rows
            ],
            "providers": providers,
            "prompt_versions": sorted(version_rows),
            "triggers": sorted(triggers),
        }

    # --- Список генераций -------------------------------------------------------------

    GENERATION_SORTS = {"created_at", "duration_ms", "attempts", "status"}

    def _generation_order(self, sort_by: str, descending: bool):
        """ORDER BY списка генераций по разрешённому полю.

        Порядок задаётся в SQL: сортировка после пагинации отвечала бы на вопрос
        «самая долгая генерация» неверно — только внутри показанной страницы.

        Второй ключ всегда `id`: без него строки с равным значением идут в
        произвольном порядке базы, и список меняется между открытиями.
        """
        if sort_by == "duration_ms":
            primary = self._duration_ms()
        elif sort_by == "attempts":
            primary = GenerationJobRow.attempts
        elif sort_by == "status":
            primary = GenerationJobRow.status
        else:
            primary = GenerationJobRow.created_at
        ordered = desc(primary) if descending else primary.asc()
        return nulls_last(ordered), GenerationJobRow.id.desc()

    async def generations(
        self,
        spec: AnalyticsFilter,
        *,
        limit: int,
        offset: int,
        sort_by: str = "created_at",
        descending: bool = True,
    ) -> tuple[int, list[dict]]:
        """Страница списка генераций с итогами попыток по каждой.

        Пагинация серверная: число генераций растёт с каждым пользователем, и
        вычитывать их целиком ради одной страницы нельзя.
        """
        source, stats = self._base()
        conditions = self._conditions(spec, stats)

        stmt = (
            select(
                GenerationJobRow.job_id,
                GenerationJobRow.profile_id,
                GenerationJobRow.trigger,
                GenerationJobRow.requested_generator,
                GenerationJobRow.status,
                GenerationJobRow.attempts,
                GenerationJobRow.program_id,
                GenerationJobRow.program_version,
                GenerationJobRow.last_error_code,
                GenerationJobRow.created_at,
                GenerationJobRow.completed_at,
                self._duration_ms().label("duration_ms"),
                WorkoutProgramRow.generation_source.label("actual_generator"),
                WorkoutProgramRow.title.label("program_title"),
                self._fallback_used().label("fallback_used"),
                self._generation_info()["fallback_reason_code"]
                .as_string()
                .label("fallback_reason_code"),
                self._program_model().label("model"),
                self._program_provider().label("provider"),
                self._prompt_version_expr(stats).label("prompt_version"),
                self._models_tried(stats).label("models_tried"),
                self._repair_attempts(stats).label("repair_attempts"),
                self._invalid_outputs(stats).label("invalid_outputs"),
                self._repaired(stats).label("repaired"),
            )
            .select_from(source)
            .where(*conditions)
            .order_by(*self._generation_order(sort_by, descending))
            .limit(limit)
            .offset(offset)
        )
        count_stmt = (
            select(func.count()).select_from(source).where(*conditions)
        )

        try:
            async with self._sessions() as session:
                total = (await session.execute(count_stmt)).scalar_one()
                rows = (await session.execute(stmt)).all()
        except SQLAlchemyError as exc:
            raise _persistence_error(exc, "Ошибка списка генераций") from exc

        return total, [self._generation_row(row) for row in rows]

    @staticmethod
    def _generation_row(row) -> dict:
        return {
            "job_id": row.job_id,
            "profile_id": row.profile_id,
            "trigger": row.trigger,
            "requested_generator": row.requested_generator,
            "actual_generator": row.actual_generator,
            "status": row.status,
            "attempts": row.attempts,
            "program_id": row.program_id,
            "program_version": row.program_version,
            "program_title": row.program_title,
            "last_error_code": row.last_error_code,
            "created_at": _iso(row.created_at),
            "completed_at": _iso(row.completed_at),
            "duration_ms": row.duration_ms,
            "fallback_used": row.fallback_used,
            "fallback_reason_code": row.fallback_reason_code,
            "model": row.model,
            "provider": row.provider,
            "prompt_version": row.prompt_version,
            "models_tried": row.models_tried,
            "repair_attempts": row.repair_attempts,
            "invalid_outputs": row.invalid_outputs,
            "repaired": row.repaired,
        }

    # --- Карточка одной генерации ------------------------------------------------------

    async def generation(self, job_id: str) -> dict | None:
        """Одна генерация: итоги, попытки моделей и вызовы ИИ.

        Попытки отдаются как список записей журнала, а не как числа: карточка
        отвечает на вопрос «что именно происходило», и здесь важен порядок
        моделей и исход каждой.
        """
        source, stats = self._base()
        stmt = (
            select(
                GenerationJobRow.job_id,
                GenerationJobRow.profile_id,
                GenerationJobRow.trigger,
                GenerationJobRow.requested_generator,
                GenerationJobRow.status,
                GenerationJobRow.attempts,
                GenerationJobRow.program_id,
                GenerationJobRow.program_version,
                GenerationJobRow.last_error_code,
                GenerationJobRow.last_error_message,
                GenerationJobRow.created_at,
                GenerationJobRow.started_at,
                GenerationJobRow.completed_at,
                self._duration_ms().label("duration_ms"),
                WorkoutProgramRow.generation_source.label("actual_generator"),
                WorkoutProgramRow.title.label("program_title"),
                WorkoutProgramRow.status.label("program_status"),
                self._fallback_used().label("fallback_used"),
                self._generation_info()["fallback_reason_code"]
                .as_string()
                .label("fallback_reason_code"),
                self._generation_info()["fallback_reason"]
                .as_string()
                .label("fallback_reason"),
                self._program_model().label("model"),
                self._program_provider().label("provider"),
                self._prompt_version_expr(stats).label("prompt_version"),
                self._models_tried(stats).label("models_tried"),
                self._repair_attempts(stats).label("repair_attempts"),
                self._invalid_outputs(stats).label("invalid_outputs"),
                self._repaired(stats).label("repaired"),
                stats.c.attempts.label("attempt_details"),
            )
            .select_from(source)
            .where(GenerationJobRow.job_id == job_id)
        )

        try:
            async with self._sessions() as session:
                row = (await session.execute(stmt)).one_or_none()
                if row is None:
                    return None
                calls = await self._calls_for_job(session, job_id)
        except SQLAlchemyError as exc:
            raise _persistence_error(exc, "Ошибка карточки генерации") from exc

        detail = self._generation_row(row)
        detail.update(
            {
                "started_at": _iso(row.started_at),
                "last_error_message": row.last_error_message,
                "program_status": row.program_status,
                "fallback_reason": row.fallback_reason,
                "attempt_details": row.attempt_details or [],
                "calls": calls,
            }
        )
        return detail

    async def _calls_for_job(self, session, job_id: str) -> list[dict]:
        """Вызовы ИИ этой генерации из журнала учёта.

        Связь по `job_id`: у записей, сделанных до появления этого поля, его
        нет, и тогда список пуст. Подставлять вместо него profile_id нельзя —
        это смешало бы вызовы разных генераций одной анкеты.
        """
        stmt = (
            select(
                AIUsageRecordRow.id,
                AIUsageRecordRow.status,
                AIUsageRecordRow.error_type,
                AIUsageRecordRow.latency_ms,
                AIUsageRecordRow.input_tokens,
                AIUsageRecordRow.output_tokens,
                AIUsageRecordRow.total_tokens,
                AIUsageRecordRow.created_at,
                AIModelRow.model_id.label("model"),
                AIProviderRow.slug.label("provider"),
                AIEndpointRow.name.label("endpoint"),
            )
            .select_from(
                AIUsageRecordRow.__table__.outerjoin(
                    AIModelRow.__table__,
                    AIModelRow.id == AIUsageRecordRow.model_id,
                )
                .outerjoin(
                    AIProviderRow.__table__,
                    AIProviderRow.id == AIUsageRecordRow.provider_id,
                )
                .outerjoin(
                    AIEndpointRow.__table__,
                    AIEndpointRow.id == AIUsageRecordRow.endpoint_id,
                )
            )
            .where(AIUsageRecordRow.job_id == job_id)
            .order_by(AIUsageRecordRow.created_at, AIUsageRecordRow.id)
        )
        rows = (await session.execute(stmt)).all()
        return [
            {
                "id": row.id,
                "status": row.status,
                "error_type": row.error_type,
                "latency_ms": row.latency_ms,
                "input_tokens": row.input_tokens,
                "output_tokens": row.output_tokens,
                "total_tokens": row.total_tokens,
                "created_at": _iso(row.created_at),
                "model": row.model,
                "provider": row.provider,
                "endpoint": row.endpoint,
            }
            for row in rows
        ]
