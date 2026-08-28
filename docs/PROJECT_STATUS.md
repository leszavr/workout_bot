# Текущий статус проекта

**Дата:** 23 августа 2026

## Кратко

Workout Bot — модульный монолит для Telegram: пользователь проходит анкету, система формирует профиль, отбирает упражнения с техническими safety-правилами, генерирует программу через AI или детерминированный генератор, валидирует её, сохраняет версию, собирает HTML с медиа и доставляет файл в Telegram.

## Что готово

### Этапы 1–2 — профиль и каталог: ГОТОВО
- анкета из 36 вопросов;
- review/confirmation и идемпотентная финализация;
- PostgreSQL и Alembic;
- каталог из 873 упражнений;
- admin notification;
- FastAPI и внутренний Next.js UI.

### Этап 3A — program pipeline: ГОТОВО
- filtering по оборудованию, опыту и предпочтениям;
- Candidate Pool с причинами исключения;
- централизованный Safety Framework;
- Safe Exercise Pool;
- детерминированный генератор;
- независимый validator;
- versioned storage и API/UI программ.

### Этап 3B — AI Gateway: ГОТОВО ТЕХНИЧЕСКИ
- providers/endpoints/models/tasks;
- OpenAI-compatible adapter;
- выбор primary/fallback моделей;
- encrypted secret storage;
- prompt versioning foundation;
- token accounting и audit events;
- Admin API/UI.

**Важно:** рабочая AI-конфигурация должна быть создана отдельно. С Phase 1.1 интерфейс `/ai` показывает, чего именно не хватает, и не даёт включить задачу в заведомо нерабочем состоянии.

### Этап 4 — AI Program Generator: ГОТОВО
- минимизированный generation context без Telegram ID, имени и profile ID;
- PromptLoader: DB-first, filesystem fallback;
- JSON extraction/parser;
- schema + business validation;
- до 2 repair attempts;
- подключение к pipeline генерации (с Phase 1.2-C — через `ProgramGenerationOrchestrator`).

### Этап 5 — generation → HTML → Telegram: ГОТОВО
- автоматическая orchestration после финализации;
- primary/fallback без циклов;
- идемпотентность;
- разделение generation и delivery;
- HTML renderer mobile-first;
- exercise media pipeline: импорт → WebP → MinIO → PostgreSQL metadata;
- доставка HTML-документа в Telegram;
- ограниченные retry и admin alert;
- rest timer в HTML.

### Phase 1.1 — AI configuration UX: ГОТОВО
- отчёт готовности AI-задачи (`GET /api/v1/admin/ai/readiness`): чек-лист шагов, эффективная цепочка моделей, фактическая стратегия генерации;
- панель готовности на `/ai` (видно, будет ли AI реально вызван);
- мастер «Быстрое подключение AI»: провайдер → эндпоинт с ключом → модель → проверка подключения → включение задачи;
- результат connection test сохраняется (`ai_endpoints.last_test_*`): «не проверялось» и «проверка провалилась» — разные состояния;
- серверный запрет включения задачи без работоспособной модели, с протоколом без адаптера или с несуществующей версией промпта;
- журнал вызовов AI и журнал изменений конфигурации в UI;
- протоколы без адаптера помечены и недоступны для выбора.

### Phase 1.1.1 — AI infrastructure management & reliability: ГОТОВО
- readiness влияет на runtime и заведомо нерабочая AI-конфигурация сразу переключается на deterministic generator;
- структурированные причины fallback и observability в админке;
- динамический AI Infrastructure Health Dashboard;
- configuration lifecycle provider/endpoint/model с safe delete;
- CI в GitHub Actions: backend на PostgreSQL, миграции, frontend lint/typecheck/build.

### Пользователи и доступ: ГОТОВО
- `admin_users` + `admin_identities`, scrypt-хеши, аварийный env-admin;
- роли `admin` и `viewer`, серверная защита mutating endpoint'ов;
- CRUD пользователей, смена своего пароля, одноразовый admin reset;
- защита от потери доступа: нельзя понизить, отключить или удалить последнего активного администратора, нельзя удалить себя;
- критическая проверка последнего администратора атомарна и защищена PostgreSQL transaction-scoped advisory lock;
- для DB-пользователей JWT идентифицирует запись, а актуальные role/activity/must_change_password читаются из БД на каждом защищённом запросе. Поэтому деактивация, удаление и изменение роли действуют немедленно;
- OAuth для Яндекс/VK/MAX подготовлен на уровне данных, но сами OAuth-флоу не реализованы.

## Post-merge sanity audit — 23.08.2026

После merge PR #5 `main` прошёл post-merge sanity audit:
- финальный CI перед merge: backend, migrations и frontend — SUCCESS;
- миграции проверены в цикле head → base → head;
- acceptance fixes из PR #6 вошли в PR #5 до merge;
- `PROJECT_STATUS.md` и `DEVELOPMENT_ROADMAP.md` синхронизированы с фактическим состоянием;
- Issues #7 и #8 относились к промежуточным падениям CI и закрыты после исправлений.

## Phase 1.2 — Reliability: DESIGN BASELINE APPROVED

23.08.2026 зафиксирован архитектурный design review в `docs/architecture/PHASE_1_2_RUNTIME_RELIABILITY.md`.

Целевой runtime: Persistent FSM → Finalization → Generation Job → AI/Deterministic → Validation → persisted Program → Delivery Job → Telegram. PostgreSQL остаётся source of truth для бизнес-состояния, Redis используется для устойчивого runtime/transient state.

Ключевые решения:
- generation и delivery — отдельные persistent операции;
- единый `ProgramGenerationOrchestrator` для Telegram и Admin API;
- idempotency boundary для генерации и доставки;
- централизованные retry/recovery правила;
- stale `RUNNING` jobs должны восстанавливаться после restart/crash;
- веб-кнопка Generate Program должна использовать тот же orchestration path;
- Phase 1.2 разбита на 1.2-A…1.2-G: FSM, generation state, orchestrator, worker/retry/recovery, delivery, admin visibility, E2E acceptance.

**Следующий рабочий этап:** Phase 1.2-D — Worker / retry / recovery.

### Phase 1.2-A — Persistent FSM: ГОТОВО

- состояние анкеты хранится в Redis (`REDIS_URL`) вместо `MemoryStorage`:
  перезапуск процесса бота не сбрасывает анкету, несколько экземпляров
  приложения работают с общим состоянием;
- `src/infrastructure/telegram/fsm_storage.py` — aiogram `RedisStorage` и
  `RedisEventIsolation` с ключами, включающими `bot_id`; изоляция обновлений
  общая для всех процессов, поэтому один ответ не обрабатывается дважды;
- бот не запускается без `REDIS_URL`, доступность проверяется до старта
  polling, соединения закрываются при остановке (повторное закрытие безопасно);
- сбой Redis нормализуется в `FSMStorageError` и превращается в понятное
  сообщение пользователю; ответы анкеты и подключение не попадают в логи;
- PostgreSQL остаётся source of truth: в Redis лежит только черновик анкеты,
  профиль сохраняется при подтверждении, и сбой runtime state не портит
  бизнес-данные;
- Redis добавлен в `docker/docker-compose.yml`, `workout-manager.sh`
  (start/stop/status/doctor/logs/test) и в CI, поэтому FSM-тесты выполняются
  реально, а не пропускаются.

### Phase 1.2-B — Persistent generation state + idempotency: ГОТОВО

- таблица `generation_jobs` (Alembic `0008`) хранит operational-состояние одной
  логической генерации: статус, число попыток, timestamps, код ошибки и ссылку на
  созданную версию программы;
- state machine `PENDING → RUNNING → SUCCEEDED|FAILED`; переход выполняется
  условным `UPDATE ... WHERE status = :expected`, поэтому один job нельзя
  запустить или закрыть дважды, а запрещённый переход отклоняется;
- **одна логическая генерация = (profile_id, бизнес-событие, номер попытки)**:
  одна анкета законно имеет несколько версий программы, поэтому `profile_id`
  как ключ не подходит; автогенерация после подтверждения анкеты и явный запрос
  администратора — разные события;
- идемпотентность обеспечивает PostgreSQL: `UNIQUE(idempotency_key)` +
  `INSERT ... ON CONFLICT DO NOTHING`. Два параллельных запроса одной логической
  генерации создают ровно один job и ровно одну программу; повтор при активной
  генерации получает HTTP 409, повтор после успеха — уже созданную программу;
- `GenerationJob` и `WorkoutProgram` — разные сущности: при отказе программа не
  создаётся, фиктивной записи ради хранения ошибки нет;
- длительный AI-вызов вынесен за границы транзакции: состояние меняется
  короткими транзакциями до и после генерации;
- ошибки разделены на non-retryable (конфигурация, валидация, отсутствующие
  данные) и transient (сеть, таймаут, rate limit, сбой persistence); сам retry
  ещё НЕ реализован;
- в job не попадают промпт, ответ провайдера, ключи и PII: сохраняются только
  стабильный код ошибки и короткое описание, из которого вычищаются секреты;
- `POST /api/v1/profiles/{id}/programs/generate` принимает необязательный
  `idempotency_key` и возвращает блок `generation` (статус, попытки, код ошибки,
  признак повторного использования);
- существующие пути генерации сохранены: AI-генерация, deterministic fallback и
  автозапуск после финализации работают как раньше.

### Phase 1.2-C — Generation Orchestrator: ГОТОВО

- `ProgramGenerationOrchestrator` — единственная application-level точка
  генерации. Telegram (автогенерация после подтверждения анкеты) и Admin API
  (кнопка `Generate Program`) приходят в неё; второго pipeline больше нет;
- `ProgramService.generate`/`build_pools` удалены: сервис отвечает только за
  чтение сохранённых версий программ. До этого этапа Admin API шёл своим
  конвейером — без readiness gate и без fallback;
- вход — `GenerationRequest`, выход — `OrchestratorResult`. Различие вызывающих
  слоёв выражено только запросом:
  - автогенерация: стратегия из `PROGRAM_PRIMARY_GENERATOR`/
    `PROGRAM_FALLBACK_GENERATOR`, `allow_fallback=True` — неработоспособный ИИ
    не ломает пользовательский сценарий;
  - запрос администратора: генератор выбран явно, `allow_fallback=False` —
    подменять выбор молча нельзя, администратор должен увидеть причину;
- fallback выполняется внутри одного job и строго один раз: primary → fallback
  → окончательный отказ. Циклов «AI → fallback → AI» нет, вторая программа и
  второй job не создаются;
- результат самодостаточен: программа, состояние job, запрошенный и фактический
  генератор, признак fallback и его машиночитаемая причина. Внутренние
  исключения AI Gateway наружу не выходят — отказ приходит как
  `GenerationFailedError` со стабильным кодом (`GenerationErrorCode`), и по
  этому коду выбирается HTTP-статус: 409 (уже выполняется), 422 (ошибка данных
  или валидации), 502 (сбой ИИ);
- ответ `POST /api/v1/profiles/{id}/programs/generate` расширен полями
  `requested_generator`, `actual_generator`, `fallback_used`,
  `fallback_reason_code`; существующие поля сохранены. `generation.status`
  зафиксирован как non-nullable: успешный ответ всегда означает завершённую
  генерацию, остальные operational-поля остались nullable;
- клиентский `idempotency_key` — обещание «это тот же запрос». Повторное
  использование с другим генератором возвращает 409: отдать программу прежнего
  генератора значило бы отменить явный выбор администратора, а создать второй job
  под тем же ключом — разрушить идемпотентность. Серверный ключ попытки
  (`profile:trigger:attempt`) под это правило не попадает: его вызывающая сторона
  не выбирала, и повторный finalize по-прежнему получает готовую программу;
- классификация отказов единственная: `classify_error` → `GenerationErrorCode` →
  `AIFallbackReason`. Второй разбор иерархии исключений удалён, поэтому
  operational-запись и журнал администратора не могут описывать одну причину
  по-разному;
- границы транзакций и идемпотентность взяты из 1.2-B без изменений: короткая
  транзакция на создание/переход job, генерация (включая AI-вызов) вне
  транзакции, короткая транзакция на закрытие. Собственного lock оркестратор не
  вводит;
- граница закреплена архитектурным тестом `tests/unit/test_generation_boundary.py`:
  он статически запрещает Telegram gateway и Admin API обращаться к
  генераторам, validator, safety, записи программы и переходам job.

**Осознанное ограничение:** retry, worker и stale-recovery не реализованы —
это Phase 1.2-D. Delivery остаётся отдельной операцией и в оркестратор не
входит (Phase 1.2-E).

### AI reliability + prompt management: ГОТОВО

Работа по FINDING-1 из `docs/infrastructure/STAGING_FULL_E2E_ACCEPTANCE_REPORT.md`
(ИИ не собрал программу, сработал детерминированный fallback). Подробности —
`docs/reports/AI_RELIABILITY_AND_PROMPT_MANAGEMENT_REPORT.md`.

- **repair получает достаточный контекст**: исходный system-промпт (правила и
  схема), собственный предыдущий ответ модели, ошибки валидации и полный
  перечень разрешённых `exercise_external_id`; выдуманные идентификаторы
  называются в запросе прямо. Раньше уходило одно `user`-сообщение «исправь эти
  ошибки», вход сжимался с ~6700 до ~120 токенов, и модель деградировала;
- **невалидный вывод переводит генерацию на следующую модель цепочки**: перебор
  ведёт `AIProgramGenerator`, потому что транспортный успех (`200 OK` с
  выдуманным упражнением) не равен пригодному ответу. `AIGateway` получил
  двухшаговый контракт `prepare()` + `generate_once()`; прежний `generate()` с
  внутренним перебором сохранён;
- deterministic fallback остался последним рубежом и решает его по-прежнему
  оркестратор: генератор отказывает только после исчерпания всей AI-цепочки;
- валидатор не ослаблен: выдуманный `external_id` остаётся ошибкой
  `exercise_not_found`;
- **журнал попыток моделей** (`GET /api/v1/admin/ai/model-attempts`, событие
  `ai_model_attempts`): по каждой генерации видно, прошёл ли первый ответ модели,
  сколько было исправлений, почему модель оставлена и дошла ли очередь до
  резервной. Промптов, ответов моделей и PII в журнале нет;
- **управление инструкциями для ИИ** через Admin API и Admin Web: список версий
  с превью, полный текст без усечения, создание, правка, удаление. Вкладка
  `/ai/prompts`;
- **политика удаления промпта**: hard delete, потому что внешних ключей на
  `prompt_templates` нет и история не рвётся. Единственный блокер — инструкция,
  выбранная в настройках задачи: удаление отклоняется 409 со списком блокеров,
  так как ссылка логическая (`prompt_version`) и база её не защищает;
- правка существующей версии разрешена сознательно: нумерация новой версии на
  каждую формулировку превращает список в мусор. `task_type` и `version` при
  правке не меняются — это идентичность промпта;
- миграций нет: схема `prompt_templates` уже содержала всё необходимое.

## Открытые проблемы и риски

### P0 — до реального production
1. Production hardening: централизованные логи, error tracking/metrics,
   backup/restore, rate limits и эксплуатационные процедуры. Устойчивое FSM
   storage закрыто в Phase 1.2-A; backup/restore Redis не требуется — там
   только незавершённые анкеты.
2. **Нет rate limiting на вход в админку** — перебор пароля остаётся задачей Phase 1.3.
3. End-to-end verification на чистом окружении: миграции, импорт каталога/медиа, AI primary, deterministic fallback, delivery failure/retry.
4. Проверка безопасности production-конфигурации и секретов.
5. Часть интеграционных тестов требует каталога упражнений; CI засеивает его автоматически.
6. `alembic check` сообщает о косметическом расхождении ORM-моделей и миграций (`unique=True` против UniqueConstraint); это существовало до текущего этапа.
7. Job, оставшийся в `RUNNING` после падения процесса, никто не восстанавливает: retry, worker и stale-recovery — Phase 1.2-D. До этого повторный запрос такой генерации будет отклоняться как «уже выполняется».
8. `GenerateProgramRequest.prompt_version` не используется: поле принимается API, но никуда не передаётся. Версия инструкции берётся из настроек задачи (`ai_task_configs.prompt_version`), и этого достаточно для промпт-инжиниринга: администратор выбирает версию в задаче и запускает генерацию. Поле осталось со времён до Phase 1.2-C; удаление меняет публичный контракт, поэтому вынесено отдельно.

### P1 — продуктовый цикл
- повторная/явная генерация и удобный статус программы;
- feedback после тренировок;
- история и прогресс;
- корректировка программы;
- уведомления и напоминания.

### P2 — коммерческий контур
- тарифы;
- entitlements/usage limits;
- usage accounting;
- billing;
- аудит коммерческих операций.

## Что НЕ следует считать готовым

- conversational AI coach;
- адаптация программы по длительной обратной связи;
- полноценный production monitoring;
- доказанная production-эксплуатация;
- монетизация.

## Следующий приоритет

Phase 1.2-D: Worker / retry / recovery — фоновая обработка, централизованный retry и восстановление stale `RUNNING` job после падения процесса. Подробный design baseline — `docs/architecture/PHASE_1_2_RUNTIME_RELIABILITY.md`; порядок дальнейших работ — `DEVELOPMENT_ROADMAP.md`.
