# Архитектура Workout Bot

Модульный монолит. Четыре слоя с зависимостью только «сверху вниз».

```
┌────────────────────────────────────────────────────────────┐
│ Transport Layer                                            │
│   apps/telegram_gateway  — aiogram handlers/keyboards/FSM  │
│   apps/backend           — FastAPI (/health, /ready,       │
│                            /api/v1: auth, profiles, users, │
│                            exercises, programs, media,     │
│                            dashboard)                      │
│   apps/web               — Next.js внутренний интерфейс    │
└───────────────────────────┬────────────────────────────────┘
                            ↓
┌────────────────────────────────────────────────────────────┐
│ Application Layer                                          │
│   src/application/questionnaire — QuestionnaireService,    │
│       описание вопросов, review, labels                    │
│   src/application/profiles      — финализация (идемпотент.)│
│   src/application/notifications — уведомление админа       │
│   src/application/programs      — pipeline генерации:      │
│       filtering, safety, generator, validator, service,    │
│       orchestrator (primary/fallback), html_renderer,      │
│       html_service, telegram_delivery, pipeline            │
│   src/application/media         — ExerciseMediaService     │
│   src/application/ai            — AI Gateway, ModelSelector,│
│       AIConfigurationService, AIReadinessService,           │
│       AIProgramGenerator, PromptLoader                     │
└───────────────────────────┬────────────────────────────────┘
                            ↓
┌────────────────────────────────────────────────────────────┐
│ Domain Layer                                               │
│   src/domain/profile.py — FitnessProfile + вложенные модели│
│   src/domain/consents.py— ConsentRecord                    │
│   src/domain/exercise.py— Exercise                         │
│   src/domain/program.py — WorkoutProgram + TrainingDay     │
│   src/domain/media.py   — ExerciseMedia                    │
│   src/domain/pools.py   — CandidatePool / SafeExercisePool │
│   src/domain/safety.py  — ExerciseCharacteristics          │
│   src/domain/enums.py   — все enum предметной области      │
└───────────────────────────┬────────────────────────────────┘
                            ↓
┌────────────────────────────────────────────────────────────┐
│ Infrastructure Layer                                       │
│   src/infrastructure/persistence — ProfileRepository       │
│       (File / Postgres), ProgramRepository,                │
│       ExerciseMediaRepository, DeliveryRepository, Alembic │
│   src/infrastructure/files       — FileStorage (Local)     │
│   src/infrastructure/media       — ObjectStorage (MinIO/S3)│
│   src/infrastructure/telegram    — TelegramAdminSender,    │
│       ProgramSender (документы), AlertSender               │
│   src/infrastructure/config.py, logging_setup.py           │
└────────────────────────────────────────────────────────────┘
```

## Хранилище данных

Основное хранилище — **PostgreSQL** (SQLAlchemy 2.0 async + asyncpg + Alembic).
Таблицы: `users`, `profiles` (JSONB), `consents`, `exercises`,
`workout_programs`, `exercise_media`, `program_deliveries`, `ai_*`
(конфигурация AI-провайдеров).

- Профиль проходит Pydantic-валидацию перед записью и после чтения:
  `Pydantic Model → Validation → PostgreSQL JSONB`. БД не является
  хранилищем произвольного JSON.
- `ProfileRepository` — интерфейс; `PostgresProfileRepository` (основной)
  и `FileProfileRepository` (dev/test, когда `DATABASE_URL` не задан).
- Миграции: `alembic upgrade head`.
- Каталог упражнений: 873 записи из `leszavr/workout`, идемпотентный
  импорт по ключу `(external_id, source)` — `scripts/import_exercises.py`.
- Бинарные медиаобъекты (WebP-фото упражнений) хранятся в **MinIO**
  (S3-compatible object storage), PostgreSQL хранит только метаданные
  (`exercise_media`: storage_key, checksum, размеры, лицензия, источник).
  MinIO не является частью Git; воспроизводимость обеспечивается
  импортёром `scripts/import_exercise_media.py` + исходным репозиторием.
- `ai_endpoints` хранит результат последней проверки подключения
  (`last_test_at`, `last_test_status`, `last_test_error_type`) — только время,
  статус и класс ошибки, без ключей и тела ответа провайдера. Это состояние,
  а не конфигурация: без него нельзя отличить «не проверялось» от
  «проверка провалилась».

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

## Pipeline генерации программ (этап 3A)

```
Profile → ExerciseFilter → CandidatePool → SafetyEngine → SafeExercisePool
→ ProgramGenerator → ProgramValidator → ProgramRepository (versioned)
```

Оркестрацию выполняет `ProgramService` (application-слой); FastAPI routes
и Telegram handlers не содержат бизнес-логики и получают сервис через
фабрику зависимостей (`apps/backend/api/v1/dependencies.py`).

### Exercise Filtering (`filtering.py`)
Детерминированный отбор кандидатов. Учитываются:
- **оборудование**: свободный текст профиля нормализуется в теги каталога
  (`EQUIPMENT_ALIASES`); зал без списка → полный набор зала; дом → только
  перечисленное + `body only`;
- **уровень подготовки**: опыт профиля → допустимые `difficulty`;
- **предпочтения**: `CardioPreference.EXCLUDE` исключает кардио;
  `excluded_exercises` пользователя исключаются по имени/алиасам.
Результат — `ExerciseCandidatePool` с причиной каждого исключения.

### Safety Framework (`safety.py`)
```
Restriction (свободный текст) → Normalization → MovementRestriction
→ SafetyRule (централизованный реестр) → ExerciseCharacteristics → Decision
```
- Нормализация: ключевые слова → `MovementRestriction`
  (avoid_high_impact, avoid_heavy_spinal_loading, avoid_overhead_loading,
  avoid_deep_knee_flexion, avoid_high_intra_abdominal_pressure,
  avoid_high_intensity_cardio).
- Характеристики упражнения выводятся rule mapping'ом из полей каталога
  (category + equipment + name patterns), без ручной разметки 873 упражнений.
- Решения: ALLOW / EXCLUDE / WARNING / REQUIRES_REVIEW. При недостатке
  данных (equipment=other, type=other) правило понижает EXCLUDE до
  REQUIRES_REVIEW вместо необоснованного исключения.
- **Safety Rules — технические правила отбора движений, а не медицинская
  диагностика или рекомендация.** Система не утверждает «упражнение
  безопасно при заболевании X»; она исключает движения того типа, которых
  профиль просит избегать. Нераспознанные ограничения → REQUIRES_REVIEW.

### ProgramGenerator (`generator.py`)
`ProgramGenerator` — Protocol (контракт). Реализации:
`DeterministicProgramGenerator` (без случайности, только из SafeExercisePool)
и `AIProgramGenerator` (этап 4, через универсальный AI-шлюз).
- цель → параметры нагрузки (подходы/повторения/отдых), длительность,
  приоритет ролей; опыт → объём тренировки;
- 1–2 тренировки/нед → full body; 3+ → сплит (ноги+жим / тяга+корпус) + full body;
- упражнения группируются по двигательной роли (legs/push/pull/core/cardio),
  compound-упражнения ранжируются выше.
Оба генератора реализуют один контракт; профиль, каталог,
репозиторий, API, валидаторы и safety-слой от выбора генератора не зависят.

### Program Validator (`validator.py`)
Независимый слой проверки (будущий AI-вывод пройдёт через него же):
строгая Pydantic-схема, существование упражнений в каталоге, принадлежность
SafeExercisePool, отсутствие дубликатов в дне, число дней и упражнений,
диапазоны повторений.

### Версионирование и хранение
Таблица `workout_programs`: каждая версия — отдельная строка
`(program_id, version)` с UNIQUE-констрейнтом; полная Pydantic-модель в
JSONB (`data`), денормализованные колонки для списков. Исторические версии
не перезаписываются. `ProgramRepository` — интерфейс,
`PostgresProgramRepository` — реализация.

### API программ
- `GET /api/v1/programs` — список (последние версии);
- `GET /api/v1/programs/{id}?version=N` — программа + список версий;
- `GET /api/v1/profiles/{id}/programs` — программы профиля;
- `POST /api/v1/profiles/{id}/programs/generate` — запуск генерации;
- `GET /api/v1/exercises/external/{external_id}` — поиск упражнения по
  каноническому ID (программы ссылаются на external_id, а не surrogate id).

### Web UI программ
`/programs` (список), `/programs/{id}` (карточка: дни, упражнения,
подходы/повторения/отдых, прогрессия, safety notes, переключение версий).
Упражнения кликабельны (ExerciseLink резолвит external_id → внутренний id).
Страница профиля: блок «Workout Programs» + кнопка «Generate Program».

## Оркестрация генерации и доставка HTML-программы (этап 5)

Полная цепочка после подтверждения анкеты:

```
Telegram Gateway (review_confirm)
        ↓
Profile Finalization (идемпотентная)
        ↓
ProgramGenerationOrchestrator
        ↓
Primary Generator (ai | deterministic)
        ↓ failure
Fallback Generator (строго один, без циклов)
        ↓
ProgramValidator → WorkoutProgram
        ↓
PostgreSQL (workout_programs, версия)
        ↓
HTML Renderer (программа + N фото упражнений)
        ↓
HTML-файл
        ↓
Telegram Delivery (document, ограниченные retry)
        ↓ ошибка доставки после retry
Уведомление администратору (программа уже сохранена)
```

### ProgramGenerationOrchestrator (`orchestrator.py`)
Соединяет фильтр, safety, генераторы, валидатор и репозиторий:
- конфигурация primary/fallback симметрична (`PROGRAM_PRIMARY_GENERATOR`,
  `PROGRAM_FALLBACK_GENERATOR`): ai→deterministic по умолчанию,
  обратный порядок тоже поддерживается;
- строго один fallback: primary → fallback → final failure, никаких циклов;
- `GenerationInfo` в программе фиксирует запрошенный и фактический
  генератор, `fallback_used` и причину fallback;
- пользователь не получает техническую ошибку AI, если fallback сработал;
- идемпотентность: повторный вызов после успешной генерации возвращает
  существующую валидную программу (`reused_existing=True`), новая версия
  создаётся только явным запросом или после failure.

### Generation ≠ Delivery
Ошибка Telegram-доставки не приводит к повторной генерации: программа уже
сохранена в PostgreSQL, доставка повторяется только на уровне
`ProgramDeliveryService`. Статусы доставки хранятся в `program_deliveries`
(pending/sending/sent/failed + число попыток).

### HTML Renderer (`html_renderer.py`, `html_service.py`)
Mobile-first HTML-файл программы (вкладки дней, карточки упражнений,
подходы/повторения/отдых, техника, безопасность, прогрессия).
- Поддерживает **произвольное число изображений** у упражнения
  (0/1/N); лимит `EXERCISE_MEDIA_MAX_PER_EXERCISE` — конфигурация
  сценария, не ограничение ядра рендерера, БД или storage.
- Упражнение без фото рендерится без изображения (описание и техника),
  placeholder-заглушки не используются.
- Два режима медиа (`PROGRAM_HTML_MEDIA_MODE`):
  `html` — изображения встраиваются как data-URI (файл автономен);
  `url` — абсолютные ссылки на media endpoint (`MEDIA_PUBLIC_BASE_URL`).
- Пользовательский текст без технической информации о генераторах.

### Telegram Delivery (`telegram_delivery.py`)
HTML отправляется как document (`workout_program_{profile_id}.html`,
MIME `text/html`) через `ProgramSender`. Ограниченные retry с backoff
(не бесконечные); после исчерпания попыток — нейтральное сообщение
пользователю и технический алерт администратору (`AlertSender`,
`ProgramAlertService`): stage, генератор, fallback, тип исключения,
correlation id. Stack trace пользователю не отправляется.

## Медиа упражнений (этап 5)

```
Exercise Catalog (leszavr/workout)
        ↓
scripts/import_exercise_media.py (Pillow → WebP, checksum, идемпотентность)
        ↓
ExerciseMediaRepository → PostgreSQL (exercise_media: метаданные)
        ↓
ObjectStorage → MinIO (бинарные WebP-объекты)
        ↓
Admin UI / HTML Renderer / GET /api/v1/media/exercises/...
```

- `ExerciseMedia` (domain): упражнение может иметь несколько изображений,
  порядок задаётся `sequence`, первое изображение — primary. Уникальность
  по `(exercise_external_id, source, sequence)`.
- Импортёр идемпотентен: повторный импорт без изменений исходников
  не создаёт дубликатов (сравнение по checksum); изменение исходного
  файла приводит к перечитыванию. Для каждого файла сохраняются
  provenance-данные: источник (`leszavr/workout`), лицензия
  (`Unlicense`, public domain), исходный путь, checksum.
- Media endpoint `GET /api/v1/media/exercises/{external_id}/{sequence}`
  отдаёт WebP из MinIO с `Cache-Control: public, max-age=86400, immutable`;
  несуществующее упражнение/изображение → 404.
- Admin UI показывает фото упражнений на карточке упражнения.

## Готовность AI-конфигурации (Phase 1.1)

```
AIProviderRepository / AIEndpointRepository / AIModelRepository
AITaskConfigRepository / PromptTemplateRepository / ModelSelector
ProviderAdapterRegistry / generation strategy (env)
                    ↓
            AIReadinessService
                    ↓
   report()  →  чек-лист + эффективная цепочка + стратегия
   validate_enable()  →  запрет включения нерабочей задачи
```

`AIReadinessService` (`src/application/ai/readiness.py`) — единственное место,
где определено, что значит «AI готов». Оно же используется как guard при
включении задачи, поэтому UI и сервер не могут разойтись в трактовке.

- Отчёт не выполняет запросов к провайдеру: читается конфигурация и
  сохранённый результат последней проверки подключения.
- Эффективная цепочка строится `ModelSelector`, но дополнительно
  отфильтровывается по наличию адаптера протокола: селектор о реестре
  адаптеров не знает, а невызываемый кандидат не должен выглядеть рабочим.
- Список поддерживаемых протоколов берётся из `ProviderAdapterRegistry`,
  а не из константы в UI.
- Стратегия генерации (`PROGRAM_PRIMARY_GENERATOR`,
  `PROGRAM_FALLBACK_GENERATOR`, `AUTO_GENERATE_PROGRAM_AFTER_FINALIZE`)
  инжектируется фабрикой зависимостей: application-слой не читает
  конфигурацию напрямую.
- `PUT /api/v1/admin/ai/tasks/{task_type}` с `enabled=true` проходит
  `validate_enable` → 422 при отсутствии работоспособной модели, протоколе
  без адаптера или несуществующей версии промпта.
- `AIGateway.test_endpoint` сохраняет результат проверки через
  `AIEndpointRepository.record_test_result`; сбой записи не ломает сам тест.

Endpoint: `GET /api/v1/admin/ai/readiness?task_type=...` (admin-only).
Подробности и осознанные ограничения — `AI.md`.

## Запуск

```bash
# PostgreSQL + MinIO (контейнеры)
docker compose -f docker/docker-compose.yml --env-file .env up -d postgres minio

# Миграции
alembic upgrade head

# Импорт каталога упражнений (один раз)
python -m scripts.import_exercises /path/to/workout --source-version <commit>

# Импорт фото упражнений в MinIO (идемпотентный, можно повторять)
python -m scripts.import_exercise_media /path/to/workout --source-version <commit>

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
