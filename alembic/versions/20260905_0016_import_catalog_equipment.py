"""Импорт значений оборудования каталога в нормализованные требования

Revision ID: 0016
Revises: 0015
Create Date: 2026-09-05

Миграция переводит существующие значения `exercises.equipment` в строки
`exercise_equipment_requirements`, используя словарь синонимов из 0015.

Старое поле НЕ удаляется и НЕ изменяется. Оно остаётся входом для повторного
сопоставления: после пополнения словаря импорт можно пересчитать, и результат
воспроизводим. Удалить его вместе с переходом на новую модель значило бы
одновременно сменить формат и потребителя, лишившись возможности откатиться.

Сопоставление здесь выполняется на SQL и только по полному совпадению синонима.
Это сознательное ограничение объёма: значения каталога — контролируемый набор из
12 строк, и каждая либо имеет ровно один canonical ID, либо не имеет ни одного.
Эвристики (вывод оборудования из названия упражнения, вывод альтернатив) в
миграцию не входят — они живут в `src/application/equipment/` и запускаются
скриптом `scripts/build_equipment_knowledge.py`, потому что их результат
пересчитывается при пополнении словаря, а миграция описывает единичный переход.

Значение `other` (122 упражнения) canonical ID не получает намеренно. Оно
означает «оборудование нужно, но какое — не указано», и записать его как
«оборудование не требуется» значило бы утверждать неверное: упражнение стало бы
выполнимым в любых условиях. Такие значения попадают в
`unmapped_equipment_values` с причиной `ambiguous` и остаются видимым пробелом
данных в Knowledge Base Health.

Неоднозначные синонимы (одно значение указывает на несколько единиц
оборудования) тоже не превращаются в REQUIRED: выбор первого варианта был бы
угадыванием. Они записываются в `unmapped_equipment_values` и ждут решения
администратора.

Идемпотентность: вставка через `ON CONFLICT DO NOTHING` по уникальному ключу
требования. Повторный `upgrade` ничего не дублирует.

Downgrade удаляет только строки с источником `catalog_import` — то есть ровно
то, что создала эта миграция. Требования, заведённые администратором вручную,
и выведенные скриптом сохраняются.
"""
from alembic import op
import sqlalchemy as sa

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

IMPORT_SOURCE = "catalog_import"

# Значение каталога, означающее «оборудование нужно, но какое — не сказано».
AMBIGUOUS_VALUE = "other"

# Значения каталога, разложенные в строки. `equipment` объявлена типом JSON, для
# которого в PostgreSQL не определены операторы массива, поэтому приводится к
# JSONB — так же, как это делает ExerciseRepository.
_CATALOG_VALUES = """
    SELECT
        e.external_id,
        e.source,
        lower(btrim(replace(value, 'ё', 'е'))) AS normalized,
        btrim(value) AS raw_value
    FROM exercises e
    CROSS JOIN LATERAL jsonb_array_elements_text(e.equipment::jsonb) AS value
    WHERE btrim(value) <> ''
"""

# Синонимы полного совпадения, у которых ровно одна цель. Синоним с несколькими
# целями — законная неоднозначность («мяч»), и выбирать за администратора нельзя.
_UNIQUE_ALIASES = """
    SELECT alias, min(equipment_id) AS equipment_id
    FROM equipment_aliases
    WHERE match_mode = 'exact'
    GROUP BY alias
    HAVING count(*) = 1
"""


def upgrade() -> None:
    bind = op.get_bind()

    bind.execute(
        sa.text(
            f"""
            WITH catalog AS ({_CATALOG_VALUES}),
                 unique_alias AS ({_UNIQUE_ALIASES})
            INSERT INTO exercise_equipment_requirements (
                exercise_external_id, exercise_source, equipment_id,
                requirement, confidence, source, notes
            )
            SELECT DISTINCT
                c.external_id,
                c.source,
                a.equipment_id,
                'required',
                'confirmed',
                :source,
                'rule=catalog_value:' || c.raw_value
            FROM catalog c
            JOIN unique_alias a ON a.alias = c.normalized
            ON CONFLICT ON CONSTRAINT uq_exercise_requirement DO NOTHING
            """
        ),
        {"source": IMPORT_SOURCE},
    )

    # Незакрытые значения: и `other`, и неизвестные словарю, и неоднозначные.
    # Информация не теряется молча — это требование этапа, а не удобство.
    bind.execute(
        sa.text(
            f"""
            WITH catalog AS ({_CATALOG_VALUES}),
                 unique_alias AS ({_UNIQUE_ALIASES})
            INSERT INTO unmapped_equipment_values (
                exercise_external_id, exercise_source, raw_value, reason, notes
            )
            SELECT DISTINCT
                c.external_id,
                c.source,
                c.raw_value,
                CASE
                    WHEN c.normalized = :ambiguous THEN 'ambiguous'
                    WHEN EXISTS (
                        SELECT 1 FROM equipment_aliases al
                        WHERE al.alias = c.normalized AND al.match_mode = 'exact'
                    ) THEN 'ambiguous'
                    ELSE 'unmapped'
                END,
                CASE
                    WHEN c.normalized = :ambiguous
                        THEN 'источник не уточняет оборудование'
                    WHEN EXISTS (
                        SELECT 1 FROM equipment_aliases al
                        WHERE al.alias = c.normalized AND al.match_mode = 'exact'
                    ) THEN 'значение указывает на несколько единиц оборудования'
                    ELSE 'значение отсутствует в словаре'
                END
            FROM catalog c
            LEFT JOIN unique_alias a ON a.alias = c.normalized
            WHERE a.equipment_id IS NULL
            ON CONFLICT ON CONSTRAINT uq_unmapped_equipment_value DO NOTHING
            """
        ),
        {"ambiguous": AMBIGUOUS_VALUE},
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "DELETE FROM exercise_equipment_requirements WHERE source = :source"
        ),
        {"source": IMPORT_SOURCE},
    )
    bind.execute(sa.text("DELETE FROM unmapped_equipment_values"))
