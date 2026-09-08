# Исправление маршрута Admin Web `/ingestion`

**Дата:** 8 сентября 2026  
**Staging:** `192.168.1.3`  
**Исходный commit:** `main @ 6de025556263442d1d1bec7faa1eb6d02736cc27`

## Root cause

Проблема не была ошибкой Next.js App Router, route group `(admin)`, reverse proxy
или клиентского маршрута.

В staging оказались развёрнуты несогласованные части одного релиза:

1. Frontend был пересобран после PR #43 и содержал `/ingestion`,
   `/ingestion/records` и `/ingestion/health`.
2. Backend продолжал работать из старого образа, созданного до merge PR #42.
   В нём не было `apps/backend/api/v1/ingestion_routes.py` и регистрации
   `ingestion_router`, поэтому запросы UI к
   `/api/v1/admin/ingestion/*` отвечали FastAPI `404 {"detail":"Not Found"}`.
3. База staging оставалась на Alembic revision `0016`. Таблицы ingestion из
   миграции `0017` (`external_sources`, `external_exercise_records` и связанные)
   отсутствовали. После обновления backend это проявилось честным `503
   ProgrammingError`, а не отсутствием frontend route.

Таким образом, исходный экран мог отдаваться frontend с HTTP 200, но после загрузки
показывал API-ошибку. Это не зависело от наличия или отсутствия записей в БД.

## Доказательство цепочки до исправления

| Звено | Наблюдение |
| --- | --- |
| `main` | `6de0255` содержит ingestion API из PR #42 и UI из PR #43. |
| Исходники frontend на staging | SHA-256 трёх ingestion-страниц совпали с локальным `main`. |
| Образ frontend | `sha256:6caf879679472920cd7ce0ff77076682480904314438281af8d317f7ccefce02`, создан 8 сентября 2026; в `.next` есть все три ingestion route artifacts. |
| HTTP frontend | `/ingestion` — 200; `/ingestion/` — 308 на canonical URL; соседний `/exercises` — 200. |
| Proxy | Отдельного nginx/traefik/caddy контейнера нет; frontend опубликован прямо на порт `3000`. |
| Старый backend | Образ был создан 5 сентября; импорт ingestion router отсутствовал, а `/api/v1/admin/ingestion/sources` отвечал 404. |
| Схема | `alembic current` показывал `0016`; таблицы `external_*` отсутствовали. |

## Исправление

На staging выполнены только необходимые операции:

1. Собран и пересоздан **только `backend`** из актуальных исходников `main` с
   `BUILD_SHA=6de025556263442d1d1bec7faa1eb6d02736cc27`.
2. Из свежесобранного backend-образа применены аддитивные миграции
   `0016 → 0017 → 0018`.

Backend после обновления: image
`sha256:6d02753ce8918197dbe6cf046dc768fbbfa2f804262eb9e7c0cf74eb2e1fc981`,
`/version` возвращает указанный `build_sha`.

Frontend, worker, Telegram Gateway, PostgreSQL/Redis/MinIO topology, WireGuard,
wstunnel и firewall не менялись. У backend, frontend, worker и Gateway после
операции `RestartCount = 0`.

## Проверка после исправления

| Проверка | Результат |
| --- | --- |
| Alembic | `0018 (head)` |
| Backend `/health` | 200, `{"status":"ok"}` |
| Backend `/ready` | 200, `storage: true` |
| `/ingestion` | 200 |
| `/ingestion/` | redirect на `/ingestion`, итоговый 200 |
| `/ingestion/records` | 200 |
| `/ingestion/health` | 200 |
| Соседние Admin Web `/exercises`, `/knowledge`, `/ai/prompts` | 200 |
| Авторизованные API sources / records / health | все 200 |
| Deployment safety | `SAFE`, блокирующих вердиктов нет |
| Docker frontend/backend | healthy |

## Данные ingestion

После применения корректной схемы API возвращает предусмотренное пустое состояние:
`items: []`, `total: 0`, `external_records_total: 0`.

Это установленный факт текущей staging-БД, а не скрытый 404: до исправления в ней
не существовали даже таблицы ingestion. Локальные копии двух внешних источников
(GitHub dataset и Kaggle CSV), которые обязательны для безопасной операции
`scripts.ingest_external_exercises.py`, на staging также не найдены. Поэтому
повторный импорт намеренно не запускался: его нельзя воспроизвести без исходных
датасетов и отдельной операции обслуживания.

## Автоматические проверки

- `npm run lint` — PASS;
- `npx tsc --noEmit` — PASS;
- `npm test` — PASS, 40 tests;
- `npm run build:check` — PASS, production build содержит 25 routes, включая
  `/ingestion`, `/ingestion/records`, `/ingestion/health`;
- `pytest tests/integration/test_ingestion_api.py` — PASS, 19 tests;
- `git diff --check` — PASS.

## Вывод

Причина была в неполном staging deployment: frontend обновили отдельно от backend
и не применили миграции ingestion. Маршрут `/ingestion` уже существовал в `main` и
в Next.js build; он не требовал нового route или workaround. После обновления
backend и схемы полный путь `frontend → ingestion API → PostgreSQL` работает.
Для отображения импортированных источников остаётся отдельно доставить локальные
копии датасетов и выполнить штатный ingestion run.
