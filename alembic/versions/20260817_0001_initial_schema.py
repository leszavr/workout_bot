"""initial schema: users, profiles, consents, exercises

Revision ID: 0001
Revises:
Create Date: 2026-08-17

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SEQUENCE IF NOT EXISTS profile_display_number_seq")

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("telegram_user_id", sa.String(64), nullable=False),
        sa.Column("telegram_username", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("telegram_user_id", name="uq_users_telegram_user_id"),
    )
    op.create_index("ix_users_telegram_user_id", "users", ["telegram_user_id"])

    op.create_table(
        "profiles",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("profile_id", sa.String(64), nullable=False),
        sa.Column("display_number", sa.String(32), nullable=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("profile_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("schema_version", sa.String(16), nullable=False, server_default="1.0"),
        sa.Column("data", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("profile_id", name="uq_profiles_profile_id"),
    )
    op.create_index("ix_profiles_profile_id", "profiles", ["profile_id"])
    op.create_index("ix_profiles_display_number", "profiles", ["display_number"])
    op.create_index("ix_profiles_user_id", "profiles", ["user_id"])
    op.create_index("ix_profiles_status", "profiles", ["status"])

    op.create_table(
        "consents",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("consent_type", sa.String(64), nullable=False),
        sa.Column("consent_version", sa.String(16), nullable=False),
        sa.Column("granted", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(100), nullable=False),
    )
    op.create_index("ix_consents_user_id", "consents", ["user_id"])
    op.create_index("ix_consents_consent_type", "consents", ["consent_type"])

    op.create_table(
        "exercises",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("external_id", sa.String(128), nullable=False),
        sa.Column("source", sa.String(64), nullable=False, server_default="leszavr/workout"),
        sa.Column("source_version", sa.String(64), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("aliases", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("technique", sa.Text(), nullable=True),
        sa.Column("common_mistakes", sa.Text(), nullable=True),
        sa.Column("primary_muscles", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("secondary_muscles", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("equipment", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("exercise_type", sa.String(64), nullable=True),
        sa.Column("difficulty", sa.String(32), nullable=True),
        sa.Column("contraindications", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("limitations", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("images", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("external_id", "source", name="uq_exercise_external_source"),
    )
    op.create_index("ix_exercises_external_id", "exercises", ["external_id"])
    op.create_index("ix_exercises_name", "exercises", ["name"])
    op.create_index("ix_exercises_exercise_type", "exercises", ["exercise_type"])
    op.create_index("ix_exercises_difficulty", "exercises", ["difficulty"])
    op.create_index("ix_exercises_is_active", "exercises", ["is_active"])


def downgrade() -> None:
    op.drop_table("exercises")
    op.drop_table("consents")
    op.drop_table("profiles")
    op.drop_table("users")
    op.execute("DROP SEQUENCE IF EXISTS profile_display_number_seq")
