# Архитектура Workout Bot

Модульный монолит с одним компонентом за сетевой границей: Telegram Gateway
развёртывается в EU (там доступен Telegram API), данные и предметная логика
остаются в RU. Слои зависят только «сверху вниз».

```
┌──────────────────── EU ─────────────────────┐
│ apps/telegram_gateway — транспорт Telegram  │
│   BackendClient, view_renderer,             │
│   delivery_poller. Данных нет: PostgreSQL,   │
│   Redis и MinIO недоступны, служебное        │
│   состояние aiogram — в памяти процесса      │
└──────────────────────┬──────────────────────┘
                       │ HTTP /internal/v1/telegram/*
                       │ (X-Internal-Service-Token)
┌──────────────────────┴─────── RU ──────────────────────────┐
│ Transport Layer                                            │
│   apps/backend           — FastAPI (/health, /ready,       │
│                            /api/v1: auth, profiles, users, │
│                            exercises, programs, media,     │
│                            dashboard; /internal/v1)        │
│   apps/worker            — retry и recovery операций       │
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
│       html_service, telegram_delivery, retry_service       │
│   src/application/telegram       — диалог анкеты (dialog),  │
│       очередь доставки (delivery_queue)                     │
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
`workout_programs`, `generation_jobs`, `exercise_media`, `program_deliveries`,
`ai_*` (конфигурация AI-провайдеров), `equipment_*` и `exercise_*` базы знаний
об оборудовании (см. «Gym Knowledge Base»).

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

### Состояние анкеты (PostgreSQL) и служебное состояние шлюза (память процесса)

Незавершённая анкета — бизнес-данные, а не runtime-состояние: в ней уже есть
имя, возраст, ограничения движений и рекомендации врача. Она хранится в
PostgreSQL (`telegram_sessions`), в RU. Профиль попадает в `profiles` при
подтверждении, но черновик пишется с первого ответа: иначе прерванная анкета
теряется целиком.

До выноса Gateway за сетевую границу черновик лежал в Redis шлюза — то есть
персональные данные хранились в EU. Затем в Redis остались только служебные
ключи aiogram с TTL, а после изоляции EU-шлюза внешнего хранилища у него нет
вовсе: Redis не использует ни один компонент системы.

- aiogram требует storage и event isolation: FSM-middleware читает состояние на
  каждом обновлении, а isolation сериализует параллельные обновления одного
  пользователя, иначе второй ответ мог бы обогнать первый. `build_isolation()` в
  `apps/telegram_gateway/main.py` отдаёт `MemoryStorage` и `SimpleEventIsolation`.
- Внешнее хранилище для этого не нужно. Хендлеры FSM не используют, поэтому при
  рестарте терять нечего. Общая блокировка между процессами тоже не нужна:
  `getUpdates` с одним токеном обслуживает ровно один процесс (второй получает
  от Telegram 409 Conflict), а очередь доставки защищена арендой в PostgreSQL.
- Отсутствие клиента хранилища — это и есть граница: в EU нечем сохранить
  пользовательские данные и нечего случайно переподключить к хранилищам RU.
  Регрессия ловится `tests/unit/test_gateway_storage_isolation.py`.

## Внутренний веб-интерфейс

Next.js (App Router, TypeScript), `apps/web`. Страницы: Dashboard,
Profiles (+ карточка с Structured View / Raw JSON), Exercises (+ карточка),
Knowledge Base (оборудование, полнота, незакрытые значения), AI, Infrastructure,
Users. Авторизация: admin login + JWT (`/api/v1/auth/login`), учётные данные
только из переменных окружения.

Фильтрация и пагинация везде серверные. Это касается и фильтров базы знаний в
каталоге упражнений: они сводятся к набору канонических идентификаторов и
попадают в тот же SQL-запрос, а не отсеивают строки после выборки страницы —
иначе «первые 50» перестали бы быть первыми пятьюдесятью подходящими.

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

Оркестрацию выполняет `ProgramGenerationOrchestrator` (application-слой) —
единственная точка генерации в системе (Phase 1.2-C). FastAPI routes и Telegram
handlers не содержат бизнес-логики и получают его через фабрику зависимостей
(`apps/backend/api/v1/dependencies.py`). `ProgramService` из того же пакета
генерацией не занимается: он только читает сохранённые версии программ.

### Exercise Filtering (`filtering.py`)
Детерминированный отбор кандидатов. Учитываются:
- **оборудование**: свободный текст профиля нормализуется в теги каталога
  (`EQUIPMENT_ALIASES`); зал без списка → полный набор зала; дом → только
  перечисленное + `body only`;
- **уровень подготовки**: опыт профиля → допустимые `difficulty`;
- **предпочтения**: `CardioPreference.EXCLUDE` исключает кардио;
  `excluded_exercises` пользователя исключаются по имени/алиасам.
Результат — `ExerciseCandidatePool` с причиной каждого исключения.

Фильтр по-прежнему работает на значениях каталога и словаре `EQUIPMENT_ALIASES`
в коде. Gym Knowledge Base (см. ниже) существует рядом и пока не подключена к
pipeline генерации: переключение фильтра на неё — отдельный шаг, потому что оно
меняет состав пула программ у всех пользователей и требует собственной приёмки.

## Gym Knowledge Base: оборудование как знание

Модель оборудования вынесена из свободных строк в нормализованное знание.
Подробности — `docs/infrastructure/GYM_KNOWLEDGE_BASE_EQUIPMENT_INTELLIGENCE_REPORT.md`.

```
Exercise → Requirement (REQUIRED | OPTIONAL | ALTERNATIVE)
         → Equipment  → Capability
                      → Specialization (leg_press → resistance_machine)
Profile  → Availability (available | unavailable | unknown)
         ↓
EquipmentCompatibilityService → compatible | incompatible | unknown + причина
```

Уровни знания и таблицы:

- `equipment_capabilities` — что объект умеет (наклонная опора, регулируемое
  сопротивление). Отдельная сущность, потому что два тренажёра разных
  производителей называются по-разному, но функционально совпадают.
- `equipment_items` — что это за объект. Строковый первичный ключ; `specializes`
  выражает «частный случай родового» (`leg_press` → `resistance_machine`), что
  необходимо, поскольку источник каталога говорит родовыми словами.
- `equipment_item_capabilities`, `equipment_aliases` — связи и синонимы. Синонимы
  живут в данных, а не в Python: добавление тренажёра больше не требует правки
  кода.
- `exercise_equipment_requirements` — потребность упражнения с различением
  «без этого нельзя» / «желательно» / «одно из» (группы `alternative_group`).
- `unmapped_equipment_values` — значения источника без canonical ID. Существует,
  чтобы импорт не терял информацию молча.
- `exercise_alternatives` — альтернативы с явным типом замены (EXACT / SIMILAR /
  PARTIAL) и обоснованием.
- `equipment_profiles`, `equipment_profile_items` — что фактически доступно
  пользователю или залу; `assume_unlisted_unavailable` отвечает, значит ли
  отсутствие позиции «нет» или «неизвестно».

Ключевое свойство: **UNKNOWN ≠ INCOMPATIBLE**. Отсутствие знания о требованиях
упражнения и отсутствие ответа пользователя про тренажёр не являются
доказательством несовместимости, и deterministic-слой не придумывает факт
отсутствия оборудования. AI получает результат как факт и в вычислении не
участвует; создавать equipment ID он не может — все ссылки обязаны существовать
в базе.

Диагностика полноты и целостности — `GET /api/v1/admin/knowledge/health`
(все числа считаются из базы) и раздел админки «База знаний».

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
- `POST /api/v1/profiles/{id}/programs/generate` — запуск генерации через
  `ProgramGenerationOrchestrator`; принимает необязательный `idempotency_key` и
  возвращает блок `generation` (статус job, число попыток, код ошибки, признак
  повторного использования, запрошенный и фактический генератор, признак и
  причину fallback). Повтор при активной генерации → `409`; тот же
  `idempotency_key` с другим генератором → `409` (конфликт параметров); ошибка
  данных или валидации → `422`; сбой явно выбранного ИИ → `502`;
- `DELETE /api/v1/programs/{id}` — удаление программы со всеми версиями и
  записями доставок (admin-only). Границы удаления — «Жизненный цикл анкет и
  программ в админке»;
- `GET /api/v1/exercises/external/{external_id}` — поиск упражнения по
  каноническому ID (программы ссылаются на external_id, а не surrogate id).

Список анкет (`GET /api/v1/profiles`) дополнительно принимает фильтры
`generated` / `delivered` и порядок `sort`, а `DELETE /api/v1/profiles/{id}`
удаляет анкету без программ.

### Web UI программ
`/programs` (список с признаком отправки и удалением), `/programs/{id}`
(карточка: дни, упражнения, подходы/повторения/отдых, прогрессия, safety notes,
переключение версий). Упражнения кликабельны (ExerciseLink резолвит external_id →
внутренний id). Страница профиля: блок «Программы по этой анкете» + кнопка
«Собрать программу» + удаление программы.

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

Единственная application-level точка генерации (Phase 1.2-C). Вход —
`GenerationRequest`, выход — `OrchestratorResult`; альтернативных generation
pipeline'ов в системе нет.

```
Telegram (автогенерация после finalize) ──┐
                                          ▼
                                GenerationRequest
                                          ▼
                        ProgramGenerationOrchestrator
                                          ▲
                                          │
Admin API (POST …/programs/generate) ─────┘
```

Различие вызывающих слоёв выражено только запросом:

| | requested_generator | allow_fallback | reuse_existing |
| --- | --- | --- | --- |
| Автогенерация после finalize | из конфигурации | да | да |
| Запрос администратора | выбран явно | нет | нет |

`allow_fallback=False` для администратора — сознательное решение: он выбрал
генератор сам, и молчаливая подмена скрыла бы неработоспособность ИИ. Отказ
возвращается как HTTP 502, программа не создаётся.

Соединяет фильтр, safety, генераторы, валидатор и репозиторий:
- конфигурация primary/fallback симметрична (`PROGRAM_PRIMARY_GENERATOR`,
  `PROGRAM_FALLBACK_GENERATOR`): ai→deterministic по умолчанию,
  обратный порядок тоже поддерживается; запрос может переопределить primary;
- строго один fallback: primary → fallback → final failure, никаких циклов;
  fallback идёт внутри того же job, второй job и вторая программа не создаются;
- перед AI-попыткой спрашивает readiness gate (Phase 1.1.1, ниже): заведомо
  нерабочая конфигурация не приводит к AI-запросу;
- `GenerationInfo` в программе фиксирует запрошенный и фактический
  генератор, `fallback_used`, человекочитаемую причину и машиночитаемый
  `fallback_reason_code`; те же данные попадают в `OrchestratorResult`;
- пользователь не получает техническую ошибку AI, если fallback сработал;
- идемпотентность: повторный вызов после успешной генерации возвращает
  существующую валидную программу (`reused_existing=True`), новая версия
  создаётся только явным запросом или после failure.

Наружный контракт ошибки — стабильный код, а не тип исключения: отказ приходит
как `GenerationFailedError` с `GenerationErrorCode`. Telegram и HTTP-слой не
разбирают внутренние исключения AI Gateway; HTTP-статус выбирается по коду
(409 / 422 / 502). Секреты вычищаются из текста отказа. Недопустимый генератор —
тоже доменный отказ (`validation_failed`), а не `ValueError`.

Классификация отказа выполняется один раз:
`exception → classify_error() → GenerationErrorCode → AIFallbackReason`
(`fallback_reason_for_code`). Второго разбора иерархии исключений нет, поэтому
operational-запись и журнал администратора не могут описывать одну причину
по-разному.

Идемпотентность клиентского ключа: `idempotency_key` — обещание «это тот же
запрос». Повторное использование с другим генератором возвращает 409
(`IdempotencyKeyConflictError`): отдать программу прежнего генератора значило бы
отменить явный выбор администратора, а создать второй job под тем же ключом —
разрушить DB-enforced идемпотентность. Серверный ключ попытки
(`profile:trigger:attempt`) под правило не попадает.

Оркестратор принимает gate и журнал fallback как две необязательные функции
(`ai_readiness_gate`, `fallback_recorder`), а не конкретный AI-сервис. Поэтому
он не зависит от AI-инфраструктуры и тестируется без неё; связывание
происходит в `apps/backend/api/v1/dependencies.py`.

Граница закреплена архитектурным тестом
`tests/unit/test_generation_boundary.py`: Admin API, worker и Telegram-контур не
могут обращаться к генераторам, `ProgramValidator`, `SafetyEngine`, записи
программы и переходам состояния job. Легитимное исключение — фабрика
зависимостей, где pipeline собирается один раз для всех вызывающих. Отдельная
проверка запрещает Gateway доступ к PostgreSQL и к структуре анкеты: без неё
граница была бы косметической — шлюз без импортов оркестрации, но с
`DATABASE_URL`, остаётся связанным с Backend напрямую.

### Generation ≠ Delivery
Ошибка Telegram-доставки не приводит к повторной генерации: программа уже
сохранена в PostgreSQL, доставка повторяется отдельно. Статусы доставки хранятся
в `program_deliveries` (pending/sending/sent/failed, число попыток, аренда,
`next_attempt_at`).

Эта же таблица служит очередью отправки: Backend ставит задание, Gateway
забирает его (`FOR UPDATE SKIP LOCKED`), отправляет файл и отчитывается.
Отдельной сущности для очереди нет — состояние доставки и есть её состояние, и
второе хранилище пришлось бы синхронизировать с первым.

### Persistent generation state (Phase 1.2-B)

Генерация — операция со своим состоянием, а не побочный эффект запроса.
`generation_jobs` отвечает на вопрос «что происходило с этим запросом
генерации» независимо от того, появилась программа или нет.

- домен: `src/domain/generation.py` — состояния `PENDING → RUNNING →
  SUCCEEDED|FAILED`, таблица разрешённых переходов, стабильные коды ошибок и их
  класс (non-retryable/transient);
- репозиторий: `src/infrastructure/persistence/postgres/generation_job_repository.py`
  — создание через `INSERT ... ON CONFLICT (idempotency_key) DO NOTHING`,
  переход через `UPDATE ... WHERE status = :expected`;
- application: `src/application/programs/generation_jobs.py` — оборачивает
  генерацию оркестратора в operational-запись, ничего не решая за него.

**Одна логическая генерация = (profile_id, бизнес-событие, номер попытки).**
`profile_id` в одиночку ключом быть не может: анкета законно имеет несколько
версий программы. Автогенерация после подтверждения анкеты и явный запрос
администратора — разные события, поэтому триггер входит в идентичность.

Идемпотентность и конкурентность обеспечивает PostgreSQL, а не Python: два
параллельных запроса одной логической генерации создают один job и одну
программу. Повтор при активной генерации получает `GenerationAlreadyRunningError`
(HTTP 409), повтор после успеха — уже созданную программу. Advisory lock не
нужен: достаточно уникальности ключа и условного `UPDATE`.

Длительный AI-вызов вне транзакции:

```
tx: создать job → PENDING
tx: PENDING → RUNNING
     генерация (AI/deterministic) + сохранение программы
tx: RUNNING → SUCCEEDED | FAILED
```

`GenerationJob` не заменяет `WorkoutProgram`: при отказе программа не
создаётся, а job хранит только код ошибки и короткое безопасное описание —
промпт, ответ провайдера, ключи и PII туда не попадают.

Retry в 1.2-B не реализован: `RETRY_WAIT` и переход `FAILED → PENDING/RUNNING`
зарезервированы для Phase 1.2-D вместе с worker и recovery stale `RUNNING`.

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

## AI-инфраструктура: runtime, health, lifecycle (Phase 1.1.1)

Конфигурация → health → readiness → runtime → fallback → observability
образуют один контур на существующих компонентах. Параллельной подсистемы
AI Health нет.

```
        AI configuration (единый источник истины)
   ai_providers / ai_endpoints / ai_models / ai_task_configs / bindings
        + ai_endpoints.last_test_*  + журнал usage
                    ↓                        ↓
      AIReadinessService          AIInfrastructureHealthService
                    ↓                        ↓
        runtime_gate()            infrastructure-health (дерево)
                    ↓                        ↓
   ProgramGenerationOrchestrator        Admin UI /ai
                    ↓
      fallback + reason_code → audit (ai_generation_fallback)
                    ↓
              fallback-events API → Admin UI
```

### Readiness влияет на runtime
`AIReadinessService.runtime_gate(task_type)` возвращает
`RuntimeGateDecision(allowed, reason, detail)`. Причина берётся из первого
блокирующего шага того же чек-листа, который показывает `report()`, поэтому
runtime и админка не могут разойтись. Оркестратор при `allowed=False` не
делает AI-запрос: если запрос допускает fallback, он сразу переходит к
детерминированному генератору, иначе (явный выбор администратора) возвращает
отказ с причиной.

Сбой самого gate не блокирует генерацию: при неизвестном состоянии попытка
выполняется, решение остаётся за AI.

### Разделение состояний
Три независимых измерения (`src/domain/ai/enums.py`):
`enabled` (configuration state), `AIHealthState` (provider/endpoint),
`AIModelAvailability` (модель). Причины fallback — `AIFallbackReason`, где
конфигурационные причины (AI не вызывался) отделены от runtime-причин
(AI вызывался и не смог).

### AIInfrastructureHealthService (`src/application/ai/health.py`)
Строит дерево provider → endpoint → model → task bindings динамически из
конфигурации; собственного реестра не хранит. Health выводится из сохранённого
connection test и последнего реального AI-вызова, поэтому чтение состояния
дешёвое и не создаёт запросов к провайдерам. Активная проверка
(`refresh()`) переиспользует `AIGateway.test_endpoint` — минимальный ping,
а не генерацию программы.

### Safe delete
Контракт удаления с зависимостями живёт в `src/application/deletion.py`
(`DeleteBlockedError`, `DeleteDependencies`) и используется всеми разделами
админки: AI-конфигурацией, анкетами и программами. Причина общего контракта —
часть связей в схеме логические (`ai_task_configs.prompt_version`,
`workout_programs.profile_id`, `program_deliveries.*`), внешних ключей на них
нет, и `DELETE` прошёл бы успешно, оставив систему в противоречивом состоянии.
База не должна быть последней инстанцией, объясняющей администратору, почему
удаление невозможно.

`AIConfigurationService` собирает зависимости до удаления
(`provider_dependencies`, `endpoint_dependencies`, `model_dependencies`,
`prompt_dependencies`) и бросает `AIDependencyError` (псевдоним
`DeleteBlockedError`) со списком блокеров → API отдаёт 409 с машиночитаемым
`blockers`. Hard delete разрешён только без зависимостей; иначе применяется
disable. Секреты каскадно удаляемых эндпоинтов удаляются из SecretStore явно, а
usage/audit-история сохраняется (FK `SET NULL`).

Endpoints: `GET /api/v1/admin/ai/infrastructure-health`,
`POST /api/v1/admin/ai/infrastructure-health/refresh`,
`GET /api/v1/admin/ai/fallback-events` (admin-only). Детали — `AI.md`.

## Жизненный цикл анкет и программ в админке

Раздел анкет накапливается: анкета остаётся в системе и после того, как программа
по ней собрана и отправлена. `ProfileAdminService`
(`src/application/profiles/admin_service.py`) отвечает за вывод неактуальных
записей и задаёт асимметричные границы удаления.

### Анкета дороже программы

```
DELETE /api/v1/profiles/{id}   → 409, если есть программы
DELETE /api/v1/programs/{id}   → 204 всегда (все версии + доставки)
```

Асимметрия обоснована ценой ошибки, а не удобством: анкету заполнял человек в
боте, восстановить её невозможно; программа производна от анкеты и собирается
заново одной кнопкой. Поэтому единственный блокер удаления анкеты — её
программы, а у программы блокеров нет вовсе.

Программа удаляется целиком, со всеми версиями: `program_id` — это программа, а
версии её история. Удаление отдельных версий не поддерживается — оно оставило бы
дыры в истории и рассинхронизировало `next_version`.

Что удаляется вместе с объектом:

| Удаляется | Программы | Доставки | `generation_jobs` |
|---|---|---|---|
| анкета (без программ) | — | да, сервисом | каскад базы (`ON DELETE CASCADE`) |
| программа | все версии | да, сервисом | ссылка обнуляется (`ON DELETE SET NULL`), запись остаётся |

История операций генерации сохраняется намеренно: она нужна для разбора
инцидентов и не содержит персональных данных.

### Маркеры исполнения и сортировка

Список анкет отдаёт `has_program` и `delivered` — по ним видно, исполнена ли
анкета. Оба признака вычисляются подзапросами `EXISTS` к фактическим данным, а не
хранятся в анкете флагами: дублирующее поле рассинхронизировалось бы после
удаления программы.

«Скачано пользователем» не отслеживается: Telegram Bot API не сообщает об
открытии присланного документа. Достоверно известен только факт отправки
(`program_deliveries.status = 'sent'`), и показывается именно он.

Сортировка (`sort`) принимается из белого списка значений, а не как имя колонки:
подстановка произвольного поля в `ORDER BY` — это и SQL-инъекция, и утечка
внутренней схемы в публичный контракт. Внутри каждой группы порядок всегда
«новые сверху», иначе список менялся бы между открытиями.

## Авторизация и пользователи админ-панели

```
admin_users  (человек: логин, роль, scrypt-хеш пароля, активность)
     ↑ 1..n
admin_identities  (аккаунт внешнего провайдера: yandex | vk | max)
```

Пользователь и способ входа разделены намеренно. Пароль — атрибут пользователя
(проверяется локально), внешний аккаунт — отдельная сущность. Поэтому
подключение входа через Яндекс/VK/MAX добавляет строку, а не колонку, и не
меняет ни схему пользователей, ни проверку прав.

`AdminUserService` (`src/application/auth/service.py`) — единственное место,
где принимаются решения о составе пользователей: политика паролей, защита от
состояния «нет активных администраторов», запрет удаления себя, сброс пароля.

Три зависимости в `apps/backend/auth.py` с разным смыслом:

| Зависимость | Кто проходит | Где используется |
|---|---|---|
| `current_user` | любой вошедший, даже с временным паролем | `/auth/me`, `/auth/change-password` |
| `require_viewer` | любой вошедший с актуальным паролем | все GET-endpoint'ы |
| `require_admin` | только роль `admin` | все изменяющие endpoint'ы |

Ограничение роли `viewer` обеспечивает сервер, а не скрытая кнопка в
интерфейсе. Роль хранится в JWT, поэтому её изменение вступает в силу после
следующего входа.

Администратор из переменных окружения сохранён как **аварийный вход** и не
имеет записи в БД (`user_id=None`): при пустой таблице пользователей или
утрате паролей доступ не теряется.

`users` (клиенты Telegram-бота) и `admin_users` (сотрудники с доступом к
интерфейсу) — разные сущности и разные таблицы; смешивать их нельзя.

Подробности, API и осознанные ограничения — `USERS_AND_ACCESS.md`.

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

# База знаний об оборудовании: словарь и требования из значений каталога
# поставляются миграциями 0014-0016. Миграция сопоставляет то, что уже лежит в
# `exercises`, поэтому в чистом окружении (миграции → импорт каталога) знание
# строит скрипт. Он идемпотентен, выполняет то же сопоставление и пересчитывает
# выводимое знание — требования по названию и альтернативы.
# `--dry-run` печатает отчёт без записи.
python -m scripts.build_equipment_knowledge

# Telegram-бот (нужны BACKEND_INTERNAL_URL и INTERNAL_SERVICE_TOKEN:
# данных у шлюза нет, всё идёт через internal API)
python -m apps.telegram_gateway.main

# Backend
uvicorn apps.backend.main:app --host 0.0.0.0 --port 8000

# Worker: повторы и recovery операций
python -m apps.worker.main

# Frontend (http://localhost:3000)
cd apps/web && npm install && npm run dev

# Тесты
pytest
```

Если `DATABASE_URL` не задан — используется файловое хранилище (dev/test).

## Разделение контуров: EU-шлюз и RU-данные

Telegram Gateway вынесен за сетевую границу и общается с Backend только по
HTTP (`/internal/v1/telegram/*`, шесть операций, service-token). Что это даёт и
чего стоило:

- **шлюз не знает предметной логики.** Backend отдаёт готовое описание того, что
  показать (`TelegramView`: текст, кнопки, тип операции), поэтому новый вопрос в
  анкете не требует развёртывания EU. Обратная сторона: контракт описывает
  отображение, а не данные, и добавление нового типа элемента интерфейса — это
  изменение контракта;
- **данных у шлюза нет.** Ни PostgreSQL, ни ключей MinIO; переменные в compose
  перечислены поимённо, для staging заведён отдельный `staging-gateway.env`.
  Проверка фактическая: общий env-файл дал бы доступ к хранилищам RU, даже если
  код им не пользуется;
- **инициатива всегда у шлюза.** EU за NAT, входящих подключений к нему нет,
  поэтому отправку готовых программ он забирает опросом очереди Backend, а не
  получает push. Цена — задержка опроса (5 с по умолчанию) между «программа
  готова» и «файл ушёл»; на фоне минут генерации она незаметна;
- **фотографии проходят через RU.** Скачать файл из Telegram может только шлюз,
  записать его обязан Backend: байты уходят телом запроса и на диске EU не
  появляются;
- **версии независимы.** Совместимость решает `contract_version`, а не совпадение
  версий или git SHA. Иначе любое обновление Backend требовало бы
  переразвёртывания EU — то есть независимости не было бы.

Application и domain слои по-прежнему не зависят от aiogram: он остался только в
`apps/telegram_gateway` и `src/infrastructure/telegram`.
