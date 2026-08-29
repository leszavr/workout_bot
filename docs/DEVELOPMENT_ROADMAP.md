# Development Roadmap

**Базовая точка:** завершены этапы 1–5, Phase 1.1 и Phase 1.1.1. План строится от фактического состояния репозитория на 23.08.2026.

## Phase 0 — Documentation baseline: DONE
- [x] зафиксировать архитектуру;
- [x] зафиксировать текущий статус;
- [x] описать продукт и границы;
- [x] создать roadmap;
- [x] описать эксплуатацию и AI;
- [x] ввести правила поддержания документации.

## Phase 1 — Production readiness: IN PROGRESS

### 1.1 AI configuration UX: DONE
- [x] guided setup для `workout_generation` (мастер «Быстрое подключение AI»);
- [x] протоколы без адаптера помечены и недоступны для выбора; сервер отклоняет включение задачи на таком протоколе;
- [x] configuration health/status (`GET /api/v1/admin/ai/readiness` + панель готовности на `/ai`);
- [x] тест подключения до включения задачи: результат проверки хранится на эндпоинте, мастер включает задачу только после успешной проверки, непроверенное подключение делает конфигурацию «не готовой»;
- [x] usage/error visibility (журнал вызовов и журнал изменений конфигурации в UI).

**Осознанное ограничение:** прямой `PUT /tasks/{task_type}` (экспертный путь) не требует успешного connection test — синтетический ping может дать ложный негатив у части провайдеров и заблокировать рабочую настройку. Требование проверки закрыто мастером и статусом готовности, а сервер жёстко отклоняет только детерминированно нерабочие конфигурации.

### 1.1.1 AI infrastructure management & reliability: DONE
- [x] readiness влияет на runtime: `AIReadinessService.runtime_gate()` перед AI-попыткой; при not ready AI не вызывается вообще;
- [x] структурированные причины fallback (`AIFallbackReason`) с разделением configuration (AI не вызывался) и runtime (AI вызывался и не смог); код причины хранится в программе и в журнале;
- [x] fallback observability: `GET /api/v1/admin/ai/fallback-events` и раздел в админке — видно requested/actual generator, причину и был ли вызов;
- [x] configuration lifecycle: view/create/edit/enable/disable/safe delete для provider/endpoint/model в API и UI;
- [x] safe delete с предварительной проверкой зависимостей: 409 с машиночитаемым списком блокеров, без broken references; usage/audit история сохраняется, секреты удаляемых эндпоинтов вычищаются;
- [x] AI Infrastructure Health Dashboard: дерево provider → endpoint → model → задачи строится динамически из конфигурации (`GET /api/v1/admin/ai/infrastructure-health` + панель на `/ai`);
- [x] разделены configuration state, infrastructure health и model availability; семантика `enabled` vs `healthy` задокументирована;
- [x] health не требует дорогих генераций: состояние читается из сохранённого connection test и журнала вызовов, активная проверка — минимальный ping по кнопке (`POST …/infrastructure-health/refresh`);
- [x] CI в GitHub Actions на PR и push в `main`: backend-тесты на реальной PostgreSQL, миграции (head → base → head), frontend lint/typecheck/build, авто-issue при падении.

**Осознанное ограничение:** фоновых периодических health-проверок нет. В проекте нет планировщика и воркеров, вводить их ради опроса провайдеров несоразмерно задаче. Автоматически обновляется дешёвое чтение состояния (включая результаты реальных AI-вызовов), активная проверка выполняется по требованию администратора.

### 1.2 Reliability: IN PROGRESS — DESIGN APPROVED

**Design baseline:** `docs/architecture/PHASE_1_2_RUNTIME_RELIABILITY.md`.

#### 1.2-A — Persistent FSM: DONE
- [x] заменить `MemoryStorage` на устойчивое production storage (Redis-backed
      FSM: `RedisStorage` + `RedisEventIsolation`, ключи с `bot_id`);
- [x] проверить restart/recovery questionnaire state;
- [x] acceptance: restart во время questionnaire не теряет критическое состояние;
- [x] `REDIS_URL` обязателен для бота, проверяется до старта polling и в
      `workout-manager.sh`; ресурсы Redis закрываются при остановке;
- [x] недоступность Redis приводит к понятному сообщению пользователю
      (`FSMStorageError` → error router), а не к молчаливой потере ответа;
- [x] Redis добавлен в docker compose и CI, тесты FSM выполняются реально.

#### 1.2-B — Generation domain state: DONE
- [x] формализована persistent generation status model: `generation_jobs`,
      состояния `PENDING → RUNNING → SUCCEEDED|FAILED`, переходы контролируются
      условным `UPDATE`, запрещённые переходы отклоняются;
- [x] idempotency boundary: одна логическая генерация = (profile_id, бизнес-событие,
      номер попытки); ключ строится детерминированно, вызывающая сторона может
      задать свой; enforced через `UNIQUE(idempotency_key)` +
      `INSERT ... ON CONFLICT DO NOTHING`, а не проверкой в Python;
- [x] persistence для generation jobs: Alembic `0008`, FK на профиль (CASCADE) и
      на версию программы (SET NULL), индексы под чтение по профилю/статусу;
- [x] acceptance duplicate generation requests: последовательный повтор и два
      параллельных запроса дают один job и одну программу; повторный запрос при
      активной генерации получает 409, а не второй job;
- [x] AI-вызов вынесен за границы транзакции: state-переходы идут короткими
      транзакциями до и после генерации;
- [x] в job не попадают промпт, ответ провайдера, ключи и PII — только стабильный
      код ошибки и безопасное краткое описание.

**Осознанное ограничение:** retry не реализован. `RETRY_WAIT` и переход
`FAILED → PENDING/RUNNING` зарезервированы для 1.2-D: статус без обработчика
оставлял бы job в состоянии, из которого его никто не выводит. Recovery stale
`RUNNING` после падения процесса тоже относится к 1.2-D.

#### 1.2-C — Generation Orchestrator: DONE
- [x] единый `ProgramGenerationOrchestrator`: единственный вход в генерацию —
      `GenerationRequest`, единственный выход — `OrchestratorResult`;
- [x] Telegram и Admin API используют один orchestration path; второй pipeline
      (`ProgramService.generate`) удалён, сервис отвечает только за чтение;
- [x] readiness/fallback/safety/validation не обходятся: единственный владелец
      этих шагов — оркестратор;
- [x] веб-кнопка `Generate Program` переведена на общий путь
      (`POST /profiles/{id}/programs/generate` → оркестратор);
- [x] различие вызывающих слоёв выражено только запросом: автогенерация берёт
      стратегию из конфигурации и разрешает fallback, явный запрос
      администратора запрещает подмену генератора;
- [x] наружу отдаётся стабильный код отказа (`GenerationFailedError`), а не
      внутренние исключения AI Gateway; HTTP-статус выбирается по коду;
- [x] архитектурный acceptance-тест `tests/unit/test_generation_boundary.py`
      статически запрещает прямые вызовы генераторов, validator, safety,
      записи программы и переходов job из Telegram/Admin API.

**Осознанное ограничение:** retry, worker и stale-recovery по-прежнему не
реализованы (1.2-D); delivery остаётся отдельной операцией и в оркестратор не
входит (1.2-E).

#### 1.2-C.1 — AI reliability + prompt management: DONE

Реакция на FINDING-1 полного staging E2E: ИИ не собрал программу, пользователь
получил алгоритмическую. Отчёт —
`docs/reports/AI_RELIABILITY_AND_PROMPT_MANAGEMENT_REPORT.md`.

- [x] repair получает достаточный контекст: system-промпт (правила и схема),
      предыдущий ответ модели, ошибки валидации, перечень разрешённых
      `exercise_external_id` и явный запрет их изобретать; выдуманные
      идентификаторы называются в запросе прямо;
- [x] повторный провал валидации переводит генерацию на следующую модель
      цепочки: перебор ведёт `AIProgramGenerator`, `AIGateway` получил
      `prepare()` + `generate_once()`, прежний `generate()` сохранён;
- [x] deterministic fallback остался последним рубежом и решает его оркестратор;
      транспортные ретраи адаптера не изменились;
- [x] валидатор не ослаблен: выдуманный `external_id` остаётся
      `exercise_not_found`;
- [x] журнал попыток моделей (`GET /api/v1/admin/ai/model-attempts`, событие
      `ai_model_attempts`) и его отображение на `/ai/logs`;
- [x] управление инструкциями: список с превью, полный текст без усечения,
      создание, правка, удаление (`/api/v1/admin/ai/prompts…`) и вкладка
      `/ai/prompts` в админке;
- [x] политика удаления: hard delete, блокируется только для инструкции,
      выбранной в настройках задачи (409 со списком блокеров);
- [x] тесты: контекст repair, переход по цепочке моделей, исчерпание цепочки,
      CRUD инструкций, защита активной инструкции, применение правки в
      `PromptLoader`.

**Осознанное ограничение:** AI playground не вводится. Итерация промпта
выполняется штатным путём «изменить инструкцию → выбрать версию в задаче →
запустить генерацию → посмотреть результат», без отдельной подсистемы прогонов.

#### 1.2-C.2 — Жизненный цикл анкет и программ в админке: DONE

Раздел анкет накапливался: удаления не было вовсе, а по списку нельзя было
понять, исполнена ли анкета.

- [x] маркеры «программа собрана» и «отправлена человеку» в списке анкет,
      вычисляемые по фактическим данным (`EXISTS`), а не хранимые флагами;
- [x] фильтры по обоим маркерам и сортировка по дате и по маркерам; порядок
      принимается из белого списка значений, а не как имя колонки;
- [x] `DELETE /api/v1/profiles/{id}` и `DELETE /api/v1/programs/{id}`, admin-only;
- [x] анкету с программами удалить нельзя (409 со списком блокеров): анкету
      заполнял человек и восстановить её невозможно, программа собирается заново;
- [x] программа удаляется целиком, со всеми версиями и записями доставок;
      история операций генерации сохраняется (`ON DELETE SET NULL`);
- [x] контракт удаления с зависимостями вынесен в `src/application/deletion.py`
      и стал общим для AI-конфигурации, анкет и программ;
- [x] UI: маркеры, фильтры, выбор порядка, удаление с подтверждением на
      `/profiles`, `/profiles/{id}` и `/programs`;
- [x] тесты: границы удаления на фейках, маркеры/фильтры/сортировка и оба
      `DELETE` на реальной PostgreSQL, запрет для роли наблюдателя.

**Осознанное ограничение:** маркер «скачано пользователем» не вводится. Telegram
Bot API не сообщает об открытии присланного документа, поэтому отслеживается
только факт отправки. Архивация вместо удаления тоже не вводится: она не решает
роста базы, а добавляет состояние и фильтр во все выборки.

#### 1.2-D — Worker / retry / recovery
- [ ] фоновые jobs;
- [ ] централизованный retry/backoff;
- [ ] retryable vs non-retryable errors (классификация уже есть в
      `GenerationErrorCode`/`GenerationErrorKind`, не хватает исполнителя);
- [ ] статус `RETRY_WAIT` и переход `FAILED → PENDING/RUNNING`;
- [ ] stale `RUNNING` recovery после restart/crash;
- [ ] acceptance worker/process crash.

#### 1.2-E — Delivery
- [ ] отдельное persistent delivery state/job;
- [ ] generation и delivery не повторяют друг друга;
- [ ] Telegram retry;
- [ ] delivery idempotency;
- [ ] acceptance Telegram temporary failure.

#### 1.2-F — Admin visibility
- [ ] generation status;
- [ ] delivery status;
- [ ] attempts/errors;
- [ ] requested/actual generation strategy;
- [ ] минимальная operational visibility без создания полноценного monitoring stack.

#### 1.2-G — E2E acceptance
- [ ] restart during questionnaire;
- [ ] restart during generation;
- [ ] restart during delivery;
- [ ] AI timeout/unavailable;
- [ ] deterministic fallback;
- [ ] duplicate generation request;
- [ ] duplicate delivery attempt;
- [ ] worker/process crash;
- [ ] stale `RUNNING` recovery.

**Правило:** 1.2-A…G реализуются отдельными проверяемыми PR. Каждый PR проходит CI и acceptance, после merge обновляются `PROJECT_STATUS.md` и этот roadmap.

### 1.2-0 — Connector & deployment baseline: DESIGN READY

Перед дальнейшим reliability engineering необходимо проверить систему на реальной production-like инфраструктуре.

- [x] архитектурно зафиксирован Connector Layer — `docs/architecture/CONNECTOR_LAYER.md`;
- [x] зафиксировано правило: сейчас конфигурация остаётся в ENV/secret variables;
- [x] определены будущие connector types: Telegram, PostgreSQL, Redis, MinIO/S3, SMTP;
- [x] определено разделение Connector Layer и специализированного AI Provider lifecycle;
- [x] зафиксирован deployment baseline и E2E acceptance matrix — `docs/architecture/DEPLOYMENT_AND_INTEGRATION_BASELINE.md`;
- [ ] получить параметры серверов и сетевые ограничения;
- [ ] выполнить Deployment Readiness Audit;
- [ ] развернуть staging/integration environment;
- [ ] выполнить реальный E2E acceptance;
- [ ] классифицировать найденные проблемы по 1.2-D / 1.2-E / 1.3.

**Важно:** 1.2-0 не реализует Connector Registry, Admin CRUD или runtime hot-swap. Это архитектурный baseline и deployment gate.

### 1.3 Operations and security
- [ ] structured logging и correlation IDs;
- [ ] error tracking/metrics;
- [ ] backup/restore PostgreSQL и media;
- [ ] rate limiting и abuse protection;
- [ ] production secrets review;
- [ ] deployment runbook;
- [ ] **repository governance:** включить branch protection для `main`, запретить прямой push, сделать успешный CI обязательным status check и определить минимальное правило review.

**Текущий repository-governance gap:** на 23.08.2026 `main` технически не защищён branch protection. Это не блокирует разработку Phase 1.2, но должно быть закрыто до начала командной/production-ориентированной работы. Если настройка GitHub repository settings недоступна через API-инструменты, выполнить её вручную в Settings → Branches/Rulesets.

**Exit criteria:** чистое окружение поднимается по документации; каталог и медиа импортируются; AI success, AI fallback и delivery failure проверены; restart не теряет критическое состояние; `main` защищён правилами merge/CI.

## Phase 2 — User feedback loop
- [ ] отметить тренировку выполненной;
- [ ] RPE/сложность/комментарий/пропуски;
- [ ] история тренировок и программ;
- [ ] агрегированный progress state;
- [ ] правила корректировки программы;
- [ ] explicit regenerate/adjust flow.

**Exit criteria:** система может обосновать следующую корректировку данными истории пользователя.

## Phase 3 — AI personalization
- [ ] profile analysis;
- [ ] feedback analysis;
- [ ] program adjustment;
- [ ] prompt/version experiments;
- [ ] quality scoring и human-review path;
- [ ] модель пользовательских предпочтений.

**Примечание:** использовать существующий AI Gateway, не создавать параллельный AI-контур.

## Phase 4 — AI Coach
- [ ] user chat;
- [ ] контекст программы, профиля и истории;
- [ ] лимиты и budget per user;
- [ ] безопасные границы ответов;
- [ ] шаблоны и накопление пользовательских паттернов.

## Phase 5 — Monetization
- [ ] тарифная модель;
- [ ] entitlements;
- [ ] quotas/usage;
- [ ] billing provider abstraction;
- [ ] платежные события и audit;
- [ ] graceful downgrade/limit messages.

## Правило приоритета

Новая функция не должна обходить существующие filtering, safety, validation, versioning и AI Gateway. Сначала закрываются exit criteria текущей фазы, затем начинается следующая.
