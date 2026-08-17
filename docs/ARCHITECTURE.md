# Архитектура Workout Bot

Модульный монолит. Четыре слоя с зависимостью только «сверху вниз».

```
┌────────────────────────────────────────────────────────────┐
│ Transport Layer                                            │
│   apps/telegram_gateway  — aiogram handlers/keyboards/FSM  │
│   apps/backend           — FastAPI (/health, /ready,       │
│                            /api/v1: auth, profiles, users, │
│                            exercises, dashboard)           │
│   apps/web               — Next.js внутренний интерфейс    │
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
│   src/domain/exercise.py— Exercise                         │
│   src/domain/enums.py   — все enum предметной области      │
└───────────────────────────┬────────────────────────────────┘
                            ↓
┌────────────────────────────────────────────────────────────┐
│ Infrastructure Layer                                       │
│   src/infrastructure/persistence — ProfileRepository       │
│       (File / Postgres), ExerciseRepository, Alembic       │
│   src/infrastructure/files       — FileStorage (Local)     │
│   src/infrastructure/telegram    — TelegramAdminSender     │
│   src/infrastructure/config.py, logging_setup.py           │
└────────────────────────────────────────────────────────────┘
```

## Хранилище данных

Основное хранилище — **PostgreSQL** (SQLAlchemy 2.0 async + asyncpg + Alembic).
Таблицы: `users`, `profiles` (JSONB), `consents`, `exercises`.

- Профиль проходит Pydantic-валидацию перед записью и после чтения:
  `Pydantic Model → Validation → PostgreSQL JSONB`. БД не является
  хранилищем произвольного JSON.
- `ProfileRepository` — интерфейс; `PostgresProfileRepository` (основной)
  и `FileProfileRepository` (dev/test, когда `DATABASE_URL` не задан).
- Миграции: `alembic upgrade head`.
- Каталог упражнений: 873 записи из `leszavr/workout`, идемпотентный
  импорт по ключу `(external_id, source)` — `scripts/import_exercises.py`.

## Внутренний веб-интерфейс

Next.js (App Router, TypeScript), `apps/web`. Страницы: Dashboard,
Profiles (+ карточка с Structured View / Raw JSON), Exercises (+ карточка).
Авторизация: admin login + JWT (`/api/v1/auth/login`), учётные данные
только из переменных окружения.

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
# PostgreSQL (контейнер)
docker compose -f docker/docker-compose.yml --env-file .env up -d postgres

# Миграции
alembic upgrade head

# Импорт каталога упражнений (один раз)
python -m scripts.import_exercises /path/to/workout --source-version <commit>

# Telegram-бот
python -m apps.telegram_gateway.main

# Backend
uvicorn apps.backend.main:app --host 0.0.0.0 --port 8000

# Frontend (http://localhost:3000)
cd apps/web && npm install && npm run dev

# Тесты
pytest
```

Если `DATABASE_URL` не задан — используется файловое хранилище (dev/test).

## Будущее разделение контуров
Telegram-специфичный код изолирован в `apps/telegram_gateway` и
`src/infrastructure/telegram`. Application/domain слои не зависят от
aiogram, что позволяет позже вынести Telegram Gateway на отдельный сервер,
общающийся с backend по API.
