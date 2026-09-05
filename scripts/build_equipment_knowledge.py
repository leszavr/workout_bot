"""Пересчёт знания об оборудовании из каталога упражнений.

Скрипт, а не миграция, потому что результат зависит от текущего содержимого
каталога и словаря и пересчитывается при их изменении. Миграция описывает
единичный переход для уже существующих данных, а это — воспроизводимая операция
обслуживания.

Почему одной миграции недостаточно. Миграция `0016` сопоставляет значения
каталога с словарём в тот момент, когда её применяют. В чистом окружении порядок
обратный: сначала `alembic upgrade head`, потом импорт каталога — и миграция
работает по пустой таблице `exercises`. Поэтому сопоставление обязано быть
повторяемой операцией: этот скрипт выполняет полный пересчёт по тем же правилам
и запускается после импорта каталога.

Что делает скрипт:

1. Читает каталог упражнений и словарь оборудования.
2. Сопоставляет значения `exercises.equipment` с canonical ID словаря —
   подтверждённые требования (`catalog_import`).
3. Для упражнений, которым значение каталога ничего не дало, выводит требования
   из названия (`name_inference`).
4. Записывает значения без canonical ID в `unmapped_equipment_values`, чтобы
   пробел данных остался видимым.
5. Пересчитывает альтернативные упражнения по признакам каталога.
6. Печатает отчёт: mapped / inferred / ambiguous / unmapped и распределение
   типов замены.

Запуск:
    python -m scripts.build_equipment_knowledge [--dry-run] [--skip-alternatives]

`--dry-run` печатает отчёт, ничего не записывая: сопоставление проверяется до
изменения данных.

Идемпотентность: выводимые записи удаляются по источнику
(`catalog_import`, `name_inference`, `derived`) и создаются заново. Требования и
альтернативы, заведённые администратором вручную (`source=admin`), не
затрагиваются.
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

# Источники, которые скрипт пересоздаёт целиком. `admin` в список не входит:
# правки администратора сильнее любого правила.
DERIVED_REQUIREMENT_SOURCES = [
    KnowledgeSource.CATALOG_IMPORT.value,
    KnowledgeSource.NAME_INFERENCE.value,
]
DERIVED_ALTERNATIVE_SOURCES = [KnowledgeSource.DERIVED.value]


async def build(*, dry_run: bool, skip_alternatives: bool) -> dict:
    sessions = get_session_factory()
    exercises_repo = ExerciseRepository(sessions)
    equipment_repo = EquipmentRepository(sessions)
    knowledge_repo = ExerciseKnowledgeRepository(sessions)

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

    # Требования администратора читаются до пересчёта: упражнение, у которого они
    # есть, правилам не отдаётся — иначе правило переопределяло бы решение
    # человека.
    existing = await knowledge_repo.requirements_for(refs)
    admin_owned = {
        key
        for key, values in existing.items()
        if any(r.source is KnowledgeSource.ADMIN for r in values)
    }
    subject = [
        e for e in exercises if (e.external_id, e.source) not in admin_owned
    ]

    importer = EquipmentKnowledgeImporter(index)
    plan = importer.build_plan(subject)

    stats: dict = {
        "exercises_total": len(exercises),
        "admin_owned": len(admin_owned),
        "requirements_confirmed": plan.report.requirements_confirmed,
        "requirements_inferred": plan.report.requirements_inferred,
        "mapped_exercises": plan.report.mapped_exercises,
        "inferred_exercises": plan.report.inferred_exercises,
        "still_unknown": plan.report.unknown_exercises,
        "unmapped_values": len(plan.unmapped),
        "report": plan.report.as_dict(),
    }

    if not dry_run:
        # Прежний результат этих же правил снимается перед записью: иначе
        # переименованное или удалённое из словаря оборудование осталось бы в
        # требованиях навсегда.
        await knowledge_repo.delete_requirements_by_source(DERIVED_REQUIREMENT_SOURCES)
        await knowledge_repo.clear_unmapped()
        stats["requirements_written"] = await knowledge_repo.bulk_insert_requirements(
            plan.requirements
        )
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
    print(f"Требования задал администратор:   {stats['admin_owned']}")
    print(f"Сопоставлено из каталога:         {stats['mapped_exercises']}")
    print(f"Выведено по названию:             {stats['inferred_exercises']}")
    print(f"Осталось без требований:          {stats['still_unknown']}")
    print(
        "Строк требований:                 "
        f"подтверждённых {stats['requirements_confirmed']}, "
        f"выведенных {stats['requirements_inferred']}"
    )
    if "requirements_written" in stats:
        print(f"  записано:                      {stats['requirements_written']}")
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
        description="Пересчёт знания об оборудовании из каталога упражнений"
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
