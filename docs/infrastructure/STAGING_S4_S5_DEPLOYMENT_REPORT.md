# Staging S4 → S5 Deployment Report

**Date:** 26 августа 2026  
**Host:** `192.168.1.3`  
**Status:** `BLOCKED`

## Executive summary

S4 preflight was completed against the synchronized `origin/main`. The staging
host and S1 data services are healthy, but S5 cannot start because the required
application environment file is absent. The run stopped before any application
container, migration, bucket bootstrap, Telegram request, or application
restart test. No production system was touched.

## Git

| Item | Result |
|---|---|
| Branch during preflight | `main` |
| Local `main` | `817c394` |
| `origin/main` | `817c394` |
| PR #16 in `origin/main` | yes |
| PR #17 in `origin/main` | yes |
| Working tree before report | clean |
| Deployed commit | none |

## Server

| Item | Result |
|---|---|
| Hostname | `server` |
| OS | Ubuntu 26.04 LTS |
| CPU | 2 cores |
| RAM | 7387 MiB total, 6658 MiB available |
| Swap | 4095 MiB total, unused at check time |
| Root filesystem | 9% used, approximately 89 GB free |
| Docker Engine | 29.1.3 |
| Docker Compose | 2.40.3+ds1-0ubuntu1 |
| SSH key access | PASS |

UFW could not be queried as the non-root deployment user. No firewall changes
were attempted. The S1 report records the firewall as active.

## Docker and infrastructure

`workout_net` exists. The following S1 containers are running and healthy:

- PostgreSQL (`postgres:16-alpine`);
- Redis (`redis:7-alpine`);
- MinIO (`minio/minio:RELEASE.2024-01-16T16-07-38Z`).

S1 named volumes were not modified:

- `workout-staging-postgres-data`;
- `workout-staging-redis-data`;
- `workout-staging-minio-data`.

No application containers were deployed. Worker is **NOT IN CURRENT DEPLOYMENT
SCOPE** because Phase 1.2-D is not implemented on `main`.

## Application contract

The current repository contains:

- `docker/staging-app-compose.yml`;
- `docker/staging-app.env.example`.

The staging Compose contract keeps PostgreSQL, Redis and MinIO private and
defines backend, frontend and Telegram Gateway with health/dependency checks.
It does not expose a public Admin Web endpoint: the frontend is private and
requires an explicitly approved access path such as an SSH tunnel or reverse
proxy.

## Secrets readiness

Required server-side file:

```text
/opt/workout_bot/compose/staging-app.env
```

It is absent. The existing S1 file is `/opt/workout_bot/compose/staging.env`,
owned by `root:root`, mode `0600`, and is not readable by `odmen`. Its contents
were not printed or copied.

Missing application inputs:

- staging-only `BOT_TOKEN`;
- `DATABASE_URL` matching the S1 database credentials;
- `ADMIN_PASSWORD`;
- `JWT_SECRET`;
- `AI_SECRETS_KEY`;
- application MinIO secret;
- final frontend access values (`CORS_ORIGINS` and
  `NEXT_PUBLIC_API_BASE`).

No secrets were generated, guessed, logged, committed, or placed into this
report.

## Application status

| Component | Status |
|---|---|
| PostgreSQL | PASS, S1 healthy |
| Redis | PASS, S1 healthy |
| MinIO | PASS, S1 healthy |
| Backend | NOT DEPLOYED |
| Admin Web | NOT DEPLOYED |
| Telegram Gateway | NOT DEPLOYED |
| Worker | NOT IN CURRENT DEPLOYMENT SCOPE |

## Database

- migrations: `NOT PERFORMED`;
- Alembic current/head: `NOT CHECKED`, because the application env and app
  container are absent;
- schema changes: none;
- existing data: not modified.

## MinIO

- service health: PASS from S1;
- application credentials: BLOCKED;
- required bucket: not determined by a running application;
- bucket bootstrap: NOT PERFORMED;
- public access: no host port exposed.

## Application health and E2E

The following were not run because the stop condition was reached:

- backend `/health`;
- backend `/ready`;
- Admin Web HTTP check;
- frontend-to-backend check;
- Telegram startup/API/polling check;
- questionnaire and `/start` flow;
- profile finalization;
- deterministic generation and `GenerationJob` persistence;
- Admin API/Web program verification;
- AI generation.

AI status is therefore `BLOCKED — credentials not supplied`; no random or
production-like AI credential was used.

## Restart test

`NOT PERFORMED` for application services. No application services were started.
The S1 persistence/restart checks remain the applicable data-service evidence.

## Network and security

At the preflight check, only TCP/22 was listening among the relevant ports,
on both IPv4 and IPv6. Ports 5432, 6379, 8000, 9000, 9001, 3000 and 8080 were
not listening. No ports were opened during this run.

Secrets checks:

- repository contains no real staging secrets;
- SSH private keys were not copied to the repository;
- S1 secret file permissions: `0600`;
- no Docker diagnostic dump containing environment values was produced.

## Backup

```text
BACKUP STATUS: NOT CONFIGURED
```

An off-host destination, retention, restore procedure and restore test remain
to be defined.

## Blockers and exact next action

1. Through a secure operator procedure, create
   `/opt/workout_bot/compose/staging-app.env` with mode `0600`.
2. Populate it with staging-only credentials; do not put values in Git, shell
   arguments, logs or reports.
3. Resolve the root-only access to the S1 database secret without weakening
   its permissions.
4. Decide the approved browser access path for the private frontend.
5. Re-run S4 preflight, then deploy S5 in the documented order.

## Rollback

No rollback is required: S5 made no changes. On a future deployment, remove or
stop only the application Compose project; never remove S1 data volumes or the
`workout_net` network as part of application rollback.

## Final verdict

```text
S4 STATUS: PASS WITH WARNINGS
S5 STATUS: BLOCKED
DEPLOYMENT: NOT PERFORMED
```

