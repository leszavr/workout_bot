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

#### 1.2-B — Generation domain state
- [ ] формализовать persistent generation status model;
- [ ] определить idempotency boundary/key;
- [ ] persistence для generation jobs/state;
- [ ] acceptance duplicate generation requests.

#### 1.2-C — Generation Orchestrator
- [ ] единый `ProgramGenerationOrchestrator`;
- [ ] Telegram и Admin API используют один orchestration path;
- [ ] readiness/fallback/safety/validation не обходятся;
- [ ] веб-кнопка `Generate Program` переводится на общий путь.

#### 1.2-D — Worker / retry / recovery
- [ ] фоновые jobs;
- [ ] централизованный retry/backoff;
- [ ] retryable vs non-retryable errors;
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
