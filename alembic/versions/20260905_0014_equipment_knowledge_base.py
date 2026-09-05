"""Gym Knowledge Base: схема оборудования, требований и альтернатив

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-05

До этой миграции знание об оборудовании существовало в единственном виде:
JSON-массив строк в `exercises.equipment` со значениями источника каталога
(`barbell`, `machine`, `other`, ...). Из такого представления нельзя ответить ни
на один практический вопрос. «Machine» не различает жим ногами и блочную тягу.
`other` у 122 упражнений не означает ничего. У 77 упражнений список пуст, и это
одинаково читается и как «оборудование не нужно», и как «неизвестно». Наконец,
добавление нового тренажёра требовало правки Python-словаря
`EQUIPMENT_ALIASES` в фильтре: словарь оборудования жил в коде, а не в данных.

Схема раскладывает знание на уровни, каждый из которых отвечает на свой вопрос:

- `equipment_capabilities` — что объект умеет (наклонная опора, регулируемое
  сопротивление). Два тренажёра разных производителей называются по-разному, но
  функционально совпадают, и упражнению нужна возможность, а не бренд.
- `equipment_items` — что это за объект. Первичный ключ строковый: на
  оборудование ссылаются данные, миграции и админка, и ссылка `cable_machine`
  читается, а `17` — нет. Колонка `specializes` выражает отношение «частный
  случай родового»: `leg_press` специализирует `resistance_machine`. Оно
  необходимо, потому что источник каталога говорит родовыми словами — у 67
  упражнений оборудование указано как `machine`, — и без связи человек с жимом
  ногами получал бы «не подходит» на упражнение «жим ногами».
- `equipment_item_capabilities` — связь «умеет». Отдельной таблицей, потому что
  связь читается в обе стороны: «что умеет этот тренажёр» и «какое оборудование
  даёт наклон».
- `equipment_aliases` — синонимы: значения источника каталога и формулировки
  пользователя. Именно они переносят сопоставление из кода в данные.
- `exercise_equipment_requirements` — потребность упражнения с различением
  REQUIRED / OPTIONAL / ALTERNATIVE.
- `unmapped_equipment_values` — значения источника, которым не нашлось
  canonical ID. Существует, чтобы импорт не терял информацию молча.
- `exercise_alternatives` — альтернативы с явным типом замены.
- `equipment_profiles` / `equipment_profile_items` — что фактически доступно
  пользователю или залу.

Существующая колонка `exercises.equipment` НЕ удаляется. Она остаётся источником
для повторного импорта и единственным входом действующего фильтра
(`src/application/programs/filtering.py`), который эта миграция не меняет.
Удаление старого поля вместе с введением нового означало бы одновременную смену
формата и потребителя, и откат стал бы невозможен.

Миграция только создаёт объекты: ни одна существующая строка не изменяется и не
удаляется. Наполнение словаря и импорт значений каталога выполняет 0015 —
отдельно, потому что схема и данные откатываются независимо.

Ограничения выражены в БД, а не только в Pydantic:

- `ck_exercise_requirement_target` — заполнена ровно одна из ссылок
  (оборудование либо возможность). Требование «нужен блочный тренажёр» и
  требование «нужно регулируемое сопротивление» — разные утверждения.
- `ck_exercise_requirement_alternative_group` — у ALTERNATIVE обязательна
  группа: три независимые строки «одно из» неотличимы от одной группы из трёх
  вариантов.
- `ck_exercise_alternative_not_self` — упражнение не является собственной
  альтернативой.
- FK на `equipment_items` и `equipment_capabilities` с ON DELETE RESTRICT:
  удалить оборудование, на которое ссылаются требования упражнений, нельзя.
  Деактивация (`is_active = false`) существует именно для этого случая.

Ссылка на упражнение — каноническая пара (`external_id`, `source`), как в
`exercise_media`. Составного FK к каталогу нет по той же причине: он привязал бы
знание к жизненному циклу строки каталога, а пересоздание каталога — штатная
операция импорта. Целостность ссылок измеряется метрикой orphan-ссылок в
Knowledge Base Health, где она видна администратору, вместо падения импорта.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

_EXERCISE_SOURCE_DEFAULT = "leszavr/workout"


def _timestamps() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def upgrade() -> None:
    op.create_table(
        "equipment_capabilities",
        sa.Column("capability_id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("name_ru", sa.String(120), nullable=False),
        sa.Column("description", sa.String(300), nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        *_timestamps(),
    )
    op.create_index(
        "ix_equipment_capabilities_is_active", "equipment_capabilities", ["is_active"]
    )

    op.create_table(
        "equipment_items",
        sa.Column("equipment_id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("name_ru", sa.String(120), nullable=False),
        sa.Column("category", sa.String(64), nullable=False),
        sa.Column("description", sa.String(300), nullable=True),
        # Родовое оборудование, частным случаем которого является запись:
        # `leg_press` специализирует `resistance_machine`. Источник каталога
        # говорит родовыми словами (у 67 упражнений указано `machine`), и без
        # этой связи человек с жимом ногами получал бы «не подходит» на
        # упражнение «жим ногами».
        sa.Column("specializes", sa.String(64), nullable=True),
        sa.Column("manufacturer", sa.String(120), nullable=True),
        sa.Column("model_name", sa.String(120), nullable=True),
        sa.Column(
            "attributes", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("source", sa.String(32), nullable=False, server_default="seed"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["specializes"],
            ["equipment_items.equipment_id"],
            name="fk_equipment_item_specializes",
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_equipment_items_category", "equipment_items", ["category"])
    op.create_index("ix_equipment_items_is_active", "equipment_items", ["is_active"])
    op.create_index("ix_equipment_items_specializes", "equipment_items", ["specializes"])

    op.create_table(
        "equipment_item_capabilities",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("equipment_id", sa.String(64), nullable=False),
        sa.Column("capability_id", sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(
            ["equipment_id"],
            ["equipment_items.equipment_id"],
            name="fk_equipment_capability_item",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["capability_id"],
            ["equipment_capabilities.capability_id"],
            name="fk_equipment_capability_capability",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "equipment_id", "capability_id", name="uq_equipment_capability"
        ),
    )
    op.create_index(
        "ix_equipment_item_capabilities_equipment_id",
        "equipment_item_capabilities",
        ["equipment_id"],
    )
    op.create_index(
        "ix_equipment_item_capabilities_capability_id",
        "equipment_item_capabilities",
        ["capability_id"],
    )

    op.create_table(
        "equipment_aliases",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("equipment_id", sa.String(64), nullable=False),
        sa.Column("alias", sa.String(120), nullable=False),
        sa.Column("match_mode", sa.String(16), nullable=False, server_default="exact"),
        sa.Column("source", sa.String(32), nullable=False, server_default="seed"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["equipment_id"],
            ["equipment_items.equipment_id"],
            name="fk_equipment_alias_item",
            ondelete="CASCADE",
        ),
        # Уникальность по паре, а не по alias: «скамья» законно означает и
        # flat_bench, и adjustable_bench. Неоднозначность такого синонима должна
        # быть видна явно, а не разрешаться выбором первой строки.
        sa.UniqueConstraint("alias", "equipment_id", name="uq_equipment_alias_target"),
    )
    op.create_index("ix_equipment_aliases_alias", "equipment_aliases", ["alias"])
    op.create_index(
        "ix_equipment_aliases_equipment_id", "equipment_aliases", ["equipment_id"]
    )

    op.create_table(
        "exercise_equipment_requirements",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("exercise_external_id", sa.String(128), nullable=False),
        sa.Column(
            "exercise_source",
            sa.String(64),
            nullable=False,
            server_default=_EXERCISE_SOURCE_DEFAULT,
        ),
        sa.Column("equipment_id", sa.String(64), nullable=True),
        sa.Column("capability_id", sa.String(64), nullable=True),
        sa.Column(
            "requirement", sa.String(16), nullable=False, server_default="required"
        ),
        sa.Column("alternative_group", sa.Integer, nullable=True),
        sa.Column(
            "confidence", sa.String(16), nullable=False, server_default="confirmed"
        ),
        sa.Column(
            "source", sa.String(32), nullable=False, server_default="catalog_import"
        ),
        sa.Column("notes", sa.String(300), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["equipment_id"],
            ["equipment_items.equipment_id"],
            name="fk_exercise_requirement_equipment",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["capability_id"],
            ["equipment_capabilities.capability_id"],
            name="fk_exercise_requirement_capability",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "exercise_external_id",
            "exercise_source",
            "equipment_id",
            "capability_id",
            "requirement",
            name="uq_exercise_requirement",
        ),
        sa.CheckConstraint(
            "(equipment_id IS NULL) <> (capability_id IS NULL)",
            name="ck_exercise_requirement_target",
        ),
        sa.CheckConstraint(
            "requirement <> 'alternative' OR alternative_group IS NOT NULL",
            name="ck_exercise_requirement_alternative_group",
        ),
    )
    op.create_index(
        "ix_exercise_requirements_exercise",
        "exercise_equipment_requirements",
        ["exercise_external_id", "exercise_source"],
    )
    op.create_index(
        "ix_exercise_equipment_requirements_exercise_external_id",
        "exercise_equipment_requirements",
        ["exercise_external_id"],
    )
    op.create_index(
        "ix_exercise_equipment_requirements_equipment_id",
        "exercise_equipment_requirements",
        ["equipment_id"],
    )
    op.create_index(
        "ix_exercise_equipment_requirements_capability_id",
        "exercise_equipment_requirements",
        ["capability_id"],
    )
    op.create_index(
        "ix_exercise_equipment_requirements_requirement",
        "exercise_equipment_requirements",
        ["requirement"],
    )
    op.create_index(
        "ix_exercise_equipment_requirements_confidence",
        "exercise_equipment_requirements",
        ["confidence"],
    )

    op.create_table(
        "unmapped_equipment_values",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("exercise_external_id", sa.String(128), nullable=False),
        sa.Column(
            "exercise_source",
            sa.String(64),
            nullable=False,
            server_default=_EXERCISE_SOURCE_DEFAULT,
        ),
        sa.Column("raw_value", sa.String(120), nullable=False),
        sa.Column("reason", sa.String(16), nullable=False, server_default="unmapped"),
        sa.Column("notes", sa.String(300), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "exercise_external_id",
            "exercise_source",
            "raw_value",
            name="uq_unmapped_equipment_value",
        ),
    )
    op.create_index(
        "ix_unmapped_equipment_values_exercise_external_id",
        "unmapped_equipment_values",
        ["exercise_external_id"],
    )
    op.create_index(
        "ix_unmapped_equipment_values_raw_value",
        "unmapped_equipment_values",
        ["raw_value"],
    )
    op.create_index(
        "ix_unmapped_equipment_values_reason", "unmapped_equipment_values", ["reason"]
    )

    op.create_table(
        "exercise_alternatives",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("exercise_external_id", sa.String(128), nullable=False),
        sa.Column(
            "exercise_source",
            sa.String(64),
            nullable=False,
            server_default=_EXERCISE_SOURCE_DEFAULT,
        ),
        sa.Column("alternative_external_id", sa.String(128), nullable=False),
        sa.Column(
            "alternative_source",
            sa.String(64),
            nullable=False,
            server_default=_EXERCISE_SOURCE_DEFAULT,
        ),
        sa.Column(
            "substitution", sa.String(16), nullable=False, server_default="similar"
        ),
        sa.Column("score", sa.Float, nullable=False, server_default="0"),
        sa.Column(
            "rationale", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("source", sa.String(32), nullable=False, server_default="derived"),
        sa.Column("notes", sa.String(300), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint(
            "exercise_external_id",
            "exercise_source",
            "alternative_external_id",
            "alternative_source",
            name="uq_exercise_alternative_pair",
        ),
        sa.CheckConstraint(
            "exercise_external_id <> alternative_external_id "
            "OR exercise_source <> alternative_source",
            name="ck_exercise_alternative_not_self",
        ),
    )
    op.create_index(
        "ix_exercise_alternatives_exercise",
        "exercise_alternatives",
        ["exercise_external_id", "exercise_source"],
    )
    op.create_index(
        "ix_exercise_alternatives_exercise_external_id",
        "exercise_alternatives",
        ["exercise_external_id"],
    )
    op.create_index(
        "ix_exercise_alternatives_alternative_external_id",
        "exercise_alternatives",
        ["alternative_external_id"],
    )
    op.create_index(
        "ix_exercise_alternatives_substitution",
        "exercise_alternatives",
        ["substitution"],
    )

    op.create_table(
        "equipment_profiles",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("profile_key", sa.String(64), nullable=False),
        sa.Column("owner_type", sa.String(16), nullable=False, server_default="user"),
        sa.Column("owner_ref", sa.String(64), nullable=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column(
            "assume_unlisted_unavailable",
            sa.Boolean,
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("source", sa.String(32), nullable=False, server_default="admin"),
        sa.Column("notes", sa.String(300), nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        *_timestamps(),
        sa.UniqueConstraint("profile_key", name="uq_equipment_profile_key"),
    )
    op.create_index(
        "ix_equipment_profiles_owner", "equipment_profiles", ["owner_type", "owner_ref"]
    )
    op.create_index(
        "ix_equipment_profiles_owner_type", "equipment_profiles", ["owner_type"]
    )
    op.create_index(
        "ix_equipment_profiles_is_active", "equipment_profiles", ["is_active"]
    )

    op.create_table(
        "equipment_profile_items",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("profile_id", sa.Integer, nullable=False),
        sa.Column("equipment_id", sa.String(64), nullable=False),
        sa.Column("quantity", sa.Integer, nullable=True),
        sa.Column(
            "availability", sa.String(16), nullable=False, server_default="available"
        ),
        sa.Column(
            "confidence", sa.String(16), nullable=False, server_default="confirmed"
        ),
        sa.Column(
            "extra_capabilities",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("source", sa.String(32), nullable=False, server_default="admin"),
        sa.Column("source_ref", sa.String(300), nullable=True),
        sa.Column("notes", sa.String(300), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["equipment_profiles.id"],
            name="fk_equipment_profile_item_profile",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["equipment_id"],
            ["equipment_items.equipment_id"],
            name="fk_equipment_profile_item_equipment",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("profile_id", "equipment_id", name="uq_profile_equipment"),
    )
    op.create_index(
        "ix_equipment_profile_items_profile_id",
        "equipment_profile_items",
        ["profile_id"],
    )
    op.create_index(
        "ix_equipment_profile_items_equipment_id",
        "equipment_profile_items",
        ["equipment_id"],
    )


def downgrade() -> None:
    # Порядок обратный созданию: сначала таблицы со ссылками, потом словари.
    op.drop_table("equipment_profile_items")
    op.drop_table("equipment_profiles")
    op.drop_table("exercise_alternatives")
    op.drop_table("unmapped_equipment_values")
    op.drop_table("exercise_equipment_requirements")
    op.drop_table("equipment_aliases")
    op.drop_table("equipment_item_capabilities")
    op.drop_table("equipment_items")
    op.drop_table("equipment_capabilities")
