"""Подготовка тестов: отдельная база данных, схема из миграций, чистые таблицы."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-0123456789abcdef")
os.environ.setdefault("ADMIN_LOGIN", "admin")
os.environ.setdefault("ADMIN_PASSWORD", "admin-password")

from sqlalchemy import create_engine, text  # noqa: E402

from app import db  # noqa: E402
from app.auth import hash_password  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.models import Supplier, Unit, User  # noqa: E402

PASSWORD = "test-password"
TABLES = "receipt_item, receipt, material, supplier, app_user, unit"


def _test_database_url() -> str:
    """Тестовая база: TEST_DATABASE_URL или рабочая база с суффиксом _test."""
    url = os.getenv("TEST_DATABASE_URL")
    return url or get_settings().database_url + "_test"


@pytest.fixture(scope="session", autouse=True)
def database() -> Iterator[None]:
    url = _test_database_url()
    name = url.rsplit("/", 1)[-1]
    admin = create_engine(url.rsplit("/", 1)[0] + "/postgres", isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        if not connection.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": name}
        ).scalar():
            connection.execute(text(f'CREATE DATABASE "{name}"'))
    admin.dispose()

    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        check=True,
        env=dict(os.environ, ALEMBIC_DATABASE_URL=url),
    )
    db.setup(url)
    yield


@pytest.fixture(autouse=True)
def clean() -> None:
    with db.engine().begin() as connection:
        connection.execute(text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))


@pytest.fixture
def session() -> Iterator[object]:
    yield from db.get_session()


@pytest.fixture
def data(session) -> dict:  # type: ignore[no-untyped-def]
    """Минимальный набор данных: два пользователя, две ЕИ, поставщик."""
    admin = User(
        login="admin",
        password_hash=hash_password(PASSWORD),
        full_name="Администратор",
        role="admin",
    )
    storekeeper = User(
        login="ivanov",
        password_hash=hash_password(PASSWORD),
        full_name="Иванов И.И.",
        role="storekeeper",
    )
    kilogram = Unit(code="кг", name="Килограмм", okei_code="166", is_integer=False)
    piece = Unit(code="шт", name="Штука", okei_code="796", is_integer=True)
    supplier = Supplier(name="ООО «Ромашка»", inn="7707083893")
    session.add_all([admin, storekeeper, kilogram, piece, supplier])
    session.commit()
    return {
        "admin": admin,
        "storekeeper": storekeeper,
        "kg": kilogram,
        "pcs": piece,
        "supplier": supplier,
    }


@pytest.fixture
def client() -> Iterator[object]:
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def signed_in(client, data):  # type: ignore[no-untyped-def]
    """Клиент, вошедший под кладовщиком."""
    response = client.post("/api/v1/auth/login", json={"login": "ivanov", "password": PASSWORD})
    assert response.status_code == 200, response.text
    return client
