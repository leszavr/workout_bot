# Базовый образ для telegram-bot и backend.
# Секреты передаются через переменные окружения, в образ не включаются.
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
COPY apps ./apps

RUN pip install --no-cache-dir .

# По умолчанию запускается backend; для бота переопределяется в compose.
CMD ["uvicorn", "apps.backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
