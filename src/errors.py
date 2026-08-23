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


class FSMStorageError(WorkoutBotError):
    """Хранилище runtime-состояния анкеты (FSM) недоступно."""


class NotificationError(WorkoutBotError):
    """Не удалось доставить уведомление администратору."""


class ProgramGenerationError(WorkoutBotError):
    """Не удалось сгенерировать программу (пустой пул, неверный профиль)."""


class GenerationFailedError(ProgramGenerationError):
    """Отказ генерации с машиночитаемым кодом (Phase 1.2-C).

    Оркестратор — единственная точка генерации, поэтому наружу он отдаёт не
    внутренние исключения AI-контура, а стабильный код отказа. Вызывающий слой
    (HTTP, Telegram) выбирает реакцию по коду и не разбирает типы исключений
    AI Gateway; operational-запись получает ту же классификацию.
    """

    def __init__(self, message: str, *, generation_error_code: str) -> None:
        super().__init__(message)
        self.generation_error_code = generation_error_code


class GenerationAlreadyRunningError(WorkoutBotError):
    """Та же логическая генерация уже выполняется.

    Отдельный тип, потому что это не ошибка: повторный запрос корректно
    отклонён серверной идемпотентностью, а не провалился.
    """


class IdempotencyKeyConflictError(WorkoutBotError):
    """Клиентский idempotency key повторно использован с другими параметрами.

    Не наследует `ProgramGenerationError`: генерация не запускалась и не
    падала — отклонён сам запрос. Ключ — это обещание вызывающей стороны «это
    тот же запрос»; если параметры отличаются, вернуть результат прошлого
    запроса нельзя (он собран другим генератором), а запустить новую генерацию
    под тем же ключом — значит разрушить идемпотентность. Поэтому оба варианта
    отвергаются, и конфликт разрешает клиент.
    """


class ProgramValidationError(WorkoutBotError):
    """Программа не прошла валидацию."""


class ProgramPersistenceError(WorkoutBotError):
    """Не удалось сохранить/прочитать программу в хранилище."""


class MediaStorageError(WorkoutBotError):
    """Ошибка object storage / работы с медиа-ассетами упражнений."""


class ProgramDeliveryError(WorkoutBotError):
    """Не удалось доставить программу пользователю."""


class HtmlRenderError(WorkoutBotError):
    """Не удалось выполнить рендеринг HTML программы."""
