# Operations Runbook

## Основной запуск

```bash
cp .env.example .env
docker compose -f docker/docker-compose.yml --env-file .env up --build
```

После первого запуска выполнить миграции и импорт каталога/медиа согласно `README.md`.

## Зависимости

- PostgreSQL — основные данные;
- MinIO — бинарные изображения упражнений;
- FastAPI — backend/API;
- Telegram gateway — пользовательский транспорт;
- Next.js — внутренняя админка.

## Обязательные проверки после развёртывания

- `/health` отвечает;
- `/ready` подтверждает готовность зависимостей;
- admin login работает;
- каталог упражнений содержит ожидаемые данные;
- media endpoint возвращает изображение;
- deterministic generation проходит;
- при настроенном AI проходит AI generation;
- при искусственной ошибке AI deterministic fallback работает;
- HTML delivery приходит в Telegram.

## Переменные

Полный список и примеры — `.env.example`. Критические группы:
- Telegram: `BOT_TOKEN`, `ADMIN_CHAT_ID`;
- PostgreSQL: `DATABASE_URL`/пароли;
- admin/JWT;
- MinIO/media;
- `PROGRAM_PRIMARY_GENERATOR`, `PROGRAM_FALLBACK_GENERATOR`;
- `AUTO_GENERATE_PROGRAM_AFTER_FINALIZE`.

Секреты не коммитить. Для production требуется отдельная процедура backup/restore и регулярная проверка восстановления.

## Инцидент: программа не пришла

1. Проверить, существует ли программа в `workout_programs`.
2. Если существует — не запускать генерацию повторно автоматически.
3. Проверить `program_deliveries` и число попыток.
4. Повторять только delivery, если генерация уже успешна.
5. Проверить admin alert и correlation ID.

## Инцидент: AI не работает

1. Проверить provider/endpoint/model/task binding.
2. Проверить включённость сущностей и connection test.
3. Проверить protocol adapter compatibility.
4. Проверить секрет и сетевую доступность.
5. Проверить, сработал ли deterministic fallback.

Не включать новые модели/провайдеры в production без тестовой генерации и проверки validator/fallback.
