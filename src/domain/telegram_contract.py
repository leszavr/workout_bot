"""Контракт Backend ↔ Telegram Gateway.

Gateway после выноса за сетевую границу не знает ни структуры анкеты, ни правил
валидации, ни порядка вопросов. Он передаёт сюда факт «пользователь сделал X» и
получает готовое к отображению описание того, что показать. Поэтому контракт
описан как server-driven view, а не как набор данных предметной области: иначе
Gateway пришлось бы снова знать, что такое вопрос, вариант ответа и переход.

Типы лежат в домене, а не в слое API, потому что у контракта две стороны в одном
репозитории: Backend их отдаёт, Gateway принимает. Общий тип — единственный
способ не разойтись молча.

Персональных данных в этих типах нет по построению: наружу уходит уже
отрендеренный текст, а не ответы пользователя.
"""
from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

# Версия контракта Gateway ↔ Backend. Меняется только при несовместимом
# изменении этих типов; Backend обязан поддерживать её вместе с предыдущей до
# обновления Gateway (expand/contract).
TELEGRAM_CONTRACT_VERSION = 1


class TelegramUpdateKind(StrEnum):
    """Что именно сделал пользователь.

    Gateway различает эти четыре случая, потому что их различает Telegram, а не
    потому что различает анкета. Смысл события определяет Backend.
    """

    COMMAND = "command"
    TEXT = "text"
    CALLBACK = "callback"
    # Фото приходит отдельным multipart-запросом: байты в JSON не помещаются
    # без base64, а раздувать тело на треть ради единообразия смысла нет.
    PHOTO = "photo"


class TelegramButton(BaseModel):
    """Кнопка. `action` уходит в callback_data и возвращается как есть."""

    model_config = ConfigDict(frozen=True)

    label: str = Field(max_length=64)
    action: str = Field(max_length=64)


class TelegramMessageKind(StrEnum):
    """Как показать текст.

    `EDIT` нужен там, где сейчас правится сообщение с вопросом: без него после
    ответа в чате оставался бы висеть вопрос с активными кнопками, на которые
    можно нажать второй раз.
    """

    SEND = "send"
    EDIT = "edit"


class TelegramDocument(BaseModel):
    """Текстовый файл, приложенный к сообщению.

    Нужен уведомлению администратора: вместе со сводкой ему уходит JSON-профиль
    заявки. Содержимое передаётся как текст, а не base64 — JSON и есть текст, и
    кодирование раздуло бы тело на треть без пользы.

    Программа тренировок через это поле не передаётся: её файл запрашивается
    отдельным вызовом по идентификатору доставки, иначе список заданий тянул бы
    за собой все файлы сразу.
    """

    model_config = ConfigDict(frozen=True)

    filename: str = Field(max_length=255)
    text_content: str
    caption: str = Field(default="", max_length=1024)


class TelegramMessage(BaseModel):
    """Одно сообщение к отображению."""

    kind: TelegramMessageKind = TelegramMessageKind.SEND
    text: str = Field(max_length=4096)
    # HTML используется только для сводки анкеты; остальной текст без разметки,
    # чтобы случайный символ из ответа пользователя не ломал сообщение.
    html: bool = False
    buttons: list[list[TelegramButton]] = Field(default_factory=list)
    # Удалить сообщение, к которому относится действие, вместо его правки.
    # Нужно для входного экрана: он заменяется первым вопросом.
    delete_current: bool = False
    # Отправить в другой чат вместо чата диалога. Нужно уведомлению
    # администратора о новой анкете: Backend в RU не имеет доступа к Bot API, а
    # отправка сообщения в другой чат для Gateway — та же операция.
    chat_id: str | None = Field(default=None, max_length=64)
    document: TelegramDocument | None = None


class TelegramView(BaseModel):
    """Полный ответ на одно событие: что показать и что ответить на нажатие."""

    messages: list[TelegramMessage] = Field(default_factory=list)
    # Всплывающий ответ на callback. Telegram требует ответить на нажатие в
    # течение нескольких секунд, иначе кнопка «зависает» с часами.
    toast: str | None = Field(default=None, max_length=200)
    toast_alert: bool = False


class TelegramUpdateRequest(BaseModel):
    """Событие Telegram в терминах контракта.

    `update_id` — идентификатор обновления Telegram, он же ключ идемпотентности.
    Telegram переотправляет обновление, если Gateway не подтвердил его получение,
    а Gateway ещё и повторяет запрос при таймауте: без ключа один ответ
    продвинул бы анкету на два шага.
    """

    update_id: int
    telegram_user_id: str = Field(max_length=64)
    chat_id: str = Field(max_length=64)
    username: str | None = Field(default=None, max_length=64)
    kind: TelegramUpdateKind
    # Текст сообщения, команда (`/start`) или callback_data — по одному полю на
    # все три случая: для Backend это одна и та же строка «что пришло».
    payload: str = Field(default="", max_length=4096)


class TelegramUpdateResponse(BaseModel):
    """Ответ на событие.

    `profile_id` заполняется только после финализации: до этого профиля ещё нет,
    и отдавать Gateway идентификатор черновика незачем — он им не пользуется.
    """

    view: TelegramView
    finished: bool = False
    profile_id: str | None = None
    # Событие уже было обработано ранее (дубликат по update_id). Gateway
    # использует признак только для лога: пользователю показывается тот же вид.
    duplicate: bool = False


class TelegramDeliveryTask(BaseModel):
    """Задание на отправку файла программы.

    Содержимое файла здесь не передаётся: оно запрашивается отдельно и живёт в
    памяти процесса Gateway ровно до отправки в Telegram.
    """

    delivery_id: int
    chat_id: str = Field(max_length=64)
    filename: str = Field(max_length=255)
    caption: str = Field(default="", max_length=1024)


class TelegramDeliveryResult(BaseModel):
    """Итог отправки. Пишется Backend'ом в состояние доставки."""

    delivered: bool
    message_id: int | None = None
    # Причина отказа для журнала. Тело ошибки Telegram сюда не копируется
    # целиком: в нём бывает содержимое запроса.
    error: str | None = Field(default=None, max_length=300)
