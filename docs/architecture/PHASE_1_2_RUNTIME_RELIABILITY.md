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

## 10. Restart / crash recovery

После restart система должна обнаруживать jobs, которые были `RUNNING` до остановки процесса, и безопасно возвращать их в обработку либо переводить в корректное terminal state по установленным правилам.

Нельзя оставлять job навсегда в `RUNNING` из-за падения worker/process.

Recovery должен быть идемпотентным: повторное выполнение не создаёт вторую программу и не отправляет дубликаты без необходимости.

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
idempotency и интеграция в существующие точки генерации (`ProgramService` для
Admin API, `ProgramGenerationOrchestrator` для Telegram). Retry, worker и
recovery по-прежнему не реализованы: stale `RUNNING` после падения процесса
остаётся открытым и закрывается в 1.2-D.

### 1.2-C — Generation Orchestrator

Единая application точка генерации для Telegram и Admin API.

### 1.2-D — Worker / retry / recovery

Фоновая обработка, retry, stale-job recovery и crash safety.

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
