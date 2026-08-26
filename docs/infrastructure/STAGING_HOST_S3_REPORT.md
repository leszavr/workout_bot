# Staging S3 Deployment Report

**Date:** 26 августа 2026  
**Server:** `192.168.1.3`  
**Deployment branch:** `feature/staging-s3-deploy`  
**Status:** `BLOCKED`

## 1. Executive summary

S3 остановлен на безопасной pre-deployment границе. SSH key access and the S1
data runtime are healthy, but the required application environment is not
present. The server contains only the S1 data-services secret file; no
application credentials were provided. No application container, migration,
bucket bootstrap, webhook, DNS or firewall change was performed.

## 2. Deployment commit

No application deployment commit was created. The report is based on
`origin/main` at `981e8bf` and is recorded separately on the S3 branch.

## 3. Server state before deployment

| Check | Result |
|---|---|
| SSH key access as `odmen` | PASS |
| Docker Engine | PASS, 29.1.3 |
| `workout_net` | PASS, exists |
| PostgreSQL | PASS, healthy container from S1 |
| Redis | PASS, healthy container from S1 |
| MinIO | PASS, healthy container from S1 |
| S1 secrets file | PASS, mode `0600` |
| Disk | PASS, approximately 9% used |
| Memory | PASS, approximately 6696 MiB available; swap unused |
| Host listening ports | PASS, only SSH/22 observed among checked ports |
| Host firewall | NOT RECHECKED in this session; no firewall changes made |

The existing S1 secret file is owned by `root:root` and cannot be read by the
deployment user. Its values were neither requested nor printed.

## 4. Containers deployed

```text
NOT PERFORMED
```

The server still has only the three S1 data-service containers:
PostgreSQL, Redis and MinIO.

## 5. Docker networks

`workout_net` exists and is used by the S1 data services. No application
network attachment was made.

## 6. Volumes

Existing S1 named volumes were not modified or removed:

- `workout-staging-postgres-data`;
- `workout-staging-redis-data`;
- `workout-staging-minio-data`.

## 7. PostgreSQL

Container health from S1: `PASS`. Application connection test and migrations:
`BLOCKED — application env and database credentials are not available to the
deployment user`.

## 8. Redis

Container health from S1: `PASS`. Application connection test:
`BLOCKED — application env was not supplied`.

## 9. MinIO

Container health from S1: `PASS`. Application access test and bucket bootstrap:
`NOT PERFORMED`. No bucket was created.

## 10. Backend

```text
NOT DEPLOYED
```

`/health` and `/ready` were not called because no backend container was started.

## 11. Telegram Gateway

```text
NOT DEPLOYED
```

`BOT_TOKEN` was not supplied. No Telegram API request or polling process was
started, so the production or staging bot was not affected.

## 12. Admin Web

```text
NOT DEPLOYED
```

Admin credentials and the final backend/frontend topology were not supplied.

## 13. Health/readiness

Data-service health: `PASS` according to the verified S1 state. Application
health/readiness: `NOT RUN` because application deployment was blocked.

## 14. Database migrations

```text
NOT PERFORMED
```

No schema or data changes were made.

## 15. E2E smoke test

```text
NOT PERFORMED
```

The test cannot start without the application environment and staging Telegram
credentials. No test data was created.

## 16. Restart/recovery test

```text
NOT PERFORMED for application containers
```

No application containers exist to restart. S1 data-service persistence and
restart checks remain covered by the S1 report.

## 17. Network exposure

The checked host state exposes SSH on TCP/22 only. No listeners were observed
on TCP 5432, 6379, 8000, 9000, 9001 or 3000, for either IPv4 or IPv6. No
application ports were opened.

## 18. Security verification

- no secrets were printed or committed;
- no production infrastructure or credentials were touched;
- S1 secret permissions remain `0600`;
- firewall and SSH configuration were not weakened;
- no application image/container was built or started;
- no public database, Redis or MinIO ports were added.

## 19. Resource usage

Before deployment: approximately 6696 MiB memory available, swap unused, and
root filesystem approximately 9% used. Application resource impact is not
applicable because deployment did not run.

## 20. Backup status

```text
BACKUP STATUS: NOT CONFIGURED
```

No backup configuration was invented or changed.

## 21. Known warnings

1. S2's application environment template is a repository contract only; it is
   not a server-side secrets file.
2. `/opt/workout_bot/compose/staging-app.env` does not exist or was not supplied.
3. The existing S1 secrets file is unreadable by `odmen` and cannot be reused
   as an application environment without an explicit deployment procedure.
4. The current repository `origin/main` has S1 data Compose but no committed
   application staging Compose deployment definition.
5. Backup destination and restore procedure remain undefined.

## 22. Blockers

Before retrying S3, provide all of the following through a secure channel and
create `/opt/workout_bot/compose/staging-app.env` with owner appropriate for
deployment and mode `0600`:

- staging-only `BOT_TOKEN`;
- `DATABASE_URL` matching the existing PostgreSQL contract;
- `ADMIN_PASSWORD` and `JWT_SECRET`;
- dedicated `AI_SECRETS_KEY`;
- MinIO application credentials matching the staging MinIO service;
- final application Compose/deployment definition, including frontend/backend
  network and internal URL values.

Do not place these values in Git, this report, or command output.

## 23. Rollback procedure

No application rollback is required because S3 made no deployment changes. If a
future retry starts application containers, rollback must use the exact
deployment commit and `docker compose down` for only the application project;
S1 data volumes and the `workout_net` network must not be removed.

## 24. Final status

```text
S3 STATUS: BLOCKED
```

