"""Интеграционные тесты ingestion внешних источников.

Требуют DATABASE_URL с применёнными миграциями. Проверяется то, чего нельзя
проверить unit-тестами: staging-слой действительно переживает процесс, повторный
импорт идемпотентен, provenance записывается по полям, а генерация продолжает
читать только canonical каталог.

Тесты работают с собственным источником (`test/ingestion`) и собственным
префиксом упражнений, после чего убирают за собой: рабочий каталог и знание об
оборудовании не изменяются.
"""
from __future__ import annotations

import pytest
from sqlalchemy import delete, select

from src.application.ingestion.candidates import ExternalExerciseCandidate
from src.application.ingestion.service import (
    CANONICAL_SOURCE_EXTERNAL,
    ExternalIngestionService,
    make_external_id,
)
from src.application.ingestion.sources import SourceRead
from src.domain.ingestion import (
    ExternalSource,
    ExternalSourceKind,
    ExternalSourceVersion,
    ImportStatus,
    IngestionDecision,
)
from src.infrastructure.config import DATABASE_URL

pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL is not set")

TEST_SOURCE_KEY = "test/ingestion"
TEST_SOURCE_KEY_SECOND = "test/ingestion-second"
TEST_SOURCE_KEYS = (TEST_SOURCE_KEY, TEST_SOURCE_KEY_SECOND)
TEST_VERSION = "v-test-1"
TEST_CANONICAL_ID = "Test_Ingestion_Anchor"
TEST_CANONICAL_SOURCE = "leszavr/workout"

TECHNIQUE = (
    "1. Встаньте прямо и возьмите штангу хватом на ширине плеч.\n"
    "2. Согните руки в локтях, поднимая штангу к плечам.\n"
    "3. Медленно опустите штангу в исходное положение."
)
TECHNIQUE_RU = TECHNIQUE

TEST_SOURCE = ExternalSource(
    source_key=TEST_SOURCE_KEY,
    name="Тестовый источник ingestion",
    kind=ExternalSourceKind.EXERCISE_CATALOG,
    data_license="test",
    attribution="test",
)


def version(record_count: int) -> ExternalSourceVersion:
    from datetime import UTC, datetime

    return ExternalSourceVersion(
        source_key=TEST_SOURCE_KEY,
        version=TEST_VERSION,
        content_hash="0" * 64,
        retrieved_at=datetime.now(UTC),
        record_count=record_count,
    )


def candidate(
    record_id: str,
    name: str,
    *,
    equipment: tuple[str, ...] = ("barbell",),
    target: str = "biceps",
    secondary: tuple[str, ...] = ("forearms",),
    technique: str | None = TECHNIQUE,
    technique_ru: str | None = TECHNIQUE_RU,
) -> ExternalExerciseCandidate:
    return ExternalExerciseCandidate(
        source_key=TEST_SOURCE_KEY,
        source_version=TEST_VERSION,
        source_record_id=record_id,
        raw_name=name,
        name=name,
        technique=technique,
        technique_ru=technique_ru,
        equipment_values=equipment,
        primary_muscle_values=(target,),
        secondary_muscle_values=secondary,
    )


def read_for(candidates: list[ExternalExerciseCandidate]) -> SourceRead:
    return SourceRead(
        source=TEST_SOURCE,
        version=version(len(candidates)),
        candidates=candidates,
        stats={"records_total": len(candidates)},
    )


@pytest.fixture
def sessions():
    from src.infrastructure.persistence.postgres.db import get_session_factory

    return get_session_factory()


@pytest.fixture
def service(sessions):
    from src.infrastructure.persistence.postgres.equipment_repository import (
        EquipmentRepository,
    )
    from src.infrastructure.persistence.postgres.exercise_media_repository import (
        ExerciseMediaRepository,
    )
    from src.infrastructure.persistence.postgres.exercise_repository import (
        ExerciseRepository,
    )
    from src.infrastructure.persistence.postgres.ingestion_repository import (
        IngestionRepository,
    )

    return ExternalIngestionService(
        exercises=ExerciseRepository(sessions),
        equipment_repository=EquipmentRepository(sessions),
        ingestion_repository=IngestionRepository(sessions),
        media_repository=ExerciseMediaRepository(sessions),
    )


@pytest.fixture
def ingestion_repository(sessions):
    from src.infrastructure.persistence.postgres.ingestion_repository import (
        IngestionRepository,
    )

    return IngestionRepository(sessions)


@pytest.fixture
def exercises(sessions):
    from src.infrastructure.persistence.postgres.exercise_repository import (
        ExerciseRepository,
    )

    return ExerciseRepository(sessions)


@pytest.fixture(autouse=True)
async def cleanup(sessions):
    """Убирает за собой только собственные записи.

    Упражнения удаляются не по источнику `workout_bot/external` целиком: этот
    источник общий для всего рабочего импорта, и удаление по нему уничтожало бы
    каталог разработчика. Удаляются только упражнения, связанные с тестовым
    источником, плюс тестовый якорь.
    """
    from src.infrastructure.persistence.postgres.models import (
        ExerciseFieldProvenanceRow,
        ExerciseProgramObservationRow,
        ExerciseRow,
        ExerciseSourceLinkRow,
        ExternalExerciseRecordRow,
        ExternalSourceRow,
    )

    async def _clean() -> None:
        async with sessions() as session:
            async with session.begin():
                # Упражнения, созданные тестовым источником: список берётся из
                # связей до их удаления.
                created = (
                    await session.execute(
                        select(
                            ExerciseSourceLinkRow.exercise_external_id,
                            ExerciseSourceLinkRow.exercise_source,
                        ).where(
                            ExerciseSourceLinkRow.source_key.in_(TEST_SOURCE_KEYS)
                        )
                    )
                ).all()

                for table, column in (
                    (ExerciseFieldProvenanceRow, ExerciseFieldProvenanceRow.source_key),
                    (
                        ExerciseProgramObservationRow,
                        ExerciseProgramObservationRow.source_key,
                    ),
                    (ExerciseSourceLinkRow, ExerciseSourceLinkRow.source_key),
                    (ExternalExerciseRecordRow, ExternalExerciseRecordRow.source_key),
                    (ExternalSourceRow, ExternalSourceRow.source_key),
                ):
                    await session.execute(
                        delete(table).where(column.in_(TEST_SOURCE_KEYS))
                    )

                for external_id, source in created:
                    if source != CANONICAL_SOURCE_EXTERNAL:
                        # Обогащённое упражнение принадлежит каталогу: его
                        # удалять нельзя, тест правит только якорь.
                        continue
                    await session.execute(
                        delete(ExerciseRow).where(
                            ExerciseRow.external_id == external_id,
                            ExerciseRow.source == source,
                        )
                    )
                await session.execute(
                    delete(ExerciseRow).where(
                        ExerciseRow.external_id == TEST_CANONICAL_ID
                    )
                )
                # Упражнения, созданные тестом до записи связей (аварийное
                # завершение прогона): опознаются по префиксу названия.
                await session.execute(
                    delete(ExerciseRow).where(
                        ExerciseRow.source == CANONICAL_SOURCE_EXTERNAL,
                        ExerciseRow.external_id.like("Test_Ingestion%"),
                    )
                )

    await _clean()
    yield
    await _clean()
    from src.infrastructure.persistence.postgres.db import dispose_engine

    await dispose_engine()


@pytest.fixture
async def anchor(exercises):
    """Canonical упражнение, с которым сопоставляются тестовые записи."""
    from src.domain.exercise import Exercise

    await exercises.upsert(
        Exercise(
            external_id=TEST_CANONICAL_ID,
            source=TEST_CANONICAL_SOURCE,
            name="Test Ingestion Anchor Curl",
            technique=None,
            technique_ru=None,
            primary_muscles=["biceps"],
            secondary_muscles=["forearms"],
            equipment=["barbell"],
            exercise_type="strength",
            difficulty="beginner",
        )
    )
    return TEST_CANONICAL_ID


# --- Планирование ----------------------------------------------------------------


async def test_dry_run_does_not_change_catalog(service, exercises):
    before = await exercises.count()
    plan = await service.plan(
        catalog_reads=[read_for([candidate("1", "Test Ingestion Brand New Curl")])]
    )
    assert plan.records[0].decision is IngestionDecision.NEW_RELEVANT
    assert plan.summary()["expected_new_exercises"] == 1
    assert await exercises.count() == before


async def test_existing_exercise_is_enriched_not_duplicated(service, exercises, anchor):
    """Совпавшая запись обогащает caталог, а не создаёт вторую строку."""
    before = await exercises.count()
    plan = await service.plan(
        catalog_reads=[read_for([candidate("1", "Test Ingestion Anchor Curl")])]
    )
    assert plan.records[0].decision is IngestionDecision.ENRICHABLE
    result = await service.apply(plan)

    assert result.created == 0
    assert result.enriched == 1
    assert await exercises.count() == before

    updated = await exercises.get_by_external_id(anchor, TEST_CANONICAL_SOURCE)
    assert updated.technique == TECHNIQUE
    assert updated.technique_ru == TECHNIQUE_RU


async def test_new_exercise_is_created_with_provenance(
    service, exercises, ingestion_repository
):
    plan = await service.plan(
        catalog_reads=[read_for([candidate("7", "Test Ingestion Unique Movement")])]
    )
    result = await service.apply(plan)
    assert result.created == 1

    external_id = make_external_id("Test Ingestion Unique Movement", "7")
    created = await exercises.get_by_external_id(external_id, CANONICAL_SOURCE_EXTERNAL)
    assert created is not None
    assert created.technique == TECHNIQUE
    # Оборудование приведено к словарю поля: иначе фильтр не узнал бы значение и
    # упражнение не попало бы ни в одну программу.
    assert created.equipment == ["barbell"]
    assert created.primary_muscles == ["biceps"]

    links = await ingestion_repository.links_for_exercises(
        [(external_id, CANONICAL_SOURCE_EXTERNAL)]
    )
    assert links[(external_id, CANONICAL_SOURCE_EXTERNAL)][0].relation.value == "origin"

    provenance = await ingestion_repository.field_provenance_for(
        external_id, CANONICAL_SOURCE_EXTERNAL
    )
    fields = {entry.field for entry in provenance}
    assert {"name", "technique", "technique_ru"} <= fields
    for entry in provenance:
        assert entry.source_key == TEST_SOURCE_KEY
        assert entry.source_version == TEST_VERSION
        assert entry.value_hash


# --- Идемпотентность -------------------------------------------------------------


async def test_second_run_is_idempotent(service, exercises, ingestion_repository):
    """Повторный импорт того же источника не создаёт упражнений заново."""
    read = read_for(
        [
            candidate("1", "Test Ingestion Idempotent Alpha"),
            candidate("2", "Test Ingestion Idempotent Beta", target="chest"),
        ]
    )
    first = await service.apply(await service.plan(catalog_reads=[read]))
    assert first.created == 2
    after_first = await exercises.count()

    second_plan = await service.plan(catalog_reads=[read])
    decisions = {r.decision for r in second_plan.records}
    assert decisions == {IngestionDecision.EXISTING}
    second = await service.apply(second_plan)

    assert second.created == 0
    assert await exercises.count() == after_first

    total, records = await ingestion_repository.list_records(
        _query(source_keys=(TEST_SOURCE_KEY,))
    )
    # Записи те же самые, а не удвоенные: staging уникален по (source, record_id).
    assert total == 2
    assert {r.import_status for r in records} == {ImportStatus.SKIPPED}


async def test_repeated_run_after_source_update_keeps_single_exercise(
    service, exercises
):
    """Обновление источника меняет запись, а не добавляет вторую."""
    read = read_for([candidate("5", "Test Ingestion Evolving Movement")])
    await service.apply(await service.plan(catalog_reads=[read]))
    count_after_first = await exercises.count()

    improved = read_for(
        [
            candidate(
                "5",
                "Test Ingestion Evolving Movement",
                technique=TECHNIQUE + "\n4. Дополнительный шаг источника.",
            )
        ]
    )
    await service.apply(await service.plan(catalog_reads=[improved]))
    assert await exercises.count() == count_after_first


# --- Дедупликация ----------------------------------------------------------------


async def test_intra_source_duplicate_creates_single_exercise(service, exercises):
    """Записи, различающиеся только пометкой съёмки, дают одно упражнение."""
    read = read_for(
        [
            candidate("10", "Test Ingestion Twin Movement"),
            candidate("11", "Test Ingestion Twin Movement (male)"),
            candidate("12", "Test Ingestion Twin Movement v. 2"),
        ]
    )
    plan = await service.plan(catalog_reads=[read])
    decisions = [r.decision for r in plan.records]
    assert decisions.count(IngestionDecision.NEW_RELEVANT) == 1
    assert decisions.count(IngestionDecision.DUPLICATE_VARIANT) == 2

    result = await service.apply(plan)
    assert result.created == 1


async def test_same_exercise_from_two_sources_is_not_duplicated(service, exercises):
    """Второй источник, описывающий то же упражнение, не создаёт вторую запись."""
    first_source = read_for([candidate("20", "Test Ingestion Shared Movement")])
    await service.apply(await service.plan(catalog_reads=[first_source]))
    count_after_first = await exercises.count()

    other = ExternalSource(
        source_key=TEST_SOURCE_KEY_SECOND,
        name="Второй тестовый источник",
        kind=ExternalSourceKind.EXERCISE_CATALOG,
    )
    second_candidate = ExternalExerciseCandidate(
        source_key=other.source_key,
        source_version="v1",
        source_record_id="xyz",
        raw_name="Shared Movement Test Ingestion",
        name="Shared Movement Test Ingestion",
        technique=TECHNIQUE,
        technique_ru=TECHNIQUE_RU,
        equipment_values=("barbell",),
        primary_muscle_values=("biceps",),
        secondary_muscle_values=("forearms",),
    )
    second_read = SourceRead(
        source=other,
        version=ExternalSourceVersion(
            source_key=other.source_key,
            version="v1",
            retrieved_at=version(1).retrieved_at,
            record_count=1,
        ),
        candidates=[second_candidate],
    )
    plan = await service.plan(catalog_reads=[second_read])
    assert plan.records[0].decision in (
        IngestionDecision.EXISTING,
        IngestionDecision.ENRICHABLE,
    )
    result = await service.apply(plan)
    assert result.created == 0
    assert await exercises.count() == count_after_first


# --- Отклонённые записи ----------------------------------------------------------


async def test_low_quality_record_is_kept_with_reason(service, ingestion_repository):
    """Отклонённая запись остаётся в staging: причина — тоже результат импорта."""
    read = read_for(
        [candidate("30", "Test Ingestion No Technique", technique=None, technique_ru=None)]
    )
    plan = await service.plan(catalog_reads=[read])
    assert plan.records[0].decision is IngestionDecision.LOW_QUALITY
    await service.apply(plan)

    total, records = await ingestion_repository.list_records(
        _query(source_keys=(TEST_SOURCE_KEY,))
    )
    assert total == 1
    record = records[0]
    assert record.import_status is ImportStatus.REJECTED
    assert record.decision is IngestionDecision.LOW_QUALITY
    assert "technique_missing" in record.quality_reasons
    assert record.import_note


async def test_source_and_version_are_recorded(service, ingestion_repository):
    await service.apply(
        await service.plan(catalog_reads=[read_for([candidate("40", "Test Ingestion Versioned")])])
    )
    sources = {s.source_key for s in await ingestion_repository.list_sources()}
    assert TEST_SOURCE_KEY in sources
    versions = await ingestion_repository.latest_versions()
    assert versions[TEST_SOURCE_KEY].version == TEST_VERSION
    assert versions[TEST_SOURCE_KEY].content_hash == "0" * 64


# --- Генерация читает только canonical -------------------------------------------


async def test_generation_reads_only_canonical_catalog(service, exercises):
    """Пул генерации собирается из caталога, включая импортированные упражнения.

    Проверяется не число, а источник данных: фильтр получает список из
    `ExerciseRepository`, и ни staging, ни внешние файлы в него не попадают.
    """
    from src.application.programs.filtering import ExerciseFilter
    from src.infrastructure.persistence.postgres.exercise_repository import ExerciseQuery

    await service.apply(
        await service.plan(
            catalog_reads=[read_for([candidate("50", "Test Ingestion Generation Curl")])]
        )
    )
    external_id = make_external_id("Test Ingestion Generation Curl", "50")

    _, catalog = await exercises.search(ExerciseQuery(is_active=True), limit=5000)
    ids = {e.external_id for e in catalog}
    assert external_id in ids

    profile = _gym_profile()
    pool = await ExerciseFilter().select_candidates(profile, catalog)
    assert external_id in {e.external_id for e in pool.included}


def _query(**kwargs):
    from src.infrastructure.persistence.postgres.ingestion_repository import RecordQuery

    return RecordQuery(**kwargs)


def _gym_profile():
    """Профиль зала: оборудование доступно, ограничений нет."""
    from src.domain.enums import ExperienceLevel, TrainingLocationType
    from src.domain.profile import FitnessProfile

    profile = FitnessProfile(profile_id="test-ingestion-profile")
    profile.training_location.primary_location = TrainingLocationType.GYM
    profile.training_location.available_equipment = []
    profile.training_background.experience_level = ExperienceLevel.OVER_1_YEAR
    return profile


async def test_origin_relation_survives_repeated_import(
    service, ingestion_repository
):
    """Роль «источник упражнения» не понижается повторным прогоном.

    На втором прогоне запись опознаётся как существующая и была бы записана как
    «дополнил данные». После этого ответ на вопрос «сколько упражнений пришло из
    источника» терялся бы, хотя ничего не изменилось.
    """
    read = read_for([candidate("60", "Test Ingestion Origin Keeper")])
    await service.apply(await service.plan(catalog_reads=[read]))
    external_id = make_external_id("Test Ingestion Origin Keeper", "60")

    await service.apply(await service.plan(catalog_reads=[read]))

    links = await ingestion_repository.links_for_exercises(
        [(external_id, CANONICAL_SOURCE_EXTERNAL)]
    )
    relations = {
        link.relation.value
        for link in links[(external_id, CANONICAL_SOURCE_EXTERNAL)]
    }
    assert relations == {"origin"}


async def test_three_runs_converge(service, exercises, ingestion_repository):
    """Третий прогон не меняет ничего: импорт сходится, а не колеблется."""
    read = read_for(
        [
            candidate("70", "Test Ingestion Converging Alpha"),
            candidate("71", "Test Ingestion Converging Alpha (male)"),
            candidate("72", "Test Ingestion Converging Beta", target="chest"),
        ]
    )
    await service.apply(await service.plan(catalog_reads=[read]))
    after_first = await exercises.count()

    second = await service.apply(await service.plan(catalog_reads=[read]))
    third = await service.apply(await service.plan(catalog_reads=[read]))

    assert (second.created, third.created) == (0, 0)
    assert third.enriched == 0
    assert third.provenance_written == 0
    assert await exercises.count() == after_first


async def test_duplicate_variant_relation_survives_repeated_import(
    service, ingestion_repository
):
    """Роль «повтор источника» не понижается повторным прогоном.

    На втором прогоне повтор находится по связи и выглядит обычным совпадением.
    Без защиты различие «повтор источника» против «дополнил данные» исчезло бы из
    отчёта, хотя ничего не изменилось.
    """
    read = read_for(
        [
            candidate("80", "Test Ingestion Relation Keeper"),
            candidate("81", "Test Ingestion Relation Keeper (male)"),
        ]
    )
    await service.apply(await service.plan(catalog_reads=[read]))
    external_id = make_external_id("Test Ingestion Relation Keeper", "80")

    await service.apply(await service.plan(catalog_reads=[read]))

    links = await ingestion_repository.links_for_exercises(
        [(external_id, CANONICAL_SOURCE_EXTERNAL)]
    )
    by_record = {
        link.source_record_id: link.relation.value
        for link in links[(external_id, CANONICAL_SOURCE_EXTERNAL)]
    }
    assert by_record == {"80": "origin", "81": "duplicate_variant"}
