# Workout Bot

Telegram-бот для сбора анкеты клиента (36 вопросов) и формирования структурированного профиля, FastAPI backend, каталог упражнений (873), генерация программ тренировок (AI + детерминированный fallback), HTML-программа с фотографиями упражнений и доставкой в Telegram, внутренний веб-интерфейс.

Модульный монолит: транспортный слой (Telegram / HTTP) отделён от бизнес-логики. Архитектура описана в [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Структура

```
apps/
  telegram_gateway/   # aiogram: handlers, keyboards, FSM (только транспорт)
  backend/            # FastAPI: /health, /ready, /api/v1 (auth, profiles, users,
                      #   exercises, programs, media)
  web/                # Next.js внутренний интерфейс (dashboard, профили, программы,
                      #   упражнения с фото)
src/
  domain/             # строгие Pydantic-модели: FitnessProfile, ConsentRecord, Exercise,
                      #   WorkoutProgram, ExerciseMedia, ExerciseCandidatePool,
                      #   SafeExercisePool, enums
  application/        # QuestionnaireService, финализация, уведомления, описание вопросов;
                      #   programs/: filtering, safety, generator, validator, service,
                      #   orchestrator (primary/fallback), html_renderer, html_service,
                      #   telegram_delivery, pipeline; media/: ExerciseMediaService
  infrastructure/     # ProfileRepository (File/Postgres), ProgramRepository, FileStorage,
                      #   ExerciseRepository, ExerciseMediaRepository, DeliveryRepository,
                      #   media/ObjectStorage (MinIO), telegram (admin/program/alert), config
alembic/              # миграции PostgreSQL (0001…0005)
scripts/              # import_exercises.py — импортёр каталога,
                      # import_exercise_media.py — импорт фото в MinIO (WebP)
tests/
  unit/               # валидация, ветвление, идемпотентность, filtering, safety, generator,
                      #   orchestrator, html, delivery, media
  integration/        # сценарии анкеты, PostgreSQL, каталог, API, pipeline генерации,
                      #   импорт медиа, E2E Stage 5 (fake Telegram transport)
docs/                 # архитектура, аудит каталога упражнений
docker/               # Dockerfile (backend/bot), Dockerfile.web, docker-compose
                      #   (postgres, minio, backend, bot, frontend)
```

## Pipeline генерации программ (этап 3A)

```
Profile → Exercise Filtering → Candidate Pool → Safety Rules → Safe Exercise Pool
→ ProgramGenerator (Deterministic) → Program Validator → Versioned Storage → API → Web
```

- **Filtering**: оборудование (нормализация свободного текста → теги каталога),
  уровень подготовки, предпочтения пользователя. Каждая причина исключения фиксируется.
- **Safety Framework**: ограничения профиля нормализуются в `MovementRestriction`
  (avoid_high_impact, avoid_heavy_spinal_loading, ...), централизованные правила
  принимают решения ALLOW / EXCLUDE / WARNING / REQUIRES_REVIEW.
  **Safety Rules — технические правила отбора движений, а не медицинская
  диагностика или рекомендация.**
- **Generator**: `ProgramGenerator` — интерфейс; реализации —
  `DeterministicProgramGenerator` (без AI) и `AIProgramGenerator`
  (этап 4, через универсальный AI-шлюз).
- **Versioning**: каждая генерация создаёт новую версию, история не перезаписывается.

## Генерация → HTML → Telegram (этап 5)

После подтверждения анкеты:

```
Questionnaire → Review → Confirm → ProfileFinalization
→ ProgramGenerationOrchestrator (primary → fallback, строго один fallback)
→ ProgramValidator → WorkoutProgram → PostgreSQL
→ HTML Renderer (программа + фото упражнений, mobile-first)
→ Telegram Delivery (HTML-файл документом, ограниченные retry)
```

- **Primary/fallback**: по умолчанию `ai → deterministic`
  (переменные `PROGRAM_PRIMARY_GENERATOR` / `PROGRAM_FALLBACK_GENERATOR`).
  Пользователь не видит технические ошибки AI, если fallback сработал.
- **Generation ≠ Delivery**: ошибка Telegram не вызывает повторную
  генерацию — программа уже сохранена, повторяется только доставка.
- **Фото упражнений**: хранятся в MinIO (WebP), метаданные — в PostgreSQL
  (`exercise_media`). Media endpoint:
  `GET /api/v1/media/exercises/{external_id}/{sequence}`.
  Источник изображений — `leszavr/workout` (Unlicense, public domain);
  provenance и лицензия сохраняются в метаданных каждого файла.

## Быстрый старт (Docker Compose)

```bash
git clone https://github.com/leszavr/workout_bot && cd workout_bot
cp .env.example .env
# заполнить в .env: BOT_TOKEN, ADMIN_CHAT_ID, POSTGRES_PASSWORD,
# ADMIN_LOGIN, ADMIN_PASSWORD, JWT_SECRET, MINIO_ACCESS_KEY, MINIO_SECRET_KEY

docker compose -f docker/docker-compose.yml --env-file .env up --build
```

После запуска доступны:

| Сервис | Адрес |
|---|---|
| Telegram bot | polling (внешнего порта нет) |
| FastAPI + Swagger | http://localhost:8000 (docs: /docs) |
| Web interface | http://localhost:3000 |
| PostgreSQL | localhost:5432 |
| MinIO API | http://localhost:9000 |
| MinIO Console | http://localhost:9001 |

Миграции и импорт данных (один раз, после первого запуска):

```bash
docker compose -f docker/docker-compose.yml --env-file .env exec backend alembic upgrade head
# импорт каталога: клонировать leszavr/workout и выполнить
docker compose -f docker/docker-compose.yml --env-file .env exec backend \
  python -m scripts.import_exercises /path/to/workout --source-version <commit>
# импорт фото упражнений в MinIO (идемпотентный: повторный запуск не создаёт дубликатов)
docker compose -f docker/docker-compose.yml --env-file .env exec backend \
  python -m scripts.import_exercise_media /path/to/workout --source-version <commit>
```

Проверка статуса медиа:

```bash
# количество записей exercise_media в PostgreSQL
docker compose -f docker/docker-compose.yml --env-file .env exec postgres \
  psql -U workout_bot -d workout_bot -c "SELECT count(*) FROM exercise_media;"
# доступность конкретного изображения (HTTP 200, image/webp)
curl -sI "http://localhost:8000/api/v1/media/exercises/Barbell_Full_Squat/1" | head -3
# объекты в MinIO — через консоль http://localhost:9001 (bucket workout-media)
```

## Локальный запуск без Docker

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # заполнить переменные

# PostgreSQL + MinIO (контейнеры)
docker compose -f docker/docker-compose.yml --env-file .env up -d postgres minio

# Миграции
alembic upgrade head

# Импорт фото упражнений в MinIO (после import_exercises; идемпотентный)
python -m scripts.import_exercise_media /path/to/workout --source-version <commit>

# Telegram-бот
python -m apps.telegram_gateway.main

# Backend
uvicorn apps.backend.main:app --host 0.0.0.0 --port 8000

# Frontend
cd apps/web && npm install && npm run dev   # http://localhost:3000
```

Если `DATABASE_URL` не задан — бот использует файловое хранилище (режим разработки/тестов).

Переменные окружения Stage 5 (без значений, см. `.env.example`):

```env
MINIO_ENDPOINT=localhost:9000
MINIO_ACCESS_KEY=
MINIO_SECRET_KEY=
MINIO_SECURE=false
MEDIA_BUCKET=workout-media
MEDIA_PUBLIC_BASE_URL=http://localhost:8000
EXERCISE_MEDIA_MAX_PER_EXERCISE=5
PROGRAM_HTML_MEDIA_MODE=html
PROGRAM_PRIMARY_GENERATOR=ai
PROGRAM_FALLBACK_GENERATOR=deterministic
AUTO_GENERATE_PROGRAM_AFTER_FINALIZE=true
```

## Пример генерации программы

```bash
# Авторизация
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"login": "admin", "password": "<ADMIN_PASSWORD>"}' | jq -r .access_token)

# Генерация программы для профиля (детерминированная, без AI)
curl -s -X POST http://localhost:8000/api/v1/profiles/<profile_id>/programs/generate \
  -H "Authorization: Bearer $TOKEN" | jq

# Список программ / программа по ID / программы профиля
curl -s http://localhost:8000/api/v1/programs -H "Authorization: Bearer $TOKEN" | jq
curl -s http://localhost:8000/api/v1/programs/<program_id> -H "Authorization: Bearer $TOKEN" | jq
curl -s http://localhost:8000/api/v1/profiles/<profile_id>/programs -H "Authorization: Bearer $TOKEN" | jq
```

В веб-интерфейсе: страница профиля → блок «Workout Programs» → кнопка «Generate Program».

## Тесты

```bash
pytest
```

Интеграционные тесты PostgreSQL/API требуют запущенной БД и `DATABASE_URL` в окружении; без них они пропускаются.
