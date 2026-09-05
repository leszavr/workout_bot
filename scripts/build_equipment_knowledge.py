"""Пересчёт выводимого знания базы: требования по названию и альтернативы.

Скрипт, а не миграция, потому что результат зависит от текущего содержимого
словаря и пересчитывается при его пополнении. Миграция описывает единичный
переход схемы и данных, а это — воспроизводимая операция обслуживания.

Что делает скрипт:

1. Читает каталог упражнений и словарь оборудования.
2. Достраивает требования для упражнений, которым импорт 0016 не смог назначить
   оборудование: сопоставляет название упражнения со словарём синонимов
   (`Ab_Roller` → `ab_wheel`, `Sled_Push` → `weight_sled`).
3. Пересчитывает альтернативные упражнения по признакам каталога.
4. Печатает отчёт: mapped / inferred / ambiguous / unmapped и распределение
   типов замены.

Запуск:
    python -m scripts.build_equipment_knowledge [--dry-run] [--skip-alternatives]

`--dry-run` печатает отчёт, ничего не записывая: сопоставление проверяется до
изменения данных.

Идемпотентность: выводимые записи удаляются по источнику
(`name_inference`, `derived`) и создаются заново. Требования и альтернативы,
заведённые администратором вручную (`source=admin`) и импортированные из
каталога (`catalog_import`), не затрагиваются.
"""
from __future__ import annotations

import argparse
import asyncio

from src.application.equipment.alternatives import ExerciseAlternativesBuilder
from src.application.equipment.import_service import EquipmentKnowledgeImporter
from src.domain.equipment import KnowledgeSource
from src.infrastructure.persistence.postgres.db import (
    dispose_engine,
    get_session_factory,
)
from src.infrastructure.persistence.postgres.equipment_repository import (
    EquipmentRepository,
)
from src.infrastructure.persistence.postgres.exercise_knowledge_repository import (
    ExerciseKnowledgeRepository,
    ExerciseRef,
)
from src.infrastructure.persistence.postgres.exercise_repository import (
    ExerciseQuery,
    ExerciseRepository,
)

# Каталог заведомо меньше этого числа; читается целиком, потому что вывод
# альтернатив сравнивает упражнения между собой.
CATALOG_LIMIT = 5000

DERIVED_REQUIREMENT_SOURCES = [KnowledgeSource.NAME_INFERENCE.value]
DERIVED_ALTERNATIVE_SOURCES = [KnowledgeSource.DERIVED.value]


async def build(*, dry_run: bool, skip_alternatives: bool) -> dict:
    sessions = get_session_factory()
    exercises_repo = ExerciseRepository(sessions)
    equipment_repo = EquipmentRepository(sessions)
    knowledge_repo = ExerciseKnowledgeRepository(sessions)

    # Прежний результат этого же правила снимается до чтения состояния. Иначе
    # выведенные ранее требования считались бы «уже известными», упражнение
    # выпало бы из пересчёта, а последующее удаление по источнику стёрло бы их
    # окончательно: повторный запуск терял знание вместо его обновления.
    if not dry_run:
        await knowledge_repo.delete_requirements_by_source(DERIVED_REQUIREMENT_SOURCES)

    # is_active=None: знание строится и для деактивированных упражнений. Иначе
    # включение упражнения обратно оставляло бы его без требований.
    _, exercises = await exercises_repo.search(
        ExerciseQuery(is_active=None), limit=CATALOG_LIMIT
    )
    index = await equipment_repo.load_index()
    if not index.items:
        raise RuntimeError(
            "Словарь оборудования пуст: примените миграции (alembic upgrade head)"
        )

    refs = [ExerciseRef(e.external_id, e.source) for e in exercises]
    existing = await knowledge_repo.requirements_for(refs)
    if dry_run:
        # Отчёт должен показывать результат чистого пересчёта, а не состояние
        # после предыдущего запуска: иначе dry-run и реальный прогон расходятся.
        existing = {
            key: [
                r for r in value if r.source is not KnowledgeSource.NAME_INFERENCE
            ]
            for key, value in existing.items()
        }

    # Вывод по названию нужен только там, где импорт каталога ничего не дал:
    # значение источника сильнее догадки по названию.
    without_requirements = [
        e
        for e in exercises
        if not existing.get((e.external_id, e.source))
    ]
    importer = EquipmentKnowledgeImporter(index)
    plan = importer.build_plan(without_requirements)
    inferred = [
        r
        for r in plan.requirements
        if r.source is KnowledgeSource.NAME_INFERENCE
    ]

    stats: dict = {
        "exercises_total": len(exercises),
        "with_requirements_before": len(exercises) - len(without_requirements),
        "inferred_requirements": len(inferred),
        "still_unknown": plan.report.unknown_exercises,
        "unmapped_values": len(plan.unmapped),
        "report": plan.report.as_dict(),
    }

    if not dry_run:
        stats["inferred_written"] = await knowledge_repo.bulk_insert_requirements(
            inferred
        )
        # unmapped из этого прохода дополняют записанное миграцией: значение
        # каталога могло быть незакрытым, а название — тоже ничего не дать.
        stats["unmapped_written"] = await knowledge_repo.record_unmapped(plan.unmapped)

    if not skip_alternatives:
        # Альтернативы считаются после записи требований: они опираются на
        # набор оборудования упражнения.
        current = await knowledge_repo.requirements_for(refs)
        builder = ExerciseAlternativesBuilder(index)
        alternatives, report = builder.build(exercises, current)
        stats["alternatives"] = report.as_dict()
        if not dry_run:
            await knowledge_repo.delete_alternatives_by_source(
                DERIVED_ALTERNATIVE_SOURCES
            )
            stats["alternatives_written"] = (
                await knowledge_repo.bulk_insert_alternatives(alternatives)
            )

    stats["health"] = await knowledge_repo.health_counters()
    return stats


def _print_report(stats: dict) -> None:
    print("=== Отчёт построения базы знаний об оборудовании ===")
    print(f"Упражнений в каталоге:            {stats['exercises_total']}")
    print(f"С требованиями до запуска:        {stats['with_requirements_before']}")
    print(f"Выведено по названию:             {stats['inferred_requirements']}")
    if "inferred_written" in stats:
        print(f"  записано:                      {stats['inferred_written']}")
    print(f"Осталось без требований:          {stats['still_unknown']}")
    print(f"Незакрытых значений:              {stats['unmapped_values']}")

    report = stats["report"]
    if report["ambiguous_details"]:
        print("\nНеоднозначные значения:")
        for value, targets in report["ambiguous_details"].items():
            print(f"  {value}: {', '.join(targets)}")
    if report["unmapped_details"]:
        print("\nЗначения без сопоставления:")
        for value, count in report["unmapped_details"].items():
            print(f"  {value}: {count}")

    if "alternatives" in stats:
        alternatives = stats["alternatives"]
        print("\nАльтернативы:")
        print(f"  упражнений с альтернативами:   {alternatives['exercises_with_alternatives']}")
        print(f"  всего связей:                  {alternatives['alternatives_total']}")
        for substitution, count in alternatives["by_substitution"].items():
            print(f"    {substitution}: {count}")
        if "alternatives_written" in stats:
            print(f"  записано:                      {stats['alternatives_written']}")

    health = stats["health"]
    print("\nKnowledge Base Health:")
    print(f"  оборудование известно:         {health['equipment_known']}")
    print(f"  оборудование неизвестно:       {health['equipment_unknown']}")
    print(f"  подтверждено:                  {health['equipment_confirmed']}")
    print(f"  выведено:                      {health['equipment_inferred']}")
    print(f"  orphan-ссылок:                 {health['orphan_equipment_references']}")
    print(
        "  невозможных комбинаций:        "
        f"{health['impossible_requirement_combinations']}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Пересчёт выводимого знания базы оборудования"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Только отчёт, без записи в базу",
    )
    parser.add_argument(
        "--skip-alternatives",
        action="store_true",
        help="Не пересчитывать альтернативные упражнения",
    )
    args = parser.parse_args()

    async def _run() -> dict:
        try:
            return await build(
                dry_run=args.dry_run, skip_alternatives=args.skip_alternatives
            )
        finally:
            await dispose_engine()

    _print_report(asyncio.run(_run()))


if __name__ == "__main__":
    main()
