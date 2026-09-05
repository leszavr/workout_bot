"""Сервис ingestion: разбор источников, план и его применение.

Два раздельных шага, и разделение принципиально.

**Планирование** (`plan`) читает источники, сопоставляет записи с canonical
каталогом, оценивает качество и принимает решение. Ничего не записывает в
каталог. Результат — план с числами, которые можно проверить до изменения данных:
именно этот шаг закрывает требование «сначала dry-run».

**Применение** (`apply`) выполняет план: создаёт новые упражнения, обогащает
существующие, записывает связи источников, provenance полей и программные
наблюдения. Оно не принимает решений заново — берёт их из плана, поэтому
применение воспроизводимо и проверяемо.

Идемпотентность обеспечивается тремя механизмами, а не осторожностью:

1. staging-запись уникальна по (`source_key`, `source_record_id`) — повторное
   чтение источника обновляет строку;
2. связь источника (`exercise_source_links`) читается сопоставлением как самый
   сильный признак: упражнение, уже импортированное из этой записи, будет найдено
   независимо от того, изменились ли правила;
3. canonical upsert идёт по (`external_id`, `source`), а external_id нового
   упражнения выводится из названия детерминированно.

Оборудование внешних упражнений не пишется вторым словарём: значения источника
кладутся в `exercises.equipment` (вход действующего фильтра), а нормализованное
знание строится существующим `EquipmentKnowledgeImporter` и записывается в
`exercise_equipment_requirements`. Иначе появился бы второй источник истины про
оборудование, чего этап прямо запрещает.
"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from src.application.equipment.import_service import EquipmentKnowledgeImporter
from src.application.equipment.matching import EquipmentMatcher
from src.application.ingestion.candidates import ExternalExerciseCandidate
from src.application.ingestion.equipment_tags import field_tags
from src.application.ingestion.normalization import display_name
from src.application.ingestion.matching import (
    ExerciseMatcher,
    MatchResult,
    build_canonical_features,
    build_equipment_context,
    variant_signature,
)
from src.application.ingestion.merge_policy import (
    FIELD_ALIASES,
    FIELD_DESCRIPTION,
    FIELD_MEDIA,
    FIELD_NAME,
    FIELD_PRIMARY_MUSCLES,
    FIELD_SECONDARY_MUSCLES,
    FIELD_TECHNIQUE,
    FIELD_TECHNIQUE_RU,
    REASON_ORIGIN,
    MergePlan,
    build_enrichment_plan,
    build_twin_enrichment_plan,
    decide,
    refine_plan_against_current,
)
from src.application.ingestion.quality import QualityAssessment, QualityScorer
from src.application.ingestion.sources import merge_aggregates, observation_metrics
from src.domain.exercise import Exercise
from src.domain.ingestion import (
    ExerciseFieldProvenance,
    ExerciseProgramObservation,
    ExerciseSourceLink,
    ExternalExerciseRecord,
    ExternalSource,
    ExternalSourceVersion,
    ImportStatus,
    IngestionDecision,
    QualityStatus,
    SourceLinkRelation,
)

logger = logging.getLogger(__name__)

# Каталог читается целиком: сопоставление сравнивает внешнюю запись со всем
# каталогом, и постраничное чтение дало бы разные результаты для разных страниц.
CATALOG_LIMIT = 5000

# Источник canonical записей, созданных из внешних каталогов. Отдельное значение,
# а не `leszavr/workout`: канонический ключ упражнения — пара
# (external_id, source), и происхождение записи обязано быть видно в ключе, иначе
# «откуда это упражнение» требует join по provenance при каждом взгляде.
CANONICAL_SOURCE_EXTERNAL = "workout_bot/external"

_UNSAFE_ID = re.compile(r"[^A-Za-z0-9_.-]+")


def make_external_id(name: str, source_record_id: str) -> str:
    """Детерминированный canonical идентификатор для нового упражнения.

    Строится из названия, а не из идентификатора источника: `0134` не читается
    ни в программе, ни в логах, ни в отчёте. Хвост из хеша записи источника
    гарантирует различие, если два разных упражнения дают одинаковое имя после
    приведения к безопасному виду.
    """
    base = _UNSAFE_ID.sub("_", name.strip()).strip("_")
    base = base[:100] or "exercise"
    digest = hashlib.sha256(source_record_id.encode("utf-8")).hexdigest()[:8]
    return f"{base}__{digest}"


def _value_hash(value: object) -> str:
    return hashlib.sha256(repr(value).encode("utf-8")).hexdigest()


@dataclass
class SourceReport:
    """Числа по одному источнику."""

    source_key: str
    kind: str
    version: str
    records_total: int = 0
    decisions: dict[str, int] = field(default_factory=dict)
    quality: dict[str, int] = field(default_factory=dict)
    with_technique: int = 0
    with_technique_ru: int = 0
    with_media: int = 0
    with_equipment_mapped: int = 0
    with_primary_muscle: int = 0
    unmapped_equipment: dict[str, int] = field(default_factory=dict)
    ambiguous_muscles: dict[str, int] = field(default_factory=dict)
    unmapped_muscles: dict[str, int] = field(default_factory=dict)
    read_stats: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "source_key": self.source_key,
            "kind": self.kind,
            "version": self.version,
            "records_total": self.records_total,
            "decisions": dict(sorted(self.decisions.items())),
            "quality": dict(sorted(self.quality.items())),
            "with_technique": self.with_technique,
            "with_technique_ru": self.with_technique_ru,
            "with_media": self.with_media,
            "with_equipment_mapped": self.with_equipment_mapped,
            "with_primary_muscle": self.with_primary_muscle,
            "unmapped_equipment": dict(
                sorted(self.unmapped_equipment.items(), key=lambda kv: -kv[1])
            ),
            "ambiguous_muscles": dict(
                sorted(self.ambiguous_muscles.items(), key=lambda kv: -kv[1])
            ),
            "unmapped_muscles": dict(
                sorted(self.unmapped_muscles.items(), key=lambda kv: -kv[1])
            ),
            "read_stats": self.read_stats,
        }


@dataclass
class PlannedRecord:
    """Внешняя запись с принятым решением и планом изменений."""

    candidate: ExternalExerciseCandidate
    match: MatchResult
    quality: QualityAssessment
    decision: IngestionDecision
    note: str
    enrichment: MergePlan | None = None

    def as_staging_record(self) -> ExternalExerciseRecord:
        return ExternalExerciseRecord(
            source_key=self.candidate.source_key,
            source_version=self.candidate.source_version,
            source_record_id=self.candidate.source_record_id,
            record_hash=self.candidate.record_hash(),
            raw_name=self.candidate.raw_name[:255],
            normalized_name=self.candidate.name[:255],
            name_key=(self.candidate.name_key or self.candidate.name)[:255],
            payload=self.candidate.as_payload(),
            quality_score=self.quality.score,
            quality_status=self.quality.status,
            quality_reasons=list(self.quality.reasons),
            decision=self.decision,
            match_confidence=self.match.confidence,
            match_reasons=list(self.match.reasons),
            matched_external_id=self.match.external_id,
            matched_source=self.match.source,
            import_status=ImportStatus.PENDING,
            import_note=self.note[:300],
        )


@dataclass
class IngestionPlan:
    """План изменения canonical каталога по внешним источникам."""

    canonical_before: int = 0
    records: list[PlannedRecord] = field(default_factory=list)
    sources: list[ExternalSource] = field(default_factory=list)
    versions: list[ExternalSourceVersion] = field(default_factory=list)
    reports: dict[str, SourceReport] = field(default_factory=dict)
    observations: list[ExerciseProgramObservation] = field(default_factory=list)
    # Наблюдения, для которых упражнения в каталоге нет: число важно, потому что
    # это мера того, насколько чужой датасет программ описывает наш каталог.
    observations_unmatched: int = 0
    # Связи, ведущие на упражнение, которого в каталоге больше нет. Число важно:
    # оно означает, что упражнения удалялись после импорта, и импорт создаст их
    # заново.
    orphan_source_links: int = 0
    # Ключи сопоставления по источнику, включая датасет программ. Нужны отчёту:
    # записи датасета программ не являются кандидатами в каталог и в `records` не
    # попадают, но пересечение источников между собой считать по ним надо.
    name_keys_by_source: dict[str, set[str]] = field(default_factory=dict)
    cross_source: dict = field(default_factory=dict)

    def by_decision(self, decision: IngestionDecision) -> list[PlannedRecord]:
        return [r for r in self.records if r.decision is decision]

    def summary(self) -> dict:
        counts: dict[str, int] = {}
        for record in self.records:
            counts[record.decision.value] = counts.get(record.decision.value, 0) + 1
        new_count = len(self.by_decision(IngestionDecision.NEW_RELEVANT))
        return {
            "canonical_before": self.canonical_before,
            "external_candidates": len(self.records),
            "decisions": dict(sorted(counts.items())),
            "expected_new_exercises": new_count,
            "expected_canonical_after": self.canonical_before + new_count,
            "expected_enriched": len(self.by_decision(IngestionDecision.ENRICHABLE)),
            "program_observations": len(self.observations),
            "program_observations_unmatched": self.observations_unmatched,
            "orphan_source_links": self.orphan_source_links,
        }


@dataclass
class ApplyResult:
    """Что фактически изменилось в canonical каталоге."""

    created: int = 0
    enriched: int = 0
    skipped: int = 0
    links_written: int = 0
    provenance_written: int = 0
    observations_written: int = 0
    media_planned: int = 0
    canonical_after: int = 0
    field_changes: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "created": self.created,
            "enriched": self.enriched,
            "skipped": self.skipped,
            "links_written": self.links_written,
            "provenance_written": self.provenance_written,
            "observations_written": self.observations_written,
            "media_planned": self.media_planned,
            "canonical_after": self.canonical_after,
            "field_changes": dict(sorted(self.field_changes.items())),
        }


class ExternalIngestionService:
    """Строит и применяет план ingestion внешних источников."""

    def __init__(
        self,
        *,
        exercises,
        equipment_repository,
        ingestion_repository,
        media_repository=None,
    ) -> None:
        self._exercises = exercises
        self._equipment = equipment_repository
        self._ingestion = ingestion_repository
        self._media = media_repository

    # --- Планирование --------------------------------------------------------

    async def plan(
        self,
        *,
        catalog_reads: list,
        program_reads: list | None = None,
        program_aggregates: dict | None = None,
    ) -> IngestionPlan:
        """Строит план по прочитанным источникам, ничего не записывая."""
        index = await self._equipment.load_index()
        if not index.items:
            raise RuntimeError(
                "Словарь оборудования пуст: примените миграции (alembic upgrade head)"
            )
        matcher_equipment = EquipmentMatcher(index)

        def resolve_equipment_ids(values: list[str]) -> frozenset[str]:
            found: set[str] = set()
            for value in values:
                match = matcher_equipment.match_catalog_value(value)
                if match.single is not None:
                    found.add(match.single)
            return frozenset(found)

        def resolve_equipment_with_gaps(
            values: list[str],
        ) -> tuple[frozenset[str], tuple[str, ...]]:
            found: set[str] = set()
            unmapped: list[str] = []
            for value in values:
                cleaned = (value or "").strip()
                if not cleaned:
                    continue
                match = matcher_equipment.match_catalog_value(cleaned)
                if match.single is not None:
                    found.add(match.single)
                else:
                    unmapped.append(cleaned)
            return frozenset(found), tuple(unmapped)

        def related_equipment(equipment_id: str) -> set[str]:
            """Оборудование, связанное с данным отношением «частное — родовое».

            Нужно, чтобы точнее названный снаряд не выглядел вторым снарядом:
            `Smith Machine Bench Press` объявляет полем родовое `machine`, а
            названием — `smith machine`, и это одно и то же оборудование.
            """
            return {
                equipment_id,
                *index.specializations_of(equipment_id),
            }

        def resolve_equipment_phrases(phrases: list[str]) -> frozenset[str]:
            """Разрешает фразы названия через словарь оборудования.

            Только однозначные совпадения: фраза `ball` указывает и на медбол, и
            на фитбол, и трактовать её как конкретный снаряд нельзя. Словарь
            отвечает на это неоднозначностью, и она здесь отбрасывается, а не
            разрешается выбором первого варианта.
            """
            found: set[str] = set()
            for phrase in phrases:
                match = matcher_equipment.match_catalog_value(phrase)
                if match.single is not None:
                    found.add(match.single)
            return frozenset(found)

        _, catalog = await self._exercises.search(
            _all_exercises_query(), limit=CATALOG_LIMIT
        )
        features = build_canonical_features(
            catalog,
            resolve_equipment_ids,
            resolve_equipment_phrases,
            related_equipment,
        )
        by_external_id = {e.external_id: e for e in catalog}
        existing_refs = {(e.external_id, e.source) for e in catalog}
        # Связи, ведущие на удалённое упражнение, отбрасываются. Иначе такая
        # связь молча запрещала бы повторный импорт: запись опознавалась бы как
        # существующая, а упражнения в каталоге не было бы. Удаление упражнения
        # администратором — штатная операция, и импорт обязан после неё
        # восстанавливать запись, а не считать её уже импортированной.
        all_links = await self._ingestion.source_links_index()
        source_links = {
            key: target for key, target in all_links.items() if target in existing_refs
        }
        orphan_links = len(all_links) - len(source_links)
        matcher = ExerciseMatcher(features, source_links=source_links)
        scorer = QualityScorer(resolve_equipment_with_gaps)

        media_present = await self._exercises_with_media()

        plan = IngestionPlan(canonical_before=len(catalog))
        plan.orphan_source_links = orphan_links

        # Дубли внутри одного источника существуют: 38 записей внешнего каталога
        # различаются только пометкой съёмки (`(male)`, `v. 2`), а после её снятия
        # описывают одно упражнение. Без проверки внутри плана каждая из них
        # создала бы отдельное canonical упражнение, и дедупликация относительно
        # каталога ничего не дала бы — дубли пришли бы парами.
        planned_identities: dict[tuple, tuple[str, str]] = {}
        # Запись, которая заняла тождество, вместе с её оценкой: с ней
        # сравниваются её дубли.
        twin_candidates: dict[
            tuple, tuple[ExternalExerciseCandidate, QualityAssessment]
        ] = {}

        for read in catalog_reads:
            plan.sources.append(read.source)
            plan.versions.append(read.version)
            report = SourceReport(
                source_key=read.source.source_key,
                kind=read.source.kind.value,
                version=read.version.version,
                read_stats=dict(read.stats),
            )
            plan.reports[read.source.source_key] = report

            for candidate in read.candidates:
                quality = scorer.assess(candidate)
                equipment_context = build_equipment_context(
                    name=candidate.name,
                    declared_values=list(candidate.equipment_values),
                    resolve_values=resolve_equipment_ids,
                    resolve_phrases=resolve_equipment_phrases,
                    related_equipment=related_equipment,
                )
                match = matcher.match(candidate, equipment=equipment_context)

                enrichment: MergePlan | None = None
                if match.matched and match.external_id in by_external_id:
                    canonical = by_external_id[match.external_id]
                    enrichment = build_enrichment_plan(
                        candidate,
                        quality,
                        canonical_name=canonical.name,
                        canonical_technique=canonical.technique,
                        canonical_technique_ru=canonical.technique_ru,
                        canonical_description=canonical.description,
                        canonical_aliases=list(canonical.aliases),
                        canonical_primary=list(canonical.primary_muscles),
                        canonical_secondary=list(canonical.secondary_muscles),
                        canonical_has_media=canonical.external_id in media_present,
                    )

                decision, note = decide(
                    candidate, match, quality, enrichment=enrichment
                )

                if decision is IngestionDecision.NEW_RELEVANT:
                    identity = (
                        candidate.core,
                        variant_signature(candidate.name, equipment_context),
                        equipment_context.effective,
                    )
                    twin = planned_identities.get(identity)
                    if twin is not None:
                        decision = IngestionDecision.DUPLICATE_VARIANT
                        note = (
                            "дубль внутри источника: та же запись уже добавлена "
                            f"как {twin[0]}"
                        )
                        match = MatchResult(
                            external_id=twin[0],
                            source=twin[1],
                            confidence=match.confidence,
                            reasons=[*match.reasons, "intra_source_duplicate"],
                            identical=False,
                            variant_of=twin[0],
                        )
                        # Дубль не создаёт упражнение, но его данные не всегда
                        # беднее: у второй записи бывает полнее техника и другое
                        # название. Без этого шага они были бы взяты только на
                        # втором прогоне, и импорт не сходился бы за один.
                        twin_candidate, twin_quality = twin_candidates[identity]
                        enrichment = build_twin_enrichment_plan(
                            candidate, twin_candidate, quality, twin_quality
                        )
                    else:
                        planned_identities[identity] = (
                            make_external_id(
                                candidate.name, candidate.source_record_id
                            ),
                            CANONICAL_SOURCE_EXTERNAL,
                        )
                        twin_candidates[identity] = (candidate, quality)

                plan.records.append(
                    PlannedRecord(
                        candidate=candidate,
                        match=match,
                        quality=quality,
                        decision=decision,
                        note=note,
                        enrichment=enrichment,
                    )
                )
                plan.name_keys_by_source.setdefault(
                    candidate.source_key, set()
                ).add(candidate.name_key or candidate.name)

                report.records_total += 1
                report.decisions[decision.value] = (
                    report.decisions.get(decision.value, 0) + 1
                )
                report.quality[quality.status.value] = (
                    report.quality.get(quality.status.value, 0) + 1
                )
                if candidate.technique:
                    report.with_technique += 1
                if candidate.technique_ru:
                    report.with_technique_ru += 1
                if candidate.media:
                    report.with_media += 1
                if quality.equipment_ids:
                    report.with_equipment_mapped += 1
                if quality.primary_muscles:
                    report.with_primary_muscle += 1
                for value in quality.unmapped_equipment:
                    report.unmapped_equipment[value] = (
                        report.unmapped_equipment.get(value, 0) + 1
                    )
                for value in quality.ambiguous_muscles:
                    report.ambiguous_muscles[value] = (
                        report.ambiguous_muscles.get(value, 0) + 1
                    )
                for value in quality.unmapped_muscles:
                    report.unmapped_muscles[value] = (
                        report.unmapped_muscles.get(value, 0) + 1
                    )

        # Датасет программ обрабатывается отдельно: его записи не являются
        # кандидатами в каталог. Наблюдение прикрепляется к упражнению, которое
        # уже есть либо будет создано этим же планом.
        if program_reads:
            planned_new = {
                record.candidate.source_record_id: make_external_id(
                    record.candidate.name, record.candidate.source_record_id
                )
                for record in plan.records
                if record.decision is IngestionDecision.NEW_RELEVANT
            }
            new_by_key = {
                record.candidate.name_key: planned_new[
                    record.candidate.source_record_id
                ]
                for record in plan.records
                if record.decision is IngestionDecision.NEW_RELEVANT
            }
            for read in program_reads:
                plan.sources.append(read.source)
                plan.versions.append(read.version)
                report = SourceReport(
                    source_key=read.source.source_key,
                    kind=read.source.kind.value,
                    version=read.version.version,
                    read_stats=dict(read.stats),
                )
                plan.reports[read.source.source_key] = report

                aggregates = program_aggregates or {}
                # Наблюдение уникально по паре «упражнение — источник», а разные
                # названия датасета сопоставляются с одним упражнением
                # (`Bench Press (Barbell)` и `Barbell Bench Press` — одна запись
                # каталога). Поэтому сначала собираются группы, потом считается
                # одно наблюдение на упражнение.
                grouped: dict[tuple[str, str], list] = {}
                grouped_records: dict[tuple[str, str], str] = {}
                for candidate in read.candidates:
                    report.records_total += 1
                    plan.name_keys_by_source.setdefault(
                        candidate.source_key, set()
                    ).add(candidate.name_key or candidate.name)
                    # Датасет программ оборудования не объявляет: единственное
                    # утверждение о снаряде — слово в названии, и оно берётся как
                    # эффективное оборудование записи.
                    equipment_context = build_equipment_context(
                        name=candidate.name,
                        declared_values=[],
                        resolve_values=resolve_equipment_ids,
                        resolve_phrases=resolve_equipment_phrases,
                        related_equipment=related_equipment,
                    )
                    match = matcher.match(candidate, equipment=equipment_context)
                    target: tuple[str, str] | None = None
                    if match.matched and match.external_id is not None:
                        target = (match.external_id, match.source or "")
                    elif candidate.name_key in new_by_key:
                        target = (
                            new_by_key[candidate.name_key],
                            CANONICAL_SOURCE_EXTERNAL,
                        )

                    if target is None:
                        plan.observations_unmatched += 1
                        report.decisions["unmatched"] = (
                            report.decisions.get("unmatched", 0) + 1
                        )
                        continue

                    report.decisions["matched"] = report.decisions.get("matched", 0) + 1
                    aggregate = aggregates.get(candidate.name)
                    if aggregate is not None:
                        grouped.setdefault(target, []).append(aggregate)
                    grouped_records.setdefault(target, candidate.source_record_id)

                for target, group in grouped.items():
                    metrics = _metrics_from_aggregate(merge_aggregates(group))
                    plan.observations.append(
                        ExerciseProgramObservation(
                            exercise_external_id=target[0],
                            exercise_source=target[1],
                            source_key=read.source.source_key,
                            source_version=read.version.version,
                            source_record_id=grouped_records[target],
                            **metrics,
                        )
                    )

        plan.cross_source = _cross_source_report(plan)
        return plan

    # --- Применение ----------------------------------------------------------

    async def apply(self, plan: IngestionPlan, *, import_media: bool = False) -> ApplyResult:
        """Применяет план: создаёт, обогащает, записывает provenance."""
        result = ApplyResult()

        for source in plan.sources:
            await self._ingestion.upsert_source(source)
        for version in plan.versions:
            await self._ingestion.upsert_version(version)

        await self._ingestion.upsert_records(
            [record.as_staging_record() for record in plan.records]
        )

        links: list[ExerciseSourceLink] = []
        provenance: list[ExerciseFieldProvenance] = []
        status_updates: list[tuple[str, str, ImportStatus, str | None]] = []
        media_jobs: list[tuple[str, str, ExternalExerciseCandidate]] = []

        for record in plan.records:
            candidate = record.candidate
            if record.decision is IngestionDecision.NEW_RELEVANT:
                external_id = make_external_id(
                    candidate.name, candidate.source_record_id
                )
                exercise = _exercise_from_candidate(
                    candidate, record.quality, external_id
                )
                await self._exercises.upsert(exercise)
                result.created += 1
                links.append(
                    ExerciseSourceLink(
                        exercise_external_id=external_id,
                        exercise_source=CANONICAL_SOURCE_EXTERNAL,
                        source_key=candidate.source_key,
                        source_record_id=candidate.source_record_id,
                        source_version=candidate.source_version,
                        relation=SourceLinkRelation.ORIGIN,
                        confidence=1.0,
                        reasons=[REASON_ORIGIN],
                    )
                )
                for field_name, value in (
                    (FIELD_NAME, exercise.name),
                    (FIELD_TECHNIQUE, exercise.technique),
                    (FIELD_TECHNIQUE_RU, exercise.technique_ru),
                    (FIELD_PRIMARY_MUSCLES, exercise.primary_muscles),
                    (FIELD_SECONDARY_MUSCLES, exercise.secondary_muscles),
                ):
                    if not value:
                        continue
                    provenance.append(
                        ExerciseFieldProvenance(
                            exercise_external_id=external_id,
                            exercise_source=CANONICAL_SOURCE_EXTERNAL,
                            field=field_name,
                            source_key=candidate.source_key,
                            source_record_id=candidate.source_record_id,
                            source_version=candidate.source_version,
                            value_hash=_value_hash(value),
                            reason=REASON_ORIGIN,
                        )
                    )
                    result.field_changes[field_name] = (
                        result.field_changes.get(field_name, 0) + 1
                    )
                if candidate.media:
                    media_jobs.append(
                        (external_id, CANONICAL_SOURCE_EXTERNAL, candidate)
                    )
                status_updates.append(
                    (
                        candidate.source_key,
                        candidate.source_record_id,
                        ImportStatus.IMPORTED,
                        record.note,
                    )
                )
                continue

            if (
                record.decision is IngestionDecision.ENRICHABLE
                and record.enrichment is not None
                and record.match.external_id is not None
            ):
                target_id = record.match.external_id
                target_source = record.match.source or ""
                existing = await self._exercises.get_by_external_id(
                    target_id, target_source
                )
                if existing is None:
                    status_updates.append(
                        (
                            candidate.source_key,
                            candidate.source_record_id,
                            ImportStatus.SKIPPED,
                            "canonical запись не найдена при применении плана",
                        )
                    )
                    result.skipped += 1
                    continue
                # План строился от снимка каталога, а применяется
                # последовательно: другая внешняя запись могла уже заполнить те
                # же поля полнее. Без повторной проверки порядок записей решал бы
                # результат, и импорт не сходился бы за один прогон.
                effective = refine_plan_against_current(record.enrichment, existing)
                if not effective.changes_anything:
                    status_updates.append(
                        (
                            candidate.source_key,
                            candidate.source_record_id,
                            ImportStatus.SKIPPED,
                            "поля уже заполнены другой записью источника",
                        )
                    )
                    result.skipped += 1
                    links.append(
                        ExerciseSourceLink(
                            exercise_external_id=target_id,
                            exercise_source=target_source,
                            source_key=candidate.source_key,
                            source_record_id=candidate.source_record_id,
                            source_version=candidate.source_version,
                            relation=SourceLinkRelation.ENRICHMENT,
                            confidence=record.match.confidence,
                            reasons=list(record.match.reasons),
                        )
                    )
                    continue
                updated = _apply_enrichment(existing, effective)
                await self._exercises.upsert(updated)
                result.enriched += 1
                for field_name in effective.fields:
                    provenance.append(
                        ExerciseFieldProvenance(
                            exercise_external_id=target_id,
                            exercise_source=target_source,
                            field=field_name,
                            source_key=candidate.source_key,
                            source_record_id=candidate.source_record_id,
                            source_version=candidate.source_version,
                            value_hash=_value_hash(effective.fields[field_name]),
                            reason=effective.reasons.get(field_name),
                        )
                    )
                    result.field_changes[field_name] = (
                        result.field_changes.get(field_name, 0) + 1
                    )
                if effective.media:
                    media_jobs.append((target_id, target_source, candidate))
                    provenance.append(
                        ExerciseFieldProvenance(
                            exercise_external_id=target_id,
                            exercise_source=target_source,
                            field=FIELD_MEDIA,
                            source_key=candidate.source_key,
                            source_record_id=candidate.source_record_id,
                            source_version=candidate.source_version,
                            value_hash=_value_hash(
                                [m.relative_path for m in effective.media]
                            ),
                            reason=effective.reasons.get(FIELD_MEDIA),
                        )
                    )
                    result.field_changes[FIELD_MEDIA] = (
                        result.field_changes.get(FIELD_MEDIA, 0) + 1
                    )
                links.append(
                    ExerciseSourceLink(
                        exercise_external_id=target_id,
                        exercise_source=target_source,
                        source_key=candidate.source_key,
                        source_record_id=candidate.source_record_id,
                        source_version=candidate.source_version,
                        relation=SourceLinkRelation.ENRICHMENT,
                        confidence=record.match.confidence,
                        reasons=list(record.match.reasons),
                    )
                )
                status_updates.append(
                    (
                        candidate.source_key,
                        candidate.source_record_id,
                        ImportStatus.ENRICHED,
                        record.note,
                    )
                )
                continue

            if record.decision is IngestionDecision.EXISTING and (
                record.match.external_id is not None
            ):
                links.append(
                    ExerciseSourceLink(
                        exercise_external_id=record.match.external_id,
                        exercise_source=record.match.source or "",
                        source_key=candidate.source_key,
                        source_record_id=candidate.source_record_id,
                        source_version=candidate.source_version,
                        relation=SourceLinkRelation.ENRICHMENT,
                        confidence=record.match.confidence,
                        reasons=list(record.match.reasons),
                    )
                )
                status_updates.append(
                    (
                        candidate.source_key,
                        candidate.source_record_id,
                        ImportStatus.SKIPPED,
                        record.note,
                    )
                )
                result.skipped += 1
                continue

            if record.decision is IngestionDecision.DUPLICATE_VARIANT and (
                record.match.external_id is not None
            ):
                target_id = record.match.external_id
                target_source = record.match.source or ""
                # Дубль не создаёт упражнение, но может дополнить созданное:
                # у второй записи бывает полнее техника и другое название.
                # Упражнение-двойник уже создано выше — записи идут в порядке
                # источника, и NEW_RELEVANT встречается раньше своих дублей.
                if record.enrichment is not None and record.enrichment.changes_anything:
                    existing = await self._exercises.get_by_external_id(
                        target_id, target_source
                    )
                    effective = (
                        refine_plan_against_current(record.enrichment, existing)
                        if existing is not None
                        else None
                    )
                    if existing is not None and effective.changes_anything:
                        await self._exercises.upsert(
                            _apply_enrichment(existing, effective)
                        )
                        for field_name in effective.fields:
                            provenance.append(
                                ExerciseFieldProvenance(
                                    exercise_external_id=target_id,
                                    exercise_source=target_source,
                                    field=field_name,
                                    source_key=candidate.source_key,
                                    source_record_id=candidate.source_record_id,
                                    source_version=candidate.source_version,
                                    value_hash=_value_hash(
                                        effective.fields[field_name]
                                    ),
                                    reason=effective.reasons.get(field_name),
                                )
                            )
                            result.field_changes[field_name] = (
                                result.field_changes.get(field_name, 0) + 1
                            )
                links.append(
                    ExerciseSourceLink(
                        exercise_external_id=target_id,
                        exercise_source=target_source,
                        source_key=candidate.source_key,
                        source_record_id=candidate.source_record_id,
                        source_version=candidate.source_version,
                        relation=SourceLinkRelation.DUPLICATE_VARIANT,
                        confidence=record.match.confidence,
                        reasons=list(record.match.reasons),
                    )
                )
                status_updates.append(
                    (
                        candidate.source_key,
                        candidate.source_record_id,
                        ImportStatus.SKIPPED,
                        record.note,
                    )
                )
                result.skipped += 1
                continue

            # LOW_QUALITY / QUESTIONABLE / UNKNOWN: запись остаётся в staging с
            # решением и причиной, canonical каталог не меняется.
            status_updates.append(
                (
                    candidate.source_key,
                    candidate.source_record_id,
                    ImportStatus.REJECTED
                    if record.decision is IngestionDecision.LOW_QUALITY
                    else ImportStatus.PENDING,
                    record.note,
                )
            )
            result.skipped += 1

        for observation in plan.observations:
            links.append(
                ExerciseSourceLink(
                    exercise_external_id=observation.exercise_external_id,
                    exercise_source=observation.exercise_source,
                    source_key=observation.source_key,
                    source_record_id=observation.source_record_id,
                    source_version=observation.source_version,
                    relation=SourceLinkRelation.OBSERVATION,
                    confidence=1.0,
                    reasons=["program_dataset_observation"],
                )
            )

        result.links_written = await self._ingestion.upsert_links(links)
        result.provenance_written = await self._ingestion.upsert_field_provenance(
            provenance
        )
        result.observations_written = await self._ingestion.upsert_observations(
            plan.observations
        )
        await self._ingestion.mark_import_status(status_updates)
        result.media_planned = sum(len(candidate.media) for _, _, candidate in media_jobs)
        result.canonical_after = await self._exercises.count()
        # Медиа-задания возвращаются вызывающей стороне: загрузка файлов требует
        # доступа к локальной копии источника и объектному хранилищу, и держать
        # эту зависимость в сервисе решений не нужно.
        self.pending_media = media_jobs
        return result

    async def _exercises_with_media(self) -> set[str]:
        if self._media is None:
            return set()
        assets = await self._media.list_all()
        return {asset.exercise_external_id for asset in assets}


def _all_exercises_query():
    """Условие выборки всего каталога, включая деактивированные упражнения.

    Деактивированные читаются намеренно: иначе внешняя запись, соответствующая
    выключенному упражнению, была бы объявлена новой, и в каталоге появился бы
    дубль отключённой записи.
    """
    from src.infrastructure.persistence.postgres.exercise_repository import (
        ExerciseQuery,
    )

    return ExerciseQuery(is_active=None)


def _exercise_from_candidate(
    candidate: ExternalExerciseCandidate,
    quality: QualityAssessment,
    external_id: str,
) -> Exercise:
    """Создаёт canonical упражнение из внешней записи.

    Значения `equipment` приводятся к словарю поля (`equipment_tags`): поле
    остаётся входом действующего фильтра, и формулировка источника в нём сделала
    бы упражнение невыполнимым для любого пользователя. Точное знание при этом не
    теряется — оно восстанавливается из canonical ID при построении требований, а
    исходная формулировка хранится в staging и provenance.

    ``difficulty`` не заполняется: уровня подготовки источник не сообщает, и
    назначить его значило бы выдумать факт. Следствие названо в отчёте этапа:
    фильтр трактует неизвестную сложность как допустимую, поэтому такие
    упражнения доступны любому уровню, пока уровень не установлен человеком либо
    отдельным этапом.
    """
    name = display_name(candidate.name)
    # Исходное написание источника сохраняется синонимом: поиск по названию
    # источника обязан находить упражнение.
    aliases = [candidate.raw_name] if candidate.raw_name != name else []
    return Exercise(
        external_id=external_id,
        source=CANONICAL_SOURCE_EXTERNAL,
        source_version=candidate.source_version,
        name=name,
        name_ru=None,
        aliases=aliases,
        description=candidate.description,
        technique=candidate.technique,
        technique_ru=candidate.technique_ru,
        primary_muscles=list(quality.primary_muscles),
        secondary_muscles=list(quality.secondary_muscles),
        equipment=field_tags(
            quality.equipment_ids,
            has_unmapped_values=bool(quality.unmapped_equipment),
        ),
        exercise_type=_exercise_type_for(candidate),
        difficulty=None,
        images=[],
        is_active=True,
    )


# Тип нагрузки по части тела источника. Источник сообщает часть тела, а не тип
# упражнения, поэтому отображение узкое: `cardio` действительно означает кардио.
_TYPE_BY_BODY_PART = {"cardio": "cardio"}

# Признаки растяжки и мобилизации в названии. Классификация по названию, а не по
# полю: поля такого у источника нет, а различие обязательно — прошлый этап
# намеренно вывел `stretching` из пула основной работы, потому что генератор
# составлял из растяжек тренировочные дни. Записать 57 растяжек источника как
# `strength` означало бы вернуть этот дефект.
_STRETCHING_MARKERS = ("stretch", "yoga pose", " pose", "mobilit")

_DEFAULT_EXERCISE_TYPE = "strength"


def _exercise_type_for(candidate: ExternalExerciseCandidate) -> str:
    body_part = (candidate.body_part or "").strip().lower()
    mapped = _TYPE_BY_BODY_PART.get(body_part)
    if mapped is not None:
        return mapped
    lowered = candidate.name.lower()
    if any(marker in lowered for marker in _STRETCHING_MARKERS):
        return "stretching"
    return _DEFAULT_EXERCISE_TYPE


def _apply_enrichment(exercise: Exercise, plan: MergePlan) -> Exercise:
    """Возвращает копию упражнения с применёнными полями плана.

    Синоним добавляется к фактическому списку записи, а не заменяет его: одно
    упражнение обогащают несколько внешних записей, и замена списка затирала бы
    синоним, добавленный предыдущей.
    """
    data = exercise.model_dump()
    for field_name, value in plan.fields.items():
        if field_name == FIELD_ALIASES:
            alias = str(value)
            existing = list(data.get("aliases") or [])
            if alias.strip().lower() not in {a.strip().lower() for a in existing}:
                existing.append(alias)
            data["aliases"] = existing
        elif field_name in (
            FIELD_TECHNIQUE,
            FIELD_TECHNIQUE_RU,
            FIELD_DESCRIPTION,
        ):
            data[field_name] = value
        elif field_name in (FIELD_PRIMARY_MUSCLES, FIELD_SECONDARY_MUSCLES):
            data[field_name] = list(value)  # type: ignore[arg-type]
    return Exercise(**data)


def _metrics_from_aggregate(aggregate) -> dict:
    return observation_metrics(aggregate)


def _cross_source_report(plan: IngestionPlan) -> dict:
    """Пересечение источников между собой и с canonical каталогом.

    Пересечение считается по ключам сопоставления, а не по названиям: иначе
    `Bench Press (Barbell)` и `Barbell Bench Press` выглядели бы разными
    упражнениями, и пересечение источников оказалось бы нулевым при полном
    совпадении содержания.

    Датасет программ участвует в пересечении, хотя кандидатом в каталог не
    является: вопрос «сколько упражнений каталога вообще встречается в чужих
    программах» относится к обоим источникам.
    """
    by_source = plan.name_keys_by_source
    matched_by_source: dict[str, set[str]] = {}
    for record in plan.records:
        if record.match.matched:
            key = record.candidate.name_key or record.candidate.name
            matched_by_source.setdefault(record.candidate.source_key, set()).add(key)

    source_keys = sorted(by_source)
    overlaps: dict[str, int] = {}
    for index, left in enumerate(source_keys):
        for right in source_keys[index + 1 :]:
            overlaps[f"{left} ∩ {right}"] = len(by_source[left] & by_source[right])

    return {
        "unique_name_keys_by_source": {k: len(v) for k, v in by_source.items()},
        "matched_canonical_by_source": {
            k: len(v) for k, v in matched_by_source.items()
        },
        "observations_matched_by_source": {
            report.source_key: report.decisions.get("matched", 0)
            for report in plan.reports.values()
            if report.kind == "program_dataset"
        },
        "source_overlaps": overlaps,
    }
