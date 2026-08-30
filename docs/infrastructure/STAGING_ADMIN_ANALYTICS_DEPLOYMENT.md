# Staging Deployment: Admin Analytics & Exercise Explorer

**Дата:** 30 августа 2026
**Сервер:** `192.168.1.3` (all-in-one staging host)
**Код:** `main @ 64de189` (PR #30 смержен)
**Ревизия БД:** `0009 → 0010`
**Статус:** DEPLOYED

## Что развёрнуто

Изменения PR #30: аналитика качества генерации (`/ai/analytics`, `/ai/generations`,
карточка генерации), обозреватель каталога упражнений (серверные фильтры,
пагинация, счётчики), корреляция телеметрии (`ai_usage_records.job_id`,
`entity_id` журнала = `job_id`), миграция 0010.

## Процедура

Staging не имеет git-репозитория: код доставляется rsync из локального
`/home/odmen/DEV/Workout/app/workout_bot` в `/home/odmen/workout_bot`.

```bash
# 1. rsync (staging-app.env, .env, data/, node_modules/, .next/ исключены)
rsync -az --delete \
  -e "ssh -i ~/.ssh/workout_staging_ed25519" \
  --exclude '.git/' --exclude '.venv/' --exclude 'data/' --exclude '__pycache__/' \
  --exclude 'node_modules/' --exclude '.next/' --exclude '.next-check/' \
  --exclude '.tmp/' --exclude '*.pyc' --exclude '.pytest_cache/' \
  --exclude '.env' --exclude 'staging-app.env' --exclude 'REPORTS/' \
  ./ odmen@192.168.1.3:/home/odmen/workout_bot/

# 2. Пересборка образов. ВАЖНО: telegram-bot — отдельный образ
#    (docker-telegram-bot), при build backend НЕ пересобирается.
cd /home/odmen/workout_bot
ENV=/home/odmen/workout_bot/staging-app.env
STAGING_APP_ENV_FILE=$ENV docker compose --env-file staging-app.env \
  -f docker/staging-app-compose.yml build backend frontend telegram-bot

# 3. Миграция — из СВЕЖЕСОБРАННОГО образа, а не через работающий контейнер:
#    в старом контейнере кода миграции ещё нет, alembic upgrade падает
#    "Can't locate revision '0010'".
STAGING_APP_ENV_FILE=$ENV docker compose --env-file staging-app.env \
  -f docker/staging-app-compose.yml run --rm --no-deps backend alembic upgrade head

# 4. Пересоздание контейнеров на новых образах
STAGING_APP_ENV_FILE=$ENV docker compose --env-file staging-app.env \
  -f docker/staging-app-compose.yml up -d
```

## Подводные камни, встреченные при деплое

1. **telegram-bot собирается отдельным образом** `docker-telegram-bot`. Первый
   `up -d` пересоздал только backend и frontend — telegram-bot остался на старом
   образе. Потребовался явный `build telegram-bot` + повторный `up -d`. Это
   существенно: Telegram-пайплайн генерации живёт именно в этом контейнере, и
   без пересборки корреляция телеметрии в нём не заработала бы.

2. **Миграцию нельзя применять через работающий контейнер.** До пересоздания в
   `docker-backend-1` крутится старый образ без файла `0010`, и
   `docker exec docker-backend-1 alembic upgrade head` падает
   «Can't locate revision '0010'». Применять через
   `compose run --rm --no-deps backend` из уже собранного образа.

3. **IPv6 к Docker registry нестабилен.** `build telegram-bot` упал с
   `connection timed out` при загрузке метаданных `python:3.12-slim` по IPv6.
   Помог повтор той же команды — метаданные закэшировались после backend/frontend.

## Проверка (sanity check)

| Проверка | Результат |
|---|---|
| Backend `/health` | PASS (`{"status":"ok"}`) |
| Backend `/ready` | PASS (`storage: true`) |
| Admin Web `/`, `/ai/analytics`, `/ai/generations`, `/exercises` | PASS (200) |
| Analytics overview на реальных данных | PASS (calls: 43 записи, 41 успех, 95.3%) |
| Analytics filters | PASS (пусто — генераций 0, честно) |
| Exercise explorer фильтр по оборудованию (был сломан) | PASS (`equipment=barbell` → 170, фасеты пришли) |
| Существующие AI-разделы (usage/audit/model-attempts/fallback-events/readiness) | PASS (200) |
| Telegram gateway | PASS (`getMe → wrkoutassist_bot` через WireGuard) |
| Миграция 0010 (job_id + 3 индекса) | PASS |

## Целостность (сверка pre/post deploy)

Ничего не потеряно, S1-инфраструктура не тронута:

| Данные | До | После |
|---|---|---|
| exercises | 873 | 873 |
| ai_usage_records | 43 | 43 |
| ai_audit_events | 63 | 63 |
| prompt_templates | 1 | 1 |
| profiles / programs / jobs | 0 / 0 / 0 | 0 / 0 / 0 |

- volumes `workout-staging-{bot,minio,postgres,redis}-data` сохранены;
- `staging-app.env` (0600, вне Git) не тронут;
- telegram-bot сохранил фиксированный IP `172.18.0.20` — policy routing в
  WireGuard-туннель цел;
- S1-сервисы (PostgreSQL, Redis, MinIO) не перезапускались.

## Ограничения

- **Аналитика генерации на staging пуста**: `generation_jobs = 0`, потому что
  профили были очищены, а прошлые 43 us- и 63 audit-записи сделаны до введения
  job-контура и имеют `entity_id = NULL`. Это честное отражение состояния, не
  поломка: блок «обращения к ИИ» видит все 43 записи. Данные появятся после
  первой генерации через Telegram или Admin API.
- Полный Telegram E2E (анкета → генерация → доставка) не прогонялся, чтобы не
  создавать тестовых пользовательских данных. Провайдер ИИ из окружения
  недоступен (проверено ранее), поэтому генерация пошла бы через алгоритмический
  fallback.

## Откат

```bash
cd /home/odmen/workout_bot
STAGING_APP_ENV_FILE=/home/odmen/workout_bot/staging-app.env \
docker compose --env-file staging-app.env -f docker/staging-app-compose.yml down
```

Без `--volumes`: данные S1 должны сохраниться. Миграция 0010 аддитивна (nullable
колонка + индексы) и откат кода не требует отката схемы; при необходимости —
`alembic downgrade 0009` из образа с кодом 0010.
