# Development Roadmap

**Базовая точка:** завершены этапы 1–5. План строится от фактического состояния репозитория на 21.08.2026.

## Phase 0 — Documentation baseline: DONE
- [x] зафиксировать архитектуру;
- [x] зафиксировать текущий статус;
- [x] описать продукт и границы;
- [x] создать roadmap;
- [x] описать эксплуатацию и AI;
- [x] ввести правила поддержания документации.

## Phase 1 — Production readiness: NEXT

### 1.1 AI configuration UX
- [ ] guided setup для `workout_generation`;
- [ ] показывать только реально поддерживаемые протоколы или явно маркировать ограничения;
- [ ] configuration health/status;
- [ ] тест подключения до включения задачи;
- [ ] usage/error visibility.

### 1.2 Reliability
- [ ] заменить MemoryStorage на устойчивое production storage;
- [ ] проверить restart/recovery сценарии;
- [ ] формализовать generation/delivery status model;
- [ ] E2E idempotency и retry acceptance.

### 1.3 Operations and security
- [ ] structured logging и correlation IDs;
- [ ] error tracking/metrics;
- [ ] backup/restore PostgreSQL и media;
- [ ] rate limiting и abuse protection;
- [ ] production secrets review;
- [ ] deployment runbook.

**Exit criteria:** чистое окружение поднимается по документации; каталог и медиа импортируются; AI success, AI fallback и delivery failure проверены; restart не теряет критическое состояние.

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
