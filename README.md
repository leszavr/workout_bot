# Workout Bot

Telegram-бот для сбора анкеты клиента (36 вопросов) и формирования структурированного профиля, + минимальный FastAPI backend.

Модульный монолит: транспортный слой (Telegram / HTTP) отделён от бизнес-логики. Архитектура описана в [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Структура

```
apps/
  telegram_gateway/   # aiogram: handlers, keyboards, FSM (только транспорт)
  backend/            # FastAPI: /health, /ready, /api/v1
src/
  domain/             # строгие Pydantic-модели: FitnessProfile, ConsentRecord, enums
  application/        # QuestionnaireService, финализация, уведомления, описание вопросов
  infrastructure/     # ProfileRepository, FileStorage, config, логирование, Telegram-адаптеры
tests/
  unit/               # валидация, ветвление, идемпотентность, ID
  integration/        # полный сценарий анкеты по всем ветвям
docs/                 # архитектура
docker/               # Dockerfile + docker-compose
```

## Запуск

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # заполнить BOT_TOKEN и ADMIN_CHAT_ID

# Telegram-бот
python -m apps.telegram_gateway.main

# Backend
uvicorn apps.backend.main:app --host 0.0.0.0 --port 8000
```

## Тесты

```bash
pytest
```

## Docker

```bash
docker compose -f docker/docker-compose.yml up --build
# PostgreSQL (пока не используется в runtime):
docker compose -f docker/docker-compose.yml --profile with-postgres up --build
```
