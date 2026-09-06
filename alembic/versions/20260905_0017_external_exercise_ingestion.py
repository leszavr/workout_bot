"""Внешние источники знаний об упражнениях: staging, provenance, наблюдения

Revision ID: 0017
Revises: 0016
Create Date: 2026-09-05

Этап вводит второй и третий источники знаний об упражнениях (каталог
`hasaneyldrm/exercises-dataset` и датасет программ Kaggle) и при этом обязан
сохранить единственную canonical сущность упражнения. Поэтому новых таблиц
упражнений здесь нет: canonical остаётся `exercises`, ключ — пара
(`external_id`, `source`), и внешняя запись попадает в неё только через merge.

Что создаётся и зачем:

- `external_sources` — реестр источников. Источник описывается данными, а не
  строковой константой в коде: у него есть вид (каталог упражнений против
  датасета программ), домашняя страница и условия использования данных и media,
  и всё это должно быть видно администратору вместе с результатами импорта.
- `external_source_versions` — версия источника: commit SHA для GitHub, хеш
  архива и дата загрузки для Kaggle. Без версии импорт невоспроизводим: «в базе
  1324 записи» не отвечает на вопрос, из какого состояния источника они взяты.
- `external_exercise_records` — staging-слой. Здесь живёт нормализованная
  внешняя запись вместе с решением о ней: степень качества, найденное
  соответствие, уверенность и причины. Строка остаётся в базе и после импорта, и
  после отклонения: «почему это упражнение не попало в каталог» — такой же
  результат этапа, как и добавленные упражнения.
- `exercise_source_links` — связь canonical упражнения с внешней записью.
  Отдельно от staging, потому что отвечает на другой вопрос: не «что мы решили
  про внешнюю запись», а «из каких источников собрано это упражнение». У одного
  упражнения источников несколько (origin + обогащение), и хранить их полем в
  `exercises` значило бы держать список в скалярной колонке.
- `exercise_field_provenance` — происхождение конкретного поля. Нужно ровно
  потому, что merge не перезаписывает поля слепо: если русская техника пришла из
  источника B, а название осталось canonical, то в отчёте и в админке это должно
  быть видно по полям, а не по записи целиком.
- `exercise_program_observations` — программное знание Kaggle: сколько программ
  включают упражнение, какие подходы и повторения там встречаются, для каких
  целей и уровней. Это наблюдение источника, а не предписание: колонки названы
  `typical_*` и `source_*`, и ни одна из них не является рекомендацией. Решение
  о нагрузке принимает генератор по методологии проекта, а не по частоте в чужих
  программах.

Почему staging — таблицы, а не файлы отчёта. Импорт обязан быть идемпотентным и
повторяемым: повторный запуск того же источника не должен ни создавать
упражнения заново, ни терять прежние решения. Для этого нужно состояние, которое
переживает процесс, и уникальный ключ (`source_key`, `source_record_id`), по
которому повторная загрузка попадает в ту же строку.

Ссылок FK на `exercises` нет по той же причине, что в `exercise_media` и
`exercise_equipment_requirements`: составной внешний ключ привязал бы знание к
жизненному циклу строки каталога, а пересоздание каталога — штатная операция
импорта. Целостность измеряется счётчиками ingestion-отчёта, где она видна
администратору.

Миграция только создаёт объекты: ни одна существующая строка не изменяется и не
удаляется. Наполнение выполняет `scripts/ingest_external_exercises.py`, потому
что результат зависит от содержимого локальной копии источника и пересчитывается
при её обновлении.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0017"
down_revision = "0016"
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
        "external_sources",
        sa.Column("source_key", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("homepage", sa.String(300), nullable=True),
        sa.Column("data_license", sa.String(200), nullable=True),
        sa.Column("media_license", sa.String(300), nullable=True),
        sa.Column("attribution", sa.String(300), nullable=True),
        sa.Column("notes", sa.String(500), nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        *_timestamps(),
        sa.CheckConstraint(
            "kind IN ('exercise_catalog', 'program_dataset')",
            name="ck_external_source_kind",
        ),
    )

    op.create_table(
        "external_source_versions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "source_key",
            sa.String(64),
            sa.ForeignKey("external_sources.source_key", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.String(128), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("record_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("notes", sa.String(500), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint(
            "source_key", "version", name="uq_external_source_version"
        ),
    )
    op.create_index(
        "ix_external_source_versions_source",
        "external_source_versions",
        ["source_key"],
    )

    op.create_table(
        "external_exercise_records",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "source_key",
            sa.String(64),
            sa.ForeignKey("external_sources.source_key", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_version", sa.String(128), nullable=False),
        sa.Column("source_record_id", sa.String(128), nullable=False),
        # Хеш нормализованной записи: по нему видно, изменился ли источник, без
        # сравнения payload целиком.
        sa.Column("record_hash", sa.String(64), nullable=False),
        sa.Column("raw_name", sa.String(255), nullable=False),
        sa.Column("normalized_name", sa.String(255), nullable=False),
        # Ключ сопоставления: нормализованное название без служебных слов и
        # порядка. Индексируется, потому что сопоставление ищет именно по нему.
        sa.Column("name_key", sa.String(255), nullable=False),
        sa.Column("payload", JSONB, nullable=False, server_default=sa.text("'{}'")),
        sa.Column("quality_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("quality_status", sa.String(16), nullable=False, server_default="review"),
        sa.Column(
            "quality_reasons", JSONB, nullable=False, server_default=sa.text("'[]'")
        ),
        sa.Column("decision", sa.String(24), nullable=False, server_default="unknown"),
        sa.Column("match_confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column(
            "match_reasons", JSONB, nullable=False, server_default=sa.text("'[]'")
        ),
        sa.Column("matched_external_id", sa.String(128), nullable=True),
        sa.Column("matched_source", sa.String(64), nullable=True),
        sa.Column("import_status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("import_note", sa.String(300), nullable=True),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint(
            "source_key", "source_record_id", name="uq_external_exercise_record"
        ),
        sa.CheckConstraint(
            "quality_status IN ('ready', 'review', 'reject')",
            name="ck_external_record_quality_status",
        ),
        sa.CheckConstraint(
            "decision IN ('existing', 'enrichable', 'new_relevant', "
            "'duplicate_variant', 'low_quality', 'questionable', 'unknown')",
            name="ck_external_record_decision",
        ),
        sa.CheckConstraint(
            "import_status IN ('pending', 'imported', 'enriched', 'skipped', "
            "'rejected')",
            name="ck_external_record_import_status",
        ),
    )
    op.create_index(
        "ix_external_records_name_key", "external_exercise_records", ["name_key"]
    )
    op.create_index(
        "ix_external_records_decision",
        "external_exercise_records",
        ["source_key", "decision"],
    )
    op.create_index(
        "ix_external_records_status",
        "external_exercise_records",
        ["source_key", "import_status"],
    )
    op.create_index(
        "ix_external_records_matched",
        "external_exercise_records",
        ["matched_external_id", "matched_source"],
    )

    op.create_table(
        "exercise_source_links",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("exercise_external_id", sa.String(128), nullable=False),
        sa.Column(
            "exercise_source",
            sa.String(64),
            nullable=False,
            server_default=_EXERCISE_SOURCE_DEFAULT,
        ),
        sa.Column(
            "source_key",
            sa.String(64),
            sa.ForeignKey("external_sources.source_key", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_record_id", sa.String(128), nullable=False),
        sa.Column("source_version", sa.String(128), nullable=False),
        sa.Column("relation", sa.String(24), nullable=False, server_default="enrichment"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("reasons", JSONB, nullable=False, server_default=sa.text("'[]'")),
        *_timestamps(),
        sa.UniqueConstraint(
            "exercise_external_id",
            "exercise_source",
            "source_key",
            "source_record_id",
            name="uq_exercise_source_link",
        ),
        sa.CheckConstraint(
            "relation IN ('origin', 'enrichment', 'duplicate_variant', "
            "'observation')",
            name="ck_exercise_source_link_relation",
        ),
    )
    op.create_index(
        "ix_exercise_source_links_exercise",
        "exercise_source_links",
        ["exercise_external_id", "exercise_source"],
    )

    op.create_table(
        "exercise_field_provenance",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("exercise_external_id", sa.String(128), nullable=False),
        sa.Column(
            "exercise_source",
            sa.String(64),
            nullable=False,
            server_default=_EXERCISE_SOURCE_DEFAULT,
        ),
        sa.Column("field", sa.String(48), nullable=False),
        sa.Column("source_key", sa.String(64), nullable=False),
        sa.Column("source_record_id", sa.String(128), nullable=False),
        sa.Column("source_version", sa.String(128), nullable=False),
        sa.Column("value_hash", sa.String(64), nullable=False),
        sa.Column("reason", sa.String(64), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint(
            "exercise_external_id",
            "exercise_source",
            "field",
            name="uq_exercise_field_provenance",
        ),
    )
    op.create_index(
        "ix_exercise_field_provenance_exercise",
        "exercise_field_provenance",
        ["exercise_external_id", "exercise_source"],
    )

    op.create_table(
        "exercise_program_observations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("exercise_external_id", sa.String(128), nullable=False),
        sa.Column(
            "exercise_source",
            sa.String(64),
            nullable=False,
            server_default=_EXERCISE_SOURCE_DEFAULT,
        ),
        sa.Column(
            "source_key",
            sa.String(64),
            sa.ForeignKey("external_sources.source_key", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_version", sa.String(128), nullable=False),
        sa.Column("source_record_id", sa.String(128), nullable=False),
        sa.Column("program_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("occurrence_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("typical_sets_median", sa.Float(), nullable=True),
        sa.Column("typical_sets_min", sa.Integer(), nullable=True),
        sa.Column("typical_sets_max", sa.Integer(), nullable=True),
        sa.Column("typical_reps_median", sa.Float(), nullable=True),
        sa.Column("typical_reps_min", sa.Integer(), nullable=True),
        sa.Column("typical_reps_max", sa.Integer(), nullable=True),
        # Отдельно от повторений: в источнике отрицательные значения обозначают
        # время удержания в секундах, и складывать их с повторениями нельзя.
        sa.Column("typical_hold_seconds_median", sa.Float(), nullable=True),
        sa.Column("typical_intensity_median", sa.Float(), nullable=True),
        sa.Column("source_goals", JSONB, nullable=False, server_default=sa.text("'{}'")),
        sa.Column("source_levels", JSONB, nullable=False, server_default=sa.text("'{}'")),
        sa.Column(
            "source_equipment_contexts",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        *_timestamps(),
        sa.UniqueConstraint(
            "exercise_external_id",
            "exercise_source",
            "source_key",
            name="uq_exercise_program_observation",
        ),
    )
    op.create_index(
        "ix_exercise_program_observations_exercise",
        "exercise_program_observations",
        ["exercise_external_id", "exercise_source"],
    )


def downgrade() -> None:
    op.drop_table("exercise_program_observations")
    op.drop_table("exercise_field_provenance")
    op.drop_table("exercise_source_links")
    op.drop_table("external_exercise_records")
    op.drop_table("external_source_versions")
    op.drop_table("external_sources")
