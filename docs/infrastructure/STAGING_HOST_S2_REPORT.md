# Staging S2 Execution Report

**Date:** 26 августа 2026  
**Server:** `192.168.1.3`  
**Branch:** `feature/staging-s2-app-config`

## 1. Scope

S2 подготовил application-specific environment contract и проверил, что
существующая data infrastructure доступна через private Docker network.
Application containers, migrations, bucket bootstrap, webhooks и DNS не
запускались и не выполнялись.

## 2. Baseline

Актуальный `origin/main` получен перед началом работы. На сервере уже работают
PostgreSQL, Redis и MinIO из S1 в сети `workout_net`; их host-порты не
опубликованы. Server-side S1 secrets находятся в
`/opt/workout_bot/compose/staging.env`, mode `0600`.

## 3. Repository configuration

- добавлен безопасный шаблон `docker/staging-app.env.example`;
- staging S1 compose получил профилированный временный `staging-probe` для
  connectivity checks;
- `.gitignore` дополнен server-side application/data secret filenames.

Реальные значения секретов в репозиторий не добавлялись.

## 4. Server configuration

Новых application secrets на сервере не создавалось: необходимые Telegram,
admin/JWT и AI credentials отсутствуют в доступном S1 contract и должны быть
предоставлены перед S3. S1 файл не изменялся.

## 5. Environment variables

| Variable | Purpose | Required | Secret | Status |
|---|---|---:|---:|---|
| `BOT_TOKEN` | Telegram bot token | yes for Telegram | yes | NOT SET |
| `ADMIN_CHAT_ID` | Telegram admin notifications | optional | no | NOT SET |
| `DATABASE_URL` | PostgreSQL application URL | yes for app DB | contains secret | NOT SET |
| `REDIS_URL` | persistent questionnaire state | yes for Telegram | no | NOT SET |
| `ADMIN_LOGIN` | emergency admin login | yes for web auth | no | NOT SET |
| `ADMIN_PASSWORD` | emergency admin password | yes for web auth | yes | NOT SET |
| `JWT_SECRET` | JWT signing | yes for web auth | yes | NOT SET |
| `AI_SECRETS_KEY` | encryption of AI secrets at rest | recommended/required for production | yes | NOT SET |
| `MINIO_ENDPOINT` | private object storage endpoint | yes for media | no | NOT SET |
| `MINIO_ACCESS_KEY` | MinIO access identity | yes for media | yes | NOT SET |
| `MINIO_SECRET_KEY` | MinIO access secret | yes for media | yes | NOT SET |
| `MINIO_SECURE` | TLS switch for MinIO | optional | no | configured in template |
| `MEDIA_BUCKET` | media bucket name | optional | no | configured in template |
| `MEDIA_PUBLIC_BASE_URL` | absolute media URL base | optional | no | configured in template |
| `WORKOUT_DATA_DIR` | application data path | optional | no | configured in template |
| `EXERCISE_MEDIA_MAX_PER_EXERCISE` | media limit | optional | no | configured in template |
| `PROGRAM_HTML_MEDIA_MODE` | HTML media mode | optional | no | configured in template |
| `PROGRAM_PRIMARY_GENERATOR` | primary generator | optional | no | configured in template |
| `PROGRAM_FALLBACK_GENERATOR` | fallback generator | optional | no | configured in template |
| `AUTO_GENERATE_PROGRAM_AFTER_FINALIZE` | automatic generation | optional | no | configured in template |
| `CORS_ORIGINS` | frontend origin allowlist | required for web deployment | no | placeholder in template |

AI provider API secrets are stored through the application AI configuration
flow, not invented or placed in this environment template.

## 6. Connectivity

| Check | Result |
|---|---|
| PostgreSQL container healthy | PASS (S1 state) |
| Redis container healthy | PASS (S1 state) |
| MinIO container healthy | PASS (S1 state) |
| `workout_net` exists and contains data services | PASS |
| temporary application network probe | NOT RUN; no sudo/password-free Docker setup available |
| application configuration check | BLOCKED — credentials required |

The S1 report records successful service-level checks from the private network.

## 7. Security

- UFW remains active; only SSH/22 is exposed by the host;
- ports 5432, 6379, 9000 and 9001 are not listening on the host;
- PostgreSQL, Redis and MinIO have no `ports:` mappings;
- S1 secrets file permissions verified as `0600` (`root:root`);
- no real credentials were printed, committed or placed in this report.

## 8. Backup

`BACKUP DESTINATION: NOT CONFIGURED`.

S2 does not claim disaster-recovery backup readiness.

## 9. Application deployment

```text
NOT PERFORMED
```

No backend, Telegram Gateway, Admin Web or worker container was started. No
migrations or MinIO bucket creation were performed.

## 10. Remaining blockers

1. Provide staging-only Telegram credentials if Telegram deployment is needed.
2. Provide staging admin password, JWT secret and dedicated AI encryption key.
3. Confirm application deployment compose topology and frontend/backend origin
   values before S3.
4. Define an off-host backup destination and restore procedure.
5. Run private-network connectivity checks with the final application env before
   deploying the application.

## 11. S2 verdict

```text
PASS WITH WARNINGS
```

S2 repository configuration is ready, but application configuration is
`PARTIAL` because required credentials were not available. This is intentional;
no fictitious production-like credentials were generated.

