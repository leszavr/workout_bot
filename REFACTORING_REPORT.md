# REFACTORING_REPORT

Дата: 2026-08-17

Рефакторинг Telegram-бота и подготовка платформы к развитию
(по заданию `.tmp/refactoring_prompt.md`).

## Изменено

### Архитектура (модульный монолит, 4 слоя)
- `src/domain/` — строгие Pydantic v2 модели: `FitnessProfile` и вложенные
  `UserIdentity`, `ClientData`, `Goals`, `TrainingBackground`,
  `TrainingPlanPreferences`, `TrainingLocation`, `HealthAndLimitations`,
  `ExercisePreferences`, `Lifestyle`, `AdditionalInformation`,
  `QuestionnaireMeta`, `ReviewMeta`, `ConsentRecord`; все enum в `enums.py`.
  `extra="forbid"`, диапазоны, ограничения длины, документация полей.
- `src/application/questionnaire/questions.py` — **единый источник истины**
  анкеты: список `QuestionDefinition` (порядок, тексты, подсказки,
  обязательность, варианты, валидаторы, парсеры, условия пропуска `skip_if`).
- `src/application/questionnaire/service.py` — `QuestionnaireService`:
  вся бизнес-логика анкеты, без Telegram-зависимостей.
- `src/application/profiles/finalization.py` — идемпотентная финализация.
- `src/application/notifications/admin_notifier.py` — уведомление админа
  с явным статусом доставки `pending → sent | failed`.
- `src/application/questionnaire/review.py`, `labels.py` — рендер сводки
  и русские подписи (транспортно-независимые).
- `src/infrastructure/persistence/profile_repository.py` — интерфейс
  `ProfileRepository` + `FileProfileRepository` (атомарная запись tmp+rename).
- `src/infrastructure/files/storage.py` — интерфейс `FileStorage` +
  `LocalFileStorage` (лимиты количества/размера/типа, очистка).
- `src/infrastructure/telegram/admin_sender.py` — Telegram-адаптер отправки
  анкеты администратору.
- `src/infrastructure/config.py` — абсолютные пути, секреты из env.
- `src/infrastructure/logging_setup.py` — единое privacy-безопасное логирование.
- `src/errors.py` — `QuestionnaireValidationError`, `ProfilePersistenceError`,
  `FileStorageError`, `NotificationError`.
- `apps/telegram_gateway/` — тонкие handlers (получить сообщение → сервис →
  ответ), клавиатуры и FSM-состояния, генерируемые из `QUESTIONS`.
- `apps/backend/main.py` — FastAPI: `GET /health`, `GET /ready`, `/api/v1/`.
- `tests/` — 53 теста (unit + integration).
- `docker/Dockerfile`, `docker/docker-compose.yml`, `.env.example`,
  `pyproject.toml`, `docs/ARCHITECTURE.md`, обновлён `README.md`.

## Удалено

- `bot.py`, `config.py` (корневые) — заменены `apps/telegram_gateway/main.py`
  и `src/infrastructure/config.py`.
- `handlers/` (questionnaire.py 884 строки, start.py, review.py, corrections.py)
  — бизнес-логика перенесена в `src/application`, транспорт в `apps/telegram_gateway`.
- `keyboards/`, `states/`, `models/`, `services/` — заменены генерируемыми
  клавиатурами/состояниями и новыми слоями.
- `requirements.txt` — заменён `pyproject.toml`.
- Дублирующий handler `return_to_questionnaire` из `review.py`
  (конфликтовал с одноимённым в questionnaire.py).
- Второй дублирующий photo-handler и логика скачивания фото внутри handler'а.

## Исправлено

1. **ID профиля (7.1)**: убрана фиксированная генерация
   `REQ-...-00001` из handler'ов. `profile_id` — UUID, присваивается сервисом;
   человекочитаемый `display_number` (`REQ-YYYYMMDD-NNNNN`) — репозиторием
   при финализации.
2. **Идемпотентная финализация (7.2)**: повторное «Подтвердить» возвращает
   существующий профиль, не создаёт дубликат и не дублирует согласия.
3. **Дублирующие handlers (7.3)**: один сценарий — одна точка обработки.
   Все вопросы с вариантами обрабатываются единым callback-handler'ом через
   маппинг `callback_data → question_id`.
4. **Фотографии (7.4)**: единый `FileStorage` с абсолютными путями, лимитами
   количества (10), размера (20 МБ), проверкой типа; интерфейс готов к замене
   на S3.
5. **Строгая модель профиля (6)**: `dict[str, Any]` заменён Pydantic-моделями;
   произвольный dict не сохраняется.
6. **Privacy (9)**: согласия — `ConsentRecord` (scope, timestamp, версия
   документа, источник), создаются только при явном подтверждении;
   поддержаны export/delete.
7. **Логирование (10)**: в логи не попадают профили, ответы, токены —
   только user_id, profile_id, event, status, error_class.
8. **Ошибки (11)**: единые типы ошибок; ошибка сохранения не приводит к
   ложному сообщению об успехе; статус доставки уведомления явный.

## Осталось (сознательно не реализовано)

- **PostgreSQL не подключён фактически.** Выбран безопасный путь (п.8 задания):
  файловое хранилище за интерфейсом `ProfileRepository`. PostgreSQL активен на
  хосте, но миграция на него сейчас потребовала бы Alembic + SQLAlchemy и
  изменения runtime без продуктовой необходимости. Интерфейс позволяет добавить
  `PostgresProfileRepository` без изменения application/transport слоёв.
  В docker-compose сервис `postgres` подготовлен (profile `with-postgres`).
- Генерация программ через LLM, AI Gateway, каталог упражнений, веб-интерфейс,
  админка, Redis/Celery — по заданию не реализуются на этом этапе.
- Физическое разделение на два сервера (Telegram Gateway / backend) —
  обеспечена только архитектурная возможность.

## Следующий этап

Реализация цепочки:

```
Exercise Catalog → Safety Rules → AI Program Generator → Program Validator → Versioned Workout Program
```

1. **PostgreSQL + Alembic**: `PostgresProfileRepository`, таблицы
   `users`, `profiles`, `consents`, `files`; миграции.
2. **Exercise Catalog**: домен `src/domain/exercises/`, каталог упражнений
   с метаданными и противопоказаниями.
3. **Safety Rules**: правила безопасности на основе
   `HealthAndLimitations` (валидация программы против ограничений).
4. **AI Program Generator**: `src/infrastructure/ai/` (AI Gateway),
   генерация программы из профиля + каталога.
5. **Program Validator**: проверка сгенерированной программы на соответствие
   Safety Rules и профилю.
6. **Versioned Workout Program**: домен `src/domain/programs/`, версионирование
   программ, выдача пользователю (HTML/файл).
