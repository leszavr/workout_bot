# Workout Bot

Telegram-бот для сбора анкеты клиента (36 вопросов) и формирования структурированного профиля, FastAPI backend, каталог упражнений (873) и внутренний веб-интерфейс.

Модульный монолит: транспортный слой (Telegram / HTTP) отделён от бизнес-логики. Архитектура описана в [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Структура

```
apps/
  telegram_gateway/   # aiogram: handlers, keyboards, FSM (только транспорт)
  backend/            # FastAPI: /health, /ready, /api/v1 (auth, profiles, users, exercises)
  web/                # Next.js внутренний интерфейс (dashboard, профили, упражнения)
src/
  domain/             # строгие Pydantic-модели: FitnessProfile, ConsentRecord, Exercise, enums
  application/        # QuestionnaireService, финализация, уведомления, описание вопросов
  infrastructure/     # ProfileRepository (File/Postgres), FileStorage, ExerciseRepository, config
alembic/              # миграции PostgreSQL
scripts/              # import_exercises.py — импортёр каталога
tests/
  unit/               # валидация, ветвление, идемпотентность, ID
  integration/        # сценарии анкеты, PostgreSQL, каталог, API
docs/                 # архитектура, аудит каталога упражнений
docker/               # Dockerfile (backend/bot), Dockerfile.web, docker-compose
```

## Быстрый старт (Docker Compose)

```bash
git clone https://github.com/leszavr/workout_bot && cd workout_bot
cp .env.example .env
# заполнить в .env: BOT_TOKEN, ADMIN_CHAT_ID, POSTGRES_PASSWORD,
# ADMIN_LOGIN, ADMIN_PASSWORD, JWT_SECRET

docker compose -f docker/docker-compose.yml --env-file .env up --build
```

После запуска доступны:

| Сервис | Адрес |
|---|---|
| Telegram bot | polling (внешнего порта нет) |
| FastAPI + Swagger | http://localhost:8000 (docs: /docs) |
| Web interface | http://localhost:3000 |
| PostgreSQL | localhost:5432 |

Миграции и импорт упражнений (один раз, после первого запуска):

```bash
docker compose -f docker/docker-compose.yml --env-file .env exec backend alembic upgrade head
# импорт каталога: клонировать leszavr/workout и выполнить
docker compose -f docker/docker-compose.yml --env-file .env exec backend \
  python -m scripts.import_exercises /path/to/workout --source-version <commit>
```

## Локальный запуск без Docker

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # заполнить переменные

# PostgreSQL (контейнер)
docker compose -f docker/docker-compose.yml --env-file .env up -d postgres

# Миграции
alembic upgrade head

# Telegram-бот
python -m apps.telegram_gateway.main

# Backend
uvicorn apps.backend.main:app --host 0.0.0.0 --port 8000

# Frontend
cd apps/web && npm install && npm run dev   # http://localhost:3000
```

Если `DATABASE_URL` не задан — бот использует файловое хранилище (режим разработки/тестов).

## Тесты

```bash
pytest
```

Интеграционные тесты PostgreSQL/API требуют запущенной БД и `DATABASE_URL` в окружении; без них они пропускаются.
