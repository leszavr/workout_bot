"""generation_jobs: persistent generation state + idempotency boundary

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-23

Phase 1.2-B. Одна логическая генерация = (профиль, бизнес-событие, номер
попытки). Идемпотентность обеспечивает БД: UNIQUE(idempotency_key), поэтому
два параллельных запроса одной логической генерации создают ровно один job.

Ссылка на результат — составная (program_id, program_version): результатом
генерации является конкретная версия программы. ON DELETE SET NULL: удаление
программы не уничтожает историю операций. Профиль — ON DELETE CASCADE: без
профиля operational-запись о его генерации смысла не имеет.

Секретов, промптов и ответов провайдера таблица не хранит: только стабильный
код ошибки и короткое безопасное описание.
"""
from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "generation_jobs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("job_id", sa.String(64), nullable=False),
        sa.Column("profile_id", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(191), nullable=False),
        sa.Column("trigger", sa.String(32), nullable=False),
        sa.Column("requested_generator", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("program_id", sa.String(64), nullable=True),
        sa.Column("program_version", sa.Integer(), nullable=True),
        sa.Column("last_error_code", sa.String(64), nullable=True),
        sa.Column("last_error_message", sa.String(500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_generation_job_idempotency_key"
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["profiles.profile_id"],
            name="fk_generation_job_profile",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["program_id", "program_version"],
            ["workout_programs.program_id", "workout_programs.version"],
            name="fk_generation_job_program",
            ondelete="SET NULL",
        ),
    )
    # job_id — публичный идентификатор записи, уникальность обеспечивает индекс
    # (так же, как её объявляет ORM-модель).
    op.create_index(
        "ix_generation_jobs_job_id", "generation_jobs", ["job_id"], unique=True
    )
    op.create_index("ix_generation_jobs_profile_id", "generation_jobs", ["profile_id"])
    op.create_index("ix_generation_jobs_status", "generation_jobs", ["status"])
    op.create_index("ix_generation_jobs_created_at", "generation_jobs", ["created_at"])
    op.create_index(
        "ix_generation_jobs_profile_status", "generation_jobs", ["profile_id", "status"]
    )
    # Номер попытки считается по завершённым job того же триггера.
    op.create_index(
        "ix_generation_jobs_profile_trigger",
        "generation_jobs",
        ["profile_id", "trigger"],
    )


def downgrade() -> None:
    op.drop_index("ix_generation_jobs_profile_trigger", table_name="generation_jobs")
    op.drop_index("ix_generation_jobs_profile_status", table_name="generation_jobs")
    op.drop_index("ix_generation_jobs_created_at", table_name="generation_jobs")
    op.drop_index("ix_generation_jobs_status", table_name="generation_jobs")
    op.drop_index("ix_generation_jobs_profile_id", table_name="generation_jobs")
    op.drop_index("ix_generation_jobs_job_id", table_name="generation_jobs")
    op.drop_table("generation_jobs")
