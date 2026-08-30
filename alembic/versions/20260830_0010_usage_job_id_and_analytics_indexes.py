"""ai_usage_records.job_id + индексы под аналитику генерации

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-30

Задача: связать телеметрию AI-контура с операцией генерации и сделать
серверную аналитику по ней возможной.

Почему `job_id` не внешний ключ. `generation_jobs` удаляется каскадом вместе с
анкетой, а журнал вызовов должен переживать удаление анкеты: FK уничтожил бы
историю обращений к ИИ. Ссылка остаётся текстовой — «висячий» job_id здесь
допустим и означает «операция удалена», а не ошибку.

Существующие строки остаются с `job_id = NULL`. Backfill невозможен и не
делается: у прошлых вызовов связи с операцией генерации не существовало, и
восстановить её по profile_id нельзя — у одной анкеты несколько генераций, и
такая догадка приписала бы вызовы не тем операциям.

Индексы. `ai_audit_events(event_type, entity_id)` — аналитика соединяет журнал
попыток с генерациями именно по этой паре; без индекса каждый запрос сводки
читал бы журнал целиком. `workout_programs(program_id, version)` уже покрыт
первичным ключом, поэтому здесь не дублируется.

Downgrade удаляет колонку вместе со связями: сохранять их некуда — до 0010
поля в схеме не было.
"""
from alembic import op
import sqlalchemy as sa

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ai_usage_records",
        sa.Column("job_id", sa.String(64), nullable=True),
    )
    op.create_index(
        "ix_ai_usage_records_job_id", "ai_usage_records", ["job_id"]
    )
    # Журнал попыток моделей выбирается по типу события и связывается с
    # генерацией по entity_id: аналитика делает это в каждом запросе.
    op.create_index(
        "ix_ai_audit_events_type_entity",
        "ai_audit_events",
        ["event_type", "entity_id"],
    )
    # Сводка и временной ряд фильтруют генерации по дате создания вместе со
    # статусом.
    op.create_index(
        "ix_generation_jobs_status_created_at",
        "generation_jobs",
        ["status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_generation_jobs_status_created_at", table_name="generation_jobs"
    )
    op.drop_index("ix_ai_audit_events_type_entity", table_name="ai_audit_events")
    op.drop_index("ix_ai_usage_records_job_id", table_name="ai_usage_records")
    op.drop_column("ai_usage_records", "job_id")
