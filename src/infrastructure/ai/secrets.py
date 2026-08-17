"""SecretStore: защищённое хранение API-ключей AI-эндпоинтов.

Требования:
- ключ шифруется at rest (Fernet, AES-128-CBC + HMAC);
- ключ никогда не возвращается API/фронтенду — только masked-представление;
- ключ не попадает в логи и audit-события;
- ротация = атомарная замена значения по ссылке;
- хранилище заменяемо на внешний secrets manager (интерфейс SecretStore).

Ключ шифрования берётся из ``AI_SECRETS_KEY``; если он не задан — выводится
из ``JWT_SECRET`` (dev-режим). В production рекомендуется отдельный ключ.
"""
from __future__ import annotations

import abc
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.domain.ai.errors import AIConfigurationError
from src.errors import ProfilePersistenceError
from src.infrastructure.config import AI_SECRETS_KEY, JWT_SECRET


def _derive_fernet_key() -> bytes:
    """Выводит Fernet-ключ из конфигурации (детерминированно)."""
    source = AI_SECRETS_KEY or JWT_SECRET
    if not source:
        raise AIConfigurationError(
            "AI_SECRETS_KEY (или JWT_SECRET) не задан: невозможно шифровать AI-секреты"
        )
    digest = hashlib.sha256(source.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def mask_secret(secret: str) -> str:
    """Маскирует секрет для отображения: sk-****...1234."""
    if not secret:
        return ""
    visible = secret[-4:] if len(secret) >= 8 else "*"
    return f"****{visible}"


class SecretStore(abc.ABC):
    """Интерфейс хранилища секретов (заменяем на внешний secrets manager)."""

    @abc.abstractmethod
    async def put(self, reference: str, secret: str) -> None:
        """Создаёт или атомарно заменяет секрет по ссылке (ротация)."""

    @abc.abstractmethod
    async def get(self, reference: str) -> str | None:
        """Возвращает расшифрованный секрет или None."""

    @abc.abstractmethod
    async def delete(self, reference: str) -> None: ...

    @abc.abstractmethod
    async def exists(self, reference: str) -> bool: ...


class EncryptedDbSecretStore(SecretStore):
    """Секреты в PostgreSQL (таблица ai_secrets), шифрованные Fernet."""

    def __init__(self, session_factory: async_sessionmaker, table) -> None:
        self._sessions = session_factory
        self._table = table
        self._fernet = Fernet(_derive_fernet_key())

    async def put(self, reference: str, secret: str) -> None:
        token = self._fernet.encrypt(secret.encode("utf-8")).decode("ascii")
        try:
            async with self._sessions() as session:
                async with session.begin():
                    stmt = pg_insert(self._table).values(
                        reference=reference, encrypted_value=token
                    )
                    stmt = stmt.on_conflict_do_update(
                        index_elements=[self._table.c.reference],
                        set_={"encrypted_value": stmt.excluded.encrypted_value},
                    )
                    await session.execute(stmt)
        except SQLAlchemyError as exc:
            raise ProfilePersistenceError(f"Не удалось сохранить секрет: {exc}") from exc

    async def get(self, reference: str) -> str | None:
        try:
            async with self._sessions() as session:
                row = (
                    await session.execute(
                        select(self._table.c.encrypted_value).where(
                            self._table.c.reference == reference
                        )
                    )
                ).scalar_one_or_none()
        except SQLAlchemyError as exc:
            raise ProfilePersistenceError(f"Не удалось прочитать секрет: {exc}") from exc
        if row is None:
            return None
        try:
            return self._fernet.decrypt(row.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise AIConfigurationError(
                "Секрет повреждён или ключ шифрования изменён"
            ) from exc

    async def delete(self, reference: str) -> None:
        try:
            async with self._sessions() as session:
                async with session.begin():
                    await session.execute(
                        delete(self._table).where(self._table.c.reference == reference)
                    )
        except SQLAlchemyError as exc:
            raise ProfilePersistenceError(f"Не удалось удалить секрет: {exc}") from exc

    async def exists(self, reference: str) -> bool:
        try:
            async with self._sessions() as session:
                row = (
                    await session.execute(
                        select(self._table.c.reference).where(
                            self._table.c.reference == reference
                        )
                    )
                ).scalar_one_or_none()
        except SQLAlchemyError as exc:
            raise ProfilePersistenceError(f"Ошибка проверки секрета: {exc}") from exc
        return row is not None
