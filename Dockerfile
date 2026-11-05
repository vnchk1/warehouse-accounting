# Образ приложения «Складской учёт» (ЛР4). Сборка: make container-check (или docker compose build).
#
# Воспроизводимость: базовый образ закреплён точной версией и дайджестом sha256,
# пакеты Python ставятся только из requirements.lock — точные версии всех пакетов
# (и транзитивных) с хешами SHA-256; pip отказывается ставить файл с другим хешем.
# Секретов в образе нет: настройки и пароли передаются при запуске переменными окружения.

# ---------------------------------------------------------------------------
# Стадия 1: виртуальное окружение с зависимостями
# ---------------------------------------------------------------------------
FROM python:3.12.15-slim-trixie@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1 AS build

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build
COPY requirements.lock ./
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --require-hashes --no-deps --only-binary=:all: -r requirements.lock \
 && /opt/venv/bin/pip check

# ---------------------------------------------------------------------------
# Стадия 2: итоговый образ — только интерпретатор, окружение и код
# ---------------------------------------------------------------------------
FROM python:3.12.15-slim-trixie@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1 AS runtime

ARG APP_UID=10001
ARG VERSION=0.1.0
LABEL org.opencontainers.image.title="warehouse" \
      org.opencontainers.image.description="Складской учёт: материалы, поставщики, поступления" \
      org.opencontainers.image.version="${VERSION}"

# Служебный пользователь без пароля, оболочки и домашнего каталога
RUN groupadd --system --gid "${APP_UID}" warehouse \
 && useradd --system --uid "${APP_UID}" --gid warehouse --no-create-home \
        --home-dir /nonexistent --shell /usr/sbin/nologin warehouse

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    APP_HOST=0.0.0.0 \
    APP_PORT=8000

COPY --from=build /opt/venv /opt/venv
WORKDIR /app
# Владелец кода — root: процесс приложения может читать файлы, но не изменять их
COPY alembic.ini pyproject.toml ./
COPY migrations ./migrations
COPY app ./app
RUN python -m compileall -q app migrations

# Всё дальнейшее — от непривилегированного пользователя (числовой UID: проверяется без /etc/passwd)
USER ${APP_UID}:${APP_UID}

EXPOSE 8000
# SIGTERM: uvicorn перестаёт принимать соединения, завершает начатые запросы (до 15 с),
# выполняет завершение приложения (закрывает соединения с базой) и выходит с кодом 0
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-m", "app.healthcheck"]

# Форма exec (JSON-массив): uvicorn — процесс PID 1 и сам получает сигнал остановки
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*", \
     "--timeout-graceful-shutdown", "15", "--no-server-header"]
