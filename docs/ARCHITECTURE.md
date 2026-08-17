# Архитектура Workout Bot

Модульный монолит. Четыре слоя с зависимостью только «сверху вниз».

```
┌────────────────────────────────────────────────────────────┐
│ Transport Layer                                            │
│   apps/telegram_gateway  — aiogram handlers/keyboards/FSM  │
│   apps/backend           — FastAPI (/health, /ready, /api) │
└───────────────────────────┬────────────────────────────────┘
                            ↓
┌────────────────────────────────────────────────────────────┐
│ Application Layer                                          │
│   src/application/questionnaire — QuestionnaireService,    │
│       описание вопросов, review, labels                    │
│   src/application/profiles      — финализация (идемпотент.)│
│   src/application/notifications — уведомление админа       │
└───────────────────────────┬────────────────────────────────┘
                            ↓
┌────────────────────────────────────────────────────────────┐
│ Domain Layer                                               │
│   src/domain/profile.py — FitnessProfile + вложенные модели│
│   src/domain/consents.py— ConsentRecord                    │
│   src/domain/enums.py   — все enum предметной области      │
└───────────────────────────┬────────────────────────────────┘
                            ↓
┌────────────────────────────────────────────────────────────┐
│ Infrastructure Layer                                       │
│   src/infrastructure/persistence — ProfileRepository (File)│
│   src/infrastructure/files       — FileStorage (Local)     │
│   src/infrastructure/telegram    — TelegramAdminSender     │
│   src/infrastructure/config.py, logging_setup.py           │
└────────────────────────────────────────────────────────────┘
```

## Ключевые решения

### Единый источник истины анкеты
`src/application/questionnaire/questions.py` содержит список `QUESTIONS`
из `QuestionDefinition`. Порядок, тексты, подсказки, обязательность,
варианты ответа, валидаторы, парсеры и условия пропуска (`skip_if`)
определены в одном месте. FSM-состояния и клавиатуры Telegram генерируются
из этого списка автоматически — рассинхронизация невозможна.

### Строгая модель профиля
`FitnessProfile` — Pydantic v2 модель с `extra="forbid"`, enum-полями,
диапазонами и ограничениями длины. Произвольный `dict` не сохраняется:
перед записью профиль проходит `model_validate` / `model_dump`.

### Идентификаторы
- `profile_id` — UUID (hex), присваивается при старте анкеты.
- `display_number` — человекочитаемый номер `REQ-YYYYMMDD-NNNNN`,
  присваивается репозиторием при финализации.

### Идемпотентная финализация
`ProfileFinalizationService.finalize()`: если профиль уже подтверждён
и сохранён — возвращается существующий результат без повторной записи
и без дублирования согласий.

### Хранилище
- `ProfileRepository` (интерфейс) → `FileProfileRepository` (атомарная
  запись через tmp+rename). Позже добавится `PostgresProfileRepository`
  без изменения application/transport слоёв.
- `FileStorage` (интерфейс) → `LocalFileStorage` (лимиты количества,
  размера, типа). Позже — `S3FileStorage`.

### Согласия (privacy)
`ConsentRecord`: scope + timestamp + версия документа + источник.
Согласия создаются только при явном подтверждении (review_confirm →
final_confirm), не по факту случайного нажатия. Архитектурно поддержаны
export/delete (`repository.get` / `repository.delete`,
`file_storage.delete_profile_files`).

### Уведомления
`AdminNotificationService` хранит явный статус доставки в профиле:
`pending → sent | failed`. Ошибка отправки не приводит к ложному
сообщению об успехе.

### Логирование
`src/infrastructure/logging_setup.py`: в логи пишутся только
user_id, profile_id, event, status, error_class. Содержимое ответов,
полные профили и токены в логи не попадают.

## Запуск

```bash
# Telegram-бот
python -m apps.telegram_gateway.main

# Backend
uvicorn apps.backend.main:app --host 0.0.0.0 --port 8000

# Тесты
pytest
```

## Будущее разделение контуров
Telegram-специфичный код изолирован в `apps/telegram_gateway` и
`src/infrastructure/telegram`. Application/domain слои не зависят от
aiogram, что позволяет позже вынести Telegram Gateway на отдельный сервер,
общающийся с backend по API.
