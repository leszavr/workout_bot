"""workout_programs: versioned storage of training programs

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-17

Каждая версия программы — отдельная строка (program_id, version);
исторические версии не перезаписываются. Полная Pydantic-модель
хранится в JSONB (data), денормализованные колонки — для списков.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workout_programs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("program_id", sa.String(64), nullable=False),
        sa.Column("profile_id", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("generation_source", sa.String(32), nullable=False, server_default="deterministic"),
        sa.Column("generator_version", sa.String(64), nullable=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("training_days_per_week", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("duration_weeks", sa.Integer(), nullable=False, server_default="8"),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("program_id", "version", name="uq_program_id_version"),
    )
    op.create_index("ix_workout_programs_program_id", "workout_programs", ["program_id"])
    op.create_index("ix_workout_programs_profile_id", "workout_programs", ["profile_id"])
    op.create_index("ix_workout_programs_status", "workout_programs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_workout_programs_status", table_name="workout_programs")
    op.drop_index("ix_workout_programs_profile_id", table_name="workout_programs")
    op.drop_index("ix_workout_programs_program_id", table_name="workout_programs")
    op.drop_table("workout_programs")
