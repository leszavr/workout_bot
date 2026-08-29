"""Удаление объектов с зависимостями: общий контракт для всех разделов админки.

Появилось в AI-конфигурации (сервисы, подключения, модели, инструкции) и теперь
используется также для анкет и программ. Смысл один: база не должна быть
последней инстанцией, объясняющей администратору, почему удаление невозможно.
Часть связей в проекте логические (`ai_task_configs.prompt_version`,
`workout_programs.profile_id`), внешних ключей на них нет, и `DELETE` прошёл бы
успешно, оставив систему в противоречивом состоянии.

Блокер — машиночитаемая запись, а не строка текста: интерфейс перечисляет, что
именно мешает, и подсказывает следующий шаг.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from src.errors import WorkoutBotError


class DeleteBlockedError(WorkoutBotError):
    """Удаление заблокировано зависимостями.

    Несёт список блокеров, чтобы UI мог объяснить причину, а не показывать
    текст ошибки базы данных.
    """

    def __init__(self, message: str, blockers: list[dict]) -> None:
        super().__init__(message)
        self.blockers = blockers


@dataclass
class DeleteDependencies:
    """Что мешает удалить объект.

    Пустой список означает «удалять безопасно»: отсутствие блокеров — это
    результат проверки, а не отсутствие проверки.
    """

    blockers: list[dict] = field(default_factory=list)

    @property
    def safe(self) -> bool:
        return not self.blockers

    def describe(self) -> str:
        return "; ".join(b["detail"] for b in self.blockers)
