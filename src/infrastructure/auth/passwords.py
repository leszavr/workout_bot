"""Хеширование паролей администраторов.

Используется `hashlib.scrypt` из стандартной библиотеки: memory-hard функция,
пригодная для паролей. Новая зависимость не добавляется намеренно — bcrypt и
argon2 в объявленных зависимостях проекта отсутствуют, а scrypt доступен
всегда.

Формат хранения (одна строка, самодостаточная — параметры внутри):

    scrypt$<n>$<r>$<p>$<salt_b64>$<hash_b64>

Параметры лежат в самой строке, поэтому их можно будет усилить в будущем, не
ломая уже сохранённые пароли: проверка читает параметры из хеша, а не из
текущих константы.

Сравнение выполняется через `hmac.compare_digest` — константное по времени.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

from src.domain.auth import MIN_PASSWORD_LENGTH

ALGORITHM = "scrypt"
# n=2**15 (32768), r=8, p=1 — ~32 МБ памяти на проверку.
SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 1
SALT_BYTES = 16
KEY_BYTES = 32
# Верхняя граница: без неё длинный пароль превращается в способ съесть CPU.
MAX_PASSWORD_LENGTH = 256

# maxmem должен вмещать 128 * n * r байт, иначе scrypt откажется считать.
_MAXMEM = 128 * SCRYPT_N * SCRYPT_R * 2


class PasswordPolicyError(ValueError):
    """Пароль не соответствует требованиям."""


def validate_password(password: str) -> None:
    """Проверяет пароль до хеширования. Бросает PasswordPolicyError."""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise PasswordPolicyError(
            f"Пароль должен быть не короче {MIN_PASSWORD_LENGTH} символов"
        )
    if len(password) > MAX_PASSWORD_LENGTH:
        raise PasswordPolicyError(
            f"Пароль должен быть не длиннее {MAX_PASSWORD_LENGTH} символов"
        )
    if password.strip() != password:
        raise PasswordPolicyError(
            "Пароль не должен начинаться или заканчиваться пробелом"
        )


def hash_password(password: str) -> str:
    """Возвращает самодостаточную строку хеша. Пароль не логируется."""
    validate_password(password)
    salt = secrets.token_bytes(SALT_BYTES)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=KEY_BYTES,
        maxmem=_MAXMEM,
    )
    return "$".join(
        [
            ALGORITHM,
            str(SCRYPT_N),
            str(SCRYPT_R),
            str(SCRYPT_P),
            base64.b64encode(salt).decode("ascii"),
            base64.b64encode(digest).decode("ascii"),
        ]
    )


def verify_password(password: str, stored: str | None) -> bool:
    """Проверяет пароль против сохранённого хеша.

    Возвращает False на любом некорректном или отсутствующем хеше: у
    пользователя без пароля (только внешний вход) войти по паролю нельзя.
    Исключения наружу не выбрасываются, чтобы форма входа не различала
    «нет пароля» и «пароль неверный».
    """
    if not stored or not password:
        return False
    try:
        algorithm, n_raw, r_raw, p_raw, salt_b64, hash_b64 = stored.split("$")
        if algorithm != ALGORITHM:
            return False
        n, r, p = int(n_raw), int(r_raw), int(p_raw)
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
    except (ValueError, TypeError):
        return False

    if len(password) > MAX_PASSWORD_LENGTH:
        return False
    try:
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=n,
            r=r,
            p=p,
            dklen=len(expected),
            maxmem=128 * n * r * 2,
        )
    except ValueError:
        return False
    return hmac.compare_digest(actual, expected)


def generate_temporary_password(length: int = 16) -> str:
    """Временный пароль для сброса администратором.

    Длина заведомо больше минимальной, алфавит без похожих символов
    (0/O, 1/l/I), чтобы пароль можно было продиктовать без ошибок.
    """
    alphabet = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))
