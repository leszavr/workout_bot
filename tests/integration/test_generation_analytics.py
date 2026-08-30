"""Интеграционные тесты аналитики генерации и каталога упражнений.

Проверяется то, что нельзя доказать без PostgreSQL: SQL-агрегация по связке
`generation_jobs` + `workout_programs` + журнал попыток, фильтры по JSON-полям
каталога и серверная сортировка с пагинацией.

Почему это не unit-тесты. Здесь проверяются сами SQL-выражения: JSONB-оператор
вхождения, `DISTINCT ON`, `jsonb_array_elements`, порядок сортировки и
согласованность `total` с числом строк. In-memory фейк доказал бы только
согласованность фейка сам с собой — и уже пропустил бы реальный дефект:
`equipment.contains([...])` на колонке типа `JSON` падает с
`operator does not exist: json ~~ text`, но в Python-фейке выглядел бы рабочим.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.application.ai.admin_service import MODEL_ATTEMPTS_EVENT_TYPE
from src.domain.enums import (
    GenerationJobStatus,
    GenerationSource,
    ProgramStatus,
)
from src.domain.generation import GenerationTrigger
from src.domain.program import (
    GenerationInfo,
    ProgramExercise,
    TrainingDay,
    WorkoutProgram,
)
from src.domain.profile import FitnessProfile
from src.infrastructure.config import DATABASE_URL
from src.infrastructure.persistence.postgres.analytics_repository import (
    AnalyticsFilter,
    GenerationAnalyticsRepository,
)
from src.infrastructure.persistence.postgres.exercise_repository import (
    ExerciseQuery,
    ExerciseRepository,
)
from src.infrastructure.persistence.postgres.models import (
    AIAuditEventRow,
    AIUsageRecordRow,
    ConsentRow,
    GenerationJobRow,
    ProfileRow,
    UserRow,
    WorkoutProgramRow,
)
from src.infrastructure.persistence.postgres.profile_repository import (
    PostgresProfileRepository,
)
from src.infrastructure.persistence.postgres.program_repository import (
    PostgresProgramRepository,
)

pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL is not set")

TEST_TELEGRAM_ID = "900777"
PROFILE_PREFIX = "test-analytics-"
JOB_PREFIX = "test-analytics-job-"

# Упражнение существующего каталога: тесты аналитики не импортируют каталог и не
# зависят от его наполнения, поэтому берут произвольный валидный external_id.
EXERCISE_ID = "Barbell_Full_Squat"


@pytest.fixture
async def engine():
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def sessions(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
def window() -> datetime:
    """Момент начала теста как граница выборки.

    База общая: в ней есть генерации других тестов и ручных прогонов. Без
    границы по времени сводка считала бы их вместе с данными теста, и тест
    падал бы по чужим записям. Фильтр по периоду — часть проверяемого
    контракта, поэтому отдельного «тестового режима» здесь не появляется.
    """
    return datetime.now(timezone.utc)


@pytest.fixture(autouse=True)
async def cleanup(sessions):
    """Удаляет только собственные записи: база общая с другими тестами."""

    async def _purge() -> None:
        async with sessions() as session:
            async with session.begin():
                profile_ids = (
                    (
                        await session.execute(
                            select(ProfileRow.profile_id).where(
                                ProfileRow.profile_id.like(f"{PROFILE_PREFIX}%")
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                await session.execute(
                    delete(AIAuditEventRow).where(
                        AIAuditEventRow.entity_id.like(f"{JOB_PREFIX}%")
                    )
                )
                await session.execute(
                    delete(AIUsageRecordRow).where(
                        AIUsageRecordRow.job_id.like(f"{JOB_PREFIX}%")
                    )
                )
                if profile_ids:
                    await session.execute(
                        delete(GenerationJobRow).where(
                            GenerationJobRow.profile_id.in_(profile_ids)
                        )
                    )
                    await session.execute(
                        delete(WorkoutProgramRow).where(
                            WorkoutProgramRow.profile_id.in_(profile_ids)
                        )
                    )
                user_ids = (
                    (
                        await session.execute(
                            select(UserRow.id).where(
                                UserRow.telegram_user_id == TEST_TELEGRAM_ID
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                if user_ids:
                    await session.execute(
                        delete(ConsentRow).where(ConsentRow.user_id.in_(user_ids))
                    )
                    await session.execute(
                        delete(ProfileRow).where(ProfileRow.user_id.in_(user_ids))
                    )
                    await session.execute(
                        delete(UserRow).where(UserRow.id.in_(user_ids))
                    )

    await _purge()
    yield
    await _purge()


# --- Фабрики данных --------------------------------------------------------------


def _profile_id() -> str:
    return f"{PROFILE_PREFIX}{uuid.uuid4().hex[:8]}"


async def _save_profile(sessions, profile_id: str) -> FitnessProfile:
    profile = FitnessProfile(profile_id=profile_id)
    profile.source.bot_user_id = TEST_TELEGRAM_ID
    profile.source.telegram_username = "test_analytics"
    profile.client.name = "Тест аналитики"
    await PostgresProfileRepository(sessions).save(profile)
    return profile


async def _save_program(
    sessions,
    profile_id: str,
    *,
    source: GenerationSource,
    fallback_used: bool = False,
    fallback_reason_code: str | None = None,
    model: str | None = None,
    provider: str | None = None,
    prompt_version: int | None = None,
) -> WorkoutProgram:
    program = WorkoutProgram(
        program_id=uuid.uuid4().hex,
        profile_id=profile_id,
        version=1,
        status=ProgramStatus.VALIDATED,
        title="Программа аналитики",
        duration_weeks=4,
        training_days_per_week=1,
        training_days=[
            TrainingDay(
                day_number=1,
                title="День 1",
                focus="Всё тело",
                exercises=[
                    ProgramExercise(
                        exercise_external_id=EXERCISE_ID,
                        order=1,
                        sets=3,
                        repetitions_min=8,
                        repetitions_max=12,
                        rest_seconds=90,
                    )
                ],
            )
        ],
        generation=GenerationInfo(
            source=source,
            requested_generator=GenerationSource.AI if fallback_used else source,
            actual_generator=source,
            fallback_used=fallback_used,
            fallback_reason_code=fallback_reason_code,
            model=model,
            provider=provider,
            prompt_version=prompt_version,
        ),
    )
    return await PostgresProgramRepository(sessions).save(program)


async def _save_job(
    sessions,
    profile_id: str,
    *,
    status: GenerationJobStatus,
    job_id: str | None = None,
    requested_generator: str = GenerationSource.AI.value,
    program: WorkoutProgram | None = None,
    error_code: str | None = None,
    attempts: int = 1,
    duration_ms: int = 1000,
    created_at: datetime | None = None,
) -> str:
    """Пишет operational-запись напрямую.

    Прогон настоящей генерации здесь не подходит: тесту нужны конкретные
    комбинации исхода, длительности и метаданных, включая те, которые в живом
    прогоне зависят от доступности провайдера.
    """
    identifier = job_id or f"{JOB_PREFIX}{uuid.uuid4().hex[:8]}"
    started = created_at or datetime.now(timezone.utc)
    completed = (
        started + timedelta(milliseconds=duration_ms)
        if status in (GenerationJobStatus.SUCCEEDED, GenerationJobStatus.FAILED)
        else None
    )
    async with sessions() as session:
        async with session.begin():
            session.add(
                GenerationJobRow(
                    job_id=identifier,
                    profile_id=profile_id,
                    idempotency_key=f"{identifier}-key",
                    trigger=GenerationTrigger.ADMIN_REQUEST.value,
                    requested_generator=requested_generator,
                    status=status.value,
                    attempts=attempts,
                    program_id=program.program_id if program else None,
                    program_version=program.version if program else None,
                    last_error_code=error_code,
                    created_at=started,
                    started_at=started,
                    completed_at=completed,
                )
            )
    return identifier


async def _save_attempts(
    sessions,
    job_id: str,
    attempts: list[dict],
    *,
    prompt_version: int | None = None,
    created_at: datetime | None = None,
) -> None:
    async with sessions() as session:
        async with session.begin():
            session.add(
                AIAuditEventRow(
                    event_type=MODEL_ATTEMPTS_EVENT_TYPE,
                    entity_type="program_generation",
                    entity_id=job_id,
                    metadata_json={
                        "task_type": "workout_generation",
                        "prompt_version": prompt_version,
                        "models_tried": len(attempts),
                        "attempts": attempts,
                    },
                    created_at=created_at or datetime.now(timezone.utc),
                )
            )


def _attempt(
    model_id: str,
    *,
    outcome: str,
    provider: str = "test-provider",
    priority: int = 1,
    is_primary: bool = True,
    initial_valid: bool = True,
    repair_attempts: int = 0,
) -> dict:
    return {
        "priority": priority,
        "is_primary": is_primary,
        "provider": provider,
        "model_id": model_id,
        "model_pk": priority,
        "initial_valid": initial_valid,
        "repair_attempts": repair_attempts,
        "outcome": outcome,
        "error_type": None if outcome == "success" else "AIProviderError",
        "detail": None,
    }


# --- Сводка ------------------------------------------------------------------------


class TestOverview:
    @pytest.mark.asyncio
    async def test_counts_successes_failures_and_fallback(self, sessions, window):
        profile = await _save_profile(sessions, _profile_id())
        ai_program = await _save_program(
            sessions,
            profile.profile_id,
            source=GenerationSource.AI,
            model="model-a",
            provider="test-provider",
            prompt_version=1,
        )
        fallback_program = await _save_program(
            sessions,
            profile.profile_id,
            source=GenerationSource.DETERMINISTIC,
            fallback_used=True,
            fallback_reason_code="ai_timeout",
        )
        await _save_job(
            sessions,
            profile.profile_id,
            status=GenerationJobStatus.SUCCEEDED,
            program=ai_program,
        )
        await _save_job(
            sessions,
            profile.profile_id,
            status=GenerationJobStatus.SUCCEEDED,
            program=fallback_program,
        )
        await _save_job(
            sessions,
            profile.profile_id,
            status=GenerationJobStatus.FAILED,
            error_code="ai_timeout",
        )

        result = await GenerationAnalyticsRepository(sessions).overview(
            AnalyticsFilter(date_from=window)
        )
        generations = result["generations"]
        assert generations["total"] == 3
        assert generations["succeeded"] == 2
        assert generations["failed"] == 1
        assert generations["by_ai"] == 1
        assert generations["by_deterministic"] == 1
        assert generations["fallback"] == 1
        assert generations["deterministic_fallback"] == 1
        assert generations["success_rate"] == 66.7
        assert generations["fallback_rate"] == 33.3

    @pytest.mark.asyncio
    async def test_empty_selection_reports_null_not_zero_percent(self, sessions):
        """0% означало бы «отказов не было», хотя генераций не было вовсе."""
        result = await GenerationAnalyticsRepository(sessions).overview(
            AnalyticsFilter(
                date_from=datetime(2000, 1, 1, tzinfo=timezone.utc),
                date_to=datetime(2000, 1, 2, tzinfo=timezone.utc),
            )
        )
        generations = result["generations"]
        assert generations["total"] == 0
        assert generations["success_rate"] is None
        assert generations["fallback_rate"] is None
        assert generations["avg_duration_ms"] is None

    @pytest.mark.asyncio
    async def test_duration_is_measured_from_start_not_creation(self, sessions, window):
        """Быстрая генерация не должна давать отрицательную длительность."""
        profile = await _save_profile(sessions, _profile_id())
        await _save_job(
            sessions,
            profile.profile_id,
            status=GenerationJobStatus.SUCCEEDED,
            duration_ms=120,
        )
        result = await GenerationAnalyticsRepository(sessions).overview(
            AnalyticsFilter(date_from=window)
        )
        assert result["generations"]["avg_duration_ms"] == 120

    @pytest.mark.asyncio
    async def test_running_generation_has_no_duration(self, sessions, window):
        """У незавершённой операции длительности нет: 0 мс было бы неправдой."""
        profile = await _save_profile(sessions, _profile_id())
        await _save_job(
            sessions, profile.profile_id, status=GenerationJobStatus.RUNNING
        )
        repository = GenerationAnalyticsRepository(sessions)
        spec = AnalyticsFilter(date_from=window)
        result = await repository.overview(spec)
        assert result["generations"]["active"] == 1
        assert result["generations"]["avg_duration_ms"] is None
        _, items = await repository.generations(spec, limit=10, offset=0)
        assert items[0]["duration_ms"] is None


# --- Попытки моделей ---------------------------------------------------------------


class TestModelAttempts:
    @pytest.mark.asyncio
    async def test_model_stats_count_attempts_not_generations(self, sessions, window):
        """Единица подсчёта — попытка модели: их больше, чем генераций."""
        profile = await _save_profile(sessions, _profile_id())
        program = await _save_program(
            sessions,
            profile.profile_id,
            source=GenerationSource.AI,
            model="model-b",
            provider="test-provider",
            prompt_version=1,
        )
        job_id = await _save_job(
            sessions,
            profile.profile_id,
            status=GenerationJobStatus.SUCCEEDED,
            program=program,
        )
        await _save_attempts(
            sessions,
            job_id,
            [
                _attempt(
                    "model-a",
                    outcome="invalid_output",
                    initial_valid=False,
                    repair_attempts=2,
                ),
                _attempt(
                    "model-b",
                    outcome="success",
                    priority=2,
                    is_primary=False,
                    initial_valid=False,
                    repair_attempts=1,
                ),
            ],
            prompt_version=1,
        )

        items = await GenerationAnalyticsRepository(sessions).models(
            AnalyticsFilter(date_from=window)
        )
        by_model = {item["model"]: item for item in items}
        assert set(by_model) == {"model-a", "model-b"}
        assert by_model["model-a"]["usage"] == 1
        assert by_model["model-a"]["invalid_outputs"] == 1
        assert by_model["model-a"]["repair_attempts"] == 2
        assert by_model["model-a"]["success_rate"] == 0.0
        assert by_model["model-b"]["succeeded"] == 1
        assert by_model["model-b"]["as_fallback"] == 1
        # Успех пришёл с исправления, а не с первого ответа.
        assert by_model["model-b"]["first_answer_rate"] == 0.0

    @pytest.mark.asyncio
    async def test_repeated_attempt_event_is_not_double_counted(
        self, sessions, window
    ):
        """Одна генерация пишет попытки дважды: числа не должны удваиваться."""
        profile = await _save_profile(sessions, _profile_id())
        job_id = await _save_job(
            sessions, profile.profile_id, status=GenerationJobStatus.FAILED
        )
        earlier = datetime.now(timezone.utc) - timedelta(seconds=5)
        await _save_attempts(
            sessions,
            job_id,
            [_attempt("model-a", outcome="invalid_output", initial_valid=False)],
            prompt_version=1,
            created_at=earlier,
        )
        await _save_attempts(
            sessions,
            job_id,
            [
                _attempt("model-a", outcome="invalid_output", initial_valid=False),
                _attempt("model-b", outcome="provider_error", priority=2),
            ],
            prompt_version=1,
        )

        repository = GenerationAnalyticsRepository(sessions)
        items = await repository.models(AnalyticsFilter(date_from=window))
        assert sum(item["usage"] for item in items) == 2

        _, generations = await repository.generations(
            AnalyticsFilter(date_from=window), limit=10, offset=0
        )
        assert generations[0]["models_tried"] == 2

    @pytest.mark.asyncio
    async def test_generation_without_attempt_event_stays_visible(
        self, sessions, window
    ):
        """Алгоритмическая генерация не пишет попыток и не должна исчезать."""
        profile = await _save_profile(sessions, _profile_id())
        program = await _save_program(
            sessions, profile.profile_id, source=GenerationSource.DETERMINISTIC
        )
        await _save_job(
            sessions,
            profile.profile_id,
            status=GenerationJobStatus.SUCCEEDED,
            requested_generator=GenerationSource.DETERMINISTIC.value,
            program=program,
        )
        total, items = await GenerationAnalyticsRepository(sessions).generations(
            AnalyticsFilter(date_from=window), limit=10, offset=0
        )
        assert total == 1
        assert items[0]["models_tried"] == 0
        assert items[0]["repair_attempts"] == 0


# --- Фильтры, сортировка, пагинация ------------------------------------------------


class TestGenerationsQuery:
    @pytest.mark.asyncio
    async def test_sorting_applies_to_whole_selection_not_page(
        self, sessions, window
    ):
        """Сортировка серверная: самая долгая генерация — первая на 1-й странице."""
        profile = await _save_profile(sessions, _profile_id())
        for duration in (100, 5000, 700):
            await _save_job(
                sessions,
                profile.profile_id,
                status=GenerationJobStatus.SUCCEEDED,
                duration_ms=duration,
            )
        repository = GenerationAnalyticsRepository(sessions)
        spec = AnalyticsFilter(date_from=window)

        total, first_page = await repository.generations(
            spec, limit=1, offset=0, sort_by="duration_ms", descending=True
        )
        assert total == 3
        assert first_page[0]["duration_ms"] == 5000

        _, ascending = await repository.generations(
            spec, limit=1, offset=0, sort_by="duration_ms", descending=False
        )
        assert ascending[0]["duration_ms"] == 100

    @pytest.mark.asyncio
    async def test_pagination_does_not_repeat_or_lose_rows(self, sessions, window):
        """Строки с равным значением не должны пересекаться между страницами."""
        profile = await _save_profile(sessions, _profile_id())
        created = datetime.now(timezone.utc)
        for _ in range(5):
            await _save_job(
                sessions,
                profile.profile_id,
                status=GenerationJobStatus.SUCCEEDED,
                created_at=created,
            )
        repository = GenerationAnalyticsRepository(sessions)
        spec = AnalyticsFilter(date_from=window)

        seen: list[str] = []
        for offset in (0, 2, 4):
            _, page = await repository.generations(spec, limit=2, offset=offset)
            seen.extend(item["job_id"] for item in page)
        assert len(seen) == 5
        assert len(set(seen)) == 5

    @pytest.mark.asyncio
    async def test_filter_by_model_finds_failed_attempt_without_program(
        self, sessions, window
    ):
        """Модель ищется по попыткам: у упавшей генерации программы нет."""
        profile = await _save_profile(sessions, _profile_id())
        job_id = await _save_job(
            sessions,
            profile.profile_id,
            status=GenerationJobStatus.FAILED,
            error_code="ai_invalid_response",
        )
        await _save_attempts(
            sessions,
            job_id,
            [_attempt("model-lonely", outcome="invalid_output", initial_valid=False)],
            prompt_version=2,
        )
        repository = GenerationAnalyticsRepository(sessions)

        total, items = await repository.generations(
            AnalyticsFilter(date_from=window, model="model-lonely"),
            limit=10,
            offset=0,
        )
        assert total == 1
        assert items[0]["job_id"] == job_id
        assert items[0]["program_id"] is None
        assert items[0]["prompt_version"] == 2

        empty_total, _ = await repository.generations(
            AnalyticsFilter(date_from=window, model="model-absent"),
            limit=10,
            offset=0,
        )
        assert empty_total == 0

    @pytest.mark.asyncio
    async def test_validation_filter_separates_repaired_from_clean(
        self, sessions, window
    ):
        profile = await _save_profile(sessions, _profile_id())
        clean_program = await _save_program(
            sessions, profile.profile_id, source=GenerationSource.AI, model="model-a"
        )
        clean_job = await _save_job(
            sessions,
            profile.profile_id,
            status=GenerationJobStatus.SUCCEEDED,
            program=clean_program,
        )
        await _save_attempts(
            sessions, clean_job, [_attempt("model-a", outcome="success")]
        )

        repaired_program = await _save_program(
            sessions, profile.profile_id, source=GenerationSource.AI, model="model-a"
        )
        repaired_job = await _save_job(
            sessions,
            profile.profile_id,
            status=GenerationJobStatus.SUCCEEDED,
            program=repaired_program,
        )
        await _save_attempts(
            sessions,
            repaired_job,
            [
                _attempt(
                    "model-a",
                    outcome="success",
                    initial_valid=False,
                    repair_attempts=1,
                )
            ],
        )

        repository = GenerationAnalyticsRepository(sessions)
        _, repaired = await repository.generations(
            AnalyticsFilter(date_from=window, validation="repaired"),
            limit=10,
            offset=0,
        )
        assert [item["job_id"] for item in repaired] == [repaired_job]

        _, valid = await repository.generations(
            AnalyticsFilter(date_from=window, validation="valid"), limit=10, offset=0
        )
        assert [item["job_id"] for item in valid] == [clean_job]

    @pytest.mark.asyncio
    async def test_fallback_filter_excludes_failed_generations(
        self, sessions, window
    ):
        """У упавшей генерации признака подмены генератора не существует."""
        profile = await _save_profile(sessions, _profile_id())
        fallback_program = await _save_program(
            sessions,
            profile.profile_id,
            source=GenerationSource.DETERMINISTIC,
            fallback_used=True,
            fallback_reason_code="ai_timeout",
        )
        fallback_job = await _save_job(
            sessions,
            profile.profile_id,
            status=GenerationJobStatus.SUCCEEDED,
            program=fallback_program,
        )
        await _save_job(
            sessions,
            profile.profile_id,
            status=GenerationJobStatus.FAILED,
            error_code="ai_timeout",
        )

        repository = GenerationAnalyticsRepository(sessions)
        _, with_fallback = await repository.generations(
            AnalyticsFilter(date_from=window, fallback=True), limit=10, offset=0
        )
        assert [item["job_id"] for item in with_fallback] == [fallback_job]

        _, without = await repository.generations(
            AnalyticsFilter(date_from=window, fallback=False), limit=10, offset=0
        )
        assert fallback_job not in [item["job_id"] for item in without]


# --- Инструкции и карточка генерации ------------------------------------------------


class TestPromptsAndDetail:
    @pytest.mark.asyncio
    async def test_prompt_stats_group_by_version(self, sessions, window):
        profile = await _save_profile(sessions, _profile_id())
        for version, status in (
            (1, GenerationJobStatus.SUCCEEDED),
            (1, GenerationJobStatus.FAILED),
            (2, GenerationJobStatus.SUCCEEDED),
        ):
            program = (
                await _save_program(
                    sessions,
                    profile.profile_id,
                    source=GenerationSource.AI,
                    model="model-a",
                    prompt_version=version,
                )
                if status is GenerationJobStatus.SUCCEEDED
                else None
            )
            job_id = await _save_job(
                sessions, profile.profile_id, status=status, program=program
            )
            await _save_attempts(
                sessions,
                job_id,
                [_attempt("model-a", outcome="success")],
                prompt_version=version,
            )

        items = await GenerationAnalyticsRepository(sessions).prompts(
            AnalyticsFilter(date_from=window)
        )
        by_version = {item["prompt_version"]: item for item in items}
        assert by_version[1]["usage"] == 2
        assert by_version[1]["succeeded"] == 1
        assert by_version[1]["success_rate"] == 50.0
        assert by_version[2]["usage"] == 1
        # Название версии берётся из существующей инструкции; удалённая версия
        # остаётся в статистике без названия.
        assert "name" in by_version[1]

    @pytest.mark.asyncio
    async def test_detail_returns_attempts_and_calls(self, sessions, window):
        profile = await _save_profile(sessions, _profile_id())
        program = await _save_program(
            sessions,
            profile.profile_id,
            source=GenerationSource.AI,
            model="model-a",
            provider="test-provider",
            prompt_version=1,
        )
        job_id = await _save_job(
            sessions,
            profile.profile_id,
            status=GenerationJobStatus.SUCCEEDED,
            program=program,
            duration_ms=2500,
        )
        await _save_attempts(
            sessions,
            job_id,
            [
                _attempt("model-a", outcome="invalid_output", initial_valid=False),
                _attempt("model-b", outcome="success", priority=2, is_primary=False),
            ],
            prompt_version=1,
        )
        async with sessions() as session:
            async with session.begin():
                session.add(
                    AIUsageRecordRow(
                        task_type="workout_generation",
                        job_id=job_id,
                        profile_id=profile.profile_id,
                        status="success",
                        latency_ms=1800,
                        total_tokens=4200,
                    )
                )

        detail = await GenerationAnalyticsRepository(sessions).generation(job_id)
        assert detail is not None
        assert detail["duration_ms"] == 2500
        assert detail["models_tried"] == 2
        assert [a["model_id"] for a in detail["attempt_details"]] == [
            "model-a",
            "model-b",
        ]
        assert len(detail["calls"]) == 1
        assert detail["calls"][0]["latency_ms"] == 1800

    @pytest.mark.asyncio
    async def test_detail_of_unknown_job_is_none(self, sessions):
        assert await GenerationAnalyticsRepository(sessions).generation("nope") is None

    @pytest.mark.asyncio
    async def test_calls_of_other_generation_are_not_mixed_in(self, sessions, window):
        """Вызовы связываются по job_id, а не по анкете: у неё много генераций."""
        profile = await _save_profile(sessions, _profile_id())
        first = await _save_job(
            sessions, profile.profile_id, status=GenerationJobStatus.FAILED
        )
        second = await _save_job(
            sessions, profile.profile_id, status=GenerationJobStatus.FAILED
        )
        async with sessions() as session:
            async with session.begin():
                session.add_all(
                    [
                        AIUsageRecordRow(
                            task_type="workout_generation",
                            job_id=first,
                            profile_id=profile.profile_id,
                            status="error",
                            latency_ms=10,
                        ),
                        AIUsageRecordRow(
                            task_type="workout_generation",
                            job_id=second,
                            profile_id=profile.profile_id,
                            status="error",
                            latency_ms=20,
                        ),
                    ]
                )

        detail = await GenerationAnalyticsRepository(sessions).generation(first)
        assert [call["latency_ms"] for call in detail["calls"]] == [10]

    @pytest.mark.asyncio
    async def test_filter_options_come_from_history(self, sessions, window):
        """Удалённая модель остаётся в фильтрах: иначе её генерации не найти."""
        profile = await _save_profile(sessions, _profile_id())
        job_id = await _save_job(
            sessions, profile.profile_id, status=GenerationJobStatus.FAILED
        )
        await _save_attempts(
            sessions,
            job_id,
            [_attempt("model-historic", outcome="provider_error")],
            prompt_version=7,
        )
        options = await GenerationAnalyticsRepository(sessions).filter_options()
        assert "model-historic" in [item["model"] for item in options["models"]]
        assert 7 in options["prompt_versions"]


# --- Каталог упражнений -------------------------------------------------------------
#
# Каталог заполнен импортом и в тестах не изменяется: проверяются свойства
# запроса (какие строки попадают в выборку и в каком порядке), а не конкретные
# числа наполнения.


class TestExerciseCatalog:
    @pytest.mark.asyncio
    async def test_equipment_filter_works_on_json_column(self, sessions):
        """JSON-колонка не поддерживает оператор вхождения без приведения к JSONB."""
        repository = ExerciseRepository(sessions)
        total, items = await repository.search(
            ExerciseQuery(equipment=("barbell",)), limit=20
        )
        assert total > 0
        assert items
        assert all("barbell" in item.equipment for item in items)

    @pytest.mark.asyncio
    async def test_multiple_values_of_one_filter_combine_with_or(self, sessions):
        repository = ExerciseRepository(sessions)
        barbell, _ = await repository.search(
            ExerciseQuery(equipment=("barbell",)), limit=1
        )
        dumbbell, _ = await repository.search(
            ExerciseQuery(equipment=("dumbbell",)), limit=1
        )
        both, items = await repository.search(
            ExerciseQuery(equipment=("barbell", "dumbbell")), limit=50
        )
        # Объединение не меньше каждого множества и не больше их суммы:
        # упражнение может требовать и штангу, и гантели одновременно.
        assert max(barbell, dumbbell) <= both <= barbell + dumbbell
        assert all(
            {"barbell", "dumbbell"} & set(item.equipment) for item in items
        )

    @pytest.mark.asyncio
    async def test_different_filters_combine_with_and(self, sessions):
        repository = ExerciseRepository(sessions)
        only_equipment, _ = await repository.search(
            ExerciseQuery(equipment=("barbell",)), limit=1
        )
        combined, items = await repository.search(
            ExerciseQuery(equipment=("barbell",), mechanics=("compound",)), limit=50
        )
        assert combined <= only_equipment
        assert all(
            "barbell" in item.equipment and item.mechanic == "compound"
            for item in items
        )

    @pytest.mark.asyncio
    async def test_difficulty_sorting_is_semantic_not_alphabetical(self, sessions):
        """Алфавитный порядок ставил бы expert между beginner и intermediate."""
        repository = ExerciseRepository(sessions)
        _, ascending = await repository.search(
            ExerciseQuery(), limit=5, sort_by="difficulty", descending=False
        )
        _, descending = await repository.search(
            ExerciseQuery(), limit=5, sort_by="difficulty", descending=True
        )
        assert {item.difficulty for item in ascending} == {"beginner"}
        assert {item.difficulty for item in descending} == {"expert"}

    @pytest.mark.asyncio
    async def test_pagination_is_stable_across_pages(self, sessions):
        repository = ExerciseRepository(sessions)
        seen: list[str] = []
        for offset in (0, 10, 20):
            _, page = await repository.search(
                ExerciseQuery(), limit=10, offset=offset, sort_by="exercise_type"
            )
            seen.extend(item.external_id for item in page)
        assert len(seen) == 30
        assert len(set(seen)) == 30

    @pytest.mark.asyncio
    async def test_search_escapes_wildcards(self, sessions):
        """`%` в запросе — символ, который ищут, а не «покажи всё»."""
        repository = ExerciseRepository(sessions)
        total, _ = await repository.search(ExerciseQuery(search="%"), limit=5)
        everything, _ = await repository.search(ExerciseQuery(), limit=5)
        assert total < everything

    @pytest.mark.asyncio
    async def test_facets_follow_the_current_filter(self, sessions):
        """Счётчики считаются по той же выборке, что и список."""
        repository = ExerciseRepository(sessions)
        overall = await repository.facets(ExerciseQuery())
        filtered = await repository.facets(ExerciseQuery(equipment=("barbell",)))

        overall_by_value = {row["value"]: row["count"] for row in overall.difficulties}
        filtered_by_value = {
            row["value"]: row["count"] for row in filtered.difficulties
        }
        assert filtered_by_value
        for value, count in filtered_by_value.items():
            assert count <= overall_by_value[value]

        # Счётчик оборудования в отфильтрованной выборке не может превышать
        # число упражнений, попавших под фильтр.
        total, _ = await repository.search(
            ExerciseQuery(equipment=("barbell",)), limit=1
        )
        barbell_count = next(
            row["count"] for row in filtered.equipment if row["value"] == "barbell"
        )
        assert barbell_count == total

    @pytest.mark.asyncio
    async def test_inactive_exercises_are_hidden_by_default(self, sessions):
        repository = ExerciseRepository(sessions)
        active, _ = await repository.search(ExerciseQuery(is_active=True), limit=1)
        every, _ = await repository.search(ExerciseQuery(is_active=None), limit=1)
        assert active <= every

    @pytest.mark.asyncio
    async def test_legacy_single_value_list_still_works(self, sessions):
        """Старый вызов используется генерацией: контракт не сломан."""
        repository = ExerciseRepository(sessions)
        items = await repository.list(equipment="barbell", limit=5)
        assert items
        assert all("barbell" in item.equipment for item in items)
