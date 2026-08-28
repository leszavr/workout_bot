# Full STAGING E2E Acceptance Report

Дата теста: 2026-08-28 (UTC)
Исполнитель: агент Kilo, по заданию `.tmp/Full_STAGING_E2E_TEST_PROMPT.md`
Тип: финальный приёмочный тест полного пользовательского пути на staging

---

## 1. Executive Summary

Полный пользовательский путь пройден реально: живой Telegram-пользователь
прошёл анкету в `@wrkoutassist_bot`, профиль сохранён в PostgreSQL,
автогенерация запустилась после finalize, программа собрана, провалидирована,
сохранена и **доставлена пользователю в Telegram как HTML-документ**
(`sent_message_id=581`, статус доставки `sent`).

**FINAL VERDICT: PASS с одной оговоркой (degraded generation).**

Все 12 критериев Full PASS из раздела 18 задания выполнены. Однако программу
собрал **не ИИ, а детерминированный резервный генератор**: primary-генератор
`ai` отработал три попытки (первичный запрос + два repair), каждый раз вернул
невалидный JSON, после чего сработал штатный fallback `ai → deterministic`.
Архитектурно это предусмотренное поведение (`PROGRAM_FALLBACK_GENERATOR=deterministic`),
пользовательский сценарий не сломался, но качество подбора программы деградирует.
Это зафиксировано как FINDING-1 (не blocker acceptance, но продуктовый дефект).

Инфраструктура staging чистая: все шесть контейнеров running, `RestartCount=0`
у всех, внутренние сервисы (PostgreSQL, Redis, MinIO) не опубликованы наружу,
selective routing работает — gateway выходит через EU-узел `31.58.181.202`,
backend через локального провайдера `91.78.244.143`.

Идемпотентность соблюдена: одно пользовательское действие дало ровно
1 профиль, 1 generation job, 1 программу, 1 доставку.

---

## 2. Test Environment

| Компонент | Значение |
| --- | --- |
| Staging host | `192.168.1.3` (Ubuntu, hostname `server`) |
| Репозиторий на сервере | `/home/odmen/workout_bot` (не git-репозиторий, выгрузка файлов) |
| Compose приложения | `/home/odmen/workout_bot/docker/staging-app-compose.yml` |
| Compose инфраструктуры | `/opt/workout_bot/compose/staging-s1-compose.yml` (root-only) |
| БД приложения | PostgreSQL, база `workout_bot` (роль `workout_bot`) |
| Alembic revision | `0008` |
| Telegram-бот | `@wrkoutassist_bot`, id `7903710552`, имя «Workout assistant» |
| Тестовый Telegram-пользователь | id `942718284`, `@svv_les` |
| Транспорт до Telegram API | wstunnel (`wstunnel-client.service`) + WireGuard `wg-workout` `10.10.10.2/24` → EU hub `31.58.181.202` |
| Каталог упражнений | 873 упражнения, source `leszavr/workout` |
| Медиа упражнений | 1746 записей в `exercise_media` |
| Генераторы | `PROGRAM_PRIMARY_GENERATOR=ai`, `PROGRAM_FALLBACK_GENERATOR=deterministic` |
| Автогенерация | `AUTO_GENERATE_PROGRAM_AFTER_FINALIZE=true` |
| Режим медиа в HTML | `PROGRAM_HTML_MEDIA_MODE=html` (data-URI) |

Секреты не читались и не выводились. Значения `BOT_TOKEN`, `DATABASE_URL`,
`JWT_SECRET`, `AI_SECRETS_KEY`, `ADMIN_PASSWORD`, ключей MinIO и WireGuard в
отчёте отсутствуют: проверялось только наличие переменных и факт успешной
операции. Файл `/opt/workout_bot/compose/staging.env` не читался,
`/home/odmen/workout_bot/staging-app.env` не изменялся.

---

## 3. Git Revision

Локальный репозиторий `/home/odmen/DEV/Workout/app/workout_bot` обновлён до
актуального `main` перед началом теста:

```text
git checkout main
git pull --ff-only origin main   # fast-forward на 5 коммитов
HEAD = 3bf63aaa3565d6426411505a6b1992d318892057
working tree = clean
```

Последние коммиты:

```text
3bf63aa Merge pull request #23 from leszavr/fix/generation-exercise-source
3715d23 feat(web): открыть и скачать HTML программы из карточки анкеты
3c60741 feat(telegram): фиксировать исход program pipeline в логе
1701825 fix(media): искать фотографии по паре external_id + source
da15c51 fix(generation): не доверять exercise_source модели и проверять его в валидаторе
```

### Соответствие staging кодовой базе `main`

На staging нет git-метаданных, поэтому ревизия подтверждена сверкой контрольных
сумм ключевых файлов, изменённых в PR #23. Все семь файлов совпадают побайтово:

| Файл | MD5 (staging = local main) |
| --- | --- |
| `src/application/ai/program_generator.py` | `8d7ade38a1cd22ab6d3eb6901d217a4e` |
| `src/application/programs/validator.py` | `be9956d835f95eb29cd99c3197564fa3` |
| `src/application/programs/orchestrator.py` | `7464daa7a2ce3fef3f857d6e4edf3e97` |
| `src/domain/pools.py` | `0bcb5d88dd479ef2d778647c4524b42f` |
| `apps/telegram_gateway/handlers/review.py` | `3bc57f09b3999475fbcb83a5504feab9` |
| `src/infrastructure/persistence/postgres/exercise_media_repository.py` | `bad4097d37ef631b47654a5b8acb19a2` |
| `prompts/program_generator/v1/system.txt` | `55eaa74274a4d79c53dd80eb0edace2e` |

Образы `docker-backend` и `docker-telegram-bot` пересобраны 2026-08-28T04:08 UTC,
то есть после мержа PR #23. Тестируемая ревизия = `main @ 3bf63aa`.

---

## 4. Infrastructure Pre-flight

### 4.1 SSH

Доступ по ключу подтверждён без пароля:

```text
ssh -o BatchMode=yes -i ~/.ssh/workout_staging_ed25519 odmen@192.168.1.3
→ SSH_OK / hostname=server / whoami=odmen / uptime 7:46
```

`BatchMode=yes` исключает парольную аутентификацию: команда не смогла бы
выполниться, если бы ключ не принимался. Парольный вход не использовался.
Passwordless sudo у `odmen` отсутствует, поэтому `wg show` и чтение
root-only конфигов не выполнялись.

### 4.2 Docker services

Состояние на момент pre-flight (до restart-теста):

| Container | Status | Started | RestartCount | Health |
| --- | --- | --- | --- | --- |
| `docker-telegram-bot-1` | running | 2026-08-28T04:08:20Z | 0 | healthcheck не определён |
| `docker-frontend-1` | running | 2026-08-28T04:08:20Z | 0 | healthy (failing streak 0) |
| `docker-backend-1` | running | 2026-08-28T04:08:13Z | 0 | healthy (failing streak 0) |
| `compose-postgres-1` | running | 2026-08-27T20:44:25Z | 0 | healthy (failing streak 0) |
| `compose-redis-1` | running | 2026-08-27T20:44:25Z | 0 | healthy (failing streak 0) |
| `compose-minio-1` | running | 2026-08-27T20:44:25Z | 0 | healthy (failing streak 0) |

Дополнительные проверки живости:

```text
docker exec compose-redis-1 redis-cli ping      → PONG
docker exec compose-postgres-1 pg_isready       → /var/run/postgresql:5432 - accepting connections
```

У `docker-telegram-bot-1` в compose не задан healthcheck, поэтому его
работоспособность подтверждается не статусом Docker, а прямыми проверками
Telegram API (раздел 6). Это не дефект теста, но заметная разница с остальными
сервисами: авария gateway не будет видна через `docker ps`.

### 4.3 Public exposure

Слушающие сокеты на хосте:

```text
0.0.0.0:22    sshd
0.0.0.0:3000  frontend (admin web)
0.0.0.0:8000  backend API
127.0.0.54:53 / 127.0.0.53:53   systemd-resolved (loopback)
127.0.0.1:38499                 loopback-only
```

`compose-postgres-1`, `compose-redis-1`, `compose-minio-1` в `docker ps`
показывают порты без host-биндинга (`5432/tcp`, `6379/tcp`, `9000/tcp`) —
публикации наружу нет.

Проверка с рабочей станции (`192.168.1.118`):

| Порт | Сервис | Результат |
| --- | --- | --- |
| 5432 | PostgreSQL | closed/filtered |
| 6379 | Redis | closed/filtered |
| 9000 | MinIO API | closed/filtered |
| 9001 | MinIO Console | closed/filtered |
| 8000 | Backend | OPEN (ожидаемо) |
| 3000 | Admin Web | OPEN (ожидаемо) |

Firewall не изменялся, только наблюдение.

### 4.4 Application health check

```text
GET http://192.168.1.3:8000/health  → 200 {"status":"ok"}            (0.003 s)
GET http://192.168.1.3:8000/ready   → 200 {"status":"ok","storage":true}
GET http://192.168.1.3:3000/        → 200
```

`/ready` возвращает `storage: true`, то есть MinIO доступен приложению.
Оба эндпоинта отвечают без аутентификации по проекту — это ожидаемый контракт
liveness/readiness, а не утечка: чувствительных данных в ответах нет.
Отдельно проверялся приватный контур: `/api/v1/admin/*` требует Bearer-токен,
логин администратора через `POST /api/v1/auth/login` вернул 200 и валидный
токен (значения не выводились).

В логах backend за период теста нет ни `Traceback`, ни 5xx: только
`GET /health 200` от healthcheck и обслуженные запросы теста.

---

## 5. Telegram Routing Verification

### 5.1 Selective routing invariant

Архитектурный инвариант «Telegram Gateway → EU route, Backend → локальный
интернет» подтверждён сравнением внешних адресов:

| Источник | Внешний IP | Маршрут |
| --- | --- | --- |
| `docker-telegram-bot-1` | `31.58.181.202` | EU hub (Amsterdam) через туннель |
| `docker-backend-1` | `91.78.244.143` | локальный провайдер напрямую |
| хост `192.168.1.3` | `91.78.244.143` | локальный провайдер напрямую |

Транспортный стек на месте: интерфейс `wg-workout` с адресом `10.10.10.2/24`
(`fd00:10:10::2/64`) поднят, служба `wstunnel-client.service` в состоянии
`active running` с описанием «wstunnel client (WSS transport for the Workout
WireGuard tunnel to the EU hub)».

Инвариант соблюдён: в туннель уходит только трафик gateway, backend и хост в
туннель не заведены. Destructive network-тесты не выполнялись, конфигурация
VPN не менялась, Зона C не затрагивалась. Приватные ключи не читались.

После restart-теста (раздел 13) маршрут проверен повторно — egress gateway
остался `31.58.181.202`, то есть перезапуск контейнера не сбрасывает
selective routing.

### 5.2 Реальный Telegram API

Проверка из контейнера gateway штатным клиентом aiogram:

```text
bot.me() → OK  id=7903710552  username=wrkoutassist_bot  name="Workout assistant"
```

Soak-тест 10 последовательных вызовов `bot.me()`:

```text
soak ok=10 fail=0 avg=0.067s max=0.673s
```

Токен не выводился: в проверке использовалась переменная окружения контейнера,
в вывод попали только публичные атрибуты бота.

Webhook-конфигурация чистая, режим — long polling:

```text
getWebhookInfo → url='' pending_update_count=0 last_error=None
```

`pending_update_count=0` означает, что накопленных необработанных апдейтов нет —
gateway забирает обновления в реальном времени.

---

## 6. Telegram Gateway Verification

### 6.1 Стабильность процесса

```text
docker inspect docker-telegram-bot-1
→ status=running  RestartCount=0  started=2026-08-28T04:08:20Z
```

Restart loop отсутствует: `RestartCount=0` при uptime более трёх часов до
начала теста. Лог запуска единственный и чистый:

```text
2026-08-28 04:08:31,364 INFO __main__ event=telegram_gateway_started fsm_storage=redis
```

Постоянных `TelegramNetworkError` в логах нет. Это подтверждает, что регрессия
из памяти проекта (`telegram_bot_container_restart_loop` — падение на `bot.me()`
из-за `TelegramNetworkError`) закрыта: с транспортом через EU-узел контейнер
поднимается штатно.

### 6.2 FSM storage

Хранилище состояний — Redis (`fsm_storage=redis` в логе старта), соединение
проверяется на старте (`FSMStorage.verify()`), при недоступности Redis
пользователь получает сообщение об ошибке через отдельный error-router,
а не молчаливое зависание.

Ключи FSM в Redis на момент pre-flight:

```text
fsm:7903710552:942718284:942718284:state → QuestionnaireStates:confirm
fsm:7903710552:942718284:942718284:data  → {profile: {...}}
dbsize = 2
```

То есть у тестового пользователя оставалось состояние `confirm` от предыдущего
прогона — это подтверждает, что FSM переживает перезапуски контейнера
(состояние в Redis, не в памяти процесса).

### 6.3 Сборка program pipeline

Проверено, что pipeline собирается в реальном окружении gateway (а не только в
тестах):

```text
is_auto_generation_enabled() = True
pipeline built OK: ProgramPipelineService
  delivery service: ProgramDeliveryService
  sender: TelegramProgramSender
  alerts: ProgramAlertService
```

Все звенья доставки присутствуют: рендер HTML, репозиторий доставок,
Telegram-sender и алерт-сервис администратору.

---

## 7. Real User Questionnaire Test

### 7.1 Метод

Реальный Telegram-пользователь (id `942718284`, `@svv_les`) прошёл анкету
вручную в `@wrkoutassist_bot`. Агент не имитировал пользователя: в репозитории
нет MTProto-клиента (Telethon/Pyrogram), сохранённой user-сессии и скрипта
эмуляции апдейтов — проверено поиском по коду. Единственный Telegram-клиент в
проекте — ботовский `aiogram.Bot`. Прямая запись в БД и подмена FSM запрещены
ограничением `test_data_via_domain_logic_only` и не применялись.

Агент параллельно наблюдал: FSM-состояние в Redis, логи gateway, счётчики
таблиц PostgreSQL.

### 7.2 Прохождение FSM

Наблюдаемая последовательность состояний (UTC):

```text
07:08:xx  QuestionnaireStates:q19_equipment_photos
07:09:40  QuestionnaireStates:q32_daily_activity
07:10:11  QuestionnaireStates:confirm
07:10:03  профиль сохранён в PostgreSQL (finalize)
```

FSM реально двигался вперёд по шагам анкеты, а не стоял на одном состоянии:
переходы `q19 → q32 → confirm` зафиксированы независимыми снимками Redis.
Апдейты обрабатывались, ошибок хранилища состояний не возникло.

### 7.3 Ветвящаяся логика и пропуски

Профиль содержит 11 пропущенных необязательных вопросов, что подтверждает
работу механизма `skip` и опциональных полей:

```text
skipped: q08_secondary_goals, q13_current_activity, q15_working_weights,
         q17_gym_name, q19_equipment_photos, q29_preferred_exercises,
         q30_disliked_exercises, q31_exercise_goals, q34_cardio_notes,
         q35_schedule_constraints, q36_free_text
```

Ветвление по здоровью отработало: `has_limitations=false`, из-за чего
зависимые вопросы (`q25_limitation_categories`, `q27_movements_to_avoid`,
`q28_medical_clearance`, `q28_doctor_recommendations`) не задавались.
Ветка «нет ограничений» — валидный путь анкеты, чувствительные медицинские
данные в тест не вносились.

Заполненность по разделам (без раскрытия значений):

| Раздел | Заполнено полей |
| --- | --- |
| `client` | 6/6 |
| `goals` | 3/5 |
| `training_background` | 3/6 |
| `training_location` | 2/5 |
| `training_plan_preferences` | 4/4 |
| `health_and_limitations` | 1/6 (ветка «нет ограничений») |
| `exercise_preferences` | 0/4 (все пропущены) |
| `lifestyle` | 2/3 |
| `additional_information` | 0/3 (все пропущены) |

Обязательные разделы (`client`, `training_plan_preferences`) заполнены
полностью, `sessions_per_week=3`.

### 7.4 Итог этапа

```text
questionnaire started   ✔ (переходы FSM q19 → q32 → confirm)
questionnaire completed ✔ (questionnaire.completed = true, last_question_id = q36_free_text)
profile finalized       ✔ (completion_status = confirmed, display_number = REQ-20260828-00002)
```

---

## 8. Profile Persistence

Профиль создан штатной доменной логикой (`ProfileFinalizationService.finalize`),
без ручных INSERT.

### 8.1 Профиль и связь с Telegram-пользователем

```text
profile_id     = eec3b0de720243ac8647b9c1c8225ee4
display_number = REQ-20260828-00002
status         = confirmed
user_id        = 1
created_at     = 2026-08-28 07:10:03 UTC
```

Связь профиля с реальным Telegram-аккаунтом:

| profile_id | display_number | status | telegram_user_id | telegram_username |
| --- | --- | --- | --- | --- |
| `1f4dc68e…` | REQ-20260827-00001 | confirmed | 942718284 | svv_les |
| `eec3b0de…` | REQ-20260828-00002 | confirmed | 942718284 | svv_les |

Первая запись — профиль от предыдущей сессии (2026-08-27), вторая — созданная
этим тестом. Новый пользователь не создавался: тот же Telegram-аккаунт
переиспользовал существующую запись `users.id=1` (upsert по
`telegram_user_id`), что и предусмотрено моделью данных.

Блок источника внутри профиля:

```json
{"platform": "telegram", "bot_user_id": "942718284", "telegram_username": "svv_les"}
```

### 8.2 Нумерация и согласия

`display_number` присвоен корректно и последовательно: предыдущий профиль
`REQ-20260827-00001`, новый `REQ-20260828-00002` — счётчик сквозной, дата в
номере соответствует дню финализации.

Все три согласия записаны в момент подтверждения:

| consent_type | version | granted | source | granted_at |
| --- | --- | --- | --- | --- |
| `data_processing` | 1.0 | true | `telegram_review_confirm` | 2026-08-28 07:10:03 UTC |
| `health_information` | 1.0 | true | `telegram_review_confirm` | 2026-08-28 07:10:03 UTC |
| `accuracy` | 1.0 | true | `telegram_review_confirm` | 2026-08-28 07:10:03 UTC |

Источник согласий `telegram_review_confirm` подтверждает, что они выданы именно
через пользовательское подтверждение в Telegram, а не проставлены служебно.

### 8.3 Отсутствие дублей

`profiles` выросла с 1 до 2 записей, `users` осталась 1 — ровно один новый
профиль на одно завершённое пользовательское действие. Уведомление
администратору отправлено один раз: `admin_notification_status = sent`.

---

## 9. Program Generation

### 9.1 Автоматический запуск после finalize

Генерация запустилась сама, без участия администратора и без Admin API:

```text
07:10:03  профиль финализирован
07:10:05  event=generation_job_running     (задержка ~2 с)
07:10:06  event=generation_started
```

Триггер job — `auto_finalization`, то есть запуск пришёл из
Telegram-хендлера `final_confirm` через фоновую задачу `run_program_pipeline`,
а не из административного запроса. Это подтверждает связку
`Profile Finalization → GenerationRequest → Orchestrator`.

### 9.2 Ход генерации: AI → repair × 2 → fallback

Полная хронология из логов gateway (UTC):

```text
07:10:06  generation_started
07:10:08  POST https://routerai.ru/api/v1/chat/completions → 200 OK
07:12:07  WARNING AI-вывод невалиден (попытка 1/3), запрашиваем исправление:
          Program validation failed: exercise_not_found:
          Упражнение Cable_Lat_Pulldown_(Generic) отсутствует в каталоге
07:12:08  POST .../chat/completions → 200 OK        (repair 1)
07:13:44  WARNING AI-вывод невалиден (попытка 2/3):
          Schema validation failed: title: Field required; duration_weeks: Field required;
          training_days_per_week: Field required; training_days: Field required;
          exercise: Extra inputs are not permitted
07:13:45  POST .../chat/completions → 200 OK        (repair 2)
07:14:57  ERROR AI-генерация не удалась после 3 попыток:
          Schema validation failed: training_days.0/1/2: Input should be a valid
          dictionary or instance of TrainingDay; exercises: Extra inputs are not permitted
07:14:57  WARNING generation_attempt_failed
07:14:57  WARNING generation_fallback_started
07:14:57  INFO    generation_fallback_success      (за ~4 мс)
07:14:57  INFO    program_persisted
07:14:57  INFO    generation_job_succeeded
```

Ключевое наблюдение: **транспорт до AI-провайдера работал безупречно** — все
три HTTP-запроса вернули `200 OK`, ни одного сетевого сбоя, ни `ai_not_configured`,
ни таймаута. Отказ произошёл на уровне содержимого ответа модели.

Расход токенов и латентность по `ai_usage_records`:

| id | model_pk | input | output | latency | status | profile_id |
| --- | --- | --- | --- | --- | --- | --- |
| 7 | 2 (`qwen/qwen3.8-flash`) | 6758 | 10767 | 120.9 s | success | `eec3b0de…` |
| 8 | 2 | 120 | 6993 | 97.2 s | success | (repair, profile не передан) |
| 9 | 2 | 131 | 5753 | 73.0 s | success | (repair, profile не передан) |

Все три вызова `status=success` на уровне AI Gateway — провайдер отдал ответ,
но контент не прошёл валидацию приложения.

### 9.3 Fallback как штатное поведение

Fallback сработал ровно так, как задан архитектурой: `PROGRAM_PRIMARY_GENERATOR=ai`,
`PROGRAM_FALLBACK_GENERATOR=deterministic`, автогенерация после finalize идёт с
`allow_fallback=True` (политика `generation_fallback_policy`). Fallback был
один и внутри того же job — вторая генерация не запускалась.

Метаданные генерации в сохранённой программе:

```json
{
  "source": "deterministic",
  "actual_generator": "deterministic",
  "requested_generator": "ai",
  "generator_version": "deterministic-1.0.0",
  "fallback_used": true,
  "fallback_reason_code": "ai_runtime_failure",
  "fallback_reason": "ai: ProgramGenerationError (ошибка генерации: Schema validation failed: ...)",
  "provider": null,
  "model": null,
  "prompt_version": null,
  "candidate_pool_total": 873,
  "safe_pool_size": 523
}
```

Причина отказа зафиксирована машиночитаемо (`ai_runtime_failure`) и текстом —
подмена генератора не молчаливая, разбор инцидента возможен без доступа к логам.

### 9.4 Safety pipeline

Прохождение штатного контура подтверждается фактическими данными программы:

- **Filtering**: `candidate_pool_total = 873` → из полного каталога отобраны кандидаты по оборудованию, опыту и исключениям.
- **SafetyEngine**: `safe_pool_size = 523` — движок сузил пул на 350 упражнений относительно каталога. Пул не пустой и не равен каталогу, то есть фильтрация реально применялась.
- **ProgramValidator**: сработал дважды и результативно. На попытке 1 он забраковал галлюцинацию модели (`Cable_Lat_Pulldown_(Generic)` — такого `external_id` в каталоге нет, проверено запросом: есть `Wide-Grip_Lat_Pulldown`, `One_Arm_Lat_Pulldown`, `Close-Grip_Front_Lat_Pulldown`, `Full_Range-Of-Motion_Lat_Pulldown`). Финальная программа имеет `status = validated`.
- **safety_notes** в программе заполнены: «Ограничения не указаны. Программа не является медицинской рекомендацией — при ухудшении самочувствия обратитесь к врачу.»

Валидатор отработал как защитный контур по назначению: невалидный AI-вывод в
БД не попал. Искусственно опасный профиль не создавался.

### 9.5 Итоговая программа

```text
program_id        = cdf0a1dd33644dc7bc9b766f1f81f6f8
version           = 1
status            = validated
generation_source = deterministic
profile_id        = eec3b0de720243ac8647b9c1c8225ee4
created_at        = 2026-08-28 07:14:57 UTC
```

Содержание:

| Параметр | Значение |
| --- | --- |
| Название | «Возвращение к тренировкам: 3 тренировки в неделю» |
| Длительность | 6 недель |
| Тренировок в неделю | 3 (совпадает с `sessions_per_week=3` из анкеты) |
| Дни | День 1 «Ноги и жимовые движения» (4 упр.), День 2 «Тяговые движения и корпус» (4 упр.), День 3 «Всё тело» (5 упр.) |
| Прогрессия | до 5% нагрузки в неделю, с описанием |
| Схема упражнения | `sets=2`, `repetitions_min/max=10/12`, `rest_seconds=75` |

Все упражнения имеют корректную каноническую ссылку `external_id` + `source`
(`leszavr/workout`) — регрессия обрезанного `exercise_source`, разобранная в
предыдущей сессии, на этой ревизии не воспроизводится.

### 9.6 GenerationJob lifecycle

```text
job_id              = 7462685ecc554bb0a3251d453c5ae54e
profile_id          = eec3b0de720243ac8647b9c1c8225ee4
trigger             = auto_finalization
requested_generator = ai
idempotency_key     = auto_finalization:eec3b0de720243ac8647b9c1c8225ee4:1
status              = succeeded
attempts            = 1
last_error_code     = (пусто)
program_id          = cdf0a1dd33644dc7bc9b766f1f81f6f8
program_version     = 1
created_at          = 07:10:05
started_at          = 07:10:05
completed_at        = 07:14:57
```

Переходы состояний: `PENDING → RUNNING (07:10:05) → SUCCEEDED (07:14:57)`,
общая длительность 4 мин 52 с (почти целиком — три вызова AI).

`attempts = 1` корректно: попытка job одна, а внутренние repair-запросы к
модели — деталь работы AI-генератора, а не повторный запуск job.
`last_error_code` пуст, поскольку job завершился успешно через fallback;
причина отказа AI сохранена в метаданных программы (`fallback_reason_code`).
`requested_generator = ai` при `generation_source = deterministic` — ровно тот
разрыв, который и должен фиксировать расхождение запрошенного и фактического
генератора.

Стоит отметить: 8 предыдущих job в таблице все имеют `trigger = admin_request`.
Этот job — первый `auto_finalization` в истории staging, то есть автоматический
путь после finalize проверен здесь впервые в реальном окружении.

---

## 10. AI / Fallback Behavior

### 10.1 Фактически использованный генератор

```text
requested_generator: ai
actual_generator:    deterministic
fallback used:       yes
fallback reason:     ai_runtime_failure — AI-вывод трижды не прошёл валидацию
                     (галлюцинация упражнения, затем два нарушения схемы)
```

### 10.2 AI настроен и доступен — отказ не инфраструктурный

Это важно отделить от прошлых инцидентов (`ai_not_configured`, `admin_ai_generation_502`).
Конфигурация AI на staging **полностью готова**, `GET /api/v1/admin/ai/readiness`
возвращает `ready: true` со всеми проверками `ok`:

| Проверка | Статус | Детали |
| --- | --- | --- |
| provider | ok | Tabitoken, Routerai |
| endpoint | ok | tabitoken.com, routerai.ru |
| api_key | ok | ключ сохранён |
| connection | ok | связь есть, последняя проверка 27.08.2026 09:47 |
| model | ok | 6 моделей зарегистрировано |
| task_models | ok | основная `qwen3.8-flash`, резервных 2 |
| task_enabled | ok | задача включена |
| prompt | ok | версия №1 из файлов проекта |
| generation_strategy | ok | основной — ИИ, резервный — алгоритм |

Цепочка моделей для `workout_generation`:

```text
priority 1 (primary): Routerai   / routerai.ru    / qwen/qwen3.8-flash
priority 2:           Routerai   / routerai.ru    / z-ai/glm-5.3-flash
priority 3:           Tabitoken  / tabitoken.com  / claude-opus-5
```

Параметры задачи: `enabled=true`, `temperature=0.7`, `timeout_seconds=120`,
`max_tokens` не ограничен.

### 10.3 Root cause: почему ИИ не собрал программу

Причина — **качество вывода модели `qwen3.8-flash` в сочетании с механикой
repair-запроса**, а не отсутствие подключения к ИИ. Разбор по попыткам:

**Попытка 1 (07:10:08 → 07:12:07, 121 с).** Модель получила полный промпт
(6758 входных токенов, включая safe pool из 523 упражнений) и вернула
структурно корректную программу, но галлюцинировала `external_id`:
`Cable_Lat_Pulldown_(Generic)`. Такого упражнения в каталоге нет — модель
собрала «правдоподобное» имя вместо выбора из предоставленного списка.
`ProgramValidator` отклонил вывод с кодом `exercise_not_found`.

**Попытка 2 (repair, 07:12:08 → 07:13:44).** Здесь проявляется системная
проблема. Repair-запрос отправляется **без исходного контекста**: в
`AIProgramGenerator._repair_request` формируется одно `user`-сообщение с текстом
ошибок и требованием «Return ONLY the corrected JSON» (`src/application/ai/program_generator.py:316-334`).
Ни system-промпта со схемой, ни safe pool, ни предыдущего ответа модели в
запросе нет. Вход сжался с 6758 до 120 токенов — модель фактически получила
только «исправь вот эти ошибки», не видя ни того, что исправлять, ни целевой
структуры. Результат предсказуем: она вернула фрагмент без обязательных полей
(`title`, `duration_weeks`, `training_days_per_week`, `training_days`) и с лишним
ключом `exercise`.

**Попытка 3 (repair, 07:13:45 → 07:14:57).** То же самое: 131 входной токен,
модель вернула `training_days` списком не-объектов и лишний ключ `exercises`.
Вывод деградировал ещё сильнее.

Итог: **исходная ошибка была единичной и легко исправимой** (один неверный
`external_id` из ~13), но repair-механизм не дал модели ни малейшего шанса —
он просит исправить документ, не показывая ни документ, ни схему. Обе
repair-попытки не приблизили результат, а увели дальше от валидной структуры.

Дополнительные факторы, усугубляющие ситуацию:

1. **Fallback по моделям не задействован.** `AIGateway.generate` перебирает
   кандидатов (`glm-5.3-flash`, `claude-opus-5`) только при `AIError` —
   сетевой/провайдерский отказ. Здесь провайдер отвечал `200 OK`, ошибка была
   валидационной (`ProgramGenerationError`), поэтому переход на более сильную
   модель не произошёл: все три вызова ушли в `qwen3.8-flash` (`model_pk=2`).
   Настроенные резервные модели в этом сценарии бесполезны.
2. **Промпт не запрещает выдумывать `external_id` достаточно жёстко.**
   Правила «используй ТОЛЬКО упражнения из safe_pool» и «НЕ придумывай
   упражнения» в `prompts/program_generator/v1/system.txt` есть, но
   `qwen3.8-flash` их нарушила. Модель уровня flash при списке из 523
   идентификаторов склонна к «достраиванию» имён.
3. **Бюджет времени расходуется на бесполезные repair.** 4 мин 52 с ушло почти
   целиком на три вызова (121 + 97 + 73 с) при `timeout_seconds=120` на вызов и
   общем бюджете 240 с. Пользователь ждал почти 5 минут, чтобы в итоге получить
   алгоритмическую программу.

Важно: предыдущие успешные AI-генерации на этом же staging были инициированы
администратором (`admin_request`) и завершались успехом с той же моделью
(`ai_usage_records` id 2, 6 — `model_pk=2`, программы `generation_source=ai`),
то есть модель способна выдавать валидный результат. Отказ вероятностный, а не
детерминированный: с первого прохода `qwen3.8-flash` попадает не всегда, а
механизм исправления не спасает.

### 10.4 Оценка для acceptance

Требование задания — «Generation должна завершиться штатным архитектурно
предусмотренным способом» — **выполнено**: fallback предусмотрен конфигурацией,
сработал один раз, программа провалидирована и сохранена, пользователь получил
результат. AI-конфигурация не менялась в рамках теста.

Однако с продуктовой точки зрения результат деградированный: заявленная
ценность продукта — программа, подобранная ИИ, а пользователь получил
алгоритмическую сборку. Зафиксировано как FINDING-1 (раздел 18).

---

## 11. Program Validation and Persistence

### 11.1 Валидация

Программа сохранена со статусом `validated`. Валидатор в этом прогоне
подтверждён «в бою»: он не пропустил AI-вывод с несуществующим упражнением
и трижды заблокировал невалидные структуры. Финальная детерминированная
программа прошла те же проверки (схема, каталог, safe pool, дубликаты,
дни, повторения) и только после этого получила `validated`.

### 11.2 Персистентность

```text
workout_programs: +1 запись (было 4, стало 5)
  program_id = cdf0a1dd33644dc7bc9b766f1f81f6f8, version = 1
```

Версия `1` корректна: для нового профиля это первая программа. Уникальность
обеспечена ключом `(program_id, version)`, job связан с программой внешним
ключом на эту пару.

Целостность связей проверена запросами: программа привязана к профилю
`eec3b0de…`, job указывает на `program_id` + `program_version = 1`,
доставка указывает на тот же `program_id`. Разрывов нет.

### 11.3 Рендер HTML

Файл программы отрендерен и проверен через штатный эндпоинт
`GET /api/v1/programs/{program_id}/html` (с админским токеном):

```text
HTTP 200, Content-Type: text/html; charset=utf-8
размер: 1 529 779 байт (~1.5 МБ)
<title>Возвращение к тренировкам: 3 тренировки в неделю</title>
изображений: 26 (все встроены как data-URI, внешних http-ссылок: 0)
```

Это тот же `ProgramHtmlService`, который использует Telegram-доставка, поэтому
проверка репрезентативна для файла, полученного пользователем.

Содержимое файла (извлечён текст, 177 строк):

- заголовок программы, навигация «День 1 / День 2 / День 3», «6 нед · 3 тр/нед · версия 1»;
- интерактивный таймер отдыха с пресетами 60 сек / 1:30 / 2:00 / 3:00, кнопки «Старт» и «Сброс»;
- блок «О программе» с честной формулировкой: «Программу собрал алгоритм подбора: из 523 упражнений, разрешённых по ответам анкеты, выбраны подходящие…» — то есть fallback раскрыт пользователю, а не замаскирован под ИИ;
- блок «Безопасность» с дисклеймером о врачебной консультации;
- блок «Прогрессия нагрузки»: до 5% в неделю;
- карточки дней с фокусом и числом упражнений;
- по каждому упражнению: русское название, английское название, схема «2 × 10–12», отдых 75 сек, раскрывающийся блок «Техника» с пошаговым описанием.

Существенно: **фотографии упражнений присутствуют** (26 изображений data-URI),
что закрывает требование `exercise_photos_not_in_repo_but_in_programs` — по
текстовому описанию не всегда понятно, как выполнять упражнение. Медиа
подтягиваются по паре `external_id` + `source` из 1746 записей `exercise_media`,
и то, что изображения нашлись, косвенно подтверждает корректность
`exercise_source` во всех упражнениях программы.

Внутренние `external_id` в HTML не выводятся (проверено: строки
`Alternate_Leg_Diagonal_Bound`, `Band_Assisted_Pull-Up` в тексте отсутствуют) —
пользователь видит только человекочитаемые названия. Служебные идентификаторы
в клиентский артефакт не утекают.

---

## 12. Telegram Delivery

Доставка реализована в коде и **реально сработала**. Раздел 19 задания
(«если delivery не реализована → FAIL») не применяется.

### 12.1 Цепочка доставки

```text
final_confirm (handlers/review.py)
  → run_program_pipeline (фоновая задача)
  → ProgramPipelineService.run_for_user
  → ProgramGenerationOrchestrator.generate
  → ProgramDeliveryService.deliver
  → ProgramHtmlService.render → HTML bytes
  → TelegramProgramSender → bot.send_document(BufferedInputFile)
  → Telegram API (через EU-туннель)
  → пользователь получил файл
```

### 12.2 Факт доставки

Лог gateway:

```text
07:14:58  INFO event=delivery_started
07:15:02  INFO event=delivery_success              (4.5 с)
07:15:02  INFO event=program_pipeline_finished     (outcome=delivered)
```

Запись в `program_deliveries` (единственная в таблице):

```text
id                = 1
program_id        = cdf0a1dd33644dc7bc9b766f1f81f6f8
profile_id        = eec3b0de720243ac8647b9c1c8225ee4
chat_id           = 942718284
filename          = workout_program_eec3b0de720243ac8647b9c1c8225ee4_v1.html
status            = sent
attempts          = 1
last_error        = (пусто)
sent_message_id   = 581
source_media_mode = html
created_at        = 2026-08-28 07:14:57 UTC
delivered_at      = 2026-08-28 07:15:02 UTC
```

`sent_message_id = 581` — идентификатор реального сообщения, присвоенный
Telegram API. Это доказательство, что файл принят Telegram и доставлен в чат,
а не просто попытка отправки на стороне приложения.

### 12.3 Что именно получил пользователь

| Параметр | Значение |
| --- | --- |
| Формат | HTML-документ (`send_document`), самодостаточный файл |
| Имя файла | `workout_program_eec3b0de720243ac8647b9c1c8225ee4_v1.html` |
| Размер | ~1.5 МБ (26 изображений встроены как data-URI) |
| Открывается | офлайн на смартфоне и ПК, внешних зависимостей нет (`http`-ссылок на изображения: 0) |

Сопровождающие текстовые сообщения по коду `run_program_pipeline`:
«⏳ Формируем вашу персональную программу...» при старте (первая финализация)
и «Ваша персональная программа тренировок готова.» — при `outcome=delivered`.

Пользователь подтвердил получение: «была собрана алгоритмически программа
тренировок, создан html-файл».

### 12.4 Отсутствие дублей доставки

`attempts = 1`, в таблице ровно одна запись доставки на один `program_id`.
Повторных отправок не было. `delivery_started` и `delivery_success` встречаются
в логе по одному разу.

Отмечу архитектурное ограничение (не дефект этого прогона): автоматического
worker/scheduler для повторной доставки не существует — метод `redeliver()`
реализован, но вызывается только вручную, а `list_failed()` нигде не
используется для авторетрая. При `status=failed` файл останется недоставленным
до вмешательства администратора, хотя пользователю сообщается «Мы попробуем
отправить её повторно». В этом прогоне доставка удалась с первой попытки, так
что на acceptance это не влияет.

---

## 13. Restart Sanity Check

Выполнен минимальный безопасный restart согласно текущей Compose topology:
перезапущен только Telegram Gateway. Chaos testing, перезапуск инфраструктуры,
volumes и production не затрагивались.

```text
docker restart docker-telegram-bot-1
07:19:44  WARNING aiogram.dispatcher Received SIGTERM signal
07:19:45  INFO    event=telegram_gateway_stopped
07:19:57  INFO    event=telegram_gateway_started fsm_storage=redis
```

Завершение корректное: SIGTERM обработан, зафиксирован штатный
`telegram_gateway_stopped`, а не аварийное падение.

Состояние после restart:

| Проверка | Результат |
| --- | --- |
| Контейнер поднялся | `status=running`, started `2026-08-28T07:19:46Z` |
| Restart loop | отсутствует, `RestartCount=0` |
| Telegram API | `bot.me()` soak 10/10 ok, avg 0.065 s, max 0.652 s |
| EU routing сохранён | egress по-прежнему `31.58.181.202` |
| Redis FSM | подключение восстановлено (`fsm_storage=redis`), состояние пользователя сохранилось |
| Бот отвечает на реальный запрос | ✔ пользователь отправил `/start`, бот ответил |

### 13.1 Доказательство активного polling-цикла

Побочный, но полезный результат: при попытке вызвать `getUpdates` из отдельного
процесса Telegram вернул конфликт, а работающий dispatcher его зафиксировал:

```text
07:26:54  ERROR aiogram.dispatcher Failed to fetch updates - TelegramConflictError:
          Conflict: terminated by other getUpdates request;
          make sure that only one bot instance is running
07:26:54  WARNING Sleep for 1.000000 seconds and try again... (tryings = 0, bot id = 7903710552)
```

Это прямое доказательство, что после restart long-polling-цикл действительно
активен и единственный: конфликт мог возникнуть только потому, что gateway
сам держал `getUpdates`. Диагностический вызов был единичным, dispatcher
восстановился по штатной логике повтора (`RestartCount` остался 0), и
последующий `/start` пользователя обработался нормально. Ошибка вызвана
диагностикой, а не дефектом приложения.

### 13.2 Поведение бота после restart

Пользователь отправил `/start`. Бот ответил и предложил начать новую анкету;
предложения «продолжить» не было — это корректно: предыдущая анкета имела
`completion_status = confirmed`, а `cmd_start` предлагает продолжение только
для незавершённых анкет (`handlers/start.py:56-70`). После `/start` состояние
FSM в Redis было очищено (`state.clear()`), ключ `state` стал пустым.

Второй полный профиль не создавался. Счётчики БД после restart и `/start`
неизменны: `users=1, profiles=2, programs=5, jobs=9, deliveries=1`.

---

## 14. Idempotency Sanity Check

Инвариант «одно завершённое пользовательское действие → по одной записи»
соблюдён полностью.

| Сущность | Ожидание | Факт | Запись |
| --- | --- | --- | --- |
| Профиль | 1 | 1 | `eec3b0de…` / REQ-20260828-00002 |
| Generation job | 1 | 1 | `7462685e…`, `attempts=1` |
| Программа | 1 | 1 | `cdf0a1dd…` version 1 |
| Доставка | 1 | 1 | `id=1`, `attempts=1`, `sent_message_id=581` |

Дельта счётчиков за весь тест:

```text
до теста:    users=1  profiles=1  programs=4  jobs=8  deliveries=0
после теста: users=1  profiles=2  programs=5  jobs=9  deliveries=1
дельта:      +0       +1          +1          +1      +1
```

`users` не изменился, потому что тот же Telegram-аккаунт переиспользовал
существующую запись через upsert по `telegram_user_id` — это ожидаемое
поведение модели, а не пропущенная запись.

Механизмы, обеспечившие идемпотентность:

- ключ `auto_finalization:eec3b0de720243ac8647b9c1c8225ee4:1` под уникальным ограничением `uq_generation_job_idempotency_key` — параллельные запуски свернулись бы в один job;
- `reuse_existing=True` при автогенерации — повторное подтверждение переиспользовало бы валидную программу вместо новой генерации;
- финализация идемпотентна: при повторном `final_confirm` возвращается `already_finalized=True`, дубликат профиля и повторное уведомление администратору не создаются;
- переходы статусов job атомарны (`UPDATE ... WHERE status = expected`).

Дополнительных технических записей, требующих объяснения, не появилось.
Три записи в `ai_usage_records` (id 7, 8, 9) — это учёт трёх вызовов модели
внутри одной генерации, ожидаемая телеметрия, а не дубликаты бизнес-сущностей.

---

## 15. Log Review

Проверены логи всех компонентов за период теста.

### 15.1 Telegram Gateway

Полный лог за прогон содержит только ожидаемые события: `generation_job_running`,
`generation_started`, три HTTP-запроса к провайдеру с `200 OK`, два `WARNING`
о невалидном AI-выводе, один `ERROR` о провале AI после 3 попыток,
`generation_fallback_started/success`, `program_persisted`,
`generation_job_succeeded`, `delivery_started`, `delivery_success`,
`program_pipeline_finished`.

| Искомая проблема | Найдено |
| --- | --- |
| `Traceback` | нет |
| Unhandled exception | нет |
| `TelegramNetworkError` | нет |
| restart loop | нет (`RestartCount=0`) |
| database connection failures | нет |
| Redis failures | нет |
| MinIO failures | нет |

Два `ERROR` в логе присутствуют, и оба объяснимы:

1. `AI-генерация не удалась после 3 попыток` — не сбой системы, а корректно
   обработанный отказ AI-генератора с последующим штатным fallback. Обработка
   предусмотрена архитектурой.
2. `TelegramConflictError` в 07:26:54 — следствие диагностического вызова
   `getUpdates` из отдельного процесса (раздел 13.1), а не дефект приложения.
   Dispatcher восстановился штатно.

### 15.2 Backend

За период теста в логе только `GET /health 200` от healthcheck и обслуженные
запросы теста. Ни `Traceback`, ни 5xx, ни ошибок подключения к БД.

### 15.3 Frontend (Admin Web)

Ошибок в логах нет, контейнер `healthy`, `GET /` отвечает 200.

### 15.4 Инфраструктура

`compose-redis-1` и `compose-minio-1`: ошибок в логах нет.

`compose-postgres-1` содержит `ERROR`/`FATAL`, и все они — следствие
диагностических запросов агента с неверными именами (`role "workout" does not
exist`, `relation "users" does not exist`, `column "error_code" does not exist`,
`column "external_id" does not exist`). Это ошибки моих исследовательских
запросов при определении реальной схемы, а не приложения. Приложение таких
запросов не делает.

Отдельно: `FATAL: terminating connection due to administrator command` от
2026-08-27 11:13 — след планового перезапуска предыдущей сессии, к текущему
тесту не относится.

---

## 16. Test Artifacts Created

### 16.1 Созданные записи

| Таблица | Запись | Идентификатор |
| --- | --- | --- |
| `profiles` | тестовый профиль | `eec3b0de720243ac8647b9c1c8225ee4` / REQ-20260828-00002 |
| `generation_jobs` | job автогенерации | `7462685ecc554bb0a3251d453c5ae54e` |
| `workout_programs` | программа v1 | `cdf0a1dd33644dc7bc9b766f1f81f6f8` |
| `program_deliveries` | доставка | `id=1`, `sent_message_id=581` |
| `consents` | 3 согласия для `user_id=1` | `data_processing`, `health_information`, `accuracy` |
| `ai_usage_records` | 3 записи телеметрии | id 7, 8, 9 |

Запись в `users` не создавалась — переиспользована существующая `id=1`.

В Telegram у пользователя остались: сообщение о принятии анкеты, служебные
сообщения pipeline и HTML-файл программы (message_id 581).

### 16.2 Возможность cleanup

Данные **не удалялись**: результаты зафиксированы, удаление требует отдельного
решения.

Штатной операции удаления профиля или программы в системе **нет**. В OpenAPI
backend отсутствуют DELETE-эндпоинты для `/api/v1/profiles/*` и
`/api/v1/programs/*` — есть только `DELETE /api/v1/admin/users/{user_id}`
(администраторы панели, не Telegram-пользователи) и удаление AI-сущностей.

Каскады в схеме подготовлены: `generation_jobs.profile_id → profiles.profile_id
ON DELETE CASCADE`, `generation_jobs.(program_id, program_version) →
workout_programs ON DELETE SET NULL`. То есть на уровне БД удаление профиля
технически снесло бы связанные job, но это будет ручной SQL, а не штатная
доменная операция.

**Рекомендация:** тестовые данные оставить. Они не мешают: профиль корректный,
программа валидная, объём минимальный. Удаление ручным SQL противоречит
ограничению `test_data_via_domain_logic_only` и в рамках этой задачи не
выполнялось.

Пользователь в ходе теста отметил потребность в удалении анкет и программ из
админки — зафиксировано как FINDING-3 (раздел 18), отдельная задача, не часть
acceptance.

---

## 17. Acceptance Matrix

| Component / Flow | Result | Evidence |
| --- | --- | --- |
| SSH | PASS | вход по ключу с `BatchMode=yes`, hostname `server`, пароль не использовался |
| Docker runtime | PASS | 6/6 контейнеров running, `RestartCount=0` у всех |
| PostgreSQL | PASS | `pg_isready: accepting connections`, healthy, alembic `0008`, 20 таблиц |
| Redis | PASS | `redis-cli ping → PONG`, healthy, FSM-хранилище работает (`fsm_storage=redis`) |
| MinIO | PASS | healthy, `/ready → storage: true`, 26 изображений отрендерены в файл |
| Backend health | PASS | `/health 200 {"status":"ok"}`, `/ready 200 {"status":"ok","storage":true}` |
| Admin Web | PASS | `http://192.168.1.3:3000/ → 200`, контейнер healthy |
| Telegram Gateway | PASS | `event=telegram_gateway_started`, `RestartCount=0`, нет `TelegramNetworkError` |
| EU Telegram routing | PASS | gateway egress `31.58.181.202`, backend `91.78.244.143`, `wg-workout` + `wstunnel-client.service` active |
| Real Telegram `bot.me()` | PASS | id `7903710552`, `@wrkoutassist_bot`; soak 10/10 ok, avg 0.067 s |
| Questionnaire | PASS | реальный проход пользователем, 11 пропусков, ветка «нет ограничений», `completed=true` |
| FSM | PASS | наблюдаемые переходы `q19 → q32 → confirm`, состояние в Redis переживает restart |
| Profile persistence | PASS | REQ-20260828-00002, `status=confirmed`, связь с tg id `942718284`, 3 согласия |
| GenerationJob | PASS | `trigger=auto_finalization`, `PENDING→RUNNING→SUCCEEDED`, `attempts=1`, ключ идемпотентности |
| Program generation | PASS (degraded) | программа создана, но `actual_generator=deterministic` вместо `ai` (FINDING-1) |
| Safety/validation | PASS | filtering 873→523, SafetyEngine применён, валидатор отклонил галлюцинацию AI, `status=validated` |
| Program persistence | PASS | `workout_programs` +1, version 1, связи с job и доставкой целостны |
| Telegram delivery | PASS | `delivery_success`, `program_deliveries.status=sent`, `sent_message_id=581`, `attempts=1` |
| User receives result | PASS | пользователь подтвердил получение HTML-файла; 1.5 МБ, 26 фото, таймер, техника |
| Restart sanity check | PASS | штатный SIGTERM→stop→start, `bot.me()` 10/10, EU-маршрут сохранён, `/start` обработан |
| Idempotency sanity | PASS | 1 действие → 1 профиль / 1 job / 1 программа / 1 доставка |
| **Final E2E acceptance** | **PASS** | полный путь от реального Telegram-пользователя до полученного файла пройден |

---

## 18. Blockers / Findings

Блокеров acceptance нет. Ни одна находка не потребовала изменения кода в рамках
этой задачи — правки не вносились, отчёт содержит только доказательства.

### FINDING-1 — AI-генератор не собрал программу, сработал fallback

**Severity:** high (продуктовый дефект, не блокер acceptance)
**Статус:** зафиксировано, код не менялся

Пользователь получил алгоритмически собранную программу вместо программы,
подобранной ИИ. AI-конфигурация при этом полностью готова (`readiness.ready=true`),
провайдер отвечал `200 OK` на все три запроса — отказ не инфраструктурный.

Root cause (детально в разделе 10.3): модель `qwen3.8-flash` на первой попытке
галлюцинировала один `external_id` (`Cable_Lat_Pulldown_(Generic)`), а
repair-механизм не смог это исправить, потому что отправляет модели только текст
ошибок без исходного контекста — ни system-промпта со схемой, ни safe pool, ни
предыдущего ответа (`src/application/ai/program_generator.py:316-334`). Вход
repair-запроса сжимается с 6758 до ~120 токенов, и модель, не видя ни документа,
ни схемы, деградирует вместо исправления.

Усугубляющие факторы:

1. Fallback по моделям не задействуется при валидационных ошибках: `AIGateway`
   перебирает кандидатов только при `AIError` (сетевой/провайдерский отказ), а
   `ProgramGenerationError` до него не доходит. Настроенные резервные модели
   (`glm-5.3-flash`, `claude-opus-5`) в этом сценарии бесполезны — все три вызова
   ушли в одну и ту же `qwen3.8-flash`.
2. Правила промпта против выдумывания `external_id` для модели уровня flash
   недостаточны при списке из 523 идентификаторов.
3. Пользователь ждал 4 мин 52 с, из которых ~170 с потрачены на две заведомо
   бесперспективные repair-попытки.

Отказ вероятностный, а не детерминированный: та же модель ранее выдавала
валидный результат (`ai_usage_records` id 2 и 6, программы с
`generation_source=ai`).

Возможные направления (требуют отдельного решения, здесь не реализованы):
передавать в repair-запрос исходный контекст и предыдущий ответ; переходить на
следующую модель цепочки после исчерпания repair; поднять приоритет более
сильной модели для этой задачи; сократить число repair-попыток в пользу
перехода на другую модель.

### FINDING-2 — Нет автоматического повтора доставки

**Severity:** medium
**Статус:** зафиксировано (архитектурное ограничение Phase 1.2-D)

`ProgramDeliveryService.redeliver()` реализован, `list_failed()` в репозитории
есть, но worker/scheduler, который бы вызывал их автоматически, отсутствует.
При `status=failed` пользователю сообщается «Мы попробуем отправить её
повторно», хотя автоматической повторной попытки не будет — понадобится
вмешательство администратора. В этом прогоне доставка удалась с первой попытки,
на acceptance не влияет.

### FINDING-3 — В админке нет удаления анкет и программ

**Severity:** medium
**Статус:** зафиксировано по замечанию пользователя в ходе теста

DELETE-эндпоинты для `/api/v1/profiles/*` и `/api/v1/programs/*` в OpenAPI
отсутствуют. Следствия: тестовые данные нельзя убрать штатной операцией
(раздел 16.2), а администратор не может удалить ошибочную анкету или программу
без ручного SQL. Каскады в схеме для этого уже подготовлены
(`ON DELETE CASCADE` на `generation_jobs.profile_id`). Отдельная продуктовая
задача, не часть acceptance.

### FINDING-4 — У Telegram Gateway нет healthcheck

**Severity:** low
**Статус:** наблюдение

В `staging-app-compose.yml` для `docker-telegram-bot-1` healthcheck не задан
(`health=none`), в отличие от backend и frontend. Работоспособность gateway
не отражается в `docker ps`: авария polling-цикла останется невидимой для
мониторинга по статусу контейнера. Именно поэтому в этом тесте потребовались
прямые проверки Telegram API.

### FINDING-5 — На staging нет git-метаданных

**Severity:** low
**Статус:** наблюдение

`/home/odmen/workout_bot` — выгрузка файлов без `.git`, поэтому развёрнутую
ревизию нельзя установить командой `git rev-parse`. В этом тесте соответствие
`main @ 3bf63aa` подтверждено сверкой MD5 семи ключевых файлов (раздел 3), но
для регулярной эксплуатации это неудобно: стоит фиксировать SHA деплоя в файле
или метке образа.

---

## 19. Final Verdict

```text
FULL STAGING E2E ACCEPTANCE: PASS
(с деградацией генерации: программу собрал fallback-алгоритм, а не ИИ)
```

Проверка по 12 критериям Full PASS из раздела 18 задания:

| № | Критерий | Результат |
| --- | --- | --- |
| 1 | Реальный Telegram bot отвечает | ✔ `/start` обработан, анкета пройдена |
| 2 | Telegram Gateway стабилен | ✔ `RestartCount=0`, нет `TelegramNetworkError` |
| 3 | EU routing работает | ✔ egress gateway `31.58.181.202` |
| 4 | Пользователь проходит анкету | ✔ реальный проход, `completed=true` |
| 5 | Профиль создаётся | ✔ REQ-20260828-00002, `confirmed` |
| 6 | Generation job создаётся | ✔ `trigger=auto_finalization`, `succeeded` |
| 7 | Программа генерируется | ✔ создана (через fallback-генератор) |
| 8 | Программа валидируется | ✔ `status=validated` |
| 9 | Программа сохраняется | ✔ `workout_programs` v1 |
| 10 | Результат доставлен через Telegram | ✔ `sent_message_id=581`, `status=sent` |
| 11 | После restart система работоспособна | ✔ bot.me 10/10, `/start` обработан |
| 12 | Нет restart loops и critical errors | ✔ `RestartCount=0`, критических ошибок нет |

Все 12 критериев выполнены — формально это `PASS`.

**Ответ на главный вопрос задания.** Workout Bot работает как полноценный
пользовательский продукт: реальный Telegram-пользователь проходит анкету и
получает готовый файл персональной программы с фотографиями, техникой
выполнения и таймером отдыха, без ручных вмешательств между шагами. Сквозной
путь замкнут и подтверждён записями в БД, логами и идентификатором
доставленного сообщения Telegram.

Оговорка существенная для продукта, но не для acceptance: ключевая ценность —
подбор программы ИИ — в этом прогоне не сработала, и пользователь получил
алгоритмическую сборку. Система отреагировала на отказ штатно и честно (в самом
файле написано «Программу собрал алгоритм подбора»), но FINDING-1 требует
отдельной работы над repair-механизмом и стратегией перебора моделей.

Инфраструктура и приложение к нагрузке готовы; узкое место — качество
AI-генерации и устойчивость её восстановления после невалидного ответа модели.
