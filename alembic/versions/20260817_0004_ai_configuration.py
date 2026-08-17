"""AI configuration: providers, endpoints, models, tasks, prompts, usage, audit

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-17

Этап 3B: универсальный AI-слой. Архитектура вокруг протоколов, а не брендов:
ai_providers (protocol) → ai_endpoints (base_url + secret_reference) →
ai_models (capabilities). API key хранится отдельно в ai_secrets (Fernet
at rest) и никогда не сериализуется. ai_task_configs + ai_task_model_bindings —
конфигурация задач с primary/fallback. prompt_templates — версионирование
(уникальность task_type+version). ai_usage_records — token accounting.
ai_audit_events — audit без секретов.

Удаление модели, привязанной к задаче, запрещено (ON DELETE RESTRICT);
используйте soft disable (enabled=false).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

# Общие значения по умолчанию для всех таблиц миграции.
_NOW = sa.text("now()")
_ON_DELETE_SET_NULL = "SET NULL"


def upgrade() -> None:
    op.create_table(
        "ai_secrets",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("reference", sa.String(128), nullable=False),
        sa.Column("encrypted_value", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.UniqueConstraint("reference", name="uq_ai_secrets_reference"),
    )

    op.create_table(
        "ai_providers",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("protocol", sa.String(32), nullable=False, server_default="openai_compatible"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.UniqueConstraint("slug", name="uq_ai_providers_slug"),
    )
    op.create_index("ix_ai_providers_protocol", "ai_providers", ["protocol"])

    op.create_table(
        "ai_endpoints",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("provider_id", sa.Integer(), sa.ForeignKey("ai_providers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("base_url", sa.String(500), nullable=False),
        sa.Column("secret_reference", sa.String(128), nullable=True),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.UniqueConstraint("secret_reference", name="uq_ai_endpoints_secret_reference"),
    )
    op.create_index("ix_ai_endpoints_provider_id", "ai_endpoints", ["provider_id"])

    op.create_table(
        "ai_models",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("endpoint_id", sa.Integer(), sa.ForeignKey("ai_endpoints.id", ondelete="CASCADE"), nullable=False),
        sa.Column("model_id", sa.String(200), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("context_window", sa.Integer(), nullable=True),
        sa.Column("max_output_tokens", sa.Integer(), nullable=True),
        sa.Column("supports_structured_output", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("supports_json_schema", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("supports_streaming", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.UniqueConstraint("endpoint_id", "model_id", name="uq_ai_model_endpoint_model"),
    )
    op.create_index("ix_ai_models_endpoint_id", "ai_models", ["endpoint_id"])
    op.create_index("ix_ai_models_model_id", "ai_models", ["model_id"])

    op.create_table(
        "ai_task_configs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("task_type", sa.String(64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("temperature", sa.Float(), nullable=False, server_default="0.7"),
        sa.Column("max_tokens", sa.Integer(), nullable=True),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False, server_default="120"),
        sa.Column("prompt_version", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.UniqueConstraint("task_type", name="uq_ai_task_configs_task_type"),
    )

    op.create_table(
        "ai_task_model_bindings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("task_config_id", sa.Integer(), sa.ForeignKey("ai_task_configs.id", ondelete="CASCADE"), nullable=False),
        # RESTRICT: удаление модели, используемой задачей, запрещено.
        sa.Column("model_id", sa.Integer(), sa.ForeignKey("ai_models.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("task_config_id", "priority", name="uq_binding_task_priority"),
        sa.UniqueConstraint("task_config_id", "model_id", name="uq_binding_task_model"),
    )
    op.create_index("ix_ai_bindings_task_config_id", "ai_task_model_bindings", ["task_config_id"])
    op.create_index("ix_ai_bindings_model_id", "ai_task_model_bindings", ["model_id"])

    op.create_table(
        "prompt_templates",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("task_type", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("system_prompt", sa.Text(), nullable=False),
        sa.Column("user_template", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.UniqueConstraint("task_type", "version", name="uq_prompt_task_version"),
    )
    op.create_index("ix_prompt_templates_task_type", "prompt_templates", ["task_type"])

    op.create_table(
        "ai_usage_records",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("task_type", sa.String(64), nullable=False),
        sa.Column("provider_id", sa.Integer(), sa.ForeignKey("ai_providers.id", ondelete=_ON_DELETE_SET_NULL), nullable=True),
        sa.Column("endpoint_id", sa.Integer(), sa.ForeignKey("ai_endpoints.id", ondelete=_ON_DELETE_SET_NULL), nullable=True),
        sa.Column("model_id", sa.Integer(), sa.ForeignKey("ai_models.id", ondelete=_ON_DELETE_SET_NULL), nullable=True),
        sa.Column("profile_id", sa.String(64), nullable=True),
        sa.Column("program_id", sa.String(64), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="success"),
        sa.Column("error_type", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
    )
    op.create_index("ix_ai_usage_task_type", "ai_usage_records", ["task_type"])
    op.create_index("ix_ai_usage_status", "ai_usage_records", ["status"])
    op.create_index("ix_ai_usage_created_at", "ai_usage_records", ["created_at"])
    op.create_index("ix_ai_usage_profile_id", "ai_usage_records", ["profile_id"])
    op.create_index("ix_ai_usage_program_id", "ai_usage_records", ["program_id"])

    op.create_table(
        "ai_audit_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("actor", sa.String(100), nullable=True),
        sa.Column("entity_type", sa.String(64), nullable=True),
        sa.Column("entity_id", sa.String(64), nullable=True),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
    )
    op.create_index("ix_ai_audit_event_type", "ai_audit_events", ["event_type"])
    op.create_index("ix_ai_audit_entity_type", "ai_audit_events", ["entity_type"])
    op.create_index("ix_ai_audit_created_at", "ai_audit_events", ["created_at"])


def downgrade() -> None:
    op.drop_table("ai_audit_events")
    op.drop_table("ai_usage_records")
    op.drop_table("prompt_templates")
    op.drop_table("ai_task_model_bindings")
    op.drop_table("ai_task_configs")
    op.drop_table("ai_models")
    op.drop_table("ai_endpoints")
    op.drop_table("ai_providers")
    op.drop_table("ai_secrets")
