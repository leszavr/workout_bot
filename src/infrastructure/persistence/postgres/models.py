"""SQLAlchemy ORM-модели PostgreSQL.

Профиль хранится как JSONB (persistence format), но перед записью и после
чтения проходит строгую Pydantic-валидацию — БД не является хранилищем
произвольного JSON.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
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

    `next_attempt_at`/`lease_*` (Phase 1.2-D) делают повтор межпроцессным:
    до них состояние повторов жило внутри одного вызова, и `failed`-запись
    никто не подхватывал.
    """

    __tablename__ = "program_deliveries"
    __table_args__ = (
        Index(
            "ix_program_deliveries_retry_queue",
            "status",
            "next_attempt_at",
            postgresql_where=text("next_attempt_at IS NOT NULL"),
        ),
        Index(
            "ix_program_deliveries_lease",
            "status",
            "lease_expires_at",
            postgresql_where=text("lease_expires_at IS NOT NULL"),
        ),
    )

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
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_owner: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GenerationJobRow(Base):
    """Persistent состояние одной логической генерации программы (Phase 1.2-B).

    Идемпотентность обеспечивает БД: UNIQUE(idempotency_key). Повторный запрос
    той же логической генерации нарушает constraint, и приложение читает уже
    существующий job вместо создания второго.

    Ссылка на программу — составная (program_id, version): версия программы, а
    не абстрактный program_id, является результатом конкретной генерации.
    ON DELETE SET NULL: удаление программы не должно уничтожать историю
    операций.

    Секретов, промптов, ответов провайдера и персональных данных здесь нет:
    только код ошибки и короткое безопасное описание.

    Phase 1.2-D добавляет `next_attempt_at` (когда повтор допустим) и аренду
    `lease_owner`/`lease_expires_at` (кто выполняет сейчас). Без аренды
    «застрявший в RUNNING» неотличим от «идёт легальная длинная генерация».
    """

    __tablename__ = "generation_jobs"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_generation_job_idempotency_key"),
        ForeignKeyConstraint(
            ["program_id", "program_version"],
            ["workout_programs.program_id", "workout_programs.version"],
            name="fk_generation_job_program",
            ondelete=_FK_ON_DELETE_SET_NULL,
        ),
        Index("ix_generation_jobs_profile_status", "profile_id", "status"),
        # Номер попытки вычисляется по завершённым job того же триггера;
        # индекс обслуживает именно этот запрос.
        Index("ix_generation_jobs_profile_trigger", "profile_id", "trigger"),
        # Очередь повторов воркера: только job с назначенным повтором.
        Index(
            "ix_generation_jobs_retry_queue",
            "status",
            "next_attempt_at",
            postgresql_where=text("next_attempt_at IS NOT NULL"),
        ),
        Index(
            "ix_generation_jobs_lease",
            "status",
            "lease_expires_at",
            postgresql_where=text("lease_expires_at IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    profile_id: Mapped[str] = mapped_column(
        ForeignKey("profiles.profile_id", ondelete="CASCADE"), index=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(191))
    trigger: Mapped[str] = mapped_column(String(32))
    requested_generator: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    program_id: Mapped[str | None] = mapped_column(String(64))
    program_version: Mapped[int | None] = mapped_column(Integer)
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    last_error_message: Mapped[str | None] = mapped_column(String(500))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_owner: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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
    # Операция генерации, в рамках которой сделан вызов. Не внешний ключ:
    # journal переживает удаление анкеты, а `generation_jobs` уходит вместе с
    # ней каскадом — FK уничтожил бы историю вызовов.
    job_id: Mapped[str | None] = mapped_column(String(64), index=True)
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


class ComponentInstanceRow(Base):
    """Зарегистрированный экземпляр распределённого компонента.

    Хранится только metadata. Никаких credentials здесь быть не может:
    содержимое строки целиком отдаётся Admin API.

    Уникален `component_id`, а не `component_type`: экземпляров одного типа
    может быть несколько (`telegram-eu-1`, `telegram-eu-2`), и второй не
    должен затирать первый.

    `capabilities` — JSONB-список строк. Отдельная таблица связей здесь была бы
    лишней: набор возможностей приходит целиком в каждом heartbeat и не
    существует независимо от компонента. Тип указан явно, а не через
    `Mapped[list]`: по умолчанию SQLAlchemy взял бы `JSON`, и модель разошлась
    бы с миграцией.

    `last_heartbeat_at` намеренно отделён от `updated_at`: heartbeat приходит
    каждую минуту и без изменения metadata, а `updated_at` показывает, когда
    компонент действительно изменился (новая версия, контракт, capabilities).
    """

    __tablename__ = "component_instances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    component_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    component_type: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(120))
    region: Mapped[str] = mapped_column(String(32), default="RU")
    version: Mapped[str] = mapped_column(String(32))
    build_sha: Mapped[str | None] = mapped_column(String(40))
    contract_version: Mapped[int] = mapped_column(Integer)
    capabilities: Mapped[list] = mapped_column(JSONB, default=list)
    status: Mapped[str] = mapped_column(String(16), default="healthy", index=True)
    last_heartbeat_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    registered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class EquipmentCapabilityRow(Base):
    """Функциональная возможность оборудования (Gym Knowledge Base).

    Отдельная таблица, а не список строк в оборудовании: возможность существует
    независимо от конкретного тренажёра и является целью ссылок сразу из двух
    мест — из оборудования и из требований упражнения. Список строк в JSONB не
    даёт ни ссылочной целостности, ни ответа на вопрос «какие возможности
    вообще существуют».

    Ключ — строковый `capability_id`, а не surrogate: на возможность ссылаются
    данные, миграции и админка, и ссылка `adjustable_resistance` читается, тогда
    как `17` — нет.
    """

    __tablename__ = "equipment_capabilities"

    capability_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    name_ru: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(String(300))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class EquipmentItemRow(Base):
    """Единица контролируемого словаря оборудования.

    Добавление нового тренажёра — вставка строки, а не изменение Python-кода:
    ни генератор, ни фильтр не содержат перечисления оборудования.

    `category` — строка, а не enum БД: категории пополняются вместе со словарём,
    а enum потребовал бы миграцию на каждое пополнение.

    `specializes` — ссылка на родовое оборудование: `leg_press` специализирует
    `resistance_machine`. Отношение нужно, потому что источник каталога говорит
    родовыми словами (у 67 упражнений оборудование указано как `machine`), и без
    него человек с жимом ногами получал бы «не подходит» на упражнение «жим
    ногами». ON DELETE SET NULL: удаление родовой записи не должно уничтожать
    частную.
    """

    __tablename__ = "equipment_items"

    equipment_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    name_ru: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(64), index=True)
    description: Mapped[str | None] = mapped_column(String(300))
    specializes: Mapped[str | None] = mapped_column(
        ForeignKey("equipment_items.equipment_id", ondelete="SET NULL"), index=True
    )
    manufacturer: Mapped[str | None] = mapped_column(String(120))
    model_name: Mapped[str | None] = mapped_column(String(120))
    attributes: Mapped[dict] = mapped_column(JSONB, default=dict)
    source: Mapped[str] = mapped_column(String(32), default="seed")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class EquipmentCapabilityLinkRow(Base):
    """Связь «оборудование умеет возможность».

    Отдельная таблица связей, потому что связь читается в обе стороны: «что
    умеет этот тренажёр» и «какое оборудование даёт наклон». JSONB-список
    отвечал бы только на первый вопрос.
    """

    __tablename__ = "equipment_item_capabilities"
    __table_args__ = (
        UniqueConstraint(
            "equipment_id", "capability_id", name="uq_equipment_capability"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    equipment_id: Mapped[str] = mapped_column(
        ForeignKey("equipment_items.equipment_id", ondelete="CASCADE"), index=True
    )
    capability_id: Mapped[str] = mapped_column(
        # RESTRICT: возможность, на которую ссылается оборудование, нельзя
        # удалить незаметно — иначе требование упражнения станет невыполнимым
        # молча.
        ForeignKey("equipment_capabilities.capability_id", ondelete="RESTRICT"),
        index=True,
    )


class EquipmentAliasRow(Base):
    """Синоним оборудования: значение источника или формулировка пользователя.

    Уникальность по (alias, equipment_id), а не по alias: «скамья» может
    означать и `flat_bench`, и `adjustable_bench`, и сопоставление такого
    синонима обязано быть неоднозначным явно, а не выбирать первый вариант.

    `match_mode` различает полное совпадение и совпадение по основе слова:
    значение каталога `body only` сопоставляется целиком, а во фразе анкеты
    «две гантели по 16 кг» нужна основа «гантел».
    """

    __tablename__ = "equipment_aliases"
    __table_args__ = (
        UniqueConstraint("alias", "equipment_id", name="uq_equipment_alias_target"),
        Index("ix_equipment_aliases_alias", "alias"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    equipment_id: Mapped[str] = mapped_column(
        ForeignKey("equipment_items.equipment_id", ondelete="CASCADE"), index=True
    )
    alias: Mapped[str] = mapped_column(String(120))
    match_mode: Mapped[str] = mapped_column(String(16), default="exact")
    source: Mapped[str] = mapped_column(String(32), default="seed")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ExerciseEquipmentRequirementRow(Base):
    """Нормализованная потребность упражнения в оборудовании.

    Ссылка на упражнение — каноническая пара (external_id, source), как в
    `exercise_media`: surrogate `exercises.id` не является каноническим
    идентификатором и меняется при пересоздании каталога.

    FK на упражнение нет по той же причине, что и в `exercise_media`: составной
    внешний ключ к (external_id, source) потребовал бы уникального индекса,
    который существует, но привязал бы требования к жизненному циклу конкретной
    строки каталога. Целостность проверяется метрикой orphan-ссылок в
    Knowledge Base Health, где она видна администратору, а не падает на импорте.

    Ровно одна из ссылок `equipment_id`/`capability_id` заполнена: это
    гарантируется CHECK-ограничением, а не только Pydantic-моделью — данные
    правятся и миграциями тоже.
    """

    __tablename__ = "exercise_equipment_requirements"
    __table_args__ = (
        UniqueConstraint(
            "exercise_external_id",
            "exercise_source",
            "equipment_id",
            "capability_id",
            "requirement",
            name="uq_exercise_requirement",
        ),
        CheckConstraint(
            "(equipment_id IS NULL) <> (capability_id IS NULL)",
            name="ck_exercise_requirement_target",
        ),
        CheckConstraint(
            "requirement <> 'alternative' OR alternative_group IS NOT NULL",
            name="ck_exercise_requirement_alternative_group",
        ),
        Index(
            "ix_exercise_requirements_exercise",
            "exercise_external_id",
            "exercise_source",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    exercise_external_id: Mapped[str] = mapped_column(String(128), index=True)
    exercise_source: Mapped[str] = mapped_column(String(64), default="leszavr/workout")
    equipment_id: Mapped[str | None] = mapped_column(
        ForeignKey("equipment_items.equipment_id", ondelete="RESTRICT"), index=True
    )
    capability_id: Mapped[str | None] = mapped_column(
        ForeignKey("equipment_capabilities.capability_id", ondelete="RESTRICT"),
        index=True,
    )
    requirement: Mapped[str] = mapped_column(String(16), default="required", index=True)
    alternative_group: Mapped[int | None] = mapped_column(Integer)
    confidence: Mapped[str] = mapped_column(String(16), default="confirmed", index=True)
    source: Mapped[str] = mapped_column(String(32), default="catalog_import")
    notes: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class UnmappedEquipmentValueRow(Base):
    """Значение оборудования источника, не получившее canonical ID.

    Существует, чтобы миграция не теряла информацию молча. Строка `other`
    в каталоге означает «оборудование нужно, но какое — не сказано»; выбросив
    её, система получила бы упражнение без требований и считала бы его
    выполнимым где угодно.
    """

    __tablename__ = "unmapped_equipment_values"
    __table_args__ = (
        UniqueConstraint(
            "exercise_external_id",
            "exercise_source",
            "raw_value",
            name="uq_unmapped_equipment_value",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    exercise_external_id: Mapped[str] = mapped_column(String(128), index=True)
    exercise_source: Mapped[str] = mapped_column(String(64), default="leszavr/workout")
    raw_value: Mapped[str] = mapped_column(String(120), index=True)
    reason: Mapped[str] = mapped_column(String(16), default="unmapped", index=True)
    notes: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ExerciseAlternativeRow(Base):
    """Альтернативное упражнение с явным типом замены.

    Тип замены хранится, а не вычисляется на чтении: «полная замена» и «похожее
    движение» — разные утверждения для пользователя, и решение о степени
    эквивалентности должно быть зафиксировано и проверяемо.

    Направление связи хранится обеими строками (A→B и B→A записываются
    отдельно), потому что степень замены не всегда симметрична: упражнение с
    меньшими требованиями к стабилизации является частичной заменой более
    сложного, но не наоборот.
    """

    __tablename__ = "exercise_alternatives"
    __table_args__ = (
        UniqueConstraint(
            "exercise_external_id",
            "exercise_source",
            "alternative_external_id",
            "alternative_source",
            name="uq_exercise_alternative_pair",
        ),
        CheckConstraint(
            "exercise_external_id <> alternative_external_id "
            "OR exercise_source <> alternative_source",
            name="ck_exercise_alternative_not_self",
        ),
        Index(
            "ix_exercise_alternatives_exercise",
            "exercise_external_id",
            "exercise_source",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    exercise_external_id: Mapped[str] = mapped_column(String(128), index=True)
    exercise_source: Mapped[str] = mapped_column(String(64), default="leszavr/workout")
    alternative_external_id: Mapped[str] = mapped_column(String(128), index=True)
    alternative_source: Mapped[str] = mapped_column(
        String(64), default="leszavr/workout"
    )
    substitution: Mapped[str] = mapped_column(String(16), default="similar", index=True)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    rationale: Mapped[dict] = mapped_column(JSONB, default=dict)
    source: Mapped[str] = mapped_column(String(32), default="derived")
    notes: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class EquipmentProfileRow(Base):
    """Профиль фактически доступного оборудования.

    Не привязан жёстко к пользователю: `owner_type` + `owner_ref` позволяют
    описать зал один раз и переиспользовать, а временный профиль («в отпуске,
    только резина») не затирает основной.

    `assume_unlisted_unavailable` отвечает, что означает отсутствие позиции в
    профиле. Для домашнего профиля, где человек перечислил всё, — «нет». Для
    зала, о котором известно только название, — «неизвестно»: придумывать
    отсутствие тренажёра нельзя.
    """

    __tablename__ = "equipment_profiles"
    __table_args__ = (
        UniqueConstraint("profile_key", name="uq_equipment_profile_key"),
        Index("ix_equipment_profiles_owner", "owner_type", "owner_ref"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_key: Mapped[str] = mapped_column(String(64))
    owner_type: Mapped[str] = mapped_column(String(16), default="user", index=True)
    owner_ref: Mapped[str | None] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(120))
    assume_unlisted_unavailable: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(32), default="admin")
    notes: Mapped[str | None] = mapped_column(String(300))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class EquipmentProfileItemRow(Base):
    """Позиция оборудования в профиле.

    `availability` хранит три состояния, включая `unknown`: «пользователь не
    сказал» и «пользователь сказал, что нет» — разные факты, и первый не должен
    исключать упражнения.

    `source_ref` хранит ссылку на источник факта, например ключ фотографии в
    объектном хранилище. Так путь «фото → кандидат → подтверждение человеком»
    выражается состоянием записи (`source=photo`, `confidence=inferred` →
    `confidence=confirmed`), а не отдельной подсистемой распознавания.
    """

    __tablename__ = "equipment_profile_items"
    __table_args__ = (
        UniqueConstraint("profile_id", "equipment_id", name="uq_profile_equipment"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("equipment_profiles.id", ondelete="CASCADE"), index=True
    )
    equipment_id: Mapped[str] = mapped_column(
        ForeignKey("equipment_items.equipment_id", ondelete="RESTRICT"), index=True
    )
    quantity: Mapped[int | None] = mapped_column(Integer)
    availability: Mapped[str] = mapped_column(String(16), default="available")
    confidence: Mapped[str] = mapped_column(String(16), default="confirmed")
    extra_capabilities: Mapped[list] = mapped_column(JSONB, default=list)
    source: Mapped[str] = mapped_column(String(32), default="admin")
    source_ref: Mapped[str | None] = mapped_column(String(300))
    notes: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class TelegramSessionRow(Base):
    """Серверное состояние диалога Telegram-анкеты.

    Состояние анкеты хранится в RU, а не в Redis Gateway: Gateway размещается в
    EU, и накопленные ответы (имя, возраст, ограничения движений, рекомендации
    врача) там хранить нельзя.

    Отдельная таблица, а не колонки в `profiles`: сессия существует до профиля.
    Брошенный на первом вопросе диалог не должен появляться в списке анкет
    администратора и в аналитике.
    """

    __tablename__ = "telegram_sessions"
    __table_args__ = (
        # У пользователя Telegram один активный диалог с ботом. Без ограничения
        # параллельные обновления создали бы две сессии, и анкета раздвоилась бы.
        UniqueConstraint("telegram_user_id", name="uq_telegram_session_user"),
        Index(
            "ix_telegram_sessions_profile",
            "profile_id",
            postgresql_where=text("profile_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_user_id: Mapped[str] = mapped_column(String(64))
    chat_id: Mapped[str | None] = mapped_column(String(64))
    username: Mapped[str | None] = mapped_column(String(64))
    # Идентификатор вопроса либо служебный экран (review/confirm). Значения
    # приходят из декларации анкеты: дублировать её в схеме БД значит с ней
    # разойтись.
    position: Mapped[str | None] = mapped_column(String(64))
    editing_question: Mapped[str | None] = mapped_column(String(64))
    # Черновик профиля с первого ответа: иначе прерванная анкета теряется целиком.
    draft: Mapped[dict | None] = mapped_column(JSONB)
    profile_id: Mapped[str | None] = mapped_column(String(64))
    # Идемпотентность: Telegram переотправляет неподтверждённое обновление, а
    # Gateway повторяет запрос при таймауте. Хранится и отданный ответ — повтор
    # обязан вернуть тот же вид, иначе Gateway нечего показать.
    last_update_id: Mapped[int | None] = mapped_column(BigInteger)
    last_view: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
