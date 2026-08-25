# Staging Infrastructure Specification

**Статус:** DESIGN BASELINE — не является инструкцией на немедленный production deployment  
**Дата:** 25 августа 2026  
**Проект:** Workout Bot  

## 1. Назначение

Этот документ фиксирует целевую инфраструктуру **закрытого staging-окружения** Workout Bot перед первым реальным развёртыванием и E2E-проверкой.

Главная цель — получить воспроизводимое окружение, максимально близкое к будущему runtime проекта, но без преждевременного вмешательства в production-серверы.

Документ является архитектурной базой для последующего deployment-задания агенту. Он **не разрешает автоматически** менять production-серверы, DNS, firewall или существующие приложения.

## 2. Текущий контекст

На 25.08.2026 выполнен повторный read-only discovery локального хоста перед
изменениями. Ниже зафиксировано фактическое состояние после discovery.

- IP: `192.168.1.3`
- Ubuntu Server 26.04 LTS
- Intel Pentium G640, 2 физических ядра
- RAM: 8 GiB (около 7.2 GiB доступно ОС)
- диск: около 465.8 GiB HDD
- активный LVM logical volume: около 100 GiB
- свободное место внутри root: около 86 GiB
- swap: 4 GiB
- Docker и прикладные сервисы отсутствуют
- PostgreSQL, Redis, MinIO, web server и Workout Bot не установлены

### Результат повторного discovery S0

- OS, сеть, firewall, SSH, пользователи, сервисы, диск, SMART и egress проверены;
- неожиданных приложений, контейнеров, баз данных и production workload не обнаружено;
- safety checkpoint пройден.

Локальный хост находится за NAT домашней/мобильной сети и не должен публиковаться напрямую в Internet.

### Известные внешние серверы

| Роль | Адрес | Текущее назначение | Решение на staging |
|---|---|---|---|
| EU VPS | `217.60.3.80` | FastPanel | использовать как VPN/external gateway только после отдельной проверки |
| RU VPS | `217.60.10.168` | CapRover / Readora | не изменять на текущем этапе |
| RU mail server | `217.60.186.52` | почтовая инфраструктура | не изменять на текущем этапе |

## 3. Целевая схема staging

```text
                         Internet
                            |
                     EU VPS / VPN gateway
                     217.60.3.80
                            |
                     WireGuard tunnel
                            |
                            v
                  +---------------------+
                  | 192.168.1.3         |
                  | Ubuntu 26.04 LTS    |
                  |                     |
                  |  Reverse Proxy      |
                  |       |             |
                  |  private network    |
                  |   +---+---+---+     |
                  |   |   |   |   |     |
                  | Backend Web TG  |    |
                  |               |      |
                  | PostgreSQL Redis     |
                  | MinIO                |
                  +---------------------+
```

### Принцип

Локальный сервер является **all-in-one staging host**. Все прикладные компоненты работают на нём, предпочтительно в Docker Compose и в общей private Docker network.

EU VPS на первом этапе рассматривается только как контролируемая внешняя точка VPN/доступа. Его FastPanel и существующие приложения не должны изменяться без отдельного задания и проверки.

## 4. Компоненты staging

| Компонент | Staging | Назначение |
|---|---|---|
| Reverse proxy | да | единственная HTTP(S)-точка входа |
| Backend/FastAPI | да | API и application services |
| Admin Web/Next.js | да | административный интерфейс |
| Telegram Gateway | да | Telegram integration |
| PostgreSQL | да | source of truth для бизнес-состояния |
| Redis | да | persistent FSM и runtime/transient state |
| MinIO | да | S3-compatible object storage для media |
| Worker | после Phase 1.2-D | retry/recovery и фоновые операции |
| SMTP | не поднимать локально | при необходимости использовать внешний mail server/SMTP |
| Monitoring | после базового deployment, до production | health, resources, jobs, backups |

## 5. Архитектурные ограничения

### 5.1 PostgreSQL

PostgreSQL остаётся **source of truth** для бизнес-состояния.

Не использовать Redis как замену PostgreSQL для:

- профилей;
- программ;
- generation jobs;
- delivery state;
- AI configuration;
- audit records.

PostgreSQL не публиковать напрямую в Internet или через внешний VPN endpoint.

### 5.2 Redis

Redis используется для:

- persistent FSM;
- runtime/transient state;
- координации, если это предусмотрено текущей реализацией.

Redis не публиковать наружу.

### 5.3 MinIO

MinIO используется как S3-compatible storage для exercise media и других разрешённых application objects.

Не публиковать наружу:

- MinIO API без необходимости;
- MinIO Console.

Доступ к MinIO должен идти через private Docker network. Внешняя публикация media возможна только если это явно предусмотрено текущим application contract и защищено соответствующим образом.

### 5.4 Telegram

На staging Telegram integration должна использовать отдельного staging-бота/token, а не production bot credentials.

Если используется polling, внешний inbound endpoint для Telegram webhook не требуется. Если будущий deployment использует webhook, он должен публиковаться только через reverse proxy с TLS.

### 5.5 AI

AI Gateway использует outbound HTTPS к configured providers.

API keys и другие secrets:

- не коммитить;
- не помещать в frontend;
- не выводить в логи;
- хранить только через предусмотренный secret/configuration mechanism.

## 6. Network boundary

Минимальная модель:

```text
Public Internet
      |
      | VPN / controlled HTTPS
      v
EU VPS
      |
      | WireGuard
      v
192.168.1.3
      |
      +-- host firewall
      |
      +-- reverse proxy :443
      |
      +-- private Docker network
             |
             +-- backend
             +-- admin web
             +-- telegram gateway
             +-- postgres
             +-- redis
             +-- minio
```

### Необходимые правила

1. PostgreSQL, Redis и MinIO не должны иметь public listener.
2. SSH должен быть доступен только из LAN/VPN/разрешённого administrative source.
3. Public HTTP(S) должен проходить через reverse proxy.
4. IPv4 и IPv6 должны иметь эквивалентную firewall policy.
5. Docker published ports должны быть минимальны; внутренние сервисы должны общаться по private network.
6. Межсервисные credentials не должны передаваться через публичные URL/query parameters.

## 7. Состояние безопасности хоста до подготовки

Discovery обнаружил:

- UFW inactive;
- `iptables` policies `INPUT/FORWARD/OUTPUT ACCEPT`;
- SSH password authentication enabled;
- SSH слушает IPv4 и IPv6;
- global IPv6 address присутствует;
- fail2ban не активен;
- `authorized_keys` пользователя `odmen` пустой на момент discovery.

Это **не ошибки выполненного deployment**, а исходное состояние чистого staging-хоста.

Перед публикацией staging через Internet/VPN необходимо отдельным change выполнить hardening:

1. установить и проверить SSH key access;
2. только после проверки ключа отключить password authentication;
3. определить разрешённые SSH sources;
4. настроить host firewall для IPv4 и IPv6;
5. определить необходимость fail2ban/equivalent;
6. проверить фактическую маршрутизацию IPv6;
7. не использовать root login для deployment.

Нельзя отключать парольный SSH до подтверждения рабочего key-based доступа.

## 8. Storage policy

Текущий LVM root volume около 100 GiB, из которых около 86 GiB свободны. На физическом диске остаётся значительный объём, не включённый в текущий LV.

Для первого staging deployment **переразметка не требуется**.

Рекомендуется:

- использовать Docker named volumes или явно определённые host paths;
- разделять данные PostgreSQL, Redis и MinIO логически;
- не хранить secrets в Git;
- следить за disk/inode usage;
- оставить неиспользованный LVM capacity как резерв.

HDD допустим для staging, но не должен считаться показателем production performance.

## 9. Resource policy

Для 2-core/8-GB staging-хоста не следует запускать тяжёлую инфраструктуру без необходимости.

Ограничения:

- не запускать дополнительные базы/сервисы «на будущее»;
- задавать разумные container resource limits;
- избегать постоянных тяжёлых build workloads на production-like runtime;
- мониторить RAM и swap;
- учитывать HDD I/O как вероятный bottleneck.

Worker Phase 1.2-D должен быть добавлен после реализации и acceptance соответствующего кода, а не заранее как пустой сервис.

## 10. Backup and recovery

До первого production deployment обязательны:

- PostgreSQL backup вне staging/production host;
- backup MinIO/application media;
- retention policy;
- автоматическая проверка успешности backup;
- периодический restore test.

Для staging backup нужен прежде всего для проверки deployment/recovery процедуры. Не использовать staging backup как единственный production backup.

## 11. Monitoring

Минимальный staging monitoring:

- host CPU/RAM/swap;
- disk space/inodes;
- container health;
- PostgreSQL availability;
- Redis availability;
- MinIO availability;
- Backend `/health` и `/ready`;
- generation job failures;
- delivery failures;
- restart/crash events.

До production должны появиться alerting и off-host retention для критических событий.

## 12. Deployment phases

### S0 — Host preparation

- подтвердить Ubuntu и ресурсы;
- проверить SMART/температуру/питание/сеть;
- установить SSH key access;
- harden SSH;
- настроить IPv4/IPv6 firewall;
- установить Docker/Compose;
- проверить Docker health.

Выполнение S0 должно остановиться на safety checkpoint, если повторный discovery
или безопасный доступ к хосту недоступны. В текущем запуске discovery завершён,
а изменения выполнялись после проверки key-based SSH.

**Не устанавливать application stack до завершения S0.**

### S1 — Base runtime

- Docker Compose;
- private Docker network;
- persistent volumes;
- resource limits;
- common environment/configuration mechanism;
- базовые healthchecks;
- безопасная публикация только reverse proxy.

### S2 — Data services

Развернуть:

- PostgreSQL;
- Redis;
- MinIO.

Проверить:

- persistence после restart;
- credentials;
- private-only connectivity;
- migration execution;
- backup/restore smoke test.

### S3 — Application

Развернуть:

- Backend;
- Admin Web;
- Telegram Gateway.

Проверить:

- startup/readiness;
- authentication;
- questionnaire;
- profile persistence;
- deterministic generation;
- AI Gateway connectivity;
- program persistence;
- HTML generation;
- Telegram delivery.

### S4 — Worker / recovery

Только после завершения Phase 1.2-D:

- worker;
- retry/backoff;
- stale RUNNING recovery;
- recovery after process restart;
- idempotent retries.

### S5 — E2E acceptance

Проверить полный сценарий:

```text
Telegram
  → questionnaire
  → profile finalization
  → generation job
  → AI / deterministic fallback
  → validation
  → program persistence
  → HTML/media
  → delivery job
  → Telegram document
```

Отдельно проверить:

- AI unavailable → deterministic fallback;
- duplicate generation request;
- restart during generation;
- restart during delivery;
- Redis restart;
- PostgreSQL restart;
- MinIO restart;
- admin visibility;
- logs do not contain secrets/PII.

## 13. Production boundary

Staging не должен автоматически превращаться в production.

Production deployment потребует отдельного design review по:

- EU/RU component placement;
- DNS/TLS;
- production secrets;
- public ingress;
- backup destination;
- monitoring/alerting;
- rollback;
- availability;
- data protection;
- mail integration.

На текущем этапе:

- `217.60.10.168` (RU/CapRover) не изменять;
- `217.60.186.52` (RU/mail) не изменять;
- `217.60.3.80` (EU/FastPanel) не изменять существующую конфигурацию без отдельного задания.

## 14. Connector architecture — future direction

Проект в будущем может получить управляемый слой connectors для Telegram, PostgreSQL, Redis, MinIO, SMTP и других внешних систем.

**Это не входит в текущий staging deployment.**

До появления отдельного connector subsystem конфигурация должна оставаться в существующем механизме переменных окружения/secret storage проекта.

Не следует добавлять универсальный connector abstraction только ради staging.

Архитектурная цель на будущее:

```text
Application
    |
    v
Connector configuration / lifecycle
    |
    +-- Telegram
    +-- PostgreSQL
    +-- Redis
    +-- MinIO
    +-- SMTP
    +-- AI providers
```

При проектировании этого слоя необходимо учитывать lifecycle, health checks, credentials, safe rotation, dependency validation и отсутствие утечки secrets в UI/logs.

## 15. Definition of Done для staging foundation

Staging foundation считается готовым, когда:

- [ ] SSH key access проверен до отключения password auth;
- [ ] firewall policy действует на IPv4 и IPv6;
- [ ] Docker/Compose установлен и воспроизводим;
- [ ] private Docker network создана;
- [ ] PostgreSQL работает с persistent volume;
- [ ] Redis работает с persistent runtime state;
- [ ] MinIO работает с persistent volume;
- [ ] ни один data service не опубликован в Internet;
- [ ] backup/restore smoke test выполнен;
- [ ] healthchecks работают;
- [ ] resource limits определены;
- [ ] staging configuration и secrets отделены от production;
- [ ] deployment/restart procedure документирована;
- [ ] приложение успешно разворачивается после чистого restart;
- [ ] E2E deployment plan готов к выполнению.

## 16. Explicit non-goals

На этом этапе НЕ делать:

- production deployment;
- перенос Readora/CapRover;
- изменение mail server;
- публичное открытие PostgreSQL/Redis/MinIO;
- перенос production bot;
- перенос production database;
- создание универсального connector framework;
- внедрение Kubernetes;
- сложную HA/cluster архитектуру;
- замену FastPanel/CapRover без отдельного решения.

## 17. Source of truth

При расхождении между этим документом и фактическим состоянием инфраструктуры сначала выполняется discovery и документ обновляется. Нельзя выполнять destructive или production-impacting операции на основании устаревшего документа.

Код проекта и его runtime-контракты являются источником истины для application-level deployment requirements. `AGENTS.md` является обязательным набором правил для агентской работы.
