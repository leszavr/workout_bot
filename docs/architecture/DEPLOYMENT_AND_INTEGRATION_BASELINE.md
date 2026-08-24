# Deployment & Integration Baseline

**Статус:** design baseline / 24.08.2026  
**Цель:** первый production-like staging deployment и реальная E2E-проверка.

## 1. Цель этапа

До дальнейшего усложнения reliability-контуров необходимо проверить систему на реальной инфраструктуре. Это не production launch и не security certification. Это контролируемый staging/integration environment, максимально близкий к будущей эксплуатации.

Порядок:

```text
Phase 1.2-C DONE
      ↓
Deployment Readiness Audit
      ↓
Staging deployment
      ↓
Real E2E acceptance
      ↓
Phase 1.2-D Worker / Retry / Recovery
      ↓
Phase 1.2-E Delivery
      ↓
Phase 1.2-F Admin visibility
      ↓
Phase 1.2-G final E2E
```

## 2. Предварительная topology

Планируемая схема с разделением серверов:

```text
                     Telegram
                         │
                         ▼
              ┌─────────────────────┐
              │ Foreign server      │
              │ Telegram Gateway    │
              │ aiogram / FSM       │
              └──────────┬──────────┘
                         │ HTTPS
                         │ authenticated service-to-service call
                         ▼
              ┌─────────────────────┐
              │ Russian server      │
              │ Backend / FastAPI   │
              │ Admin Web           │
              │ AI Gateway          │
              │ Redis               │
              │ PostgreSQL          │
              │ MinIO/S3            │
              └─────────────────────┘
```

Это **целевой baseline**, а не утверждение о том, что каждый компонент обязательно должен находиться именно на указанном сервере. Финальная topology определяется после получения параметров серверов и сетевых ограничений.

## 3. Основные границы

### Foreign server

Предварительно:

- Telegram Gateway;
- Telegram Bot API connectivity;
- polling/webhook lifecycle;
- локальные runtime secrets, относящиеся только к Telegram Gateway;
- HTTPS client connection к Backend.

### Russian server

Предварительно:

- FastAPI backend;
- Admin Web;
- PostgreSQL;
- Redis;
- MinIO/S3;
- AI Gateway и provider connectivity, если конкретный provider доступен из этого сегмента.

Нельзя переносить компонент только ради схемы. Решение принимается по latency, network reachability, policy и operational simplicity.

## 4. Gateway → Backend

Gateway не должен напрямую обращаться к PostgreSQL, Redis, MinIO или внутренним application services российского сервера.

Его внешний контракт должен проходить через Backend/API или специально выделенный internal service API.

Минимальные требования:

- TLS;
- authentication/service credential;
- timeout;
- безопасная обработка ошибок;
- correlation/request ID;
- отсутствие секретов и PII в URL/query parameters;
- минимальные firewall rules.

Если текущая реализация не поддерживает такой boundary, это фиксируется как deployment blocker и сначала устраняется отдельным PR.

## 5. Deployment Readiness Audit

Перед установкой проверить repository `main`:

### Application

- startup commands всех сервисов;
- обязательные ENV;
- optional ENV;
- secrets;
- migrations;
- health/readiness;
- graceful shutdown;
- Redis FSM;
- PostgreSQL;
- MinIO/S3;
- Telegram;
- AI Gateway;
- Admin authentication.

### Build

- Dockerfiles;
- compose/deployment manifests;
- frontend production build;
- backend dependency installation;
- migration image/command;
- static assets/media.

### Network

- inbound ports;
- outbound connectivity;
- DNS/TLS;
- firewall/security groups;
- Gateway → Backend path;
- Backend → PostgreSQL/Redis/MinIO;
- Backend → AI provider.

### Operations

- logs;
- restart policy;
- persistent volumes;
- PostgreSQL backup/restore procedure;
- media backup/re-import;
- deployment rollback;
- migration rollback policy.

Audit должен закончиться таблицей:

```text
Component | Required | Current state | Blocker | Action
```

## 6. Staging deployment rules

До первого запуска:

1. создать отдельные staging credentials;
2. не использовать production secrets;
3. выполнить database migration отдельно и явно;
4. проверить `/health` и `/ready`;
5. проверить Redis до запуска Telegram polling;
6. проверить object storage;
7. проверить AI configuration/readiness;
8. проверить Admin authentication;
9. включить безопасное logging;
10. зафиксировать deployed commit SHA.

Нельзя считать deployment успешным только потому, что процессы запущены.

## 7. E2E acceptance matrix

### A. Questionnaire

- `/start`;
- прохождение анкеты;
- Redis FSM;
- restart Gateway во время анкеты;
- завершение анкеты;
- профиль появляется в PostgreSQL.

### B. Deterministic generation

- profile finalized;
- GenerationJob создан;
- generation succeeds;
- программа сохранена;
- одна версия программы;
- повторный запрос не создаёт duplicate.

### C. AI generation

- configured provider/model;
- readiness check;
- successful AI generation;
- validation;
- persistence;
- requested/actual generator.

### D. AI failure/fallback

Проверить отдельно:

- provider unavailable;
- timeout;
- rate limit, если можно безопасно симулировать;
- invalid/unusable AI response;
- not-ready configuration.

Для автоматического pipeline:

```text
AI failure
   ↓
classified error
   ↓
fallback decision
   ↓
deterministic generation
   ↓
ONE GenerationJob
   ↓
ONE saved program
```

### E. Admin

- login;
- profiles;
- programs;
- AI configuration;
- readiness;
- infrastructure health;
- fallback events;
- generation errors.

### F. Restart/recovery baseline

До 1.2-D необходимо зафиксировать текущее поведение:

- restart Gateway during questionnaire;
- restart Backend before generation;
- restart Backend during generation;
- restart Backend after generation;
- restart Redis;
- restart PostgreSQL.

Если текущая архитектура ожидаемо не восстанавливает generation job после crash, это не считать багом 1.2-C: зарегистрировать как входные данные для 1.2-D.

## 8. Security baseline

До staging:

- secrets не коммитятся;
- `.env` не публикуется;
- PostgreSQL не выставляется в public Internet без необходимости;
- Redis не выставляется в public Internet;
- MinIO admin API не выставляется публично без необходимости;
- Admin Web/API защищены authentication;
- Gateway service credential ограничен назначением;
- TLS используется для межсерверного API;
- firewall разрешает только необходимые направления.

## 9. Observability baseline

На staging достаточно:

- structured application logs;
- service startup/shutdown events;
- generation job ID;
- profile/program identifiers без лишнего PII;
- error class/code;
- connector/service name;
- request/correlation ID.

Полноценный metrics/trace/monitoring stack относится к Phase 1.3 и не должен блокировать первый deployment, если базовые health/logging работают.

## 10. Backup baseline

Перед E2E с реальными данными определить:

- PostgreSQL backup;
- restore test;
- media/object storage backup или reproducible import;
- retention;
- где хранятся backup credentials.

Staging может использовать короткую retention, но процедура restore должна быть проверяема.

## 11. Rollback

Каждый deployment должен быть связан с commit SHA.

Rollback должен предусматривать:

1. остановку нового application version;
2. возврат предыдущего image/commit;
3. оценку совместимости DB schema;
4. запрет автоматического downgrade migration без проверки;
5. восстановление сервисов;
6. sanity check `/health`/`/ready`.

## 12. Что не является частью первого deployment

Не блокировать staging следующими задачами:

- Connector Registry/Admin CRUD;
- multi-bot UI;
- dynamic infrastructure hot-swap;
- Redis worker/retry scheduler;
- полноценный monitoring stack;
- billing;
- production traffic/load testing.

## 13. Entry criteria

Deployment начинается только после:

- актуального `main`;
- зелёного CI;
- известных server parameters;
- SSH/access credentials безопасно переданных оператору/агенту;
- DNS/IP/network information;
- списка required secrets;
- согласованной topology.

## 14. Exit criteria

Staging считается принятым, если:

- все необходимые сервисы стабильно запускаются;
- migrations применяются;
- health/readiness зелёные;
- Telegram принимает и завершает анкету;
- profile сохраняется;
- deterministic generation работает;
- AI generation работает при настроенном provider;
- AI failure/fallback проверен;
- generation idempotency проверена;
- Admin UI доступна;
- restart questionnaire проверен;
- известное поведение generation crash зафиксировано для 1.2-D;
- нет утечки secrets/PII в логах;
- deployed commit SHA зафиксирован.

## 15. Следующий инженерный этап

После staging E2E не следует автоматически чинить всё найденное в одном PR.

Каждая проблема классифицируется:

- deployment blocker → отдельный fix PR;
- 1.2-D reliability → worker/retry/recovery PR;
- 1.2-E delivery → delivery PR;
- 1.3 operations/security → operations PR;
- documentation gap → docs PR.

Это сохраняет трассируемость roadmap и облегчает acceptance каждого этапа.
