"""Persistent состояние генерации программы (Phase 1.2-B).

`GenerationJob` — operational-запись об одной логической генерации. Это не
замена `WorkoutProgram`: программа существует только при успехе, job существует
всегда и отвечает на вопрос «что происходило с этим запросом генерации».

Состояния:

    PENDING → RUNNING → SUCCEEDED
                     └→ FAILED

`RETRY_WAIT` здесь сознательно нет: планировщика повторов ещё не существует
(Phase 1.2-D), а статус без обработчика оставлял бы job в состоянии, из
которого его никто не выводит.

Что в job НЕ хранится: prompt, ответ провайдера, ключи, заголовки авторизации
и персональные данные. Только стабильный код ошибки и короткое безопасное
описание; подробности остаются в существующем logging/audit-контуре.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from src.domain.ai.errors import (
    AIConfigurationError,
    AIConnectionError,
    AIError,
    AIInvalidResponseError,
    AIRateLimitError,
    AITimeoutError,
    AIUnsupportedProtocolError,
)
from src.domain.enums import GenerationJobStatus
from src.errors import (
    ProgramGenerationError,
    ProgramPersistenceError,
    ProgramValidationError,
)

MAX_ERROR_MESSAGE_LENGTH = 500
IDEMPOTENCY_KEY_MAX_LENGTH = 191


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


class GenerationTrigger(StrEnum):
    """Бизнес-событие, из которого выросла генерация.

    Триггер входит в idempotency key: автоматическая генерация после
    подтверждения анкеты и явный запрос администратора — разные логические
    операции, и повтор одной не должен подавлять другую.
    """

    AUTO_FINALIZATION = "auto_finalization"
    ADMIN_REQUEST = "admin_request"


class GenerationErrorCode(StrEnum):
    """Стабильные коды ошибок генерации.

    Значения попадают в БД и в будущую админку, поэтому переименование кода —
    breaking change, а не косметика.
    """

    PROFILE_NOT_FOUND = "profile_not_found"
    VALIDATION_FAILED = "validation_failed"
    AI_NOT_CONFIGURED = "ai_not_configured"
    AI_UNSUPPORTED_PROTOCOL = "ai_unsupported_protocol"
    AI_TIMEOUT = "ai_timeout"
    AI_CONNECTION_FAILED = "ai_connection_failed"
    AI_RATE_LIMITED = "ai_rate_limited"
    AI_INVALID_RESPONSE = "ai_invalid_response"
    AI_RUNTIME_FAILURE = "ai_runtime_failure"
    GENERATION_FAILED = "generation_failed"
    PERSISTENCE_FAILED = "persistence_failed"
    UNEXPECTED_ERROR = "unexpected_error"


class GenerationErrorKind(StrEnum):
    """Класс ошибки. Retry на этом этапе НЕ реализуется.

    Класс нужен, чтобы будущий retry-контур (Phase 1.2-D) не переклассифицировал
    исторические записи: решение «повторяемо ли это» принимается по коду,
    сохранённому в момент отказа.
    """

    NON_RETRYABLE = "non_retryable"
    TRANSIENT = "transient"


# Конфигурация, валидация и отсутствующие данные повторной попыткой не
# исправляются; сетевые/провайдерские сбои — исправляются.
_NON_RETRYABLE_CODES = frozenset(
    {
        GenerationErrorCode.PROFILE_NOT_FOUND,
        GenerationErrorCode.VALIDATION_FAILED,
        GenerationErrorCode.AI_NOT_CONFIGURED,
        GenerationErrorCode.AI_UNSUPPORTED_PROTOCOL,
        GenerationErrorCode.AI_INVALID_RESPONSE,
        GenerationErrorCode.GENERATION_FAILED,
    }
)

# Разрешённые переходы. Всё, чего здесь нет, запрещено, включая
# SUCCEEDED → RUNNING, SUCCEEDED → FAILED и FAILED → RUNNING.
# FAILED → PENDING/RUNNING зарезервировано для retry-контура Phase 1.2-D и
# сейчас не разрешается: у неуспешной попытки нет обработчика, который довёл
# бы её до конца.
ALLOWED_TRANSITIONS: dict[GenerationJobStatus, frozenset[GenerationJobStatus]] = {
    GenerationJobStatus.PENDING: frozenset({GenerationJobStatus.RUNNING}),
    GenerationJobStatus.RUNNING: frozenset(
        {GenerationJobStatus.SUCCEEDED, GenerationJobStatus.FAILED}
    ),
    GenerationJobStatus.SUCCEEDED: frozenset(),
    GenerationJobStatus.FAILED: frozenset(),
}

TERMINAL_STATUSES = frozenset(
    {GenerationJobStatus.SUCCEEDED, GenerationJobStatus.FAILED}
)
ACTIVE_STATUSES = frozenset({GenerationJobStatus.PENDING, GenerationJobStatus.RUNNING})


class GenerationJobTransitionError(Exception):
    """Попытка выполнить запрещённый переход состояния job."""

    def __init__(
        self, current: GenerationJobStatus, target: GenerationJobStatus
    ) -> None:
        super().__init__(
            f"Недопустимый переход состояния генерации: {current.value} → {target.value}"
        )
        self.current = current
        self.target = target


def can_transition(
    current: GenerationJobStatus, target: GenerationJobStatus
) -> bool:
    return target in ALLOWED_TRANSITIONS[current]


def ensure_transition(
    current: GenerationJobStatus, target: GenerationJobStatus
) -> None:
    if not can_transition(current, target):
        raise GenerationJobTransitionError(current, target)


def error_kind(code: GenerationErrorCode | str) -> GenerationErrorKind:
    """Класс ошибки по её коду. Неизвестный код считается transient.

    Неизвестный код появляется только у записей, сделанных другой версией
    кода; считать его окончательным отказом хуже, чем позволить будущему
    retry-контуру перепроверить.
    """
    try:
        normalized = GenerationErrorCode(code)
    except ValueError:
        return GenerationErrorKind.TRANSIENT
    return (
        GenerationErrorKind.NON_RETRYABLE
        if normalized in _NON_RETRYABLE_CODES
        else GenerationErrorKind.TRANSIENT
    )


def classify_error(exc: BaseException) -> GenerationErrorCode:
    """Относит исключение генерации к стабильному коду ошибки."""
    if isinstance(exc, ProgramValidationError):
        return GenerationErrorCode.VALIDATION_FAILED
    if isinstance(exc, ProgramPersistenceError):
        return GenerationErrorCode.PERSISTENCE_FAILED
    if isinstance(exc, AITimeoutError):
        return GenerationErrorCode.AI_TIMEOUT
    if isinstance(exc, AIRateLimitError):
        return GenerationErrorCode.AI_RATE_LIMITED
    if isinstance(exc, AIConnectionError):
        return GenerationErrorCode.AI_CONNECTION_FAILED
    if isinstance(exc, AIInvalidResponseError):
        return GenerationErrorCode.AI_INVALID_RESPONSE
    if isinstance(exc, AIUnsupportedProtocolError):
        return GenerationErrorCode.AI_UNSUPPORTED_PROTOCOL
    if isinstance(exc, AIConfigurationError):
        return GenerationErrorCode.AI_NOT_CONFIGURED
    if isinstance(exc, AIError):
        return GenerationErrorCode.AI_RUNTIME_FAILURE
    if isinstance(exc, ProgramGenerationError):
        return GenerationErrorCode.GENERATION_FAILED
    return GenerationErrorCode.UNEXPECTED_ERROR


# Текст ошибки провайдера может содержать эхо запроса. Хранить его целиком в
# operational-записи нельзя: она читается администратором и попадает в бэкапы.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]+"),
    re.compile(r"(?i)\bsk-[A-Za-z0-9._\-]{8,}"),
    re.compile(
        r"(?i)\b(api[_-]?key|authorization|token|secret|password)\b\s*[:=]\s*\S+"
    ),
)


def safe_error_message(exc: BaseException | str) -> str:
    """Короткое безопасное описание отказа: без секретов и без промпта."""
    raw = exc if isinstance(exc, str) else str(exc)
    cleaned = " ".join(raw.split())
    for pattern in _SECRET_PATTERNS:
        cleaned = pattern.sub("[redacted]", cleaned)
    return cleaned[:MAX_ERROR_MESSAGE_LENGTH]


def build_idempotency_key(
    *, profile_id: str, trigger: GenerationTrigger, attempt: int
) -> str:
    """Стабильный ключ одной логической генерации.

    Одна логическая генерация — это одна попытка построить программу для
    конкретного профиля в рамках конкретного бизнес-события. `attempt`
    вычисляется из числа уже исчерпанных попыток того же триггера, поэтому
    ключ детерминирован: повторный клик и параллельный дубликат дают тот же
    ключ, а законная повторная генерация — следующий.
    """
    if attempt < 1:
        raise ValueError("attempt должен быть >= 1")
    key = f"{trigger.value}:{profile_id}:{attempt}"
    if len(key) > IDEMPOTENCY_KEY_MAX_LENGTH:
        raise ValueError("idempotency key превышает допустимую длину")
    return key


def build_client_idempotency_key(*, profile_id: str, client_key: str) -> str:
    """Ключ логической генерации, заданный вызывающей стороной.

    Нужен там, где клиент может повторить один и тот же запрос (сетевой
    ретрай, повторная отправка формы) и обязан получить тот же результат.
    Ключ ограничен профилем: чужой запрос не может занять ключ соседнего
    профиля. Серверная защита от дубликатов на этот ключ не опирается — она
    работает и без него.
    """
    normalized = " ".join(client_key.split())
    if not normalized:
        raise ValueError("client idempotency key пуст")
    key = f"client:{profile_id}:{normalized}"
    if len(key) > IDEMPOTENCY_KEY_MAX_LENGTH:
        raise ValueError("idempotency key превышает допустимую длину")
    return key


class GenerationJob(BaseModel):
    """Operational-запись о генерации. Персональных данных не содержит."""

    model_config = ConfigDict(extra="forbid")

    id: int | None = None
    job_id: str = Field(min_length=1, max_length=64)
    profile_id: str = Field(min_length=1, max_length=64)
    idempotency_key: str = Field(min_length=1, max_length=IDEMPOTENCY_KEY_MAX_LENGTH)
    trigger: GenerationTrigger
    requested_generator: str = Field(max_length=32)
    status: GenerationJobStatus = GenerationJobStatus.PENDING
    attempts: int = Field(default=0, ge=0)
    program_id: str | None = Field(default=None, max_length=64)
    program_version: int | None = Field(default=None, ge=1)
    last_error_code: str | None = Field(default=None, max_length=64)
    last_error_message: str | None = Field(
        default=None, max_length=MAX_ERROR_MESSAGE_LENGTH
    )
    created_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None

    @property
    def is_active(self) -> bool:
        return self.status in ACTIVE_STATUSES

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    def error_kind(self) -> GenerationErrorKind | None:
        if self.last_error_code is None:
            return None
        return error_kind(self.last_error_code)

    # --- переходы -------------------------------------------------------------
    # Методы меняют только in-memory состояние; персистентность и проверку
    # перехода в БД выполняет репозиторий в одной транзакции.

    def start(self) -> None:
        ensure_transition(self.status, GenerationJobStatus.RUNNING)
        self.status = GenerationJobStatus.RUNNING
        self.attempts += 1
        self.started_at = _utcnow()

    def succeed(self, *, program_id: str, program_version: int) -> None:
        ensure_transition(self.status, GenerationJobStatus.SUCCEEDED)
        self.status = GenerationJobStatus.SUCCEEDED
        self.program_id = program_id
        self.program_version = program_version
        self.last_error_code = None
        self.last_error_message = None
        self.completed_at = _utcnow()

    def fail(self, *, error_code: GenerationErrorCode | str, message: str) -> None:
        ensure_transition(self.status, GenerationJobStatus.FAILED)
        self.status = GenerationJobStatus.FAILED
        code = (
            error_code.value
            if isinstance(error_code, GenerationErrorCode)
            else error_code
        )
        self.last_error_code = code[:64]
        self.last_error_message = safe_error_message(message)
        self.completed_at = _utcnow()
