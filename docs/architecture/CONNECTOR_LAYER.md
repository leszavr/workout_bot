# Infrastructure Connector Layer

**Статус:** архитектурное решение / baseline 24.08.2026  
**Реализация:** не требуется на текущем этапе. Текущие ENV/secret variables остаются источником runtime-конфигурации.

## 1. Зачем нужен слой

Workout Bot постепенно использует несколько внешних инфраструктурных систем: Telegram, PostgreSQL, Redis, MinIO/S3, SMTP, AI providers и другие сервисы. Нельзя допускать, чтобы application-код был связан с конкретным способом получения конфигурации (`os.getenv()`), конкретным клиентом или единственным экземпляром инфраструктуры.

Цель Connector Layer — отделить **что приложению нужно** от **где и как это подключено**.

Пример будущей конфигурации:

```text
TelegramConnector #1 → Bot A
TelegramConnector #2 → Bot B
PostgresConnector #1 → primary DB
PostgresConnector #2 → test DB
SMTPConnector #1 → primary SMTP
MinioConnector #1 → RU object storage
```

Это архитектурная возможность, а не требование немедленно сделать multi-instance/multi-database runtime.

## 2. Главный принцип

```text
Application / Domain
        ↓
  connector contract
        ↓
 infrastructure adapter
        ↓
 ENV / secret store / future connector registry
```

Application-слой не должен читать ENV напрямую и не должен знать конкретные SDK инфраструктурных сервисов.

Конкретные реализации принадлежат Infrastructure Layer.

## 3. Connector contract

Будущий базовый контракт должен концептуально поддерживать:

```text
Connector
├── id / logical name
├── type
├── enabled
├── validate_configuration()
├── connect()
├── health_check()
└── close()
```

Дополнительно конкретный тип может предоставлять безопасный `connection_test()`.

Контракт не должен заставлять все connector types иметь одинаковую бизнес-логику. Например, Telegram и PostgreSQL имеют разные способы проверки доступности.

## 4. Источник конфигурации — сейчас и потом

### Сейчас

```text
ENV / secret variables
        ↓
configuration / dependencies
        ↓
connector adapter
```

Никакого хранения инфраструктурных credentials в PostgreSQL ради этой абстракции не вводить.

### Будущее

Когда появится необходимость динамического управления:

```text
Admin UI
   ↓
Connector Registry
   ↓
DB metadata + encrypted secret references
   ↓
ConnectorResolver
   ↓
adapters
```

Connector Registry должен появляться только отдельным этапом после подтверждения реальной потребности.

## 5. Что может стать connector type

Минимальный ожидаемый набор:

- `telegram` — Telegram Bot API;
- `postgres` — PostgreSQL;
- `redis` — FSM/runtime state;
- `minio` / `s3` — object storage;
- `smtp` — почта;
- `ai` — внешний AI provider.

AI уже имеет отдельный Provider/Endpoint/Model lifecycle. Не дублировать его новым generic CRUD. Connector Layer должен быть инфраструктурным фундаментом, а AI configuration остаётся специализированной подсистемой.

## 6. Health и lifecycle

Connector health — runtime-состояние, а не конфигурация.

Минимальные состояния:

```text
UNKNOWN → HEALTHY
       ↘ UNHEALTHY
```

Допустимо хранить timestamp последней проверки и безопасный класс ошибки. Нельзя хранить API keys, access tokens, полные ответы сервисов или PII в health state.

Периодический health scheduler сейчас **не вводится**. Активная проверка выполняется по требованию или естественно во время реального использования сервиса.

## 7. Безопасность

Credentials:

- не попадают в application logs;
- не возвращаются Admin API;
- не участвуют в обычных health responses;
- должны быть отделены от обычных configuration values;
- при появлении DB-backed registry должны храниться через существующий безопасный secret/encryption механизм, а не plaintext JSONB.

Администратор должен видеть состояние подключения, но не секрет.

## 8. Runtime replacement — важное ограничение

Connector Registry в будущем **не означает**, что администратор может произвольно переключить работающий процесс с одной PostgreSQL/Redis системы на другую без контроля lifecycle.

Для stateful connectors смена конфигурации может требовать:

- validation;
- connection test;
- drain/restart;
- миграций;
- rollback;
- проверки совместимости схемы.

Поэтому runtime hot-swap является отдельным архитектурным решением и не следует автоматически из наличия Registry.

## 9. Multi-bot

Ключи runtime state уже допускают `bot_id` в Redis FSM. В будущем несколько Telegram connectors должны изолировать:

- bot credentials;
- polling/webhook lifecycle;
- bot-specific FSM namespace;
- delivery routing.

Не допускается случайное использование Bot A credentials для Bot B.

## 10. Что НЕ делать сейчас

Не реализовывать в текущем этапе:

- Connector CRUD в Admin UI;
- таблицы `connectors`;
- хранение credentials в БД;
- dynamic runtime hot-swap;
- универсальный DI container;
- background health scheduler;
- multi-database runtime;
- multi-bot UI.

Сейчас достаточно соблюдать dependency direction и не создавать новые прямые инфраструктурные зависимости в application/domain слоях.

## 11. Definition of architectural readiness

Архитектура считается подготовленной, когда:

- application code зависит от contracts, а не SDK;
- infrastructure adapters скрывают конкретные clients;
- ENV остаётся допустимым Configuration Source;
- health является отдельным runtime concern;
- credentials отделены от обычных данных;
- специализированный AI Gateway не заменяется generic connector CRUD;
- будущий Connector Registry можно добавить без переписывания domain/application logic.

## 12. Связь с deployment

Первый deployment не должен ждать Connector Registry. Staging/production-like окружение использует ENV/secret variables и те же infrastructure adapters.

После успешного E2E deployment можно решить, какие connectors действительно требуют динамического управления.
