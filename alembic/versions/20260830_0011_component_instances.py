"""component_instances: реестр экземпляров распределённых компонентов

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-30

Задача: Backend должен видеть фактическое состояние компонентов, которые
разворачиваются независимо и в другом сетевом сегменте (Telegram Gateway на
EU-узле). До этой миграции такого источника истины не существовало: версия
Gateway была известна только тому, кто его деплоил.

Почему PostgreSQL, а не Redis. Реестр отвечает на вопрос «какая версия
компонента развёрнута» и служит основанием для решения о деплое. Такое
состояние должно переживать перезапуск и очистку кэша, поэтому business state
остаётся в PostgreSQL (см. AGENTS.md, Phase 1.2).

Уникален `component_id`, а не `component_type`: экземпляров одного типа может
быть несколько (`telegram-eu-1`, `telegram-eu-2`), и второй не должен затирать
первый. Именно этот индекс делает heartbeat идемпотентным.

`last_heartbeat_at` отделён от `updated_at`: heartbeat приходит раз в минуту без
изменения metadata, а `updated_at` должен показывать, когда компонент
действительно изменился.

Секретов таблица не хранит и не должна: её содержимое целиком отдаётся
Admin API. Миграция аддитивная, существующие данные не затрагивает; downgrade
удаляет только созданную таблицу.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "component_instances",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("component_id", sa.String(64), nullable=False),
        sa.Column("component_type", sa.String(32), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("region", sa.String(32), nullable=False, server_default="RU"),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("build_sha", sa.String(40), nullable=True),
        sa.Column("contract_version", sa.Integer(), nullable=False),
        sa.Column(
            "capabilities", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default="healthy"),
        sa.Column(
            "last_heartbeat_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "registered_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_component_instances_component_id",
        "component_instances",
        ["component_id"],
        unique=True,
    )
    op.create_index(
        "ix_component_instances_component_type",
        "component_instances",
        ["component_type"],
    )
    # Список компонентов всегда сортируется и фильтруется по свежести
    # heartbeat: без индекса определение офлайна читало бы таблицу целиком.
    op.create_index(
        "ix_component_instances_last_heartbeat_at",
        "component_instances",
        ["last_heartbeat_at"],
    )
    op.create_index(
        "ix_component_instances_status", "component_instances", ["status"]
    )


def downgrade() -> None:
    op.drop_index("ix_component_instances_status", table_name="component_instances")
    op.drop_index(
        "ix_component_instances_last_heartbeat_at", table_name="component_instances"
    )
    op.drop_index(
        "ix_component_instances_component_type", table_name="component_instances"
    )
    op.drop_index(
        "ix_component_instances_component_id", table_name="component_instances"
    )
    op.drop_table("component_instances")
