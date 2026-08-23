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

**Важно:** рабочая AI-конфигурация должна быть создана отдельно. С Phase 1.1
интерфейс `/ai` показывает, чего именно не хватает, и не даёт включить задачу
в заведомо нерабочем состоянии.

### Этап 4 — AI Program Generator: ГОТОВО
- минимизированный generation context без Telegram ID, имени и profile ID;
- PromptLoader: DB-first, filesystem fallback;
- JSON extraction/parser;
- schema + business validation;
- до 2 repair attempts;
- подключение к ProgramService.

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
- отчёт готовности AI-задачи (`GET /api/v1/admin/ai/readiness`): чек-лист
  шагов, эффективная цепочка моделей, фактическая стратегия генерации;
- панель готовности на `/ai` (видно, будет ли AI реально вызван);
- мастер «Быстрое подключение AI»: провайдер → эндпоинт с ключом → модель →
  проверка подключения → включение задачи;
- результат connection test сохраняется (`ai_endpoints.last_test_*`):
  «не проверялось» и «проверка провалилась» — разные состояния;
- серверный запрет включения задачи без работоспособной модели, с протоколом
  без адаптера или с несуществующей версией промпта;
- журнал вызовов AI и журнал изменений конфигурации в UI;
- протоколы без адаптера помечены и недоступны для выбора.

### Phase 1.1.1 — AI infrastructure management & reliability: ГОТОВО
- readiness влияет на runtime и заведомо нерабочая AI-конфигурация сразу
  переключается на deterministic generator;
- структурированные причины fallback и observability в админке;
- динамический AI Infrastructure Health Dashboard;
- configuration lifecycle provider/endpoint/model с safe delete;
- CI в GitHub Actions: backend на PostgreSQL, миграции, frontend lint/typecheck/build.

### Пользователи и доступ: ГОТОВО
- `admin_users` + `admin_identities`, scrypt-хеши, аварийный env-admin;
- роли `admin` и `viewer`, серверная защита mutating endpoint'ов;
- CRUD пользователей, смена своего пароля, одноразовый admin reset;
- защита от потери доступа: нельзя понизить, отключить или удалить последнего
  активного администратора, нельзя удалить себя;
- критическая проверка последнего администратора атомарна и защищена
  PostgreSQL transaction-scoped advisory lock;
- для DB-пользователей JWT идентифицирует запись, а актуальные role/activity/
  must_change_password читаются из БД на каждом защищённом запросе. Поэтому
  деактивация, удаление и изменение роли действуют немедленно;
- OAuth для Яндекс/VK/MAX подготовлен на уровне данных, но сами OAuth-флоу
  не реализованы.

## Открытые проблемы и риски

### P0 — до реального production
1. Production hardening: Redis/устойчивое FSM storage, централизованные логи,
   error tracking/metrics, backup/restore, rate limits и эксплуатационные процедуры.
2. **Нет rate limiting на вход в админку** — перебор пароля остаётся задачей Phase 1.3.
3. End-to-end verification на чистом окружении: миграции, импорт каталога/медиа,
   AI primary, deterministic fallback, delivery failure/retry.
4. Проверка безопасности production-конфигурации и секретов.
5. Веб-кнопка `Generate Program` пока идёт по отдельному пути через
   `ProgramService` без fallback/gate; требуется единая точка генерации через
   `ProgramGenerationOrchestrator` (Phase 1.2).
6. Часть интеграционных тестов требует каталога упражнений; CI засеивает его
   автоматически.
7. `alembic check` сообщает о косметическом расхождении ORM-моделей и миграций
   (`unique=True` против UniqueConstraint); это существовало до текущего этапа.

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

Phase 1.2: устойчивое FSM storage, restart/recovery, единая точка генерации,
формальная generation/delivery status model и E2E idempotency/retry acceptance.
Затем Phase 1.3: operations/security. Подробный порядок —
`DEVELOPMENT_ROADMAP.md`.
