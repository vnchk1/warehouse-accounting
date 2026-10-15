"""Тестовая конфигурация, общая для unit- и интеграционных тестов.

1. Параметры приложения в тестах задаются здесь и не берутся из .env
   разработчика: результат прогона не зависит от локальных настроек.
2. Приложение в тестах видит только тестовую базу данных: DATABASE_URL
   подменяется значением TEST_DATABASE_URL (из окружения или из .env).
   Имя тестовой базы обязано оканчиваться на «_test» — тесты очищают таблицы,
   и рабочая база не должна попасть под очистку даже по ошибке в настройках.
3. Пропуск тестов запрещён (CONTRIBUTING.md): отметки skip, skipif, xfail и
   вызовы pytest.skip() превращаются в ошибку прогона.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

#: Параметры приложения для тестов (переменные окружения, как в .env.example)
TEST_SETTINGS = {
    "SECRET_KEY": "test-secret-key-0123456789abcdef",
    "ADMIN_LOGIN": "admin",
    "ADMIN_PASSWORD": "admin-password",
    "APP_HOST": "127.0.0.1",
    "APP_PORT": "8000",
    "SESSION_HOURS": "12",
    "PASSWORD_MIN_LENGTH": "8",
}
#: Адрес-заглушка, если тестовая база не настроена: unit-тестам база не нужна
NO_DATABASE = "postgresql+psycopg://nobody@localhost:1/not_configured_test"
FORBIDDEN_MARKERS = ("skip", "skipif", "xfail")


def _from_dotenv(name: str) -> str | None:
    """Значение переменной из файла .env проекта (если файл есть)."""
    path = ROOT / ".env"
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() == name and not key.lstrip().startswith("#"):
            return value.strip().strip("'\"")
    return None


def resolve_test_database_url() -> str | None:
    """TEST_DATABASE_URL из окружения или .env; иначе рабочая база + «_test»."""
    url = os.environ.get("TEST_DATABASE_URL") or _from_dotenv("TEST_DATABASE_URL")
    if not url:
        working = os.environ.get("DATABASE_URL") or _from_dotenv("DATABASE_URL")
        url = working + "_test" if working else None
    return url


TEST_DATABASE_URL = resolve_test_database_url()
os.environ.update(TEST_SETTINGS)
os.environ["DATABASE_URL"] = TEST_DATABASE_URL or NO_DATABASE

from app.config import get_settings  # noqa: E402 — окружение задаётся до импорта приложения

get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Запрет пропуска тестов
# ---------------------------------------------------------------------------
def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    marked = [
        item.nodeid
        for item in items
        if any(next(item.iter_markers(name), None) for name in FORBIDDEN_MARKERS)
    ]
    if marked:
        raise pytest.UsageError(
            "Пропуск тестов запрещён (CONTRIBUTING.md), отметки skip/skipif/xfail: "
            + ", ".join(marked)
        )


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    report = yield
    if report.skipped:
        report.outcome = "failed"
        report.longrepr = f"Пропуск теста запрещён (CONTRIBUTING.md): {item.nodeid}"
    return report


@pytest.hookimpl(wrapper=True)
def pytest_make_collect_report(
    collector: pytest.Collector,
) -> Generator[None, pytest.CollectReport, pytest.CollectReport]:
    report = yield
    if report.skipped:
        report.outcome = "failed"
        report.longrepr = f"Пропуск тестов запрещён (CONTRIBUTING.md): {collector.nodeid}"
    return report
