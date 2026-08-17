"""Единая точка ошибок приложения.

Telegram handlers и application-сервисы не должны оперировать
инфраструктурными исключениями напрямую: все ошибки нормализуются
в эти типы, а пользователю показываются только безопасные сообщения.
"""
from __future__ import annotations


class WorkoutBotError(Exception):
    """Базовая ошибка приложения."""


class QuestionnaireValidationError(WorkoutBotError):
    """Ответ пользователя не прошёл валидацию.

    ``user_message`` — безопасный текст, который можно показать в Telegram.
    """

    def __init__(self, user_message: str) -> None:
        super().__init__(user_message)
        self.user_message = user_message


class ProfilePersistenceError(WorkoutBotError):
    """Не удалось сохранить/прочитать профиль в хранилище."""


class FileStorageError(WorkoutBotError):
    """Ошибка файлового хранилища (лимит размера, количества, запись)."""


class NotificationError(WorkoutBotError):
    """Не удалось доставить уведомление администратору."""
