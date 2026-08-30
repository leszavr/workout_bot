# Component & Connector Architecture

**Статус:** реализовано / 30.08.2026
**Ветка:** `feature/component-connector-architecture`
**Миграция:** `0011_component_instances`
**Релиз:** `2.2.0`, контракт Backend ↔ Gateway `v1`

## 1. Зачем этот этап

Telegram Gateway размещается в EU-сегменте, Backend и Admin Web — в RU. Это уже
разные единицы развёртывания, но до этого этапа Backend не знал, какая версия
Gateway фактически работает: единственным источником была память того, кто
деплоил. Обновление Backend выполнялось «на удачу» — сломает оно Gateway или
нет, выяснялось после деплоя.

Этап закрывает четыре вещи:

1. каждый компонент машиночитаемо сообщает о себе версию, сборку и контракт;
2. Backend ведёт реестр фактически развёрнутых экземпляров;
3. совместимость определяется контрактом, а не совпадением версий или git SHA;
4. CI может до деплоя получить ответ SAFE/BLOCKED.

MAX Gateway не реализуется. Универсальное управление подключениями
(PostgreSQL/Redis/MinIO/SMTP через админку) не реализуется — см. раздел 11.

## 2. Архитектура Connector Layer

```text
Application (генерация, доставка, алерты)
        │
        ▼
ConnectorDirectory ── capability, а не тип компонента
        │
        ▼
Component Registry (PostgreSQL)
        ▲
        │ heartbeat + metadata (HTTPS, service token)
        │
┌───────┴────────┬──────────────────┬─────────────────┐
│ Telegram GW    │ MAX GW (future)  │ SMTP (future)   │
└────────────────┴──────────────────┴─────────────────┘
```

Ключевое решение: connector-контракт описан через **capability**, а не через
`component_type`. Код, которому нужна доставка сообщения, спрашивает
`Capability.TELEGRAM_DELIVERY` (в будущем — `MAX_DELIVERY`) и не содержит
Telegram-специфичных допущений. Появление MAX Gateway добавляет capability, а
не ветвление по типу в вызывающем коде.

Отдельный DI-контейнер не вводился: сервисы собираются существующими
фабриками `build_*`, как весь остальной backend.

Файлы:

| Слой | Файл | Роль |
| --- | --- | --- |
| domain | `src/domain/components.py` | типы, статусы, требования, вердикты, текущие контракты |
| application | `src/application/components/compatibility.py` | чистая логика совместимости и safety gate |
| application | `src/application/components/registry.py` | `ComponentRegistryService`, `ConnectorDirectory` |
| infrastructure | `src/infrastructure/persistence/postgres/component_repository.py` | реестр в PostgreSQL |
| infrastructure | `src/infrastructure/components/heartbeat_client.py` | HTTP-транспорт heartbeat |
| API | `apps/backend/api/v1/internal_routes.py` | `/internal/v1/*` для процессов и CI |
| API | `apps/backend/api/v1/component_routes.py` | `/api/v1/admin/components` для админки |
| auth | `apps/backend/service_auth.py` | service-to-service токен |
| gateway | `apps/telegram_gateway/component.py` | metadata Gateway и запуск heartbeat |

## 3. Component Registry

Таблица `component_instances` (миграция `0011`, аддитивная). Хранит **только
metadata**: credentials в реестр не попадают и не могут попасть — содержимое
строки целиком отдаётся Admin API.

```text
component_id (unique)   component_type   name       region
version                 build_sha        contract_version
capabilities (JSONB)    status
last_heartbeat_at       registered_at    updated_at
```

Почему PostgreSQL, а не Redis: реестр — основание для решения о деплое, такое
состояние должно переживать перезапуск и очистку кэша (AGENTS.md, Phase 1.2).

Уникален `component_id`, а не `component_type`: `telegram-eu-1` и
`telegram-eu-2` сосуществуют, второй не затирает первый. Этот же уникальный
индекс делает heartbeat идемпотентным.

`last_heartbeat_at` отделён от `updated_at`: heartbeat приходит раз в минуту без
изменения metadata, а `updated_at` показывает, когда компонент действительно
изменился (новая версия, контракт, capabilities).

Backend в реестре **не хранится**: он и есть тот, кто ведёт реестр. Админке он
отдаётся из собственной сборки (`self_reported: true`).

## 4. Versioning

Единственный источник версии — `src/version.py` (`APP_VERSION = "2.2.0"`),
согласован с `pyproject.toml`. FastAPI-метаданные больше не содержат
захардкоженную строку.

`BUILD_SHA` приходит из окружения сборки/деплоя (`docker compose` передаёт
`BUILD_SHA` в оба сервиса) и никогда не участвует в решении о совместимости —
только в трассировке.

Сейчас Backend и Gateway собираются из одного пакета, поэтому их `version`
совпадает. Это следствие упаковки, а не требование: реестр сравнивает версии
независимо, и разделение образов ничего не сломает.

Версии сравниваются численно (`parse_version`): лексикографически
`"2.10.0" < "2.9.0"`, что дало бы ложный `UPDATE_REQUIRED`.

## 5. Contract versioning

`contract_version` — версия протокола Backend ↔ компонент. Меняется **только**
при изменении самого протокола, а не при каждом релизе: иначе независимый
деплой стал бы невозможен.

Backend объявляет **множество** поддерживаемых контрактов
(`BACKEND_SUPPORTED_CONTRACTS`), а не одно значение. Это и есть механизм
EXPAND → MIGRATE → CONTRACT:

```text
EXPAND    Backend: supported = (3, 4)   Gateway: 3   → COMPATIBLE
MIGRATE   Backend: supported = (3, 4)   Gateway: 4   → COMPATIBLE
CONTRACT  Backend: supported = (4,)     Gateway: 3   → UPDATE_REQUIRED / BLOCKED
```

Убрать старый контракт можно только после миграции всех экземпляров — и safety
gate это проверяет до деплоя, а не после.

Текущие значения: Backend `contract_version = 1`, `supported = (1,)`; Gateway
`GATEWAY_CONTRACT_VERSION = 1`.

## 6. Compatibility rules

Требование на тип компонента (`ComponentRequirement`): множество поддерживаемых
контрактов, `min_version` (жёсткая граница) и `recommended_version` (мягкий
сигнал).

Порядок проверок в `evaluate()` (важен: обратный порядок сообщал бы «обновите
версию» там, где несовместим протокол):

| Условие | Состояние | Блокирует деплой |
| --- | --- | --- |
| heartbeat старше `OFFLINE_AFTER` (180 с) | `OFFLINE` | нет |
| требований к типу нет | `UNKNOWN` | нет |
| контракт ниже минимального | `UPDATE_REQUIRED` | да |
| контракт выше всех поддерживаемых | `INCOMPATIBLE` | да |
| версия ниже `min_version` | `UPDATE_REQUIRED` | да |
| версия ниже рекомендуемой | `UPDATE_RECOMMENDED` | нет |
| компонент сам сообщает `degraded` | `HEALTHY` + пояснение | нет |
| иначе | `COMPATIBLE` | нет |

Два отдельных состояния для несовместимости сделаны намеренно:
`UPDATE_REQUIRED` — обновлять нужно компонент, `INCOMPATIBLE` — обновлять нужно
Backend. Одно значение на оба случая направило бы администратора не туда.

`UNKNOWN` не сводится к «совместим»: проверка не выполнялась, и притворяться,
что всё в порядке, нельзя.

## 7. Heartbeat

`POST /internal/v1/components/heartbeat` — регистрация и heartbeat одной
операцией. У компонента нет состояния «уже зарегистрирован», поэтому его
перезапуск или пересоздание записи в БД восстанавливает контур сам.

Свойства:

- **идемпотентность** — upsert по `component_id`, `registered_at` сохраняется;
- **аутентификация** — общий секрет `X-Internal-Service-Token`
  (`INTERNAL_SERVICE_TOKEN`), сравнение через `compare_digest`. Без секрета
  internal API отвечает 503, а не пропускает запрос;
- **никаких секретов в payload** — только metadata; тест это проверяет;
- **не является условием работы** — недоступность Backend логируется, бот
  продолжает обслуживать анкеты. Мониторинг не должен быть точкой отказа
  бизнес-функции;
- **вердикт возвращается компоненту** — узнав `update_required`, Gateway пишет
  ERROR в собственный лог: несовместимость видна с обеих сторон.

Интервал 60 с, порог офлайна 180 с. Порог заметно больше интервала: одна
пропущенная отправка при сетевой заминке не должна показывать «Gateway упал».

Почему отдельный механизм аутентификации, а не admin JWT: у Gateway нет
пользователя, роли и срока сессии — это процесс. Admin JWT дал бы ему доступ ко
всему Admin API вместо одного endpoint'а. mTLS не вводился: канал RU↔EU уже
защищён туннелем, а инфраструктура ключей не дала бы выигрыша на этом этапе.

## 8. Deployment safety

`GET /internal/v1/deployment-safety` — machine-readable ответ для CI:

```json
{ "result": "SAFE" | "BLOCKED", "backend_version": "2.2.0",
  "backend_contracts": [1], "blocking": [...], "verdicts": [...] }
```

Блокируют только живые экземпляры с несовместимым контрактом или версией ниже
`min_version`. `UPDATE_RECOMMENDED` не блокирует — это сигнал. `OFFLINE` и
`UNKNOWN` не блокируют: остановленный экземпляр невозможно сломать обновлением,
а блокировка из-за выключенного контейнера сделала бы gate неработоспособным.

Тот же вердикт доступен админке (`/api/v1/admin/components/deployment-safety`):
администратор должен видеть причину блокировки, а не только факт.

Endpoint доступен по service-токену, потому что его потребитель — пайплайн, а
не браузер: получить admin JWT в CI нельзя.

## 9. Deployment manifest и independent deployment

`deploy/release-manifest.json` фиксирует состав релиза: версия и контракт каждого
компонента, требования Backend, правила обновления. Тест
`test_release_manifest_matches_code_contracts` не даёт манифесту разойтись с
кодом — иначе он начал бы врать deployment tooling.

Правила:

```text
Изменился только Admin Web        → Gateway НЕ переразворачивается
Изменился Backend, контракт тот же → Gateway НЕ переразворачивается
Изменился контракт Gateway         → Gateway требует отдельного деплоя
```

Равенство git SHA или версий между компонентами не требуется нигде — ни в коде,
ни в манифесте, ни в проверках.

## 10. Telegram Gateway

Бизнес-логика, EU routing, WireGuard/wstunnel и selective routing **не
менялись**. Добавлено ровно две вещи:

1. `apps/telegram_gateway/component.py` — metadata (id, версия, сборка,
   контракт, capabilities `telegram_polling` + `telegram_delivery`);
2. фоновая задача heartbeat в `main.py`, корректно отменяемая при остановке.

Если `BACKEND_INTERNAL_URL` или `INTERNAL_SERVICE_TOKEN` не заданы, heartbeat не
запускается и бот работает как раньше — локальная разработка не требует реестра.

`TELEGRAM_COMPONENT_ID` обязан различаться у разных экземпляров, иначе они
перезапишут друг друга в реестре. Это единственное новое операционное
требование.

## 11. Что сознательно НЕ реализовано

- **MAX Gateway** — заведён только тип компонента и место для capability;
- **CRUD коннекторов в админке** — компонент попадает в реестр, когда сам
  сообщает о себе. Ручная запись означала бы, что админка показывает желаемое
  состояние вместо фактического;
- **универсальное управление PostgreSQL/Redis/MinIO/SMTP через UI**, визуальный
  конструктор подключений, plugin marketplace — вне объёма (см.
  `docs/architecture/CONNECTOR_LAYER.md`);
- **хранение credentials в БД через админку** — конфигурация остаётся в
  environment/secrets;
- **migration framework для контрактов** — заложена только модель контрактов;
- **HTTP-граница Backend ↔ Gateway для бизнес-логики.** Gateway по-прежнему
  импортирует `build_generation_orchestrator` и работает с той же PostgreSQL.
  Это остаётся известным архитектурным разрывом (`DEPLOYMENT_AND_INTEGRATION_BASELINE.md`,
  раздел 4) и не входило в объём этого этапа: здесь строилась основа
  версионирования и совместимости, а не перенос генерации за сетевую границу;
- **фоновый health scheduler** — состояние определяется по свежести heartbeat,
  активных проверок Backend не делает;
- **автоматический вывод офлайн-экземпляров** — запись удаляется вручную
  администратором, чтобы «моргнувший» контейнер не терял историю регистрации.

## 12. Проверки

| Проверка | Команда | Результат |
| --- | --- | --- |
| Unit-тесты | `.venv/bin/python -m pytest tests/unit` | 711 passed (в т.ч. 32 новых) |
| Интеграционные | `./workout-manager.sh test integration` | 255 passed (в т.ч. 16 новых) |
| Типы фронтенда | `npx tsc --noEmit` | без ошибок |
| Линтер фронтенда | `npm run lint` | без предупреждений |
| Миграция | `alembic upgrade head` | `0010 → 0011` применена на dev и test БД |
| Smoke | `curl /version`, heartbeat, deployment-safety | ответы корректны, вердикт `COMPATIBLE`, gate `SAFE` |

Покрытие тестами по требованиям этапа:

- metadata: состав, отсутствие секретов, явный статус;
- регистрация: повторная регистрация идемпотентна, обновление версии не
  создаёт дубль, невалидный `component_id` отклоняется (422), без токена — 401;
- офлайн: просроченный heartbeat, «одна пропущенная отправка не гасит»,
  сохранение последней известной версии;
- совместимость: `3/3`, `4/3`, `2/3`, контракт новее Backend, версия ниже
  минимума, рекомендуемая версия, деградация, неизвестный тип;
- deployment safety: SAFE при совместимом контракте, BLOCKED при сброшенном
  контракте, frontend-only, telegram-only, офлайн не блокирует, пустой реестр;
- multiple instances: `telegram-eu-1` и `telegram-eu-2` без конфликтов и с
  независимыми вердиктами;
- манифест: соответствие кода и `release-manifest.json`.

## 13. Staging deployment (30.08.2026)

Хост `192.168.1.3`, `BUILD_SHA=13913fd`. Процедура — по
`STAGING_ADMIN_ANALYTICS_DEPLOYMENT.md`: rsync → build трёх образов → миграция
из свежесобранного образа (`run --rm --no-deps backend alembic upgrade head`) →
`up -d`.

В `staging-app.env` добавлены `INTERNAL_SERVICE_TOKEN` (сгенерирован
`openssl rand -hex 32` на самом хосте, в Git не попадает),
`BACKEND_INTERNAL_URL=http://backend:8000`, `TELEGRAM_COMPONENT_ID=telegram-eu-1`,
`TELEGRAM_REGION=EU`, `BACKEND_REGION=RU`. Прежний файл сохранён как
`staging-app.env.bak.*`, режим 0600. S1-инфраструктура (postgres/redis/minio) не
перезапускалась, volumes не затрагивались.

| Проверка | Результат |
| --- | --- |
| Миграция | `0010 → 0011` применена |
| `/health`, `/ready` | `ok`, `storage: true` |
| `/version` | `2.2.0`, `build_sha=13913fd`, contract `v1` |
| Heartbeat Gateway | `event=component_heartbeat_ok state=compatible`, HTTP 200 |
| Реестр | `telegram-eu-1`, region `EU`, capabilities `telegram_polling`+`telegram_delivery` |
| Deployment safety | `SAFE`, `blocking: []` |
| Admin API | `/api/v1/admin/components` → 2 записи (backend self-reported + Gateway) |
| Admin UI | `/infrastructure` → 200 |
| EU routing | `ip rule ... from 172.18.0.20 lookup 51820` на месте, IP бота `172.18.0.20`, `api.telegram.org` отвечает 302 из контейнера |
| `RestartCount` | 0 у всех трёх контейнеров |

Полный destructive E2E не выполнялся: изменения не затрагивают генерацию,
доставку и данные. Проверялся только контур, добавленный этим этапом, плюс
регрессия health/routing/restart.

## 14. Дальнейшие шаги

1. Вынести Gateway → Backend взаимодействие за HTTP-границу (единственный
   оставшийся deployment blocker целевой topology);
2. подключить `/internal/v1/deployment-safety` в CI как обязательный шаг перед
   деплоем Backend;
3. при появлении MAX Gateway: добавить capability и требование, реализация
   connector-контракта уже не потребует изменений в реестре и Admin UI;
4. при разделении образов — вынести версию Gateway из общего `src/version.py`;
5. рассмотреть авто-архивацию записей, не отвечающих дольше суток, если реестр
   начнёт накапливать мёртвые экземпляры.
