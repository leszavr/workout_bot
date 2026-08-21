# Changelog / История этапов

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
