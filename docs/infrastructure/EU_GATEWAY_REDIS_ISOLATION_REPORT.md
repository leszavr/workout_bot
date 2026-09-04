# Изоляция EU Telegram Gateway от хранилищ RU

Дата: 4 сентября 2026. Ветка `feature/eu-gateway-redis-isolation`.
Развёрнуто и проверено на staging `192.168.1.3`.

## Задача

Сетевая граница Gateway оставила у шлюза одно хранилище — Redis в EU для
служебных ключей aiogram с TTL. Задача: устранить ненужную доступность шлюза к
внутренним хранилищам RU-сегмента, соблюдая `minimal change + least privilege` и
не затрагивая VPN, routing и RU-инфраструктуру.

## Discovery: нужен ли шлюзу Redis

aiogram требует два объекта: `storage` для FSM-состояния и `events_isolation`
для сериализации параллельных обновлений одного пользователя. Проверено, зачем
им внешнее хранилище:

| Аргумент за Redis | Факт |
| --- | --- |
| «Состояние анкеты потеряется при рестарте» | Хендлеры шлюза FSM не используют вовсе: позиция диалога и ответы лежат в `telegram_sessions` (PostgreSQL, RU). Терять при рестарте нечего — проверено тестом `test_restart_loses_nothing` и вживую на staging 3 сентября |
| «Нужна общая блокировка между экземплярами» | `getUpdates` с одним токеном обслуживает ровно один процесс: второй получает от Telegram 409 Conflict. Очередь доставки защищена арендой в PostgreSQL (`FOR UPDATE SKIP LOCKED`), а не блокировкой в шлюзе |
| «Redis нужен worker'у или Backend» | Ни одного обращения: worker держит очередь повторов и аренду в PostgreSQL (`apps/worker/main.py:20`), у Backend клиента Redis нет |

Вывод: Redis не нужен ни шлюзу, ни остальным компонентам. Служебное состояние
aiogram переведено в память процесса.

## Слой 1: у кода нет зависимости

| Изменение | Файл |
| --- | --- |
| `MemoryStorage` + `SimpleEventIsolation` вместо `RedisStorage`/`RedisEventIsolation` | `apps/telegram_gateway/main.py` (`build_isolation`) |
| Удалён `FSMStorage` (клиент Redis, verify, close) | `src/infrastructure/telegram/fsm_storage.py` — удалён |
| Удалён обработчик сбоя хранилища и его роутер | `apps/telegram_gateway/handlers/errors.py` — удалён |
| Удалён `FSMStorageError` | `src/errors.py` |
| Удалены `REDIS_URL` и `GATEWAY_STATE_TTL_SECONDS` | `src/infrastructure/config.py` |
| `aiogram[redis]` → `aiogram`: клиента Redis больше нет в образе | `pyproject.toml` |
| Удалён Redis-гейт запуска бота, `ensure_redis`, `redis_probe`, `env_redis_url`, `REDIS_URL` из `doctor` и из прогона тестов | `workout-manager.sh` |
| Удалён сервис `redis` из CI, добавлен isolation-тест в job `contracts` | `.github/workflows/ci.yml` |

Образ один на Backend, Gateway и worker, поэтому снят сам extra: пока клиент
установлен, вернуть подключение можно одной строкой.

Диалог при недоступности Backend по-прежнему отвечает пользователю — это делает
`handlers/dialog.py`, а не удалённый обработчик хранилища.

## Слой 2: у шлюза нет credentials

| Изменение | Файл |
| --- | --- |
| Удалён контейнер `gateway-redis` и `REDIS_URL` у сервиса шлюза | `docker/docker-compose.yml`, `docker/staging-app-compose.yml` |
| Удалён `WORKOUT_DATA_DIR`: каталога данных в EU нет | `docker/staging-app-compose.yml` |
| Удалены `REDIS_URL` и `GATEWAY_STATE_TTL_SECONDS` из примера env шлюза | `docker/staging-gateway.env.example` |
| Удалён `REDIS_URL` из примера env приложения | `docker/staging-app.env.example`, `.env.example` |
| На хосте удалена строка `GATEWAY_STATE_TTL_SECONDS` из `staging-gateway.env` (резервная копия сохранена) | staging |

Фактический состав окружения контейнера шлюза после развёртывания — 11
переменных приложения:

    ADMIN_CHAT_ID BACKEND_INTERNAL_URL BACKEND_REQUEST_RETRIES
    BACKEND_REQUEST_TIMEOUT_SECONDS BACKEND_RETRY_DELAY_SECONDS BOT_TOKEN
    BUILD_SHA COMPONENT_REGION INTERNAL_SERVICE_TOKEN TELEGRAM_COMPONENT_ID
    TELEGRAM_COMPONENT_NAME TELEGRAM_DELIVERY_BATCH_SIZE
    TELEGRAM_DELIVERY_POLL_INTERVAL_SECONDS

Ни `REDIS_URL`, ни `DATABASE_URL`, ни `MINIO_*`, ни `JWT_SECRET`, ни
`AI_SECRETS_KEY`. Тома у контейнера нет (`Mounts=[]`), корневая ФС для `appuser`
недоступна на запись.

## Слой 3: сетевая доступность

Discovery показал: удалить шлюз из `workout_net` нельзя — связность
Gateway → Backend идёт по той же сети, а policy routing на хосте матчит
фиксированный адрес контейнера `172.18.0.20` (`ip rule` для таблицы 51820).
Отдельная сеть потребовала бы второго интерфейса у шлюза и правки policy
routing, то есть выхода за границы задачи.

Проверено, почему обычный firewall здесь не работает: контейнеры находятся на
одном bridge `br-599d5a1e8212`, трафик между ними коммутируется на канальном
уровне. `br_netfilter` на хосте не загружен, `/proc/sys/net/bridge` отсутствует.
Счётчики `FORWARD` и `DOCKER-USER` при обращении шлюза к PostgreSQL не
изменились (43879 пакетов до и после пяти попыток), то есть правила в семействе
`ip` этот трафик не видят.

Решение — deny в семействе `bridge`, которое такой трафик видит. Одобрено
архитектором как точечное изменение.

### Правило

`deploy/nftables-workout-gateway-isolation.nft` (на хосте —
`/etc/nftables-workout-gateway-isolation.nft`):

```
table bridge workout_gateway_isolation {}
delete table bridge workout_gateway_isolation

table bridge workout_gateway_isolation {
	chain forward {
		type filter hook forward priority filter; policy accept;

		ip saddr 172.18.0.20 tcp dport { 5432, 6379, 9000, 9001 } \
			counter drop comment "EU gateway must not reach RU storages"
	}
}
```

- источник — фиксированный адрес шлюза из compose (`TELEGRAM_BOT_IP`);
- порты: PostgreSQL 5432, Redis 6379, MinIO API 9000, MinIO Console 9001
  (консоль была доступна с шлюза, поэтому включена);
- `policy accept`: таблица запрещает только перечисленное, остальной трафик
  bridge не затрагивает. Порта Backend (8000) и Telegram (443) в правиле нет;
- пустое объявление + `delete` перед созданием даёт идемпотентность: повторный
  запуск заменяет таблицу, а не добавляет второе правило;
- `flush ruleset` не используется: правила ufw и docker живут в семействах
  `ip`/`ip6` и не пересекаются с этой таблицей.

### Персистентность

`deploy/workout-gateway-isolation.service` (на хосте —
`/etc/systemd/system/workout-gateway-isolation.service`), `enabled`:

```
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/sbin/nft -f /etc/nftables-workout-gateway-isolation.nft
ExecStop=-/usr/sbin/nft delete table bridge workout_gateway_isolation
After=docker.service network-pre.target
```

Отдельный unit, а не `nftables.service`: тот выключен, и его включение
применило бы `/etc/nftables.conf` с `flush ruleset`, то есть снесло бы правила
ufw и docker. Глобальный ruleset не тронут, `ufw status` остался `active`,
цепочки `FORWARD`/`DOCKER-USER` на месте.

### Rollback

```bash
systemctl stop workout-gateway-isolation      # снять правило (таблица удаляется)
systemctl disable workout-gateway-isolation   # не применять при загрузке
rm /etc/systemd/system/workout-gateway-isolation.service
rm /etc/nftables-workout-gateway-isolation.nft
systemctl daemon-reload
```

Проверено фактически: после `stop` все четыре порта снова `TCP=OPEN`, после
`start` — снова заблокированы. Ничего кроме этой таблицы откат не затрагивает.

## Слой 4: regression-тесты

`tests/unit/test_gateway_storage_isolation.py`, 41 проверка. Включён в job
`contracts` CI.

- маркеры хранилищ (`REDIS_URL`, `RedisStorage`, `DATABASE_URL`,
  `create_async_engine`, `MINIO_*`, …) в каждом файле шлюза;
- граф импортов процесса от точки входа: `redis`, `sqlalchemy`, `asyncpg`,
  `minio` не должны появляться и транзитивно, через общий модуль;
- `aiogram[redis]` и прямая зависимость `redis` отсутствуют в `pyproject.toml`;
- разрешённые направления сохранены: `aiogram`, `httpx`, `backend_client`;
- запись на диск: `open`, `write_bytes`, `FSInputFile`, `PHOTOS_DIR` и прочее
  запрещены; отправка документов — только `BufferedInputFile`;
- compose: у сервиса шлюза нет переменных хранилищ, нет общего env-файла, нет
  тома, нет `WORKOUT_DATA_DIR`, нет сервиса `gateway-redis`;
- env-пример шлюза: ключей хранилищ нет, `BOT_TOKEN`/`BACKEND_INTERNAL_URL`/
  `INTERNAL_SERVICE_TOKEN` на месте;
- сетевое правило: файл и unit в репозитории, адрес совпадает с `TELEGRAM_BOT_IP`
  из compose, порты хранилищ перечислены, портов 8000 и 443 в правиле нет,
  идемпотентность, отсутствие `flush ruleset`, наличие `ExecStop`.

Отдельный тест проверяет, что точка входа собирает именно `MemoryStorage` +
`SimpleEventIsolation`, а не только фикстура тестов.

## Развёртывание на staging

Порядок: rsync → build `telegram-bot`, `backend`, `worker` → пересоздание
контейнеров → удаление orphan `gateway-redis` → правило nftables → unit.
Миграций не потребовалось: `alembic current` = `0013 (head)` до и после.

## Проверки

### Требуемый уровень изоляции

| Поток | Требование | Факт |
| --- | --- | --- |
| Gateway → Backend Internal API | ALLOWED | PASS: `backend:8000` OPEN, polling очереди каждые 5 с, `contract` = `{"contract_version":1,"backend_version":"2.3.0"}` |
| Gateway → Telegram API | ALLOWED | PASS: `bot.me()` id=7903710552, установленное соединение `149.154.166.110:443` |
| Gateway → Component Registry | ALLOWED | PASS: `event=component_heartbeat_ok state=compatible` |
| Gateway → Redis | DENIED | BLOCKED тремя слоями: клиента нет (`ImportError`), `REDIS_URL` нет, сетевой путь `TimeoutError` |
| Gateway → PostgreSQL | DENIED | BLOCKED: `DATABASE_URL` нет, сетевой путь `TimeoutError` (до правила — `InvalidPasswordError`) |
| Gateway → MinIO API | DENIED | BLOCKED: ключей нет, сетевой путь `TimeoutError` (до правила — HTTP 403) |
| Gateway → MinIO Console | DENIED | BLOCKED: `TimeoutError` |

Счётчик правила растёт при попытках: `packets 16 bytes 960` после серии проб.

### RU-инфраструктура не затронута

| Проверка | Результат |
| --- | --- |
| Backend → PostgreSQL / Redis / MinIO | OPEN, OPEN, OPEN |
| Worker → PostgreSQL / Redis / MinIO | OPEN, OPEN, OPEN |
| Backend `/health`, `/ready` | `{'status': 'ok'}`, `{'status': 'ok', 'storage': True}` |
| `deployment-safety` | `SAFE`, blocking `[]`; gateway 1.0.0 и worker 1.0.0 — `compatible` |
| Целостность данных | exercises 873, profiles 28, programs 29, generation_jobs 28, telegram_sessions 1, deliveries 28, users 27 |
| Застрявшие операции | `sending` с истёкшей арендой — 0, `running` с истёкшей арендой — 0 |
| RestartCount всех семи контейнеров | 0 |
| egress | gateway `31.58.181.202` (EU-туннель), backend и worker `91.79.244.18` |
| `ip rule` для `172.18.0.20` | 3 правила на месте, не изменялись |
| `wg-workout` | handshake активен |
| ufw | `active`, цепочки docker на месте |

### После reboot хоста

Хост перезагружен целиком. После подъёма:

- unit `enabled`/`active`, таблица bridge на месте;
- адрес шлюза снова `172.18.0.20` — правило продолжает адресовать его;
- Gateway → PostgreSQL/Redis/MinIO API/MinIO Console: BLOCKED (`TimeoutError`);
- Gateway → Backend: OPEN; `bot.me()` PASS; egress `31.58.181.202`;
- Backend и worker → все три хранилища: OPEN;
- `/health`, `/ready`, `deployment-safety` = `SAFE`;
- данные без изменений, RestartCount всех контейнеров 0;
- `ip rule` (3 правила) и handshake `wg-workout` на месте.

### Локальные проверки

| Проверка | Результат |
| --- | --- |
| `pytest tests/unit` | 832 passed, 16 skipped |
| `pytest tests/integration` | 279 passed (на тестовой БД после `alembic upgrade head`) |
| `scripts.check_contracts` | контракты согласованы |
| `docker compose config` для обоих compose | валидны, `gateway-redis` отсутствует |
| Smoke: импорт шлюза с блокировкой `redis`/`sqlalchemy`/`asyncpg`/`minio` | процесс собирается, `MemoryStorage` + `SimpleEventIsolation`, один роутер `telegram_gateway.dialog` |

## Что не менялось

WireGuard (`wg-workout`, `wg-personal`), wstunnel, IPv6 transport, routing
EU ↔ RU, policy routing Telegram-трафика, публичные порты, SSH policy, docker
topology (сеть `workout_net` и её подсеть), S1-инфраструктура
(`staging-s1-compose.yml`), схема БД. Redis RU остался на месте и продолжает
работать: его использование прекратилось на уровне приложения, а не
инфраструктуры.

## Осознанные ограничения

1. **Redis RU остаётся запущенным** контейнером, к которому не подключается ни
   один компонент (`dbsize` = 0, `connected_clients` = 1 — локальный `redis-cli`
   проверки). Удаление самого контейнера и его тома — отдельная задача по
   RU-инфраструктуре, в scope изоляции EU не входит.
2. **Правило адресует фиксированный IP.** Адрес закреплён в compose
   (`TELEGRAM_BOT_IP`, по умолчанию `172.18.0.20`), от него же зависит
   существующий policy routing, поэтому связь надёжная. Но при смене адреса
   правило нужно обновлять синхронно с compose; на это указывает тест, который
   сверяет адрес в правиле с compose.
3. **Осиротевший том прежнего `gateway-redis`** остался в docker (dangling,
   пустой — 4 КБ, файлов нет). Не удалён: удаление тома — необратимая операция,
   и она не требуется для изоляции. Убирается `docker volume prune` при
   плановой уборке.
4. **Изоляция проверена по TCP и по протоколу изнутри контейнера**, но не
   средствами внешнего сканера. Для текущей задачи этого достаточно: проверка
   выполняется из того же namespace, откуда шёл бы реальный доступ.

## Rollback всей задачи

1. Код: `git revert` коммита ветки, rsync на staging, пересборка и пересоздание
   `telegram-bot`, `backend`, `worker`. Схема БД не менялась, миграции откатывать
   не нужно.
2. Env шлюза: восстановить `staging-gateway.env` из резервной копии
   `staging-gateway.env.bak.<timestamp>` (в ней есть `GATEWAY_STATE_TTL_SECONDS`);
   `REDIS_URL` задавался в compose, а не в env-файле.
3. Контейнер `gateway-redis`: возвращается вместе с compose из revert.
4. Сетевое правило: последовательность из раздела «Rollback» выше.
