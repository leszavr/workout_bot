"""exercise_media + program_deliveries (Stage 5)

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-18

Stage 5: end-to-end генерация программы, медиа упражнений и доставка.

exercise_media:
    Метаданные медиа-ассетов упражнений (файлы хранятся в object storage,
    здесь — только metadata). Упражнение связано с 1..N ассетами; количество
    НЕ ограничено схемой — лимит задаётся конфигурацией
    (EXERCISE_MEDIA_MAX_PER_EXERCISE). Идемпотенность импорта: UNIQUE по
    (external_id, source, sequence) — повторный импорт не создаёт дубликатов.

program_deliveries:
    Статус доставки HTML-программы пользователю в Telegram.
    Delivery retry НЕ равен generation retry: строка обновляется отдельно
    после успешного сохранения программы.
"""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "exercise_media",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("exercise_external_id", sa.String(128), nullable=False),
        sa.Column("exercise_source", sa.String(64), nullable=False, server_default="leszavr/workout"),
        sa.Column("media_type", sa.String(32), nullable=False, server_default="image"),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("storage_key", sa.String(300), nullable=False),
        sa.Column("mime_type", sa.String(100), nullable=False, server_default="image/webp"),
        sa.Column("width", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("height", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("source", sa.String(64), nullable=True),
        sa.Column("source_url", sa.String(500), nullable=True),
        sa.Column("license", sa.String(200), nullable=True),
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
        sa.UniqueConstraint(
            "exercise_external_id",
            "exercise_source",
            "sequence",
            name="uq_exercise_media_external_source_sequence",
        ),
    )
    op.create_index(
        "ix_exercise_media_external_id",
        "exercise_media",
        ["exercise_external_id", "exercise_source"],
    )
    op.create_index("ix_exercise_media_checksum", "exercise_media", ["checksum"])

    op.create_table(
        "program_deliveries",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("program_id", sa.String(64), nullable=False),
        sa.Column("profile_id", sa.String(64), nullable=False),
        sa.Column("chat_id", sa.String(64), nullable=True, index=True),
        sa.Column("filename", sa.String(255), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending", index=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.String(500), nullable=True),
        sa.Column("sent_message_id", sa.Integer(), nullable=True),
        sa.Column("source_media_mode", sa.String(32), nullable=True),
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
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_program_deliveries_profile_id", "program_deliveries", ["profile_id"])
    op.create_index("ix_program_deliveries_program_id", "program_deliveries", ["program_id"])


def downgrade() -> None:
    op.drop_index("ix_program_deliveries_program_id", table_name="program_deliveries")
    op.drop_index("ix_program_deliveries_profile_id", table_name="program_deliveries")
    op.drop_table("program_deliveries")
    op.drop_index("ix_exercise_media_checksum", table_name="exercise_media")
    op.drop_index("ix_exercise_media_external_id", table_name="exercise_media")
    op.drop_table("exercise_media")
