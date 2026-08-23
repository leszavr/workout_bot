"""SQLAlchemy ORM-модели PostgreSQL.

Профиль хранится как JSONB (persistence format), но перед записью и после
чтения проходит строгую Pydantic-валидацию — БД не является хранилищем
произвольного JSON.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    type_annotation_map = {dict: JSONB}


# FK-политика для usage-записей: удаление конфигурации не теряет историю.
_FK_ON_DELETE_SET_NULL = "SET NULL"


class UserRow(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_user_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    telegram_username: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ProfileRow(Base):
    __tablename__ = "profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    display_number: Mapped[str | None] = mapped_column(String(32), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    profile_version: Mapped[int] = mapped_column(Integer, default=1)
    schema_version: Mapped[str] = mapped_column(String(16), default="1.0")
    data: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ConsentRow(Base):
    __tablename__ = "consents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    consent_type: Mapped[str] = mapped_column(String(64), index=True)
    consent_version: Mapped[str] = mapped_column(String(16))
    granted: Mapped[bool] = mapped_column(Boolean, default=True)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(String(100))


class ExerciseRow(Base):
    __tablename__ = "exercises"
    __table_args__ = (
        UniqueConstraint("external_id", "source", name="uq_exercise_external_source"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    external_id: Mapped[str] = mapped_column(String(128), index=True)
    source: Mapped[str] = mapped_column(String(64), default="leszavr/workout")
    source_version: Mapped[str | None] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(255), index=True)
    name_ru: Mapped[str | None] = mapped_column(String(255))
    aliases: Mapped[list] = mapped_column(JSON, default=list)
    description: Mapped[str | None] = mapped_column(Text)
    technique: Mapped[str | None] = mapped_column(Text)
    technique_ru: Mapped[str | None] = mapped_column(Text)
    common_mistakes: Mapped[str | None] = mapped_column(Text)
    primary_muscles: Mapped[list] = mapped_column(JSON, default=list)
    secondary_muscles: Mapped[list] = mapped_column(JSON, default=list)
    equipment: Mapped[list] = mapped_column(JSON, default=list)
    exercise_type: Mapped[str | None] = mapped_column(String(64), index=True)
    difficulty: Mapped[str | None] = mapped_column(String(32), index=True)
    force: Mapped[str | None] = mapped_column(String(16))
    mechanic: Mapped[str | None] = mapped_column(String(16))
    contraindications: Mapped[list] = mapped_column(JSON, default=list)
    limitations: Mapped[list] = mapped_column(JSON, default=list)
    images: Mapped[list] = mapped_column(JSON, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class WorkoutProgramRow(Base):
    """Версия программы тренировок.

    Храним полную Pydantic-модель в JSONB (data) + денормализованные
    колонки для списков/фильтров. Каждая версия — отдельная строка,
    история не перезаписывается.
    """

    __tablename__ = "workout_programs"
    __table_args__ = (
        UniqueConstraint("program_id", "version", name="uq_program_id_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    program_id: Mapped[str] = mapped_column(String(64), index=True)
    profile_id: Mapped[str] = mapped_column(String(64), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    generation_source: Mapped[str] = mapped_column(String(32), default="deterministic")
    generator_version: Mapped[str | None] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(200))
    training_days_per_week: Mapped[int] = mapped_column(Integer, default=3)
    duration_weeks: Mapped[int] = mapped_column(Integer, default=8)
    data: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ExerciseMediaRow(Base):
    """Метаданные медиа-ассетов упражнений (Stage 5).

    Файлы хранятся в object storage (MinIO); здесь — metadata:
    storage_key, размеры, checksum, источник и лицензия.
    """

    __tablename__ = "exercise_media"
    __table_args__ = (
        UniqueConstraint(
            "exercise_external_id",
            "exercise_source",
            "sequence",
            name="uq_exercise_media_external_source_sequence",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    exercise_external_id: Mapped[str] = mapped_column(String(128), index=True)
    exercise_source: Mapped[str] = mapped_column(String(64), default="leszavr/workout")
    media_type: Mapped[str] = mapped_column(String(32), default="image")
    sequence: Mapped[int] = mapped_column(Integer)
    storage_key: Mapped[str] = mapped_column(String(300))
    mime_type: Mapped[str] = mapped_column(String(100), default="image/webp")
    width: Mapped[int] = mapped_column(Integer, default=0)
    height: Mapped[int] = mapped_column(Integer, default=0)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    checksum: Mapped[str] = mapped_column(String(64), index=True)
    source: Mapped[str | None] = mapped_column(String(64))
    source_url: Mapped[str | None] = mapped_column(String(500))
    license: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ProgramDeliveryRow(Base):
    """Жизненный цикл доставки HTML-программы пользователю.

    Delivery retry независим от generation retry: после успешной генерации
    повторные попытки доставки не запускают новую генерацию.
    """

    __tablename__ = "program_deliveries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    program_id: Mapped[str] = mapped_column(String(64), index=True)
    profile_id: Mapped[str] = mapped_column(String(64), index=True)
    chat_id: Mapped[str | None] = mapped_column(String(64), index=True)
    filename: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(String(500))
    sent_message_id: Mapped[int | None] = mapped_column(Integer)
    source_media_mode: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# --- AI Configuration (этап 3B) -------------------------------------------------


class AISecretRow(Base):
    """Зашифрованные секреты AI-эндпоинтов (Fernet at rest).

    Хранит только зашифрованное значение; reference — ключ для SecretStore.
    """

    __tablename__ = "ai_secrets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    reference: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    encrypted_value: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AIProviderRow(Base):
    """Логический поставщик AI (протокол, не бренд модели)."""

    __tablename__ = "ai_providers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100))
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    protocol: Mapped[str] = mapped_column(String(32), default="openai_compatible", index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AIEndpointRow(Base):
    """Техническая точка подключения. API key здесь НЕТ — только secret_reference."""

    __tablename__ = "ai_endpoints"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider_id: Mapped[int] = mapped_column(
        ForeignKey("ai_providers.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(100))
    base_url: Mapped[str] = mapped_column(String(500))
    secret_reference: Mapped[str | None] = mapped_column(String(128), unique=True)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=60)
    max_retries: Mapped[int] = mapped_column(Integer, default=2)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    # Результат последней проверки подключения: только время, статус и класс
    # ошибки. Ключи и тело ответа провайдера здесь не хранятся.
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_test_status: Mapped[str | None] = mapped_column(String(16))
    last_test_error_type: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AIModelRow(Base):
    """Конфигурация модели. Поведение определяется capabilities, не названием."""

    __tablename__ = "ai_models"
    __table_args__ = (
        UniqueConstraint("endpoint_id", "model_id", name="uq_ai_model_endpoint_model"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    endpoint_id: Mapped[int] = mapped_column(
        ForeignKey("ai_endpoints.id", ondelete="CASCADE"), index=True
    )
    model_id: Mapped[str] = mapped_column(String(200), index=True)
    display_name: Mapped[str] = mapped_column(String(200))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    context_window: Mapped[int | None] = mapped_column(Integer)
    max_output_tokens: Mapped[int | None] = mapped_column(Integer)
    supports_structured_output: Mapped[bool] = mapped_column(Boolean, default=False)
    supports_json_schema: Mapped[bool] = mapped_column(Boolean, default=False)
    supports_streaming: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AITaskConfigRow(Base):
    """Конфигурация AI-задачи (task-scoped, не глобальная ACTIVE_AI_MODEL)."""

    __tablename__ = "ai_task_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_type: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    temperature: Mapped[float] = mapped_column(Float, default=0.7)
    max_tokens: Mapped[int | None] = mapped_column(Integer)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=120)
    prompt_version: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AITaskModelBindingRow(Base):
    """Привязка модели к задаче: priority=1 → primary, 2+ → fallback."""

    __tablename__ = "ai_task_model_bindings"
    __table_args__ = (
        UniqueConstraint("task_config_id", "priority", name="uq_binding_task_priority"),
        UniqueConstraint("task_config_id", "model_id", name="uq_binding_task_model"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_config_id: Mapped[int] = mapped_column(
        ForeignKey("ai_task_configs.id", ondelete="CASCADE"), index=True
    )
    model_id: Mapped[int] = mapped_column(
        # RESTRICT: нельзя удалить модель, привязанную к задаче.
        ForeignKey("ai_models.id", ondelete="RESTRICT"),
        index=True,
    )
    priority: Mapped[int] = mapped_column(Integer)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)


class PromptTemplateRow(Base):
    """Версионируемый шаблон промпта. Версия неизменяема после создания."""

    __tablename__ = "prompt_templates"
    __table_args__ = (
        UniqueConstraint("task_type", "version", name="uq_prompt_task_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_type: Mapped[str] = mapped_column(String(64), index=True)
    version: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(200))
    system_prompt: Mapped[str] = mapped_column(Text)
    user_template: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AIUsageRecordRow(Base):
    """Учёт AI-вызова. НЕ хранит prompt/ответ/ключи/персональные данные."""

    __tablename__ = "ai_usage_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_type: Mapped[str] = mapped_column(String(64), index=True)
    provider_id: Mapped[int | None] = mapped_column(
        ForeignKey("ai_providers.id", ondelete=_FK_ON_DELETE_SET_NULL)
    )
    endpoint_id: Mapped[int | None] = mapped_column(
        ForeignKey("ai_endpoints.id", ondelete=_FK_ON_DELETE_SET_NULL)
    )
    model_id: Mapped[int | None] = mapped_column(
        ForeignKey("ai_models.id", ondelete=_FK_ON_DELETE_SET_NULL)
    )
    profile_id: Mapped[str | None] = mapped_column(String(64), index=True)
    program_id: Mapped[str | None] = mapped_column(String(64), index=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    total_tokens: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), default="success", index=True)
    error_type: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class AIAuditEventRow(Base):
    """Audit-события административных изменений AI-конфигурации.

    metadata НЕ должна содержать секреты (контролируется сервисным слоем).

    Таблица исторически названа с префиксом `ai_`, но служит единым журналом
    административных событий проекта, включая управление пользователями:
    плодить параллельный журнал хуже, чем терпеть легаси-имя.
    """

    __tablename__ = "ai_audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    actor: Mapped[str | None] = mapped_column(String(100))
    entity_type: Mapped[str | None] = mapped_column(String(64), index=True)
    entity_id: Mapped[str | None] = mapped_column(String(64))
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class AdminUserRow(Base):
    """Пользователь админ-панели.

    Отдельная таблица от `users`: там клиенты Telegram-бота, здесь —
    сотрудники с доступом к внутреннему интерфейсу. Сущности разные,
    смешивать нельзя.

    `password_hash` допускает NULL: у пользователя, который входит только
    через внешнего провайдера, локального пароля нет.
    """

    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    login: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(16), default="viewer", index=True)
    password_hash: Mapped[str | None] = mapped_column(String(255))
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AdminIdentityRow(Base):
    """Аккаунт внешнего провайдера, привязанный к пользователю админки.

    Существует, чтобы подключение входа через Яндекс/VK/MAX не требовало
    менять таблицу пользователей: добавляется строка, а не колонка.

    Токены провайдера здесь НЕ хранятся — только идентификатор аккаунта.
    """

    __tablename__ = "admin_identities"
    __table_args__ = (
        UniqueConstraint(
            "provider", "provider_user_id", name="uq_admin_identity_provider_user"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("admin_users.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(32), index=True)
    provider_user_id: Mapped[str] = mapped_column(String(191))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
