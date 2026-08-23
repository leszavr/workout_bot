"""Домен авторизации админ-панели: пользователь, роль, внешняя идентичность.

Разделение пользователя и способа входа сделано намеренно. `AdminUser` — это
человек с ролью и доступом. `AdminIdentity` — конкретный способ, которым он
подтверждает личность у внешнего провайдера (Яндекс, VK, MAX).

Благодаря этому добавление входа через внешнего провайдера не потребует
менять таблицу пользователей и переписывать проверку прав: достаточно создать
запись идентичности и связать её с существующим пользователем. Один человек
может иметь и пароль, и несколько внешних идентичностей.

Пароль умышленно НЕ является идентичностью: это атрибут самого пользователя
(`password_hash`), потому что хранится и проверяется локально. Внешние
провайдеры пароля не имеют — у таких пользователей `password_hash=None`.

Сам хеш пароля наружу не отдаётся: DTO ответов API его не содержат.
"""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

# Минимальная длина пароля. Ниже этого значения сервер пароль не принимает.
MIN_PASSWORD_LENGTH = 10


class AdminRole(StrEnum):
    """Роль в админ-панели.

    ADMIN — полный доступ, включая управление пользователями.
    VIEWER — только чтение: любые изменяющие операции запрещены на сервере,
    а не только скрыты в интерфейсе.
    """

    ADMIN = "admin"
    VIEWER = "viewer"


class AuthProvider(StrEnum):
    """Способ подтверждения личности.

    PASSWORD — локальный пароль (хеш на пользователе).
    Остальные — внешние провайдеры. Значения заведены заранее, чтобы схема
    и контракт API не менялись при подключении конкретного провайдера;
    сами OAuth-флоу пока не реализованы.
    """

    PASSWORD = "password"
    YANDEX = "yandex"
    VK = "vk"
    MAX = "max"


# Провайдеры, для которых нужен внешний OAuth-флоу (пароль сюда не входит).
EXTERNAL_AUTH_PROVIDERS = frozenset(
    {AuthProvider.YANDEX, AuthProvider.VK, AuthProvider.MAX}
)


class AdminUser(BaseModel):
    """Пользователь админ-панели.

    `password_hash` хранится здесь, но никогда не попадает в API-ответы:
    роуты используют отдельные DTO без этого поля.
    """

    model_config = ConfigDict(extra="forbid")

    id: int | None = None
    login: str = Field(min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9._-]+$")
    display_name: str | None = Field(default=None, max_length=120)
    role: AdminRole = AdminRole.VIEWER
    password_hash: str | None = Field(default=None, max_length=255)
    # Пароль выдан администратором как временный: до его смены доступ к
    # остальному API закрыт.
    must_change_password: bool = False
    is_active: bool = True
    last_login_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def can_write(self) -> bool:
        return self.role is AdminRole.ADMIN


class AdminIdentity(BaseModel):
    """Связь пользователя с аккаунтом внешнего провайдера.

    `provider_user_id` — идентификатор пользователя на стороне провайдера.
    Токены доступа здесь не хранятся: они нужны только на время флоу.
    """

    model_config = ConfigDict(extra="forbid")

    id: int | None = None
    user_id: int
    provider: AuthProvider
    provider_user_id: str = Field(min_length=1, max_length=191)
    created_at: datetime | None = None
