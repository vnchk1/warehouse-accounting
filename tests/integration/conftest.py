"""Интеграционные тесты: настоящая база PostgreSQL, схема — из миграций.

Изоляция данных:
- тестовая база отдельная (см. tests/conftest.py), создаётся при первом
  запуске и приводится к последней версии схемы командой alembic upgrade head;
- перед каждым тестом все таблицы очищаются (TRUNCATE ... RESTART IDENTITY),
  поэтому тесты не зависят друг от друга и от порядка запуска;
- каждый тест создаёт нужные ему данные сам (фикстура data и помощники ниже).
"""

from __future__ import annotations

import functools
import os
import subprocess
import sys
from collections.abc import Iterator
from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app import db
from app.auth import hash_password
from app.models import Base, Supplier, Unit, User
from tests.conftest import ROOT

PASSWORD = "test-password"
V1 = "/api/v1"


@functools.cache
def password_hash() -> str:
    """Хеш пароля тестовых пользователей: argon2 медленный, считаем один раз."""
    return hash_password(PASSWORD)


def database_url() -> str:
    url = os.environ["DATABASE_URL"]
    name = make_url(url).database or ""
    if not name.endswith("_test"):
        pytest.exit(
            f"Тестовая база должна называться *_test, а указана «{name}». Задайте "
            "TEST_DATABASE_URL в .env (см. .env.example)",
            returncode=4,
        )
    return url


def server_url(url: str) -> str:
    """Подключение к служебной базе postgres того же сервера."""
    return make_url(url).set(database="postgres").render_as_string(hide_password=False)


def create_database(url: str, *, recreate: bool = False) -> None:
    """Создаёт базу данных (роли нужно право CREATEDB)."""
    name = make_url(url).database
    admin = create_engine(server_url(url), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            if recreate:
                connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
            exists = connection.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": name}
            ).scalar()
            if not exists:
                connection.execute(text(f'CREATE DATABASE "{name}"'))
    finally:
        admin.dispose()


def drop_database(url: str) -> None:
    name = make_url(url).database
    admin = create_engine(server_url(url), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    finally:
        admin.dispose()


def run_alembic(url: str, *args: str) -> subprocess.CompletedProcess[str]:
    """Команда alembic для указанной базы (как make migrate, но с другим адресом)."""
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=ROOT,
        env=dict(os.environ, ALEMBIC_DATABASE_URL=url),
        capture_output=True,
        text=True,
        check=False,
    )


def alembic(url: str, *args: str) -> str:
    result = run_alembic(url, *args)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


@pytest.fixture(scope="session", autouse=True)
def database() -> Iterator[str]:
    url = database_url()
    try:
        create_database(url)
    except OperationalError as error:
        pytest.exit(
            f"Нет подключения к PostgreSQL ({make_url(url)}): {error.orig}\n"
            "Запустите сервер (sudo service postgresql start) и проверьте "
            "TEST_DATABASE_URL в .env",
            returncode=3,
        )
    if run_alembic(url, "upgrade", "head").returncode != 0:
        # Тестовая база осталась от другой ветки (неизвестная версия схемы).
        # Она одноразовая: создаём заново и применяем миграции этой ветки.
        create_database(url, recreate=True)
        alembic(url, "upgrade", "head")
    db.setup(url)
    yield url
    db.engine().dispose()


@pytest.fixture(autouse=True)
def clean(database: str) -> None:
    """Пустые таблицы перед каждым тестом."""
    tables = ", ".join(table.name for table in Base.metadata.sorted_tables)
    with db.engine().begin() as connection:
        # Не ждать бесконечно, если предыдущий тест оставил незакрытый сеанс.
        connection.execute(text("SET LOCAL lock_timeout = '10s'"))
        connection.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


@pytest.fixture
def session() -> Iterator[Session]:
    yield from db.get_session()


@pytest.fixture
def data(session: Session) -> dict[str, Any]:
    """Минимальный набор: администратор, кладовщик, две ЕИ, поставщик."""
    admin = User(
        login="admin",
        password_hash=password_hash(),
        full_name="Администратор",
        role="admin",
    )
    storekeeper = User(
        login="ivanov",
        password_hash=password_hash(),
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
def client() -> Iterator[TestClient]:
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


def login(client: TestClient, user: str) -> TestClient:
    """Вход под указанным пользователем (предыдущий сеанс завершается)."""
    client.cookies.clear()
    response = client.post(f"{V1}/auth/login", json={"login": user, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return client


@pytest.fixture
def signed_in(client: TestClient, data: dict[str, Any]) -> TestClient:
    """Клиент, вошедший под кладовщиком."""
    return login(client, "ivanov")


@pytest.fixture
def as_admin(client: TestClient, data: dict[str, Any]) -> TestClient:
    """Клиент, вошедший под администратором."""
    return login(client, "admin")


# ---------------------------------------------------------------------------
# Помощники: создание записей через HTTP API
# ---------------------------------------------------------------------------
def new_material(
    client: TestClient, unit_id: int, sku: str = "CEM-500", name: str = "Цемент М500"
) -> dict[str, Any]:
    response = client.post(f"{V1}/materials", json={"sku": sku, "name": name, "unit_id": unit_id})
    assert response.status_code == 201, response.text
    return response.json()


def new_receipt(
    client: TestClient, supplier_id: int, day: date | str | None = None
) -> dict[str, Any]:
    payload = {"receipt_date": str(day or date.today()), "supplier_id": supplier_id}
    response = client.post(f"{V1}/receipts", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def add_item(
    client: TestClient, receipt_id: int, material_id: int, quantity: str = "1", price: str = "10"
) -> Response:
    return client.post(
        f"{V1}/receipts/{receipt_id}/items",
        json={"material_id": material_id, "quantity": quantity, "price": price},
    )
