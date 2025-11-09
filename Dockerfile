# syntax=docker/dockerfile:1

# Базовый образ с Python
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    POETRY_HOME=/opt/poetry \
    POETRY_VENV=/opt/poetry-venv \
    POETRY_CACHE_DIR=/opt/.cache

# Установка curl для инсталляции Poetry
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
  && rm -rf /var/lib/apt/lists/*

# Устанавливаем Poetry
RUN python3 -m venv $POETRY_VENV \
    && $POETRY_VENV/bin/pip install -U pip setuptools \
    && $POETRY_VENV/bin/pip install poetry \
    && ln -s $POETRY_VENV/bin/poetry /usr/local/bin/poetry

WORKDIR /app

# --- Слой зависимостей (кэшируется отдельно) ---
FROM base AS deps
WORKDIR /app

# Копируем только файлы зависимостей, чтобы кэшировать слой
COPY pyproject.toml poetry.lock ./

# Настраиваем Poetry для создания виртуального окружения в директории проекта
# Устанавливаем зависимости проекта в виртуальное окружение
# --no-root: не устанавливать сам проект как пакет
# --no-interaction: не запрашивать интерактивный ввод
RUN poetry config virtualenvs.in-project true \
    && poetry install --no-interaction --no-root --no-dev

# --- Финальный образ ---
FROM base AS runtime
WORKDIR /app

# Копируем виртуальное окружение с зависимостями
COPY --from=deps /app/.venv /app/.venv

# Используем .venv по умолчанию
ENV VIRTUAL_ENV=/app/.venv \
    PATH=/app/.venv/bin:$PATH

# Копируем исходники приложения
COPY . .

# Создаем директорию для логов
RUN mkdir -p logs

# По умолчанию запускаем бота
CMD ["python", "main.py"]

