# Phase 1.2 — Runtime Reliability Design Review

**Дата:** 23 августа 2026  
**Статус:** APPROVED DESIGN BASELINE

## 1. Цель

Phase 1.2 переводит runtime из модели, где критическое состояние и orchestration зависят от процесса приложения, в restart-safe модель с PostgreSQL как source of truth и устойчивым runtime state.

Целевой pipeline:

```text
Telegram
  ↓
Persistent FSM
  ↓
Questionnaire
  ↓
Finalization
  ↓
Generation Job
  ↓
AI / Deterministic
  ↓
Validation
  ↓
Program persisted
  ↓
Delivery Job
  ↓
Telegram
  ↓
Completed
```

Главные требования: restart/recovery, идемпотентность, разделение generation и delivery, retry/recovery и единая точка orchestration.

## 2. Границы

Phase 1.2 включает:

- persistent FSM вместо `MemoryStorage`;
- формальную модель состояния generation и delivery;
- единый `ProgramGenerationOrchestrator`;
- persistent jobs и idempotency;
- retry и recovery после restart/crash;
- отдельную delivery operation;
- E2E acceptance критических failure-сценариев;
- минимальную admin visibility состояния generation/delivery там, где она нужна для эксплуатации.

Phase 1.2 **не включает** полноценный monitoring stack, billing, conversational coach или долгосрочную персонализацию.

## 3. Source of truth

PostgreSQL остаётся главным хранилищем бизнес-состояния:

- profiles;
- programs;
- generation state;
- delivery state;
- audit/operational records.

Redis используется для устойчивого runtime/transient state, прежде всего FSM и, если это потребуется существующей архитектуре, координации фоновых задач. Redis не становится заменой PostgreSQL для бизнес-данных.

## 4. Questionnaire state

FSM должен переживать restart процесса и работать при нескольких экземплярах приложения.

Целевое бизнес-состояние questionnaire:

```text
DRAFT → IN_PROGRESS → COMPLETED → FINALIZED
```

Техническая реализация может использовать существующие aiogram FSM state names; эти состояния не должны дублировать или заменять persistent business state без необходимости.

## 5. Generation state

Вводится persistent generation job/state с минимум следующими семантиками:

```text
PENDING
  ↓
RUNNING
  ├── RETRY_WAIT
  ├── SUCCEEDED
  └── FAILED
```

Рекомендуемые поля: job id, profile id, status, strategy/requested generator, idempotency key, attempt count, last error code/message, timestamps и next retry time. Точные поля должны следовать существующим моделям/репозиториям и принципу KISS; не добавлять поля только «на будущее».

`FAILED` означает окончательную неуспешную попытку, но не потерю данных: состояние должно быть диагностируемым и, если ошибка retryable, поддерживать повторную обработку.

### 5.1 Реализовано в 1.2-B

Таблица `generation_jobs` (`GenerationJobRow`), домен — `src/domain/generation.py`,
репозиторий — `src/infrastructure/persistence/postgres/generation_job_repository.py`,
application-слой — `src/application/programs/generation_jobs.py`.

Фактическая state machine:

```text
PENDING → RUNNING → SUCCEEDED
                 └→ FAILED
```

| Из | В | Разрешён |
| --- | --- | --- |
| PENDING | RUNNING | да |
| RUNNING | SUCCEEDED | да |
| RUNNING | FAILED | да |
| PENDING | SUCCEEDED / FAILED | нет |
| SUCCEEDED | любое | нет |
| FAILED | любое | нет (`FAILED → PENDING/RUNNING` зарезервировано для 1.2-D) |

`RETRY_WAIT` не введён сознательно: планировщика повторов ещё нет, и статус без
обработчика оставлял бы job в состоянии, из которого его никто не выводит. Он
появляется вместе с worker/retry (1.2-D).

Класс ошибки (`GenerationErrorKind`) вычисляется по стабильному коду
(`GenerationErrorCode`), сохранённому в момент отказа: конфигурация, валидация и
отсутствующие данные — `non_retryable`; сеть, таймауты, rate limit и сбой
persistence — `transient`. Retry по этой классификации в 1.2-B не выполняется.

Сохраняются только код ошибки и короткое безопасное описание: промпт, ответ
провайдера, ключи и заголовки авторизации в job не попадают (перед записью
сообщение проходит редактирование секретов).

### 5.2 Реализовано в 1.2-D

Переход `FAILED → RUNNING` открыт: у неуспешной попытки появился обработчик.

| Из | В | Разрешён |
| --- | --- | --- |
| PENDING | RUNNING | да |
| RUNNING | SUCCEEDED / FAILED | да |
| FAILED | RUNNING | да (только через захват воркером) |
| FAILED | PENDING | нет |
| SUCCEEDED | любое | нет |

`RETRY_WAIT` не введён и здесь. Ожидание повтора — это `FAILED` с заполненным
`next_attempt_at`, а не отдельный статус: `FAILED` уже означает «попытка
закончилась неудачей», и второе значение с тем же смыслом заставило бы каждого
читателя (админка, аналитика, `next_attempt`) проверять два статуса вместо
одного.

Новые поля `generation_jobs` (миграция `0012`):

- `next_attempt_at` — момент, с которого повтор допустим. NULL = повтор не
  назначен: так выглядят и успешные job, и окончательные отказы;
- `lease_owner` / `lease_expires_at` — аренда захваченного job.

Возврата в `PENDING` нет: повтор всегда выполняет тот, кто захватил job, поэтому
состояние «повтор назначен, но никем не взят» выражается полем, а не статусом.

Non-retryable отказ и исчерпание попыток дают одинаковое внешнее состояние —
`FAILED` без `next_attempt_at`. Для администратора это один и тот же факт
«система больше не пробует»; отличие читается по коду ошибки и числу попыток.

## 6. Delivery state

Generation и delivery — две разные операции.

```text
PENDING → RUNNING → RETRY_WAIT → DELIVERED
                       └────────→ FAILED
```

Правило: программа сначала должна быть сохранена, затем создаётся/актуализируется delivery state. Ошибка Telegram не должна приводить к повторной генерации программы.

После успешной отправки сохраняется Telegram message/file identifier в соответствии с существующим безопасным контрактом.

## 7. Generation Orchestrator

Нужна единая application-level точка:

`ProgramGenerationOrchestrator`

Она должна владеть единым pipeline:

```text
profile/context
  ↓
readiness / strategy selection
  ↓
AI or deterministic generation
  ↓
safety / validation
  ↓
program persistence
  ↓
delivery job creation
```

Telegram handlers и Admin API не должны самостоятельно собирать альтернативные generation pipelines.

Особенно важно устранить текущий известный gap: веб-кнопка `Generate Program` должна использовать тот же orchestration path, что и автоматическая генерация.

### 7.1 Реализовано в 1.2-C

**Статус: DONE.** Генерация имеет ровно одну application-level точку.

Контракт: вход — `GenerationRequest`, выход — `OrchestratorResult`
(`src/application/programs/orchestrator.py`).

```text
Telegram (auto finalization) ──┐
                               ▼
                     GenerationRequest
                               ▼
              ProgramGenerationOrchestrator
                               ▲
                               │
Admin API (POST …/programs/generate) ──┘
```

Различие вызывающих слоёв выражено только запросом, а не отдельным конвейером:

| | requested_generator | allow_fallback | reuse_existing |
| --- | --- | --- | --- |
| Автогенерация после finalize | из конфигурации | да | да |
| Запрос администратора | выбран явно | нет | нет |

`allow_fallback=False` — сознательное решение: администратор выбрал генератор
сам, и молчаливая подмена скрыла бы от него неработоспособность ИИ. Отказ
приходит как HTTP 502, программа не создаётся.

Второй pipeline устранён: `ProgramService.generate`/`build_pools` удалены,
сервис только читает сохранённые версии программ. До 1.2-C Admin API шёл своим
путём — без readiness gate и без fallback.

Ошибки наружу: оркестратор отдаёт `GenerationFailedError` со стабильным
`GenerationErrorCode`. Telegram и HTTP не разбирают внутренние исключения AI
Gateway; HTTP-статус выбирается по коду (409 / 422 / 502). Секреты вычищаются
из текста отказа перед выходом наружу. Недопустимый генератор — тоже доменный
отказ (`validation_failed`), а не `ValueError`: оркестратор обязан отвечать
одинаково любому вызывающему слою, а не только HTTP с pydantic-валидацией.

Классификация отказа выполняется один раз:

```text
exception → classify_error() → GenerationErrorCode → AIFallbackReason
```

Второй разбор иерархии исключений удалён. Он давал общий `ai_runtime_failure`
для rate limit, сетевого сбоя и неподдерживаемого протокола, из-за чего
operational-запись и журнал администратора описывали одну причину по-разному.
Таблица `_FALLBACK_REASON_BY_CODE` обязана быть полной: тест сверяет её с
`GenerationErrorCode`, поэтому новый код нельзя добавить, не решив, как он виден
администратору.

Идемпотентность клиентского ключа: `idempotency_key` — обещание вызывающей
стороны «это тот же запрос». Повторное использование с другим
`requested_generator` возвращает `IdempotencyKeyConflictError` (HTTP 409): отдать
программу прежнего генератора значило бы отменить явный выбор администратора, а
создать второй job под тем же ключом — разрушить DB-enforced идемпотентность.
Конфликт проверяется до разбора статуса, потому что он не зависит от того, чем
закончилась предыдущая генерация. Серверный ключ попытки
(`profile:trigger:attempt`) под правило не попадает: его вызывающая сторона не
выбирала, смена генератора там означает изменение конфигурации приложения между
запусками, и повторный finalize должен получать готовую программу, а не ошибку.

Fallback выполняется внутри одного job и строго один раз: primary → fallback →
окончательный отказ. Циклов нет, второй job и вторая программа не создаются.

Границы транзакций и идемпотентность унаследованы из 1.2-B без изменений;
собственного lock оркестратор не вводит.

Граница закреплена архитектурным тестом
`tests/unit/test_generation_boundary.py`: он статически запрещает Telegram
gateway и Admin API обращаться к генераторам, `ProgramValidator`,
`SafetyEngine`, записи программы и переходам состояния job. Легитимное
исключение — `apps/backend/api/v1/dependencies.py`, где pipeline собирается один
раз для обоих слоёв.

Не входит в 1.2-C: retry, worker, stale `RUNNING` recovery (1.2-D) и delivery
(1.2-E). Delivery по-прежнему выполняется после генерации отдельной операцией и
частью оркестратора не является.

## 8. Idempotency

Повтор одного логического запроса генерации не должен создавать несколько независимых программ.

Для generation должна существовать стабильная логическая idempotency boundary, например комбинация существующего business identifier + generation/version context или отдельный idempotency key. Конкретный ключ выбирается после анализа текущих моделей и потребителей.

Обязательные acceptance cases:

- повторный запрос до начала job;
- повторный запрос во время `RUNNING`;
- повторный запрос после `SUCCEEDED`;
- повтор после process crash;
- повтор delivery после неизвестного результата Telegram call.

### 8.1 Выбранная boundary (1.2-B)

**Одна логическая генерация определяется как: (profile_id, бизнес-событие
генерации, номер попытки этого события).** Ключ строится детерминированно:
`{trigger}:{profile_id}:{attempt}`; вызывающая сторона может задать вместо этого
свой ключ, и тогда он нормализуется в `client:{profile_id}:{key}`.

Почему не `profile_id`:

- одна анкета законно имеет несколько программ — `workout_programs` версионирует
  их по `(program_id, version)`, историю не перезаписывает, и админ-API
  сознательно позволяет собрать новую версию;
- автоматическая генерация после подтверждения анкеты и явный запрос
  администратора — разные бизнес-события: повтор одного не должен подавлять
  другое, поэтому триггер входит в идентичность;
- «попытка» нужна, чтобы после неудачи можно было законно повторить генерацию,
  не смешивая её с предыдущим отказом.

Номер попытки считается по числу исчерпанных job того же триггера:

- автогенерация: исчерпанным считается только `FAILED` (успех означает, что
  программа уже есть, и повторный finalize обязан вернуть её, а не собрать
  вторую). Исключение — успешный job, потерявший ссылку на программу: иначе
  автогенерация оказалась бы заблокированной навсегда;
- запрос администратора: исчерпан любой завершённый job, потому что администратор
  просит именно новую программу.

Идемпотентность обеспечивает PostgreSQL: `UNIQUE(idempotency_key)` +
`INSERT ... ON CONFLICT (idempotency_key) DO NOTHING`. Проверки «если нет — создай»
на стороне приложения нет: она не защищает несколько backend-процессов.

Concurrency: два параллельных запроса одной логической генерации получают
одинаковый ключ; вставку выигрывает один, второй читает победителя. Если job
активен, повторный запрос получает `GenerationAlreadyRunningError` (HTTP 409) и
второй генерации не запускает. Advisory lock не потребовался: уникальности ключа
и условного `UPDATE ... WHERE status = :expected` достаточно.

Границы транзакций: длительный AI-вызов не выполняется внутри транзакции.

```text
tx: определить номер попытки
tx: создать job (ON CONFLICT DO NOTHING) → PENDING
tx: PENDING → RUNNING
-- вне транзакции: генерация (AI/deterministic) + сохранение программы
tx: RUNNING → SUCCEEDED | FAILED
```

Связь с программой: `GenerationJob.program_id/program_version` ссылаются на
конкретную версию `workout_programs` (`ON DELETE SET NULL`). При отказе программа
не создаётся, фиктивной записи ради хранения ошибки нет.

## 9. Retry policy

Retry централизуется на application/worker уровне.

Retryable ошибки обычно включают transient network/provider/Telegram/database failures. Non-retryable — invalid input, safety/validation failure и заведомо неверная конфигурация.

Backoff и максимальное число попыток должны использовать существующие retry conventions проекта и конфигурацию AI Gateway, а не вводить несколько независимых механизмов без необходимости.

### 9.1 Реализовано в 1.2-D

Единая политика `RetryPolicy` (`src/domain/retry.py`) на два потребителя —
генерацию и доставку. Значения задаются конфигурацией, не кодом:

| Параметр | Значение | Обоснование |
| --- | --- | --- |
| `WORKER_MAX_ATTEMPTS` | 3 | Внутри одной попытки AI-контур уже перебирает все подключённые модели. Внешние повторы лечат только недоступность провайдера, а её решают планово |
| `WORKER_RETRY_INITIAL_DELAY_SECONDS` | 60 | Больше типичного сетевого сбоя и окна rate limit провайдера |
| `WORKER_RETRY_MULTIPLIER` | 4 | Паузы 60 с → 240 с: второй повтор попадает за пределы короткой аварии |
| `WORKER_RETRY_MAX_DELAY_SECONDS` | 900 | Дальше повтор уезжает за границу разумного ожидания пользователя |
| `WORKER_LEASE_SECONDS` | 1860 | Чуть больше `MAX_TOTAL_BUDGET_SECONDS` (1800 с): короткая аренда отобрала бы job у живого исполнителя |
| `WORKER_POLL_INTERVAL_SECONDS` | 15 | Повтор transient-отказа не срочен, а холостой опрос — это запрос к БД |

Бесконечных повторов нет: `max_attempts` — жёсткая граница.

Повторяются только `transient`-коды и только триггер `auto_finalization`.
`admin_request` система не повторяет: администратор выбрал генератор и запретил
fallback, ответ с причиной отказа уже отдан, и молчаливая пересборка программы
отменила бы его решение.

## 10. Restart / crash recovery

После restart система должна обнаруживать jobs, которые были `RUNNING` до остановки процесса, и безопасно возвращать их в обработку либо переводить в корректное terminal state по установленным правилам.

Нельзя оставлять job навсегда в `RUNNING` из-за падения worker/process.

Recovery должен быть идемпотентным: повторное выполнение не создаёт вторую программу и не отправляет дубликаты без необходимости.

### 10.1 Реализовано в 1.2-D

Признак «застрял» — истёкшая аренда, а не время в статусе. Время в `RUNNING`
здесь не работает: легальная AI-генерация занимает до 30 минут, и любой порог
по `started_at` либо отбирал бы job у живого исполнителя, либо не отличал бы
зависший job от работающего.

`RUNNING` без аренды не трогается: такой job создан синхронным путём (Telegram
или Admin API) в другом процессе, который воркеру не подотчётен. Это осознанное
ограничение текущего шага — оно снимается вместе с переносом генерации в worker
(следующая задача, вынос Gateway за сетевую границу).

Идемпотентность повтора обеспечивается тем, что повтор — это *та же* логическая
генерация: `idempotency_key` не пересчитывается, job не создаётся заново,
растёт только `attempts`. Поэтому вторая программа появиться не может.

Взаимное исключение: `SELECT ... FOR UPDATE SKIP LOCKED` в той же транзакции,
что и перевод в `RUNNING`. Redis для этого не используется — после его потери
обработка продолжается.

## 11. Delivery

Delivery выполняется независимо от generation:

```text
Program persisted
  ↓
Delivery state persisted
  ↓
Telegram send
  ↓
Delivered state
```

Если Telegram временно недоступен, retry относится к delivery, а не к generation.

## 12. Admin observability

Администратор должен иметь возможность определить минимум:

- generation status;
- requested/actual strategy;
- attempt count;
- last error code;
- delivery status;
- delivery attempts;
- возможность отличить generation failure от delivery failure.

Не требуется строить полноценную monitoring platform в Phase 1.2.

## 13. Обязательные failure scenarios

Acceptance Phase 1.2 должен покрыть:

1. restart во время questionnaire;
2. restart во время generation;
3. restart во время delivery;
4. AI timeout;
5. AI unavailable → deterministic fallback;
6. Telegram temporary failure;
7. duplicate generation request;
8. duplicate delivery attempt;
9. worker/process crash;
10. stale `RUNNING` job recovery.

## 14. Порядок реализации

### 1.2-A — Persistent FSM

Redis-backed FSM и restart-safe questionnaire.

### 1.2-B — Generation domain state

Persistent generation state/job, status model и idempotency boundary.

**Статус: DONE.** Введены `generation_jobs`, state machine, DB-enforced
idempotency и интеграция в существовавшие тогда точки генерации (`ProgramService`
для Admin API, `ProgramGenerationOrchestrator` для Telegram; в 1.2-C эти два
пути объединены). Retry, worker и recovery по-прежнему не реализованы: stale
`RUNNING` после падения процесса остаётся открытым и закрывается в 1.2-D.

### 1.2-C — Generation Orchestrator

Единая application точка генерации для Telegram и Admin API.

**Статус: DONE.** Введены `GenerationRequest`/`OrchestratorResult`, стратегия
определяется на запрос, второй pipeline (`ProgramService.generate`) удалён,
Admin endpoint переведён на оркестратор, наружный контракт ошибки — стабильный
код вместо исключений AI Gateway. Retry, worker и recovery не реализованы
(1.2-D), delivery остаётся отдельной операцией (1.2-E).

### 1.2-D — Worker / retry / recovery

Фоновая обработка, retry, stale-job recovery и crash safety.

**Статус: DONE.** Введён отдельный контейнер-worker (`apps/worker`), очередь
повторов и аренда в PostgreSQL (миграция `0012`), политика повторов
(`src/domain/retry.py`), открыт переход `FAILED → RUNNING`. Повтор генерации
идёт через `ProgramGenerationOrchestrator.retry`, повтор доставки — через
существующий `ProgramDeliveryService.redeliver`. Подробности — раздел 9.1 и 10.1,
отчёт `docs/reports/PHASE_1_2_D_WORKER_RETRY_RECOVERY_REPORT.md`.

### 1.2-E — Delivery

Отдельный delivery state/job, retry и delivery idempotency.

### 1.2-F — Admin visibility

Минимальная operational visibility generation/delivery.

### 1.2-G — E2E acceptance

Полный набор restart/idempotency/retry/failure сценариев.

Каждый подпункт выполняется отдельным проверяемым PR. Не смешивать несколько архитектурных границ в один большой PR без необходимости.

## 15. Правила для разработчиков и агентов

- Перед работой обязательно получить актуальный `main` из репозитория и прочитать `AGENTS.md`, `PROJECT_STATUS.md`, `DEVELOPMENT_ROADMAP.md` и этот документ.
- Перед изменением core/shared/public contract найти всех потребителей.
- Сначала расширять существующие механизмы проекта; не создавать параллельные storage/orchestration/retry контуры.
- PostgreSQL остаётся source of truth для бизнес-состояния.
- Нельзя обходить Safety Framework, Validator, ProgramRepository или AI Gateway ради нового runtime path.
- Не помещать Telegram ID, имя и другие лишние PII в AI generation context.
- Не использовать UI-only проверки вместо серверных.
- Для каждого подпункта: tests → CI → acceptance → merge → обновление документации.

## 16. Exit criteria Phase 1.2

Phase 1.2 считается завершённой, когда:

- FSM переживает restart;
- generation и delivery имеют формальное persistent состояние;
- существует единый generation orchestrator;
- duplicate requests безопасно обрабатываются;
- retry/recovery работают для заявленных transient failures;
- stale jobs восстанавливаются после crash/restart;
- generation не повторяется из-за ошибки delivery;
- обязательные E2E failure scenarios проходят CI;
- документация и roadmap соответствуют фактическому коду.
