# Базовый образ для telegram-bot и backend.
# Секреты передаются через переменные окружения, в образ не включаются.
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
COPY apps ./apps
COPY alembic.ini ./
COPY alembic ./alembic
COPY scripts ./scripts
# Файловые промпты — источник инструкций для ИИ, когда версия не задана в БД
# (PromptLoader: DB-first, файловый fallback). Без них контейнер поднимается, но
# AI-генерация падает на AIConfigurationError «промпты не найдены».
COPY prompts ./prompts

RUN pip install --no-cache-dir . \
    # Запуск от непривилегированного пользователя. Право на запись даётся только
    # data/: остальное дерево приложение менять не должно.
    && useradd --create-home --shell /usr/sbin/nologin appuser \
    && mkdir -p /app/data \
    && chown appuser:appuser /app/data
USER appuser

# По умолчанию запускается backend; для бота переопределяется в compose.
CMD ["uvicorn", "apps.backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
