"""Интеграционные тесты Admin API внешних источников знаний об упражнениях.

Требуют DATABASE_URL с применёнными миграциями. Проверяется то, чего нельзя
проверить unit-тестами: маршруты действительно смонтированы, доступ закрыт
авторизацией, фильтры считаются на сервере, а происхождение полей отдаётся тем
же контрактом, который читает Admin Web.

Тесты создают собственный источник (`test/ingestion-api`) и убирают его за
собой: рабочие данные импорта не изменяются.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

import apps.backend.auth as auth_module
from apps.backend.main import app
from src.infrastructure.config import DATABASE_URL

pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL is not set")

auth_module.ADMIN_LOGIN = "admin"
auth_module.ADMIN_PASSWORD = "test-admin-password"
auth_module.JWT_SECRET = "test-jwt-secret-with-sufficient-length-32b"

API = "/api/v1/admin/ingestion"
SOURCE_KEY = "test/ingestion-api"
SOURCE_VERSION = "test-version-1"
EXERCISE_ID = "Test_Ingestion_Api_Exercise"
EXERCISE_SOURCE = "leszavr/workout"


@pytest.fixture(scope="module")
def client() -> TestClient:
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module", autouse=True)
def reset_engine_after_module():
    yield
    from src.infrastructure.persistence.postgres.db import reset_engine_state

    reset_engine_state()


@pytest.fixture(scope="module")
def auth_headers(client: TestClient) -> dict:
    response = client.post(
        "/api/v1/auth/login",
        json={"login": "admin", "password": "test-admin-password"},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture(autouse=True)
def seeded(client: TestClient):
    """Наполняет staging-слой предсказуемыми записями и убирает их за собой."""

    async def _purge() -> None:
        from sqlalchemy import delete

        from src.infrastructure.persistence.postgres.db import get_session_factory
        from src.infrastructure.persistence.postgres.models import (
            ExerciseFieldProvenanceRow,
            ExerciseProgramObservationRow,
            ExerciseSourceLinkRow,
            ExternalExerciseRecordRow,
            ExternalSourceRow,
            ExternalSourceVersionRow,
        )

        async with get_session_factory()() as session:
            async with session.begin():
                for table, column in (
                    (
                        ExerciseFieldProvenanceRow,
                        ExerciseFieldProvenanceRow.source_key,
                    ),
                    (
                        ExerciseProgramObservationRow,
                        ExerciseProgramObservationRow.source_key,
                    ),
                    (ExerciseSourceLinkRow, ExerciseSourceLinkRow.source_key),
                    (ExternalExerciseRecordRow, ExternalExerciseRecordRow.source_key),
                    (
                        ExternalSourceVersionRow,
                        ExternalSourceVersionRow.source_key,
                    ),
                    (ExternalSourceRow, ExternalSourceRow.source_key),
                ):
                    await session.execute(delete(table).where(column == SOURCE_KEY))

    async def _seed() -> None:
        from src.domain.ingestion import (
            ExerciseFieldProvenance,
            ExerciseProgramObservation,
            ExerciseSourceLink,
            ExternalExerciseRecord,
            ExternalSource,
            ExternalSourceKind,
            ExternalSourceVersion,
            ImportStatus,
            IngestionDecision,
            QualityStatus,
            SourceLinkRelation,
        )
        from src.infrastructure.persistence.postgres.db import get_session_factory
        from src.infrastructure.persistence.postgres.ingestion_repository import (
            IngestionRepository,
        )

        repository = IngestionRepository(get_session_factory())
        await repository.upsert_source(
            ExternalSource(
                source_key=SOURCE_KEY,
                name="Тестовый источник API",
                kind=ExternalSourceKind.EXERCISE_CATALOG,
                homepage="https://example.invalid/dataset",
                data_license="MIT",
                media_license="© Правообладатель медиа",
                attribution="© Правообладатель медиа",
            )
        )
        await repository.upsert_version(
            ExternalSourceVersion(
                source_key=SOURCE_KEY,
                version=SOURCE_VERSION,
                content_hash="a" * 64,
                retrieved_at=datetime.now(UTC),
                record_count=3,
            )
        )

        def record(
            record_id: str,
            name: str,
            decision: IngestionDecision,
            quality: QualityStatus,
            status: ImportStatus,
            confidence: float,
        ) -> ExternalExerciseRecord:
            return ExternalExerciseRecord(
                source_key=SOURCE_KEY,
                source_version=SOURCE_VERSION,
                source_record_id=record_id,
                record_hash="b" * 64,
                raw_name=name,
                normalized_name=name,
                name_key=name.lower(),
                payload={"name": name, "technique": "1. Шаг\n2. Шаг"},
                quality_score=0.9 if quality is QualityStatus.READY else 0.5,
                quality_status=quality,
                quality_reasons=["technique_present"],
                decision=decision,
                match_confidence=confidence,
                match_reasons=["normalized_name_match"],
                matched_external_id=(
                    EXERCISE_ID
                    if decision
                    in (IngestionDecision.EXISTING, IngestionDecision.ENRICHABLE)
                    else None
                ),
                matched_source=(
                    EXERCISE_SOURCE
                    if decision
                    in (IngestionDecision.EXISTING, IngestionDecision.ENRICHABLE)
                    else None
                ),
                import_status=status,
                import_note="причина решения",
            )

        await repository.upsert_records(
            [
                record(
                    "api-1",
                    "Api Fixture Bench Press",
                    IngestionDecision.NEW_RELEVANT,
                    QualityStatus.READY,
                    ImportStatus.IMPORTED,
                    0.0,
                ),
                record(
                    "api-2",
                    "Api Fixture Existing Curl",
                    IngestionDecision.EXISTING,
                    QualityStatus.READY,
                    ImportStatus.SKIPPED,
                    0.95,
                ),
                record(
                    "api-3",
                    "Api Fixture Broken Row",
                    IngestionDecision.LOW_QUALITY,
                    QualityStatus.REJECT,
                    ImportStatus.REJECTED,
                    0.0,
                ),
            ]
        )
        await repository.upsert_links(
            [
                ExerciseSourceLink(
                    exercise_external_id=EXERCISE_ID,
                    exercise_source=EXERCISE_SOURCE,
                    source_key=SOURCE_KEY,
                    source_record_id="api-2",
                    source_version=SOURCE_VERSION,
                    relation=SourceLinkRelation.ENRICHMENT,
                    confidence=0.95,
                    reasons=["normalized_name_match"],
                )
            ]
        )
        await repository.upsert_field_provenance(
            [
                ExerciseFieldProvenance(
                    exercise_external_id=EXERCISE_ID,
                    exercise_source=EXERCISE_SOURCE,
                    field="technique_ru",
                    source_key=SOURCE_KEY,
                    source_record_id="api-2",
                    source_version=SOURCE_VERSION,
                    value_hash="c" * 64,
                    reason="filled_missing_value",
                )
            ]
        )
        await repository.upsert_observations(
            [
                ExerciseProgramObservation(
                    exercise_external_id=EXERCISE_ID,
                    exercise_source=EXERCISE_SOURCE,
                    source_key=SOURCE_KEY,
                    source_version=SOURCE_VERSION,
                    source_record_id="api-2",
                    program_count=12,
                    occurrence_count=30,
                    typical_sets_median=4.0,
                    typical_reps_median=8.0,
                    typical_hold_seconds_median=None,
                    source_goals={"Bodybuilding": 20},
                    source_levels={"Intermediate": 18},
                )
            ]
        )

    client.portal.call(_purge)
    client.portal.call(_seed)
    yield
    client.portal.call(_purge)


# --- Доступ ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/sources",
        "/records",
        "/health",
        f"/exercises/{EXERCISE_ID}/provenance",
    ],
)
def test_endpoints_require_authorization(client: TestClient, path: str):
    assert client.get(f"{API}{path}").status_code == 401


# --- Источники ------------------------------------------------------------------


def test_sources_expose_version_and_licenses(client: TestClient, auth_headers: dict):
    """Версия и условия использования отдаются вместе с источником.

    Без версии «в базе N записей» не отвечает на вопрос, из какого состояния
    источника они взяты; без условий на медиа непонятно, чью атрибуцию хранить.
    """
    response = client.get(f"{API}/sources", headers=auth_headers)
    assert response.status_code == 200
    item = next(
        i for i in response.json()["items"] if i["source_key"] == SOURCE_KEY
    )
    assert item["kind"] == "exercise_catalog"
    assert item["version"] == SOURCE_VERSION
    assert item["content_hash"] == "a" * 64
    assert item["data_license"] == "MIT"
    assert item["media_license"] == "© Правообладатель медиа"
    assert item["record_count"] == 3
    assert item["counts"]["decision:new_relevant"] == 1
    assert item["counts"]["decision:existing"] == 1
    assert item["counts"]["decision:low_quality"] == 1
    assert item["counts"]["quality:reject"] == 1


# --- Записи ---------------------------------------------------------------------


def test_records_are_filtered_on_server(client: TestClient, auth_headers: dict):
    """Фильтры серверные: клиент не получает лишние записи и фильтрует не сам."""
    all_records = client.get(
        f"{API}/records?source={SOURCE_KEY}&limit=50", headers=auth_headers
    ).json()
    assert all_records["total"] == 3

    only_new = client.get(
        f"{API}/records?source={SOURCE_KEY}&decision=new_relevant", headers=auth_headers
    ).json()
    assert only_new["total"] == 1
    assert only_new["items"][0]["decision"] == "new_relevant"

    rejected = client.get(
        f"{API}/records?source={SOURCE_KEY}&status=rejected", headers=auth_headers
    ).json()
    assert rejected["total"] == 1
    assert rejected["items"][0]["quality_status"] == "reject"

    confident = client.get(
        f"{API}/records?source={SOURCE_KEY}&min_confidence=0.9", headers=auth_headers
    ).json()
    assert confident["total"] == 1
    assert confident["items"][0]["source_record_id"] == "api-2"

    found = client.get(
        f"{API}/records?source={SOURCE_KEY}&search=broken", headers=auth_headers
    ).json()
    assert found["total"] == 1
    assert found["items"][0]["source_record_id"] == "api-3"


def test_rejected_records_stay_visible(client: TestClient, auth_headers: dict):
    """Отклонённая запись остаётся в выдаче вместе с причиной."""
    response = client.get(
        f"{API}/records?source={SOURCE_KEY}&decision=low_quality", headers=auth_headers
    )
    item = response.json()["items"][0]
    assert item["import_status"] == "rejected"
    assert item["import_note"]
    assert item["quality_reasons"]


def test_records_list_omits_payload(client: TestClient, auth_headers: dict):
    """В списке payload не отдаётся: инструкции источника весят слишком много."""
    items = client.get(
        f"{API}/records?source={SOURCE_KEY}", headers=auth_headers
    ).json()["items"]
    assert all("payload" not in item for item in items)


def test_single_record_includes_payload(client: TestClient, auth_headers: dict):
    response = client.get(f"{API}/records/{SOURCE_KEY}/api-1", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["payload"]["name"] == "Api Fixture Bench Press"
    assert body["name_key"]
    assert body["record_hash"]


def test_unknown_record_gives_404(client: TestClient, auth_headers: dict):
    response = client.get(
        f"{API}/records/{SOURCE_KEY}/does-not-exist", headers=auth_headers
    )
    assert response.status_code == 404


def test_invalid_decision_value_is_rejected(client: TestClient, auth_headers: dict):
    """Значения фильтров проверяются схемой, а не подставляются в запрос."""
    response = client.get(f"{API}/records?decision=whatever", headers=auth_headers)
    assert response.status_code == 422


def test_pagination_is_server_side(client: TestClient, auth_headers: dict):
    first = client.get(
        f"{API}/records?source={SOURCE_KEY}&limit=1&offset=0", headers=auth_headers
    ).json()
    second = client.get(
        f"{API}/records?source={SOURCE_KEY}&limit=1&offset=1", headers=auth_headers
    ).json()
    assert first["total"] == second["total"] == 3
    assert len(first["items"]) == len(second["items"]) == 1
    assert (
        first["items"][0]["source_record_id"] != second["items"][0]["source_record_id"]
    )


# --- Происхождение --------------------------------------------------------------


def test_provenance_returns_fields_sources_and_observations(
    client: TestClient, auth_headers: dict
):
    response = client.get(
        f"{API}/exercises/{EXERCISE_ID}/provenance?source={EXERCISE_SOURCE}",
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()

    assert [f["field"] for f in body["fields"]] == ["technique_ru"]
    assert body["fields"][0]["source_key"] == SOURCE_KEY
    assert body["fields"][0]["reason"] == "filled_missing_value"

    assert body["sources"][0]["relation"] == "enrichment"
    assert body["sources"][0]["confidence"] == 0.95

    observation = body["program_observations"][0]
    # Поля называются как в базе: это статистика чужих программ, а не назначение
    # нагрузки, и переименование на границе API поменяло бы смысл данных.
    assert observation["program_count"] == 12
    assert observation["typical_sets_median"] == 4.0
    assert observation["typical_reps_median"] == 8.0
    assert observation["source_goals"] == {"Bodybuilding": 20}


def test_provenance_of_unknown_exercise_is_empty_not_error(
    client: TestClient, auth_headers: dict
):
    """Отсутствие внешних данных — не ошибка: упражнение могло прийти из caталога."""
    response = client.get(
        f"{API}/exercises/No_Such_Exercise/provenance", headers=auth_headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["fields"] == []
    assert body["sources"] == []
    assert body["program_observations"] == []


# --- Полнота --------------------------------------------------------------------


def test_health_counts_are_computed_from_database(
    client: TestClient, auth_headers: dict
):
    response = client.get(f"{API}/health", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["external_records_total"] >= 3
    assert body["records_imported"] >= 1
    assert body["records_rejected"] >= 1
    assert body["by_source"][SOURCE_KEY]["decision:new_relevant"] == 1
    assert body["by_source"][SOURCE_KEY]["import:rejected"] == 1


# --- Поиск записи не зависит от пагинации ---------------------------------------

BULK_SOURCE_KEY = "test/ingestion-api-bulk"
# Больше страницы списка. Прежняя реализация detail endpoint искала запись среди
# первых 200 отданных строк, поэтому объём должен превышать именно эту границу.
BULK_RECORD_COUNT = 260
PAST_PAGE_INDEX = 250


@pytest.fixture
def bulk_records(client: TestClient):
    """Источник с числом записей больше страницы списка."""

    async def _purge() -> None:
        from sqlalchemy import delete

        from src.infrastructure.persistence.postgres.db import get_session_factory
        from src.infrastructure.persistence.postgres.models import (
            ExternalExerciseRecordRow,
            ExternalSourceRow,
        )

        async with get_session_factory()() as session:
            async with session.begin():
                await session.execute(
                    delete(ExternalExerciseRecordRow).where(
                        ExternalExerciseRecordRow.source_key == BULK_SOURCE_KEY
                    )
                )
                await session.execute(
                    delete(ExternalSourceRow).where(
                        ExternalSourceRow.source_key == BULK_SOURCE_KEY
                    )
                )

    async def _seed() -> None:
        from src.domain.ingestion import (
            ExternalExerciseRecord,
            ExternalSource,
            ExternalSourceKind,
        )
        from src.infrastructure.persistence.postgres.db import get_session_factory
        from src.infrastructure.persistence.postgres.ingestion_repository import (
            IngestionRepository,
        )

        repository = IngestionRepository(get_session_factory())
        await repository.upsert_source(
            ExternalSource(
                source_key=BULK_SOURCE_KEY,
                name="Тестовый источник: объём больше страницы",
                kind=ExternalSourceKind.EXERCISE_CATALOG,
            )
        )
        # Названия нумерованы с ведущими нулями: список сортируется по
        # нормализованному названию, поэтому позиция записи в выдаче совпадает с
        # её номером, и «за пределами первой страницы» — проверяемый факт.
        await repository.upsert_records(
            [
                ExternalExerciseRecord(
                    source_key=BULK_SOURCE_KEY,
                    source_version="bulk-1",
                    source_record_id=f"bulk-{index:04d}",
                    record_hash="d" * 64,
                    raw_name=f"Bulk Fixture Exercise {index:04d}",
                    normalized_name=f"Bulk Fixture Exercise {index:04d}",
                    name_key=f"bulk exercise fixture {index:04d}",
                    payload={"name": f"Bulk Fixture Exercise {index:04d}"},
                )
                for index in range(BULK_RECORD_COUNT)
            ]
        )

    client.portal.call(_purge)
    client.portal.call(_seed)
    yield
    client.portal.call(_purge)


def test_record_beyond_first_page_is_found(
    client: TestClient, auth_headers: dict, bulk_records
):
    """Запись за пределами первой страницы списка отдаётся detail endpoint'ом.

    Прежняя реализация читала первые 200 записей источника и искала нужную среди
    них: существующая запись с 201-й позиции отвечала 404. Тест фиксирует именно
    этот случай, поэтому позиция записи проверяется, а не предполагается.
    """
    record_id = f"bulk-{PAST_PAGE_INDEX:04d}"

    # 1. Запись существует и лежит за пределами первой страницы.
    page = client.get(
        f"{API}/records?source={BULK_SOURCE_KEY}&limit=200&offset=0",
        headers=auth_headers,
    ).json()
    assert page["total"] == BULK_RECORD_COUNT
    assert record_id not in {item["source_record_id"] for item in page["items"]}

    # 2. Detail endpoint её находит.
    response = client.get(
        f"{API}/records/{BULK_SOURCE_KEY}/{record_id}", headers=auth_headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["source_record_id"] == record_id
    assert body["normalized_name"] == f"Bulk Fixture Exercise {PAST_PAGE_INDEX:04d}"
    assert body["payload"]["name"] == f"Bulk Fixture Exercise {PAST_PAGE_INDEX:04d}"


def test_last_record_of_large_source_is_found(
    client: TestClient, auth_headers: dict, bulk_records
):
    """Последняя запись источника находится так же, как первая."""
    for index in (0, BULK_RECORD_COUNT - 1):
        response = client.get(
            f"{API}/records/{BULK_SOURCE_KEY}/bulk-{index:04d}", headers=auth_headers
        )
        assert response.status_code == 200, index
        assert response.json()["source_record_id"] == f"bulk-{index:04d}"


def test_record_id_is_case_sensitive(
    client: TestClient, auth_headers: dict, bulk_records
):
    """Идентификатор записи сравнивается точно, а не без учёта регистра.

    Ключ приходит от источника, и «похожий» идентификатор — не тот же
    идентификатор: отдавать по нему запись означало бы утверждать соответствие,
    которого источник не устанавливал.
    """
    response = client.get(
        f"{API}/records/{BULK_SOURCE_KEY}/BULK-0250", headers=auth_headers
    )
    assert response.status_code == 404


def test_record_of_another_source_is_not_returned(
    client: TestClient, auth_headers: dict, bulk_records
):
    """Запись ищется в пределах своего источника.

    Идентификаторы источников независимы: `0001` у одного источника и `0001` у
    другого — разные записи, и отдавать чужую значило бы смешивать источники.
    """
    response = client.get(
        f"{API}/records/{SOURCE_KEY}/bulk-0250", headers=auth_headers
    )
    assert response.status_code == 404
