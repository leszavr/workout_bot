# Changelog / История этапов

## 2026-08-22 — Phase 1.1.1: AI infrastructure management & reliability

Readiness и fallback:
- `AIReadinessService.runtime_gate()`: readiness теперь влияет на генерацию.
  При заведомо нерабочей конфигурации AI-запрос не выполняется вообще,
  оркестратор сразу берёт детерминированный генератор;
- структурированные причины fallback (`AIFallbackReason`) с разделением
  configuration (AI не вызывался) и runtime (AI вызывался и не смог);
- `GenerationInfo.fallback_reason_code` хранит машиночитаемую причину рядом с
  человекочитаемой (миграция не требуется: программа лежит в JSONB);
- fallback пишется в существующий журнал событий AI-контура
  (`ai_generation_fallback`) без персональных данных;
- `GET /api/v1/admin/ai/fallback-events` + раздел «Почему AI не сработал» в
  админке: requested/actual generator, причина, признак «AI вызывался».

Infrastructure health:
- `AIInfrastructureHealthService` и `GET /api/v1/admin/ai/infrastructure-health`:
  дерево provider → endpoint → model → задачи строится динамически из
  конфигурации, собственного реестра нет ни в backend, ни во frontend;
- разделены configuration state (`enabled`), infrastructure health
  (`AIHealthState`) и model availability (`AIModelAvailability`);
- health выводится из сохранённого connection test и последнего реального
  AI-вызова: сценарий «провайдер отвалился» виден без запросов к провайдеру;
- `POST …/infrastructure-health/refresh` — активная проверка включённых
  эндпоинтов через существующий минимальный ping, без генерации программ;
- панель «Состояние AI-инфраструктуры» на `/ai` с manual refresh, last checked,
  loading/error states и авто-обновлением состояния.

Lifecycle конфигурации:
- edit/enable/disable/safe delete для провайдера, эндпоинта и модели в UI
  (раньше в интерфейсе были только create и переключение enabled);
- зависимости проверяются до удаления: 409 с машиночитаемым списком блокеров
  вместо ошибки целостности БД; broken references не создаются;
- секреты удаляемых эндпоинтов вычищаются из SecretStore, включая каскадное
  удаление вместе с провайдером;
- usage/audit-история при удалении конфигурации сохраняется.

CI:
- `.github/workflows/ci.yml` на PR и push в `main`: backend-тесты на реальной
  PostgreSQL с засевом каталога упражнений, проверка цепочки миграций
  (head → base → head) и единственного alembic head, frontend
  lint/typecheck/production build;
- при падении CI на PR автоматически создаётся issue с меткой `ci-failure`.

Тесты: +80 (369 всего) — readiness gate, классификация runtime-причин,
состояния health, safe delete и lifecycle через API.

## 2026-08-21 — Phase 1.1: AI configuration UX
- `AIReadinessService`: единый чек-лист готовности AI-задачи, эффективная
  цепочка моделей и фактическая стратегия генерации;
- `GET /api/v1/admin/ai/readiness` (admin-only, без живых вызовов провайдера);
- результат connection test сохраняется на эндпоинте (миграция 0006):
  «не проверялось» отличается от «проверка провалилась»;
- включение AI-задачи в заведомо нерабочем состоянии запрещено на сервере
  (нет моделей, все модели недоступны, протокол без адаптера, несуществующая
  версия промпта → 422);
- `/ai`: панель готовности, мастер «Быстрое подключение AI» (проверка
  подключения выполняется до включения задачи), журналы вызовов и изменений
  конфигурации, протоколы без адаптера помечены и недоступны для выбора;
- исправлен молчаливый сброс `timeout_seconds` задачи при сохранении из UI.

## 2026-08-21 — Documentation baseline
- создан единый индекс документации;
- зафиксирован фактический статус проекта;
- добавлен roadmap дальнейшей разработки;
- добавлены product, AI и operations guides;
- введено правило обновления документации после каждого этапа.

## 2026-08-20 — AI Settings UI/UX Audit
- проведён отдельный аудит страницы `/ai`;
- зафиксированы UX-разрывы и фактическое состояние конфигурации;
- подробности: `REPORTS/AI_SETTINGS_UI_UX_AUDIT.md`.

## 2026-08-19 — Stage 5
- orchestration генерации;
- HTML delivery в Telegram;
- media storage/import pipeline;
- rest timer;
- обновление архитектурной документации.

## 2026-08-18 — Stage 4
- AI Program Generator;
- generation context без прямых идентификаторов пользователя;
- JSON parsing, validation и repair;
- подключение AI generator к ProgramService.

## 2026-08-17 — Stage 3A/3B
- program filtering/safety/deterministic generation/versioning;
- Universal AI Gateway и конфигурация providers/endpoints/models/tasks;
- Admin API и web UI.

## Ранние этапы
- Telegram questionnaire;
- profile persistence и consent;
- PostgreSQL/Alembic;
- exercise catalog и admin UI.
