"""admin_users + admin_identities: пользователи админ-панели

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-22

Управление пользователями внутреннего интерфейса. До этой миграции
администратор был один и задавался переменными окружения, а пароль
сравнивался в открытом виде.

Две таблицы вместо одной сделаны намеренно:

- `admin_users` — сам человек: логин, роль (admin|viewer), хеш пароля,
  флаг обязательной смены пароля, активность;
- `admin_identities` — аккаунты внешних провайдеров (Яндекс, VK, MAX),
  привязанные к пользователю. Подключение такого входа в будущем не
  потребует менять схему пользователей: добавляется строка, а не колонка.

`password_hash` допускает NULL: пользователь, входящий только через внешнего
провайдера, локального пароля не имеет. Токены провайдеров не хранятся.

Аддитивная миграция: существующие таблицы не затрагиваются, данные не
переносятся. Администратор из переменных окружения продолжает работать как
аварийный вход, поэтому доступ при обновлении не теряется.
"""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "admin_users",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("login", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=True),
        sa.Column(
            "role", sa.String(length=16), nullable=False, server_default="viewer"
        ),
        # NULL — вход только через внешнего провайдера.
        sa.Column("password_hash", sa.String(length=255), nullable=True),
        sa.Column(
            "must_change_password",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
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
    )
    op.create_index(
        "ix_admin_users_login", "admin_users", ["login"], unique=True
    )
    op.create_index("ix_admin_users_role", "admin_users", ["role"])
    op.create_index("ix_admin_users_is_active", "admin_users", ["is_active"])

    op.create_table(
        "admin_identities",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_user_id", sa.String(length=191), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["admin_users.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "provider", "provider_user_id", name="uq_admin_identity_provider_user"
        ),
    )
    op.create_index(
        "ix_admin_identities_user_id", "admin_identities", ["user_id"]
    )
    op.create_index(
        "ix_admin_identities_provider", "admin_identities", ["provider"]
    )


def downgrade() -> None:
    op.drop_index("ix_admin_identities_provider", table_name="admin_identities")
    op.drop_index("ix_admin_identities_user_id", table_name="admin_identities")
    op.drop_table("admin_identities")
    op.drop_index("ix_admin_users_is_active", table_name="admin_users")
    op.drop_index("ix_admin_users_role", table_name="admin_users")
    op.drop_index("ix_admin_users_login", table_name="admin_users")
    op.drop_table("admin_users")
