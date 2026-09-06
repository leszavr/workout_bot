"""Импорт знаний об упражнениях из внешних источников.

Скрипт, а не миграция: результат зависит от содержимого локальных копий
источников и от текущего состояния caталога, и пересчитывается при их изменении.
Миграция описывает единичный переход схемы, а это — воспроизводимая операция
обслуживания данных.

Порядок работы жёсткий и следует требованию этапа: сначала полный dry-run по обоим
источникам с реальными числами, затем — применение того же плана.

    # 1. Отчёт без изменения caталога
    python -m scripts.ingest_external_exercises \
        --github /path/to/exercises-dataset \
        --kaggle /path/to/kaggle-csv-dir \
        --dry-run --report /tmp/ingestion_dry_run.json

    # 2. Применение
    python -m scripts.ingest_external_exercises \
        --github /path/to/exercises-dataset \
        --kaggle /path/to/kaggle-csv-dir \
        --import-media --report /tmp/ingestion_applied.json

    # 3. Пересчёт знания об оборудовании для добавленных упражнений
    python -m scripts.build_equipment_knowledge

Шаг 3 обязателен и не выполняется здесь: знание об оборудовании строится
существующим `EquipmentKnowledgeImporter` по всему каталогу, и вызывать его
внутри ingestion значило бы иметь два места, откуда оно перестраивается.

Идемпотентность. Повторный запуск с теми же источниками не создаёт упражнений:
связь `exercise_source_links`, записанная предыдущим запуском, читается
сопоставлением как самый сильный признак соответствия, и запись получает решение
`existing` вместо `new_relevant`. Проверяется тестом
`tests/integration/test_external_ingestion.py::test_second_run_is_idempotent`.

Версия источника. Для GitHub-каталога берётся commit SHA локального клона; если
копия развёрнута из архива без `.git`, версию нужно передать аргументом
`--github-version`. Для Kaggle стабильного номера версии нет, поэтому версия —
хеш содержимого CSV; при необходимости её можно задать `--kaggle-version`.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from src.application.ingestion.media_importer import ExternalMediaImporter
from src.application.ingestion.service import ExternalIngestionService
from src.application.ingestion.sources import (
    read_github_dataset,
    read_kaggle_dataset,
)
from src.infrastructure.media.object_storage import create_object_storage
from src.infrastructure.persistence.postgres.db import (
    dispose_engine,
    get_session_factory,
)
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


async def run(args: argparse.Namespace) -> dict:
    sessions = get_session_factory()
    exercises = ExerciseRepository(sessions)
    equipment = EquipmentRepository(sessions)
    ingestion_repository = IngestionRepository(sessions)
    media_repository = ExerciseMediaRepository(sessions)

    service = ExternalIngestionService(
        exercises=exercises,
        equipment_repository=equipment,
        ingestion_repository=ingestion_repository,
        media_repository=media_repository,
    )

    catalog_reads = []
    github_root: Path | None = None
    if args.github:
        github_root = Path(args.github)
        catalog_reads.append(
            read_github_dataset(github_root, version=args.github_version)
        )

    program_reads = []
    program_aggregates: dict = {}
    if args.kaggle:
        read, aggregates = read_kaggle_dataset(
            Path(args.kaggle),
            version=args.kaggle_version,
            row_limit=args.kaggle_row_limit,
        )
        program_reads.append(read)
        program_aggregates = aggregates

    if not catalog_reads and not program_reads:
        raise SystemExit("Не указан ни один источник: используйте --github/--kaggle")

    plan = await service.plan(
        catalog_reads=catalog_reads,
        program_reads=program_reads,
        program_aggregates=program_aggregates,
    )

    output: dict = {
        "mode": "dry_run" if args.dry_run else "applied",
        "summary": plan.summary(),
        "sources": {key: report.as_dict() for key, report in plan.reports.items()},
        "cross_source": plan.cross_source,
        "samples": _samples(plan, args.samples),
    }

    if args.dry_run:
        output["ingestion_health"] = await ingestion_repository.health_counters()
        return output

    result = await service.apply(plan)
    output["applied"] = result.as_dict()

    if args.import_media and github_root is not None:
        importer = ExternalMediaImporter(
            media_repository=media_repository,
            storage=create_object_storage(),
            source_root=github_root,
        )
        media_stats = await importer.import_for(service.pending_media)
        output["media"] = media_stats.as_dict()
    elif service.pending_media:
        output["media"] = {
            "skipped": sum(len(c.media) for _, _, c in service.pending_media),
            "reason": "--import-media не указан",
        }

    output["ingestion_health"] = await ingestion_repository.health_counters()
    return output


def _samples(plan, limit: int) -> dict:
    """Примеры решений по каждой категории.

    Нужны отчёту: числа без примеров невозможно проверить, а «120 новых
    упражнений» без списка не отличается от «120 случайных строк».
    """
    from src.domain.ingestion import IngestionDecision

    samples: dict[str, list] = {}
    for decision in IngestionDecision:
        records = plan.by_decision(decision)[:limit]
        if not records:
            continue
        samples[decision.value] = [
            {
                "source": record.candidate.source_key,
                "record_id": record.candidate.source_record_id,
                "name": record.candidate.name,
                "quality": record.quality.status.value,
                "quality_score": record.quality.score,
                "confidence": record.match.confidence,
                "matched": record.match.external_id,
                "reasons": record.match.reasons[:5],
                "note": record.note,
            }
            for record in records
        ]
    return samples


def _print_report(output: dict) -> None:
    summary = output["summary"]
    print("=== Отчёт ingestion внешних источников ===")
    print(f"Режим:                            {output['mode']}")
    print(f"Canonical упражнений до:          {summary['canonical_before']}")
    print(f"Внешних кандидатов:               {summary['external_candidates']}")
    for decision, count in summary["decisions"].items():
        print(f"  {decision:20s} {count}")
    print(f"Ожидается новых упражнений:       {summary['expected_new_exercises']}")
    print(f"Ожидается обогащено:              {summary['expected_enriched']}")
    print(f"Canonical после (ожидание):       {summary['expected_canonical_after']}")
    print(
        "Программных наблюдений:           "
        f"{summary['program_observations']} "
        f"(без соответствия: {summary['program_observations_unmatched']})"
    )
    if summary.get("orphan_source_links"):
        print(
            "Связей на удалённые упражнения:   "
            f"{summary['orphan_source_links']} (записи будут импортированы заново)"
        )

    for source_key, report in output["sources"].items():
        print(f"\n--- {source_key} ({report['kind']}, версия {report['version']}) ---")
        print(f"  записей:                       {report['records_total']}")
        print(f"  с техникой:                    {report['with_technique']}")
        print(f"  с русской техникой:            {report['with_technique_ru']}")
        print(f"  с медиа:                       {report['with_media']}")
        print(f"  оборудование сопоставлено:     {report['with_equipment_mapped']}")
        print(f"  целевая мышца сопоставлена:    {report['with_primary_muscle']}")
        if report["unmapped_equipment"]:
            print("  незакрытое оборудование:")
            for value, count in list(report["unmapped_equipment"].items())[:10]:
                print(f"    {value}: {count}")
        if report["ambiguous_muscles"]:
            print("  неоднозначные мышцы:")
            for value, count in list(report["ambiguous_muscles"].items())[:10]:
                print(f"    {value}: {count}")
        if report["unmapped_muscles"]:
            print("  незакрытые мышцы:")
            for value, count in list(report["unmapped_muscles"].items())[:10]:
                print(f"    {value}: {count}")

    cross = output.get("cross_source") or {}
    if cross:
        print("\n--- Пересечение источников ---")
        for key, value in (cross.get("unique_name_keys_by_source") or {}).items():
            print(f"  {key}: уникальных ключей {value}")
        for key, value in (cross.get("source_overlaps") or {}).items():
            print(f"  {key}: {value}")
        for key, value in (cross.get("matched_canonical_by_source") or {}).items():
            print(f"  {key} ∩ canonical (упражнения): {value}")
        for key, value in (
            cross.get("observations_matched_by_source") or {}
        ).items():
            print(f"  {key} ∩ canonical (наблюдения): {value}")

    applied = output.get("applied")
    if applied:
        print("\n--- Применено ---")
        print(f"  создано упражнений:            {applied['created']}")
        print(f"  обогащено упражнений:          {applied['enriched']}")
        print(f"  пропущено записей:             {applied['skipped']}")
        print(f"  связей источников:             {applied['links_written']}")
        print(f"  provenance полей:              {applied['provenance_written']}")
        print(f"  программных наблюдений:        {applied['observations_written']}")
        print(f"  canonical после:               {applied['canonical_after']}")
        if applied["field_changes"]:
            print("  изменённые поля:")
            for field_name, count in applied["field_changes"].items():
                print(f"    {field_name}: {count}")

    media = output.get("media")
    if media:
        print("\n--- Медиа ---")
        for key, value in media.items():
            print(f"  {key}: {value}")

    health = output.get("ingestion_health")
    if health:
        print("\n--- Состояние ingestion ---")
        for key, value in health.items():
            print(f"  {key}: {value}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Импорт знаний об упражнениях из внешних источников"
    )
    parser.add_argument(
        "--github",
        default=None,
        help="Путь к локальной копии hasaneyldrm/exercises-dataset",
    )
    parser.add_argument(
        "--github-version",
        default=None,
        help="Версия источника (commit SHA), если копия без .git",
    )
    parser.add_argument(
        "--kaggle",
        default=None,
        help="Путь к каталогу с CSV датасета программ Kaggle",
    )
    parser.add_argument(
        "--kaggle-version",
        default=None,
        help="Версия датасета Kaggle (по умолчанию — хеш содержимого)",
    )
    parser.add_argument(
        "--kaggle-row-limit",
        type=int,
        default=None,
        help="Ограничение числа строк Kaggle (для проверки на подмножестве)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Только отчёт: canonical каталог не изменяется",
    )
    parser.add_argument(
        "--import-media",
        action="store_true",
        help="Загрузить медиа внешних записей в объектное хранилище",
    )
    parser.add_argument(
        "--report",
        default=None,
        help="Записать полный отчёт в JSON-файл",
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=10,
        help="Сколько примеров показывать по каждой категории решения",
    )
    args = parser.parse_args()

    async def _run() -> dict:
        try:
            return await run(args)
        finally:
            await dispose_engine()

    output = asyncio.run(_run())
    _print_report(output)
    if args.report:
        Path(args.report).write_text(
            json.dumps(output, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        print(f"\nПолный отчёт: {args.report}")


if __name__ == "__main__":
    main()
