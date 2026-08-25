# Staging S1 Execution Report

**Date:** 25 августа 2026  
**Server:** `192.168.1.3`  
**Branch:** `feature/staging-s1-runtime`  
**Status:** PASS WITH WARNINGS

## Executive summary

S1 подготовил и запустил только data infrastructure: PostgreSQL, Redis и
MinIO. Сервисы работают в существующей private Docker network `workout_net`,
не имеют host port mappings и не доступны из LAN через UFW. Backend, Telegram
Gateway, Admin Web, Worker, application `.env`, production credentials, DNS и
reverse proxy не разворачивались.

## Baseline

Перед изменениями проверены Docker/Compose, пустой список контейнеров,
отсутствие Docker volumes, существующая сеть `workout_net`, UFW, слушающие
порты, `/opt/workout_bot`, LVM, RAM/swap, systemd и отсутствие установленных
PostgreSQL/Redis/MinIO. Состояние соответствовало S0, кроме ожидаемого
появления Docker system services после S0.

## Changes

- добавлен отдельный `docker/staging-s1-compose.yml` только для data services;
- добавлен безопасный шаблон `docker/staging.env.example` без credentials;
- на сервере создан secrets-only файл `/opt/workout_bot/compose/staging.env`
  с permissions `0600` (значения в этот отчёт не попадают);
- созданы named volumes для PostgreSQL, Redis и MinIO;
- использована внешняя сеть `workout_net`;
- добавлены healthchecks и initial resource limits/reservations;
- application stack не запускался.

## Versions

| Component | Version/image |
|---|---|
| Docker Engine | 29.1.3 |
| Docker Compose | 2.40.3+ds1-0ubuntu1 |
| PostgreSQL | postgres:16-alpine |
| Redis | redis:7-alpine |
| MinIO | RELEASE.2024-01-16T16-07-38Z |

## Docker configuration

Compose file intentionally contains no `ports:` entries. PostgreSQL, Redis and
MinIO communicate only via `workout_net`. Restart policy is `unless-stopped`.
The three services have CPU/memory bounds appropriate for a 2-core/8 GiB HDD
staging host; they are initial limits, not production capacity planning.

| Service | CPU | Memory limit | Reservation |
|---|---:|---:|---:|
| PostgreSQL | 1.0 | 1536 MiB | 512 MiB |
| Redis | 0.5 | 512 MiB | 128 MiB |
| MinIO | 0.75 | 1024 MiB | 256 MiB |

## Network and firewall

The host continues to expose only SSH TCP/22. UFW remains active with deny
incoming and deny routed traffic for IPv4 and IPv6. No application or data
service ports were added. LAN probes to 5432, 6379, 9000 and 9001 are expected
to fail; service-level checks are performed from a temporary container attached
to `workout_net`, not through host ports.

## Storage and secrets

Named volumes:

- `workout-staging-postgres-data`;
- `workout-staging-redis-data`;
- `workout-staging-minio-data`.

Secrets exist only in `/opt/workout_bot/compose/staging.env` on the staging
host, mode `0600`, and are not copied to Git or logs. The repository contains
only placeholders. Same-disk backup is not disaster recovery backup.

## Healthchecks

- PostgreSQL: `pg_isready` against the configured database/user;
- Redis: `redis-cli ping`;
- MinIO: `/minio/health/live`.

## Persistence tests

Each service receives a temporary marker/key/object, is restarted, and the
marker is read back. Test data is removed after verification. No application
data is created.

## Backup contract

S1 prepares the backup boundary but does not claim a disaster-recovery backup.
The next operations stage must define an off-host destination, encryption/key
handling, retention, PostgreSQL dump/base-backup procedure, MinIO media backup,
verification and restore test. A copy under `/opt/workout_bot/backups` alone is
not sufficient.

## Verification

| Check | Result |
|---|---|
| PostgreSQL ready | PASS |
| Redis PONG | PASS |
| MinIO healthy | PASS |
| Docker Compose config | PASS |
| Healthchecks | PASS |
| Persistence | PASS |
| Network isolation | PASS |
| Host firewall | PASS, unchanged/active |
| No secrets in Git | PASS |
| Backup contract | WARN, destination not defined |
| Reboot survivability | NOT RUN; Docker enabled and restart policy verified |
| Application deployment | NOT PERFORMED |

## Warnings and remaining work

1. SSH source is still broader than a future VPN-only policy; it was not
   changed during S1.
2. External backup destination and restore test remain open.
3. S2 must create application-specific staging environment values separately;
   this S1 file is not an application `.env`.
4. PostgreSQL migrations and MinIO bucket creation belong to application/data
   deployment and were not performed.

## Recommendations for S2

- review the Compose images and pin/update policy;
- define off-host backup destination before real data;
- create application staging env outside Git;
- deploy application only after `/health`, `/ready`, migration and secret
  contracts are reviewed;
- preserve the application commit SHA and rollback procedure.

```text
S1 STATUS: PASS WITH WARNINGS
Application deployment: NOT PERFORMED
Ready for S2: YES, after review of backup destination and staging secrets
```
