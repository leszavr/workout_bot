"""Синонимы и оборудование внешних источников упражнений

Revision ID: 0018
Revises: 0017
Create Date: 2026-09-05

Значения оборудования источника `hasaneyldrm/exercises-dataset` отличаются от
значений действующего каталога: там, где `leszavr/workout` пишет `body only`,
внешний источник пишет `body weight`; там, где у нас `machine`, у него
`leverage machine`. Словарь оборудования при этом не создаётся заново — он
пополняется, как и предусмотрено прошлым этапом: новое оборудование это строка в
`equipment_items` плюс строки в `equipment_aliases`, а не правка Python-кода.

Что делает миграция:

1. Добавляет три единицы оборудования, которых в словаре не было: `bosu_ball`,
   `upper_body_ergometer`, `ski_ergometer`. Все три встречаются во внешнем
   каталоге и ни одна не выражается существующей записью: bosu-платформа
   отличается от фитбола характером опоры, а ручной и лыжный эргометры — от
   дорожки и велотренажёра типом движения.
2. Добавляет `exact`-синонимы для значений внешнего источника, у которых есть
   ровно один canonical эквивалент.

Чего миграция сознательно НЕ делает.

`weighted`, `assisted`, `rope`, `roller` и `hammer` синонимов не получают.
Причина та же, по которой `other` не получил синоним в 0015: это не названия
оборудования, а указание на способ выполнения либо родовое слово, за которым
стоят разные снаряды. `weighted` означает «с дополнительным весом» — это может
быть пояс, диск или гантель; `assisted` — «с облегчением», то есть гравитрон
либо резина; `rope` в источнике объединяет канаты для раскачивания, канат для
лазания, скакалку и рукоять-верёвку блока; `roller` — и валик, и ролик для
пресса; `hammer` — и кувалда, и хват «молот» (в каталоге есть `Hammer Curls` с
гантелями). Сопоставить их одним ID нельзя, а выбрать «наиболее вероятный»
значило бы записать догадку как факт. Такие значения остаются незакрытыми и
видны в отчёте ingestion, где их закрывает человек.

`sled machine` сопоставляется с `resistance_machine`, а не с `leg_press`,
несмотря на то что все 15 упражнений источника с этим значением — вариации жима
ногами и гакк-приседа. Причина в семантике родового: требование
`resistance_machine` закрывается любой его специализацией, включая `leg_press` и
`hack_squat`, а обратное неверно. Родовое требование здесь точнее: источник не
различает, на каком именно тренажёре выполняется движение, и назначить `leg_press`
означало бы объявить упражнение невыполнимым в зале, где есть гакк-машина, но нет
жима ногами.

`stepmill machine` сопоставляется с `stair_climber`: степмилл и есть
лестничный тренажёр, разница в конструкции привода, а не в движении.

Идемпотентность: все вставки идут через `ON CONFLICT DO NOTHING`. Повторный
`upgrade` ничего не дублирует и не перезаписывает правки администратора.

Downgrade удаляет только собственные вставки и только если на строку никто не
ссылается: оборудование, на которое уже сослались требования упражнений,
остаётся, потому что удаление сделало бы требование невыполнимым молча.
"""
from alembic import op
import sqlalchemy as sa

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None

SEED_SOURCE = "seed"

# (equipment_id, name, name_ru, category, specializes, capabilities, aliases)
NEW_EQUIPMENT: tuple[
    tuple[
        str,
        str,
        str,
        str,
        str | None,
        tuple[str, ...],
        tuple[tuple[str, str], ...],
    ],
    ...,
] = (
    (
        "bosu_ball",
        "BOSU balance trainer",
        "BOSU-платформа",
        "ball",
        None,
        ("unstable_support", "ground_support"),
        (("bosu ball", "exact"), ("bosu", "exact"), ("босу", "stem")),
    ),
    (
        "upper_body_ergometer",
        "Upper body ergometer",
        "Ручной эргометр",
        "cardio",
        "cardio_machine",
        ("continuous_cardio", "adjustable_resistance"),
        (
            ("upper body ergometer", "exact"),
            ("hands bike", "exact"),
            ("ручной эргометр", "stem"),
        ),
    ),
    (
        "ski_ergometer",
        "Ski ergometer",
        "Лыжный эргометр",
        "cardio",
        "cardio_machine",
        ("continuous_cardio", "adjustable_resistance"),
        (
            ("skierg machine", "exact"),
            ("ski ergometer", "exact"),
            ("лыжный эргометр", "stem"),
        ),
    ),
)

# Синонимы значений внешнего источника для уже существующего оборудования.
# (alias, match_mode, equipment_id)
EXTERNAL_ALIASES: tuple[tuple[str, str, str], ...] = (
    ("body weight", "exact", "bodyweight"),
    ("leverage machine", "exact", "resistance_machine"),
    ("lever machine", "exact", "resistance_machine"),
    # `lever` и `leverage` как отдельные слова: внешний каталог называет так
    # рычажные тренажёры («lever seated row», «leverage chest press»), и без
    # синонима такое упражнение выглядит выполняемым тем оборудованием, которое
    # указано полем — а поле у этих записей говорит `barbell`.
    ("lever", "exact", "resistance_machine"),
    ("leverage", "exact", "resistance_machine"),
    ("sled machine", "exact", "resistance_machine"),
    ("stability ball", "exact", "exercise_ball"),
    ("ez barbell", "exact", "ez_curl_bar"),
    ("resistance band", "exact", "resistance_band"),
    ("wheel roller", "exact", "ab_wheel"),
    ("ab wheel", "exact", "ab_wheel"),
    ("elliptical machine", "exact", "elliptical"),
    ("stepmill machine", "exact", "stair_climber"),
    ("sledge hammer", "exact", "sledgehammer"),
    ("weight plate", "exact", "weight_plate"),
    ("dip cage", "exact", "dip_station"),
)


def _normalize(value: str) -> str:
    return " ".join(value.strip().lower().replace("ё", "е").split())


def upgrade() -> None:
    connection = op.get_bind()

    for (
        equipment_id,
        name,
        name_ru,
        category,
        specializes,
        capabilities,
        aliases,
    ) in NEW_EQUIPMENT:
        connection.execute(
            sa.text(
                """
                INSERT INTO equipment_items
                    (equipment_id, name, name_ru, category, specializes, source,
                     is_active)
                VALUES (:equipment_id, :name, :name_ru, :category, :specializes,
                        :source, true)
                ON CONFLICT (equipment_id) DO NOTHING
                """
            ),
            {
                "equipment_id": equipment_id,
                "name": name,
                "name_ru": name_ru,
                "category": category,
                "specializes": specializes,
                "source": SEED_SOURCE,
            },
        )
        for capability_id in capabilities:
            connection.execute(
                sa.text(
                    """
                    INSERT INTO equipment_item_capabilities
                        (equipment_id, capability_id)
                    VALUES (:equipment_id, :capability_id)
                    ON CONFLICT (equipment_id, capability_id) DO NOTHING
                    """
                ),
                {"equipment_id": equipment_id, "capability_id": capability_id},
            )
        for alias, match_mode in aliases:
            connection.execute(
                sa.text(
                    """
                    INSERT INTO equipment_aliases
                        (equipment_id, alias, match_mode, source)
                    VALUES (:equipment_id, :alias, :match_mode, :source)
                    ON CONFLICT (alias, equipment_id) DO NOTHING
                    """
                ),
                {
                    "equipment_id": equipment_id,
                    "alias": _normalize(alias),
                    "match_mode": match_mode,
                    "source": SEED_SOURCE,
                },
            )

    for alias, match_mode, equipment_id in EXTERNAL_ALIASES:
        # Существование оборудования проверяется отдельным запросом, а не
        # подзапросом в INSERT: один и тот же параметр в списке значений и в
        # WHERE даёт asyncpg разные выводимые типы (text против varchar), и
        # запрос падает до сервера.
        exists = connection.execute(
            sa.text(
                "SELECT 1 FROM equipment_items WHERE equipment_id = :equipment_id"
            ),
            {"equipment_id": equipment_id},
        ).scalar()
        if not exists:
            continue
        connection.execute(
            sa.text(
                """
                INSERT INTO equipment_aliases
                    (equipment_id, alias, match_mode, source)
                VALUES (:equipment_id, :alias, :match_mode, :source)
                ON CONFLICT (alias, equipment_id) DO NOTHING
                """
            ),
            {
                "equipment_id": equipment_id,
                "alias": _normalize(alias),
                "match_mode": match_mode,
                "source": SEED_SOURCE,
            },
        )


def downgrade() -> None:
    connection = op.get_bind()

    for alias, _match_mode, equipment_id in EXTERNAL_ALIASES:
        connection.execute(
            sa.text(
                """
                DELETE FROM equipment_aliases
                WHERE alias = :alias
                  AND equipment_id = :equipment_id
                  AND source = :source
                """
            ),
            {
                "alias": _normalize(alias),
                "equipment_id": equipment_id,
                "source": SEED_SOURCE,
            },
        )

    for equipment_id, *_rest in NEW_EQUIPMENT:
        # Оборудование, на которое уже ссылаются требования или профили, не
        # удаляется: удаление сделало бы требование невыполнимым молча.
        connection.execute(
            sa.text(
                """
                DELETE FROM equipment_aliases
                WHERE equipment_id = :equipment_id
                  AND source = :source
                  AND NOT EXISTS (
                      SELECT 1 FROM exercise_equipment_requirements
                      WHERE equipment_id = :equipment_id
                  )
                """
            ),
            {"equipment_id": equipment_id, "source": SEED_SOURCE},
        )
        connection.execute(
            sa.text(
                """
                DELETE FROM equipment_item_capabilities
                WHERE equipment_id = :equipment_id
                  AND NOT EXISTS (
                      SELECT 1 FROM exercise_equipment_requirements
                      WHERE equipment_id = :equipment_id
                  )
                """
            ),
            {"equipment_id": equipment_id},
        )
        connection.execute(
            sa.text(
                """
                DELETE FROM equipment_items
                WHERE equipment_id = :equipment_id
                  AND source = :source
                  AND NOT EXISTS (
                      SELECT 1 FROM exercise_equipment_requirements
                      WHERE equipment_id = :equipment_id
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM equipment_profile_items
                      WHERE equipment_id = :equipment_id
                  )
                """
            ),
            {"equipment_id": equipment_id, "source": SEED_SOURCE},
        )
