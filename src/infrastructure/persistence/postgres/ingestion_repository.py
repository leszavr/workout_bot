"""Репозиторий staging-слоя внешних источников и provenance.

Отдельно от каталога упражнений и от базы знаний об оборудовании, потому что
отвечает на третий вопрос: не «какие упражнения есть» и не «что им нужно», а
«что мы прочитали из внешних источников и что с этим сделали».

Все операции идемпотентны по конструкции: staging-запись уникальна по паре
(`source_key`, `source_record_id`), связь источника — по внешней записи и
упражнению, provenance — по полю упражнения. Повторный импорт того же источника
обновляет те же строки, а не создаёт вторые.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import case, delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker

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
from src.errors import ProfilePersistenceError
from src.infrastructure.persistence.postgres.models import (
    ExerciseFieldProvenanceRow,
    ExerciseProgramObservationRow,
    ExerciseSourceLinkRow,
    ExternalExerciseRecordRow,
    ExternalSourceRow,
    ExternalSourceVersionRow,
)

# Размер порции массовой вставки. Ограничение PostgreSQL на число параметров
# запроса — 65535; у staging-записи 18 колонок, и 1000 строк дают 18 тысяч
# параметров с запасом.
INSERT_CHUNK_SIZE = 500


def _chunks(values: list, size: int = INSERT_CHUNK_SIZE):
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _dedupe(payload: list[dict], keys: tuple[str, ...]) -> list[dict]:
    """Оставляет по одной строке на ключ конфликта, сохраняя последнюю.

    Нужно не для порядка, а для корректности: `INSERT ... ON CONFLICT DO UPDATE`
    падает с «cannot affect row a second time», если один и тот же ключ встречается
    в одном запросе дважды. А это штатная ситуация: два разных упражнения внешнего
    источника могут обогатить одно canonical (`push-up (on stability ball)` и
    `push up on bosu ball` оба сопоставляются с `Pushups`), и оба дают provenance
    для одного поля.

    Сохраняется последняя строка, потому что применение плана идёт
    последовательно: последнее значение — то, которое фактически оказалось в
    canonical записи.
    """
    unique: dict[tuple, dict] = {}
    for row in payload:
        unique[tuple(row[key] for key in keys)] = row
    return list(unique.values())


def _persistence_error(exc: SQLAlchemyError, what: str) -> ProfilePersistenceError:
    return ProfilePersistenceError(f"{what}: {exc.__class__.__name__}")


@dataclass(frozen=True)
class RecordQuery:
    """Условия выборки staging-записей. Пустые поля не ограничивают выборку."""

    source_keys: tuple[str, ...] = ()
    decisions: tuple[str, ...] = ()
    quality_statuses: tuple[str, ...] = ()
    import_statuses: tuple[str, ...] = ()
    search: str | None = None
    min_confidence: float | None = None
    max_confidence: float | None = None


class IngestionRepository:
    """Чтение и запись staging-слоя, связей источников и provenance."""

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._sessions = session_factory

    # --- Источники и версии --------------------------------------------------

    async def upsert_source(self, source: ExternalSource) -> None:
        values = {
            "source_key": source.source_key,
            "name": source.name,
            "kind": source.kind.value,
            "homepage": source.homepage,
            "data_license": source.data_license,
            "media_license": source.media_license,
            "attribution": source.attribution,
            "notes": source.notes,
            "is_active": source.is_active,
        }
        update = {k: v for k, v in values.items() if k != "source_key"}
        try:
            async with self._sessions() as session:
                async with session.begin():
                    stmt = pg_insert(ExternalSourceRow).values(**values)
                    await session.execute(
                        stmt.on_conflict_do_update(
                            index_elements=["source_key"], set_=update
                        )
                    )
        except SQLAlchemyError as exc:
            raise _persistence_error(exc, "Не удалось сохранить источник") from exc

    async def upsert_version(self, version: ExternalSourceVersion) -> None:
        values = {
            "source_key": version.source_key,
            "version": version.version,
            "content_hash": version.content_hash,
            "retrieved_at": version.retrieved_at,
            "record_count": version.record_count,
            "notes": version.notes,
        }
        update = {
            k: v for k, v in values.items() if k not in ("source_key", "version")
        }
        try:
            async with self._sessions() as session:
                async with session.begin():
                    stmt = pg_insert(ExternalSourceVersionRow).values(**values)
                    await session.execute(
                        stmt.on_conflict_do_update(
                            constraint="uq_external_source_version", set_=update
                        )
                    )
        except SQLAlchemyError as exc:
            raise _persistence_error(exc, "Не удалось сохранить версию источника") from exc

    async def list_sources(self) -> list[ExternalSource]:
        try:
            async with self._sessions() as session:
                rows = (
                    await session.execute(
                        select(ExternalSourceRow).order_by(ExternalSourceRow.source_key)
                    )
                ).scalars().all()
        except SQLAlchemyError as exc:
            raise _persistence_error(exc, "Ошибка чтения источников") from exc
        return [
            ExternalSource(
                source_key=row.source_key,
                name=row.name,
                kind=ExternalSourceKind(row.kind),
                homepage=row.homepage,
                data_license=row.data_license,
                media_license=row.media_license,
                attribution=row.attribution,
                notes=row.notes,
                is_active=row.is_active,
            )
            for row in rows
        ]

    async def latest_versions(self) -> dict[str, ExternalSourceVersion]:
        """Последняя (по времени чтения) версия каждого источника."""
        try:
            async with self._sessions() as session:
                rows = (
                    await session.execute(
                        select(ExternalSourceVersionRow).order_by(
                            ExternalSourceVersionRow.source_key,
                            ExternalSourceVersionRow.retrieved_at.desc(),
                        )
                    )
                ).scalars().all()
        except SQLAlchemyError as exc:
            raise _persistence_error(exc, "Ошибка чтения версий источников") from exc
        result: dict[str, ExternalSourceVersion] = {}
        for row in rows:
            if row.source_key in result:
                continue
            result[row.source_key] = ExternalSourceVersion(
                source_key=row.source_key,
                version=row.version,
                content_hash=row.content_hash,
                retrieved_at=row.retrieved_at,
                record_count=row.record_count,
                notes=row.notes,
            )
        return result

    # --- Staging-записи ------------------------------------------------------

    async def upsert_records(self, records: list[ExternalExerciseRecord]) -> int:
        """Идемпотентно сохраняет staging-записи.

        Конфликт по (`source_key`, `source_record_id`) обновляет строку: повторное
        чтение источника пересчитывает решение, а не заводит вторую запись.
        """
        if not records:
            return 0
        payload = [
            {
                "source_key": r.source_key,
                "source_version": r.source_version,
                "source_record_id": r.source_record_id,
                "record_hash": r.record_hash,
                "raw_name": r.raw_name[:255],
                "normalized_name": r.normalized_name[:255],
                "name_key": r.name_key[:255],
                "payload": r.payload,
                "quality_score": r.quality_score,
                "quality_status": r.quality_status.value,
                "quality_reasons": r.quality_reasons,
                "decision": r.decision.value,
                "match_confidence": r.match_confidence,
                "match_reasons": r.match_reasons,
                "matched_external_id": r.matched_external_id,
                "matched_source": r.matched_source,
                "import_status": r.import_status.value,
                "import_note": r.import_note,
                "imported_at": r.imported_at,
            }
            for r in records
        ]
        update_columns = [
            "source_version",
            "record_hash",
            "raw_name",
            "normalized_name",
            "name_key",
            "payload",
            "quality_score",
            "quality_status",
            "quality_reasons",
            "decision",
            "match_confidence",
            "match_reasons",
            "matched_external_id",
            "matched_source",
            "import_status",
            "import_note",
            "imported_at",
        ]
        payload = _dedupe(payload, ("source_key", "source_record_id"))
        try:
            async with self._sessions() as session:
                async with session.begin():
                    written = 0
                    for chunk in _chunks(payload):
                        stmt = pg_insert(ExternalExerciseRecordRow).values(chunk)
                        stmt = stmt.on_conflict_do_update(
                            constraint="uq_external_exercise_record",
                            set_={
                                column: getattr(stmt.excluded, column)
                                for column in update_columns
                            },
                        )
                        result = await session.execute(stmt)
                        written += result.rowcount or 0
                    return written
        except SQLAlchemyError as exc:
            raise _persistence_error(exc, "Не удалось сохранить staging-записи") from exc

    async def mark_import_status(
        self,
        updates: list[tuple[str, str, ImportStatus, str | None]],
    ) -> int:
        """Отмечает фактический результат применения плана по записям.

        Кортеж: (`source_key`, `source_record_id`, статус, примечание).
        """
        if not updates:
            return 0
        try:
            async with self._sessions() as session:
                async with session.begin():
                    changed = 0
                    for source_key, record_id, status, note in updates:
                        result = await session.execute(
                            ExternalExerciseRecordRow.__table__.update()
                            .where(
                                ExternalExerciseRecordRow.source_key == source_key,
                                ExternalExerciseRecordRow.source_record_id == record_id,
                            )
                            .values(
                                import_status=status.value,
                                import_note=(note or None),
                                imported_at=func.now()
                                if status
                                in (ImportStatus.IMPORTED, ImportStatus.ENRICHED)
                                else None,
                            )
                        )
                        changed += result.rowcount or 0
                    return changed
        except SQLAlchemyError as exc:
            raise _persistence_error(exc, "Не удалось отметить статус импорта") from exc

    async def list_records(
        self, query: RecordQuery, *, limit: int = 100, offset: int = 0
    ) -> tuple[int, list[ExternalExerciseRecord]]:
        conditions = []
        if query.source_keys:
            conditions.append(ExternalExerciseRecordRow.source_key.in_(query.source_keys))
        if query.decisions:
            conditions.append(ExternalExerciseRecordRow.decision.in_(query.decisions))
        if query.quality_statuses:
            conditions.append(
                ExternalExerciseRecordRow.quality_status.in_(query.quality_statuses)
            )
        if query.import_statuses:
            conditions.append(
                ExternalExerciseRecordRow.import_status.in_(query.import_statuses)
            )
        if query.min_confidence is not None:
            conditions.append(
                ExternalExerciseRecordRow.match_confidence >= query.min_confidence
            )
        if query.max_confidence is not None:
            conditions.append(
                ExternalExerciseRecordRow.match_confidence <= query.max_confidence
            )
        if query.search:
            like = "%" + query.search.replace("\\", "\\\\").replace("%", "\\%").replace(
                "_", "\\_"
            ) + "%"
            conditions.append(
                ExternalExerciseRecordRow.normalized_name.ilike(like, escape="\\")
            )

        stmt = (
            select(ExternalExerciseRecordRow)
            .where(*conditions)
            .order_by(
                ExternalExerciseRecordRow.source_key,
                ExternalExerciseRecordRow.normalized_name,
                ExternalExerciseRecordRow.id,
            )
            .limit(limit)
            .offset(offset)
        )
        count_stmt = (
            select(func.count()).select_from(ExternalExerciseRecordRow).where(*conditions)
        )
        try:
            async with self._sessions() as session:
                total = (await session.execute(count_stmt)).scalar_one()
                rows = (await session.execute(stmt)).scalars().all()
        except SQLAlchemyError as exc:
            raise _persistence_error(exc, "Ошибка чтения staging-записей") from exc
        return total, [_record_to_domain(row) for row in rows]

    async def record_counts(self) -> dict[str, dict[str, int]]:
        """Счётчики решений и статусов импорта по каждому источнику."""
        try:
            async with self._sessions() as session:
                decision_rows = (
                    await session.execute(
                        select(
                            ExternalExerciseRecordRow.source_key,
                            ExternalExerciseRecordRow.decision,
                            func.count(),
                        ).group_by(
                            ExternalExerciseRecordRow.source_key,
                            ExternalExerciseRecordRow.decision,
                        )
                    )
                ).all()
                status_rows = (
                    await session.execute(
                        select(
                            ExternalExerciseRecordRow.source_key,
                            ExternalExerciseRecordRow.import_status,
                            func.count(),
                        ).group_by(
                            ExternalExerciseRecordRow.source_key,
                            ExternalExerciseRecordRow.import_status,
                        )
                    )
                ).all()
                quality_rows = (
                    await session.execute(
                        select(
                            ExternalExerciseRecordRow.source_key,
                            ExternalExerciseRecordRow.quality_status,
                            func.count(),
                        ).group_by(
                            ExternalExerciseRecordRow.source_key,
                            ExternalExerciseRecordRow.quality_status,
                        )
                    )
                ).all()
        except SQLAlchemyError as exc:
            raise _persistence_error(exc, "Ошибка счётчиков ingestion") from exc

        result: dict[str, dict[str, int]] = {}
        for source_key, value, count in decision_rows:
            result.setdefault(source_key, {})[f"decision:{value}"] = count
        for source_key, value, count in status_rows:
            result.setdefault(source_key, {})[f"import:{value}"] = count
        for source_key, value, count in quality_rows:
            result.setdefault(source_key, {})[f"quality:{value}"] = count
        return result

    # --- Связи источников ----------------------------------------------------

    async def upsert_links(self, links: list[ExerciseSourceLink]) -> int:
        """Идемпотентно сохраняет связи «упражнение ← внешняя запись».

        Роли `origin` и `duplicate_variant` не понижаются. Обе описывают
        исторический факт, который повторный прогон уже не видит:

        - `origin` — упражнение создано из этой внешней записи. На втором прогоне
          запись опознаётся как существующая, и роль стала бы `enrichment`; после
          этого ответ на вопрос «сколько упражнений пришло из источника» терялся
          бы, хотя ничего не менялось;
        - `duplicate_variant` — запись оказалась повтором другой записи того же
          источника. На втором прогоне она находится по связи и выглядит обычным
          совпадением, и различие «повтор источника» против «дополнил данные»
          исчезло бы из отчёта.
        """
        if not links:
            return 0
        payload = [
            {
                "exercise_external_id": link.exercise_external_id,
                "exercise_source": link.exercise_source,
                "source_key": link.source_key,
                "source_record_id": link.source_record_id,
                "source_version": link.source_version,
                "relation": link.relation.value,
                "confidence": link.confidence,
                "reasons": link.reasons,
            }
            for link in links
        ]
        payload = _dedupe(
            payload,
            (
                "exercise_external_id",
                "exercise_source",
                "source_key",
                "source_record_id",
            ),
        )
        try:
            async with self._sessions() as session:
                async with session.begin():
                    written = 0
                    for chunk in _chunks(payload):
                        stmt = pg_insert(ExerciseSourceLinkRow).values(chunk)
                        stmt = stmt.on_conflict_do_update(
                            constraint="uq_exercise_source_link",
                            set_={
                                "source_version": stmt.excluded.source_version,
                                "relation": case(
                                    (
                                        ExerciseSourceLinkRow.relation.in_(
                                            (
                                                SourceLinkRelation.ORIGIN.value,
                                                SourceLinkRelation.DUPLICATE_VARIANT.value,
                                            )
                                        ),
                                        ExerciseSourceLinkRow.relation,
                                    ),
                                    else_=stmt.excluded.relation,
                                ),
                                "confidence": stmt.excluded.confidence,
                                "reasons": stmt.excluded.reasons,
                            },
                        )
                        result = await session.execute(stmt)
                        written += result.rowcount or 0
                    return written
        except SQLAlchemyError as exc:
            raise _persistence_error(exc, "Не удалось сохранить связи источников") from exc

    async def source_links_index(self) -> dict[tuple[str, str], tuple[str, str]]:
        """Отображение внешней записи в canonical упражнение.

        Нужно сопоставлению: связь, записанная предыдущим импортом, — самый
        сильный признак соответствия, и без неё повторный импорт зависел бы от
        совпадения правил, а не от факта.

        Связь `duplicate_variant` включена наравне с `origin` и `enrichment`:
        она означает «эта внешняя запись описывает то же упражнение, что уже
        импортированная». На повторном прогоне такая запись обязана снова
        указывать на то же упражнение, иначе она создала бы дубль — при том что
        первый прогон её дублем и признал.

        Связь `observation` исключена: она принадлежит датасету программ, чьи
        записи упражнениями не являются, и использовать её как соответствие
        каталогу значило бы объявить строку программы упражнением.
        """
        try:
            async with self._sessions() as session:
                rows = (
                    await session.execute(
                        select(
                            ExerciseSourceLinkRow.source_key,
                            ExerciseSourceLinkRow.source_record_id,
                            ExerciseSourceLinkRow.exercise_external_id,
                            ExerciseSourceLinkRow.exercise_source,
                        ).where(
                            ExerciseSourceLinkRow.relation.in_(
                                (
                                    SourceLinkRelation.ORIGIN.value,
                                    SourceLinkRelation.ENRICHMENT.value,
                                    SourceLinkRelation.DUPLICATE_VARIANT.value,
                                )
                            )
                        )
                    )
                ).all()
        except SQLAlchemyError as exc:
            raise _persistence_error(exc, "Ошибка чтения связей источников") from exc
        return {
            (source_key, record_id): (external_id, source)
            for source_key, record_id, external_id, source in rows
        }

    async def links_for_exercises(
        self, refs: list[tuple[str, str]]
    ) -> dict[tuple[str, str], list[ExerciseSourceLink]]:
        if not refs:
            return {}
        external_ids = sorted({external_id for external_id, _ in refs})
        wanted = set(refs)
        try:
            async with self._sessions() as session:
                rows = (
                    await session.execute(
                        select(ExerciseSourceLinkRow).where(
                            ExerciseSourceLinkRow.exercise_external_id.in_(external_ids)
                        )
                    )
                ).scalars().all()
        except SQLAlchemyError as exc:
            raise _persistence_error(exc, "Ошибка чтения связей упражнения") from exc
        result: dict[tuple[str, str], list[ExerciseSourceLink]] = {}
        for row in rows:
            key = (row.exercise_external_id, row.exercise_source)
            if key not in wanted:
                continue
            result.setdefault(key, []).append(
                ExerciseSourceLink(
                    exercise_external_id=row.exercise_external_id,
                    exercise_source=row.exercise_source,
                    source_key=row.source_key,
                    source_record_id=row.source_record_id,
                    source_version=row.source_version,
                    relation=SourceLinkRelation(row.relation),
                    confidence=row.confidence,
                    reasons=list(row.reasons or []),
                )
            )
        return result

    # --- Provenance полей ----------------------------------------------------

    async def upsert_field_provenance(
        self, entries: list[ExerciseFieldProvenance]
    ) -> int:
        if not entries:
            return 0
        payload = [
            {
                "exercise_external_id": entry.exercise_external_id,
                "exercise_source": entry.exercise_source,
                "field": entry.field,
                "source_key": entry.source_key,
                "source_record_id": entry.source_record_id,
                "source_version": entry.source_version,
                "value_hash": entry.value_hash,
                "reason": entry.reason,
            }
            for entry in entries
        ]
        payload = _dedupe(
            payload, ("exercise_external_id", "exercise_source", "field")
        )
        try:
            async with self._sessions() as session:
                async with session.begin():
                    written = 0
                    for chunk in _chunks(payload):
                        stmt = pg_insert(ExerciseFieldProvenanceRow).values(chunk)
                        stmt = stmt.on_conflict_do_update(
                            constraint="uq_exercise_field_provenance",
                            set_={
                                "source_key": stmt.excluded.source_key,
                                "source_record_id": stmt.excluded.source_record_id,
                                "source_version": stmt.excluded.source_version,
                                "value_hash": stmt.excluded.value_hash,
                                "reason": stmt.excluded.reason,
                            },
                        )
                        result = await session.execute(stmt)
                        written += result.rowcount or 0
                    return written
        except SQLAlchemyError as exc:
            raise _persistence_error(exc, "Не удалось сохранить provenance полей") from exc

    async def field_provenance_for(
        self, external_id: str, source: str
    ) -> list[ExerciseFieldProvenance]:
        try:
            async with self._sessions() as session:
                rows = (
                    await session.execute(
                        select(ExerciseFieldProvenanceRow)
                        .where(
                            ExerciseFieldProvenanceRow.exercise_external_id
                            == external_id,
                            ExerciseFieldProvenanceRow.exercise_source == source,
                        )
                        .order_by(ExerciseFieldProvenanceRow.field)
                    )
                ).scalars().all()
        except SQLAlchemyError as exc:
            raise _persistence_error(exc, "Ошибка чтения provenance полей") from exc
        return [
            ExerciseFieldProvenance(
                exercise_external_id=row.exercise_external_id,
                exercise_source=row.exercise_source,
                field=row.field,
                source_key=row.source_key,
                source_record_id=row.source_record_id,
                source_version=row.source_version,
                value_hash=row.value_hash,
                reason=row.reason,
            )
            for row in rows
        ]

    # --- Программные наблюдения ----------------------------------------------

    async def upsert_observations(
        self, observations: list[ExerciseProgramObservation]
    ) -> int:
        if not observations:
            return 0
        payload = [
            {
                "exercise_external_id": o.exercise_external_id,
                "exercise_source": o.exercise_source,
                "source_key": o.source_key,
                "source_version": o.source_version,
                "source_record_id": o.source_record_id,
                "program_count": o.program_count,
                "occurrence_count": o.occurrence_count,
                "typical_sets_median": o.typical_sets_median,
                "typical_sets_min": o.typical_sets_min,
                "typical_sets_max": o.typical_sets_max,
                "typical_reps_median": o.typical_reps_median,
                "typical_reps_min": o.typical_reps_min,
                "typical_reps_max": o.typical_reps_max,
                "typical_hold_seconds_median": o.typical_hold_seconds_median,
                "typical_intensity_median": o.typical_intensity_median,
                "source_goals": o.source_goals,
                "source_levels": o.source_levels,
                "source_equipment_contexts": o.source_equipment_contexts,
            }
            for o in observations
        ]
        update_columns = [
            "source_version",
            "source_record_id",
            "program_count",
            "occurrence_count",
            "typical_sets_median",
            "typical_sets_min",
            "typical_sets_max",
            "typical_reps_median",
            "typical_reps_min",
            "typical_reps_max",
            "typical_hold_seconds_median",
            "typical_intensity_median",
            "source_goals",
            "source_levels",
            "source_equipment_contexts",
        ]
        payload = _dedupe(
            payload, ("exercise_external_id", "exercise_source", "source_key")
        )
        try:
            async with self._sessions() as session:
                async with session.begin():
                    written = 0
                    for chunk in _chunks(payload):
                        stmt = pg_insert(ExerciseProgramObservationRow).values(chunk)
                        stmt = stmt.on_conflict_do_update(
                            constraint="uq_exercise_program_observation",
                            set_={
                                column: getattr(stmt.excluded, column)
                                for column in update_columns
                            },
                        )
                        result = await session.execute(stmt)
                        written += result.rowcount or 0
                    return written
        except SQLAlchemyError as exc:
            raise _persistence_error(
                exc, "Не удалось сохранить программные наблюдения"
            ) from exc

    async def observations_for(
        self, external_id: str, source: str
    ) -> list[ExerciseProgramObservation]:
        try:
            async with self._sessions() as session:
                rows = (
                    await session.execute(
                        select(ExerciseProgramObservationRow).where(
                            ExerciseProgramObservationRow.exercise_external_id
                            == external_id,
                            ExerciseProgramObservationRow.exercise_source == source,
                        )
                    )
                ).scalars().all()
        except SQLAlchemyError as exc:
            raise _persistence_error(exc, "Ошибка чтения наблюдений") from exc
        return [_observation_to_domain(row) for row in rows]

    # --- Диагностика ---------------------------------------------------------

    async def health_counters(self) -> dict[str, int]:
        """Числа, которыми проверяется результат ingestion."""
        try:
            async with self._sessions() as session:
                async def scalar(stmt) -> int:
                    return (await session.execute(stmt)).scalar_one() or 0

                records_total = await scalar(
                    select(func.count()).select_from(ExternalExerciseRecordRow)
                )
                links_total = await scalar(
                    select(func.count()).select_from(ExerciseSourceLinkRow)
                )
                provenance_total = await scalar(
                    select(func.count()).select_from(ExerciseFieldProvenanceRow)
                )
                observations_total = await scalar(
                    select(func.count()).select_from(ExerciseProgramObservationRow)
                )
                exercises_with_links = await scalar(
                    select(
                        func.count(
                            func.distinct(ExerciseSourceLinkRow.exercise_external_id)
                        )
                    )
                )
                exercises_with_observations = await scalar(
                    select(
                        func.count(
                            func.distinct(
                                ExerciseProgramObservationRow.exercise_external_id
                            )
                        )
                    )
                )
                imported = await scalar(
                    select(func.count())
                    .select_from(ExternalExerciseRecordRow)
                    .where(
                        ExternalExerciseRecordRow.import_status
                        == ImportStatus.IMPORTED.value
                    )
                )
                enriched = await scalar(
                    select(func.count())
                    .select_from(ExternalExerciseRecordRow)
                    .where(
                        ExternalExerciseRecordRow.import_status
                        == ImportStatus.ENRICHED.value
                    )
                )
                review = await scalar(
                    select(func.count())
                    .select_from(ExternalExerciseRecordRow)
                    .where(
                        ExternalExerciseRecordRow.quality_status
                        == QualityStatus.REVIEW.value
                    )
                )
                rejected = await scalar(
                    select(func.count())
                    .select_from(ExternalExerciseRecordRow)
                    .where(
                        ExternalExerciseRecordRow.quality_status
                        == QualityStatus.REJECT.value
                    )
                )
                duplicates = await scalar(
                    select(func.count())
                    .select_from(ExternalExerciseRecordRow)
                    .where(
                        ExternalExerciseRecordRow.decision
                        == IngestionDecision.DUPLICATE_VARIANT.value
                    )
                )
        except SQLAlchemyError as exc:
            raise _persistence_error(exc, "Ошибка диагностики ingestion") from exc
        return {
            "external_records_total": records_total,
            "source_links_total": links_total,
            "field_provenance_total": provenance_total,
            "program_observations_total": observations_total,
            "exercises_with_source_links": exercises_with_links,
            "exercises_with_observations": exercises_with_observations,
            "records_imported": imported,
            "records_enriched": enriched,
            "records_review": review,
            "records_rejected": rejected,
            "records_duplicate_variant": duplicates,
        }

    async def delete_records_of_source(self, source_key: str) -> int:
        """Удаляет staging-записи источника.

        Нужно только повторному разбору источника с нуля: связи и provenance при
        этом не затрагиваются, потому что описывают уже применённые изменения
        canonical каталога, а не текущее чтение источника.
        """
        try:
            async with self._sessions() as session:
                async with session.begin():
                    result = await session.execute(
                        delete(ExternalExerciseRecordRow).where(
                            ExternalExerciseRecordRow.source_key == source_key
                        )
                    )
                    return result.rowcount or 0
        except SQLAlchemyError as exc:
            raise _persistence_error(exc, "Не удалось удалить staging-записи") from exc


def _record_to_domain(row: ExternalExerciseRecordRow) -> ExternalExerciseRecord:
    return ExternalExerciseRecord(
        source_key=row.source_key,
        source_version=row.source_version,
        source_record_id=row.source_record_id,
        record_hash=row.record_hash,
        raw_name=row.raw_name,
        normalized_name=row.normalized_name,
        name_key=row.name_key,
        payload=dict(row.payload or {}),
        quality_score=row.quality_score,
        quality_status=QualityStatus(row.quality_status),
        quality_reasons=list(row.quality_reasons or []),
        decision=IngestionDecision(row.decision),
        match_confidence=row.match_confidence,
        match_reasons=list(row.match_reasons or []),
        matched_external_id=row.matched_external_id,
        matched_source=row.matched_source,
        import_status=ImportStatus(row.import_status),
        import_note=row.import_note,
        imported_at=row.imported_at,
    )


def _observation_to_domain(
    row: ExerciseProgramObservationRow,
) -> ExerciseProgramObservation:
    return ExerciseProgramObservation(
        exercise_external_id=row.exercise_external_id,
        exercise_source=row.exercise_source,
        source_key=row.source_key,
        source_version=row.source_version,
        source_record_id=row.source_record_id,
        program_count=row.program_count,
        occurrence_count=row.occurrence_count,
        typical_sets_median=row.typical_sets_median,
        typical_sets_min=row.typical_sets_min,
        typical_sets_max=row.typical_sets_max,
        typical_reps_median=row.typical_reps_median,
        typical_reps_min=row.typical_reps_min,
        typical_reps_max=row.typical_reps_max,
        typical_hold_seconds_median=row.typical_hold_seconds_median,
        typical_intensity_median=row.typical_intensity_median,
        source_goals=dict(row.source_goals or {}),
        source_levels=dict(row.source_levels or {}),
        source_equipment_contexts=dict(row.source_equipment_contexts or {}),
    )
