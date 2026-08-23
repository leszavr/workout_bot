"""Unit-тесты хеширования паролей администраторов.

Проверяется то, что легко сломать незаметно: уникальность соли, устойчивость
проверки к мусору вместо хеша, отказ при отсутствии пароля и соблюдение
политики длины.

Без БД и без сети.
"""
from __future__ import annotations

import pytest

from src.domain.auth import MIN_PASSWORD_LENGTH
from src.infrastructure.auth.passwords import (
    ALGORITHM,
    MAX_PASSWORD_LENGTH,
    PasswordPolicyError,
    generate_temporary_password,
    hash_password,
    validate_password,
    verify_password,
)

GOOD_PASSWORD = "correct-horse-battery-staple"


def test_hash_has_self_describing_format():
    """Параметры лежат в самом хеше — иначе их нельзя усилить без миграции."""
    stored = hash_password(GOOD_PASSWORD)
    parts = stored.split("$")

    assert parts[0] == ALGORITHM
    assert len(parts) == 6
    # Ни при каких условиях в строке не должно быть самого пароля.
    assert GOOD_PASSWORD not in stored


def test_correct_password_verifies():
    assert verify_password(GOOD_PASSWORD, hash_password(GOOD_PASSWORD)) is True


def test_wrong_password_rejected():
    assert verify_password("another-password-x", hash_password(GOOD_PASSWORD)) is False


def test_salt_is_unique_per_hash():
    """Одинаковые пароли обязаны давать разные хеши."""
    assert hash_password(GOOD_PASSWORD) != hash_password(GOOD_PASSWORD)


def test_missing_hash_never_authenticates():
    """Пользователь без пароля (только внешний вход) не входит по паролю."""
    assert verify_password(GOOD_PASSWORD, None) is False
    assert verify_password(GOOD_PASSWORD, "") is False


def test_empty_password_rejected():
    assert verify_password("", hash_password(GOOD_PASSWORD)) is False


@pytest.mark.parametrize(
    "stored",
    [
        "garbage",
        "scrypt$only$three",
        "bcrypt$16384$8$1$c2FsdA==$aGFzaA==",  # чужой алгоритм
        "scrypt$notanumber$8$1$c2FsdA==$aGFzaA==",
        "scrypt$16384$8$1$!!!$!!!",  # не base64
    ],
)
def test_broken_hash_is_rejected_without_raising(stored: str):
    """Мусор в колонке не должен приводить ни к падению, ни к входу."""
    assert verify_password(GOOD_PASSWORD, stored) is False


def test_short_password_rejected():
    with pytest.raises(PasswordPolicyError):
        hash_password("a" * (MIN_PASSWORD_LENGTH - 1))


def test_minimum_length_accepted():
    password = "a" * MIN_PASSWORD_LENGTH
    assert verify_password(password, hash_password(password)) is True


def test_overlong_password_rejected():
    """Без верхней границы длинный пароль превращается в способ съесть CPU."""
    with pytest.raises(PasswordPolicyError):
        hash_password("a" * (MAX_PASSWORD_LENGTH + 1))


def test_padded_password_rejected():
    with pytest.raises(PasswordPolicyError):
        validate_password(" " + GOOD_PASSWORD)


def test_temporary_password_satisfies_policy():
    temporary = generate_temporary_password()

    validate_password(temporary)
    assert verify_password(temporary, hash_password(temporary)) is True


def test_temporary_passwords_are_unique():
    assert generate_temporary_password() != generate_temporary_password()


def test_temporary_password_avoids_ambiguous_characters():
    """Пароль диктуют голосом: 0/O и 1/l/I путаются."""
    for _ in range(20):
        assert not set(generate_temporary_password()) & set("0O1lI")
