"""Приведение оборудования внешних записей к словарю поля `exercises.equipment`.

Поле `exercises.equipment` — не свободный список: это вход действующего
детерминированного фильтра (`src/application/programs/filtering.py`), у которого
словарь значений задан кодом (`CATALOG_EQUIPMENT`, 12 значений). Прошлый этап
намеренно оставил и поле, и фильтр без изменений, потому что нормализованное
знание живёт отдельно, в `exercise_equipment_requirements`.

Отсюда требование к ingestion: значения в поле должны остаться в одном словаре.
Если записать туда формулировки внешнего источника (`body weight`,
`leverage machine`, `stability ball`), фильтр не узнает ни одну из них и
исключит упражнение с причиной «нет оборудования» — то есть тысяча
импортированных упражнений не попадёт ни в одну программу, и импорт окажется
бессмысленным для генерации.

Поэтому оборудование внешней записи проходит два шага:

1. формулировка источника → canonical ID словаря оборудования. Это делают данные:
   `equipment_aliases`, пополненные миграцией 0018. Кода здесь нет;
2. canonical ID → значение словаря поля. Это делает таблица ниже.

Второй шаг — код, а не данные, сознательно: словарь поля задан кодом фильтра, и
держать отображение в базе значило бы разнести два конца одного соответствия по
разным местам. Таблица исчезнет вместе с полем, когда фильтр начнёт читать
нормализованные требования напрямую (рекомендация следующего этапа).

Потеря точности здесь есть, и она названа: `smith_machine`, `leg_press` и
`treadmill` дают одно значение `machine`, потому что более точного в словаре поля
нет. Точное знание при этом не теряется: оно восстанавливается из того же
canonical ID при построении требований (`scripts/build_equipment_knowledge.py`),
а исходная формулировка источника сохраняется в staging-записи и в provenance.

`other` означает «оборудование нужно, но какое — словарь поля не выражает». Это
тот же смысл, что у 122 упражнений действующего каталога, и фильтр уже трактует
его отдельно от «оборудование не нужно».
"""
from __future__ import annotations

# Значения словаря поля `exercises.equipment` (см. filtering.py).
TAG_BANDS = "bands"
TAG_BARBELL = "barbell"
TAG_BODY_ONLY = "body only"
TAG_CABLE = "cable"
TAG_DUMBBELL = "dumbbell"
TAG_EZ_CURL_BAR = "e-z curl bar"
TAG_EXERCISE_BALL = "exercise ball"
TAG_FOAM_ROLL = "foam roll"
TAG_KETTLEBELLS = "kettlebells"
TAG_MACHINE = "machine"
TAG_MEDICINE_BALL = "medicine ball"
TAG_OTHER = "other"

# canonical equipment_id → значение словаря поля.
# Перечислено только то, что действительно выражается словарём поля; остальное
# получает `other` и остаётся точным в требованиях.
CANONICAL_TO_FIELD_TAG: dict[str, str] = {
    # Свободный вес
    "bodyweight": TAG_BODY_ONLY,
    "barbell": TAG_BARBELL,
    "trap_bar": TAG_BARBELL,
    "axle_bar": TAG_BARBELL,
    "log_bar": TAG_BARBELL,
    "ez_curl_bar": TAG_EZ_CURL_BAR,
    "dumbbell": TAG_DUMBBELL,
    "kettlebell": TAG_KETTLEBELLS,
    # Блок
    "cable_machine": TAG_CABLE,
    "lat_pulldown": TAG_CABLE,
    "seated_row_machine": TAG_CABLE,
    # Тренажёры: словарь поля не различает силовые и кардио, и `machine` — то же
    # значение, которым действующий каталог описывает «Elliptical Trainer».
    "resistance_machine": TAG_MACHINE,
    "smith_machine": TAG_MACHINE,
    "chest_press_machine": TAG_MACHINE,
    "shoulder_press_machine": TAG_MACHINE,
    "pec_deck": TAG_MACHINE,
    "leg_press": TAG_MACHINE,
    "hack_squat": TAG_MACHINE,
    "leg_extension": TAG_MACHINE,
    "leg_curl": TAG_MACHINE,
    "hip_abduction_machine": TAG_MACHINE,
    "hip_adduction_machine": TAG_MACHINE,
    "calf_machine": TAG_MACHINE,
    "cardio_machine": TAG_MACHINE,
    "treadmill": TAG_MACHINE,
    "stationary_bike": TAG_MACHINE,
    "elliptical": TAG_MACHINE,
    "rowing_machine": TAG_MACHINE,
    "stair_climber": TAG_MACHINE,
    "upper_body_ergometer": TAG_MACHINE,
    "ski_ergometer": TAG_MACHINE,
    # Резина и мячи
    "resistance_band": TAG_BANDS,
    "medicine_ball": TAG_MEDICINE_BALL,
    "exercise_ball": TAG_EXERCISE_BALL,
    "foam_roller": TAG_FOAM_ROLL,
}


def field_tags(
    canonical_ids: frozenset[str], *, has_unmapped_values: bool
) -> list[str]:
    """Значения для `exercises.equipment` по canonical оборудованию записи.

    ``has_unmapped_values`` — были ли у записи формулировки оборудования, которым
    canonical ID не нашёлся (`weighted`, `assisted`, `rope`). Такие формулировки
    означают «оборудование нужно, но какое — источник не уточняет», и обязаны
    давать `other`, а не исчезать: иначе упражнение выглядело бы бесснарядным.
    """
    tags: list[str] = []
    unresolved = False
    for equipment_id in sorted(canonical_ids):
        tag = CANONICAL_TO_FIELD_TAG.get(equipment_id)
        if tag is None:
            unresolved = True
            continue
        if tag not in tags:
            tags.append(tag)
    if (unresolved or has_unmapped_values) and TAG_OTHER not in tags:
        tags.append(TAG_OTHER)
    return tags
