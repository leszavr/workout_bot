# Staging S3.1 Final Preparation Report

**Date:** 26 августа 2026  
**Server:** `192.168.1.3`  
**Status:** `BLOCKED`

## 1. Git state

- local branch after synchronization: `main`;
- local `main` and `origin/main`: `e83ab8a`;
- PR #16 and PR #17 are included in `origin/main`;
- working tree was clean before S3.1 changes;
- this report and the staging application Compose contract are prepared on
  `feature/staging-s3.1-preparation`.

## 2. Repository and deployment contract

The current repository contains S1 data Compose and the S2 application env
template. The normal `docker/docker-compose.yml` is development-oriented: it
publishes database, Redis, MinIO, backend and frontend ports and therefore is
not suitable for staging unchanged.

Added `docker/staging-app-compose.yml` as a staging-only deployment contract:

- PostgreSQL, Redis and MinIO are extended from the S1 definitions;
- backend, frontend and Telegram Gateway use `workout_net`;
- data services have no host `ports:` mappings;
- backend/frontend use `expose`, not public host bindings;
- healthchecks, dependencies, restart policies and resource limits are defined;
- no worker is included because Phase 1.2-D is not implemented on `main`.

The file is a contract only. It was not started.

## 3. Server pre-deployment state

| Check | Result |
|---|---|
| SSH key access | PASS |
| Docker | PASS, 29.1.3 |
| `workout_net` | PASS |
| PostgreSQL/Redis/MinIO | PASS, healthy from S1 |
| S1 env permissions | PASS, `0600` |
| Disk | PASS, approximately 9% used |
| Memory | PASS, approximately 6696 MiB available; swap unused |
| Checked listeners | PASS, only SSH/22 among deployment-related ports |
| Application env | BLOCKED, not present |

## 4. Secrets readiness

`/opt/workout_bot/compose/staging-app.env` is not available. No credentials
were generated, guessed, printed or copied. Required values and their sources:

| Variable/group | Source | Required | Safe local generation |
|---|---|---:|---|
| `BOT_TOKEN` | BotFather, staging bot only | yes for Telegram | no, external credential |
| `DATABASE_URL` | S1 PostgreSQL credentials plus service name `postgres` | yes | password must come from server secret contract |
| `ADMIN_PASSWORD` | staging operator | yes for Admin Web | yes, if accepted by operator |
| `JWT_SECRET` | staging operator | yes for auth | yes, cryptographically random |
| `AI_SECRETS_KEY` | staging operator | recommended/required for production | yes, cryptographically random |
| MinIO application credentials | S1 MinIO secret contract | yes for media | no, must match MinIO configuration |
| `REDIS_URL` | application contract, service `redis` | yes for Telegram | yes, non-secret in current S1 setup |
| `CORS_ORIGINS` / `NEXT_PUBLIC_API_BASE` | final access topology | yes for web | no, deployment decision required |

AI provider API keys are configured through the protected AI admin flow and
must not be added to this file or report.

## 5. Stop condition

Because mandatory application secrets and the server-side application env are
missing, S3.1 correctly stops before deployment. The following were not run:

- application `docker compose up`;
- migrations;
- MinIO bucket bootstrap;
- Telegram API calls or polling;
- backend health/readiness checks;
- E2E smoke test;
- application restart tests.

## 6. Final preparation state

```text
Application Compose contract: READY FOR REVIEW
Application secrets: BLOCKED
Deployment: NOT PERFORMED
Database migrations: NOT PERFORMED
E2E: NOT PERFORMED
Backup: NOT CONFIGURED
```

## 7. Exact next step

1. Create `/opt/workout_bot/compose/staging-app.env` through a secure operator
   procedure, with mode `0600` and no values in Git or shell output.
2. Resolve the database credential mapping: the S1 file is root-readable only,
   so deployment must use an explicit privileged deployment procedure rather
   than weakening its permissions.
3. Review the frontend access topology. The current contract keeps frontend
   private; WinSCP/SSH tunnelling or a separately approved reverse proxy is
   required for browser access.
4. Re-run the preflight and only then deploy infrastructure, migrations,
   backend, frontend and the staging Telegram bot in order.

## 8. Verdict

```text
S3.1 STATUS: BLOCKED
```

