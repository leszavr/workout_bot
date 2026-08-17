"""exercises: add name_ru, technique_ru, force, mechanic

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-17

"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("exercises", sa.Column("name_ru", sa.String(255), nullable=True))
    op.add_column("exercises", sa.Column("technique_ru", sa.Text(), nullable=True))
    op.add_column("exercises", sa.Column("force", sa.String(16), nullable=True))
    op.add_column("exercises", sa.Column("mechanic", sa.String(16), nullable=True))


def downgrade() -> None:
    op.drop_column("exercises", "mechanic")
    op.drop_column("exercises", "force")
    op.drop_column("exercises", "technique_ru")
    op.drop_column("exercises", "name_ru")
