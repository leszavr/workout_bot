# Workout Bot

Telegram-бот для сбора анкеты клиента (36 вопросов), формирования структурированного профиля и генерации персональной программы тренировок. В системе есть каталог из 873 упражнений, AI и детерминированный генераторы, safety/validation pipeline, HTML-программа с фотографиями и доставка в Telegram.

## Документация

**Начните с [`docs/README.md`](docs/README.md).**

- [`docs/PROJECT_STATUS.md`](docs/PROJECT_STATUS.md) — текущее состояние и открытые риски;
- [`docs/PRODUCT.md`](docs/PRODUCT.md) — цель и границы продукта;
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — архитектура;
- [`docs/DEVELOPMENT_ROADMAP.md`](docs/DEVELOPMENT_ROADMAP.md) — дальнейший план;
- [`docs/AI.md`](docs/AI.md) — AI Gateway и генерация;
- [`docs/OPERATIONS.md`](docs/OPERATIONS.md) — запуск и эксплуатация;
- [`docs/DEVELOPMENT_WORKFLOW.md`](docs/DEVELOPMENT_WORKFLOW.md) — правила ведения работ;
- [`docs/CHANGELOG.md`](docs/CHANGELOG.md) — история крупных изменений.

## Текущий pipeline

```text
Questionnaire → Profile → Filtering → Safety → Generator
→ Validation → Versioned Storage → HTML → Telegram
```

Генераторы: `AIProgramGenerator` и `DeterministicProgramGenerator`. По умолчанию orchestration использует `AI → deterministic fallback`.

## Быстрый старт

```bash
git clone https://github.com/leszavr/workout_bot && cd workout_bot
cp .env.example .env
docker compose -f docker/docker-compose.yml --env-file .env up --build
```

После первого запуска выполнить миграции и импорт каталога/медиа. Подробные инструкции и проверки — в `docs/OPERATIONS.md` и `.env.example`.

## Локальная разработка

Окружением управляет `./workout-manager.sh`: поднимает PostgreSQL, Redis и MinIO, backend и веб-интерфейс, показывает логи и состояние.

```bash
./workout-manager.sh start     # логи в терминале, Ctrl+C останавливает службы
./workout-manager.sh status    # что запущено и к какой базе подключён backend
./workout-manager.sh doctor    # проверка окружения перед работой
./workout-manager.sh help      # все команды
```

## Основные сервисы

| Сервис | Адрес |
|---|---|
| FastAPI + Swagger | http://localhost:8000 (`/docs`) |
| Web interface | http://localhost:3000 |
| PostgreSQL | localhost:5432 |
| Redis (состояние анкеты) | localhost:6379 |
| MinIO API | http://localhost:9000 |
| MinIO Console | http://localhost:9001 |

## Тесты

```bash
./workout-manager.sh test        # весь набор
./workout-manager.sh test unit   # только unit-тесты, без БД
```

Интеграционные тесты работают с реальной PostgreSQL и удаляют свои данные, поэтому им нужна отдельная база: адрес задаётся в `TEST_DATABASE_URL`. Запуск по рабочей базе скрипт не выполнит. Тестам устойчивого FSM нужен доступный Redis (`REDIS_URL`, либо отдельный `TEST_REDIS_URL`).
