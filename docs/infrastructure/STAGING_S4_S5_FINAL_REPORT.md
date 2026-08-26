# Staging S4/S5 Final Deployment Report

**Date:** 26 August 2026  
**Server:** `192.168.1.3`  
**Status:** `DEPLOYED`

## Scope

The staging application was deployed alongside the existing S1 PostgreSQL,
Redis and MinIO services. S1 volumes, containers, private data-service ports,
SSH policy and production systems were not changed.

## Deployment topology

- `backend`: Docker service `docker-backend-1`, published on TCP `8000` for
  LAN access by the Admin Web;
- `frontend`: Docker service `docker-frontend-1`, published on TCP `3000`;
- `telegram-bot`: Docker service `docker-telegram-bot-1`, no published port;
- PostgreSQL, Redis and MinIO remain attached only to `workout_net` and have
  no host listeners on TCP `5432`, `6379`, `9000` or `9001`.

The application uses the protected server-side file
`/home/odmen/workout_bot/staging-app.env` (mode `0600`). It is outside Git and
is not included in the deployed source archive.

## Configuration and data services

- A dedicated PostgreSQL role and database were provisioned through the S1
  PostgreSQL container without reading or exposing the S1 secret file.
- Alembic migrations were applied successfully through revision `0008 (head)`.
- A dedicated MinIO application user and the `workout-media` bucket were
  provisioned through the S1 MinIO container without exposing root credentials.
- MinIO object write, read and delete were verified with a temporary probe
  object, which was removed after the check.
- PostgreSQL, Redis and MinIO reported `healthy`.

## Application verification

| Check | Result |
|---|---|
| Backend `/health` | PASS (`{"status":"ok"}`) |
| Backend `/ready` | PASS (`storage: true`) |
| Admin Web HTTP | PASS (`200`) |
| Admin API login using protected staging credentials | PASS |
| Frontend bundle API base | PASS (`http://192.168.1.3:8000`) |
| Telegram gateway process | PASS (running; no recent errors) |
| Backend/frontend/Telegram restart | PASS |
| App logs after restart | PASS (no traceback/error/exception detected) |
| Public data-service ports | PASS (none) |

The Telegram gateway process is connected and running. No user-facing Telegram
conversation was performed during deployment, so no test user data was created.

## Access

- Admin Web: `http://192.168.1.3:3000`
- Backend API health: `http://192.168.1.3:8000/health`
- Backend API readiness: `http://192.168.1.3:8000/ready`

LAN access to both HTTP services was tested from the deployment workspace.
Firewall rules were not inspected because the deployment user has no privileged
firewall access; no firewall rule was changed.

## Known limitations

1. The frontend image build reports five high-severity `npm audit` findings in
   its existing dependency tree. The image builds successfully; dependency
   remediation is outside this deployment change.
2. No worker/retry service is part of the current application Compose scope.
3. A full questionnaire-to-delivery Telegram E2E scenario and AI generation
   were not run, to avoid creating test user data or exercising unconfigured
   external AI endpoints.

## Rollback

From `/home/odmen/workout_bot`, stop only the application project:

```bash
STAGING_APP_ENV_FILE=/home/odmen/workout_bot/staging-app.env \
docker compose --env-file staging-app.env -f docker/staging-app-compose.yml down
```

Do not add `--volumes`; the S1 data volumes and `workout_net` must be retained.
