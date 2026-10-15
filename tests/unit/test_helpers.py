"""Разбор полей экранных форм и параметры подключения для резервного копирования."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from app import dbadmin, rules
from app.web import go, opt_date, opt_id, req_date, req_id


# ---------------------------------------------------------------------------
# Поля отбора: пустое значение означает «без ограничения»
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(("value", "expected"), [("", None), ("  ", None), (None, None)])
def test_optional_date_empty(value: str | None, expected: None) -> None:
    assert opt_date(value, field="date_from", title="с") is expected


def test_optional_date_parsed() -> None:
    assert opt_date(" 2026-01-02 ", field="date_from", title="с") == date(2026, 1, 2)


@pytest.mark.parametrize("value", ["02.01.2026", "2026-13-01", "вчера"])
def test_optional_date_wrong_format(value: str) -> None:
    with pytest.raises(rules.AppError) as error:
        opt_date(value, field="date_to", title="по")
    assert error.value.field == "date_to"
    assert "ГГГГ-ММ-ДД" in error.value.message


def test_required_date() -> None:
    assert req_date("2026-03-04", field="receipt_date", title="Дата") == date(2026, 3, 4)
    with pytest.raises(rules.AppError) as error:
        req_date("", field="receipt_date", title="Дата")
    assert "обязательно" in error.value.message


@pytest.mark.parametrize(
    ("value", "expected"),
    [("", None), (None, None), ("0", None), ("-1", None), ("abc", None), ("5", 5), (" 7 ", 7)],
)
def test_optional_id(value: str | None, expected: int | None) -> None:
    assert opt_id(value) == expected


def test_required_id() -> None:
    assert req_id(" 3 ", field="supplier_id", title="Поставщик") == 3
    for empty in ("", None, "0", "abc"):
        with pytest.raises(rules.AppError) as error:
            req_id(empty, field="supplier_id", title="Поставщик")
        assert error.value.field == "supplier_id"
        assert "«Поставщик»: выберите значение" in error.value.message


def test_redirect_after_success() -> None:
    plain = go("/suppliers")
    assert (plain.status_code, plain.headers["location"]) == (303, "/suppliers")
    with_message = go("/suppliers", "Сохранено")
    assert with_message.headers["location"].startswith("/suppliers?msg=")


# ---------------------------------------------------------------------------
# Резервное копирование: подключение и поиск программ
# ---------------------------------------------------------------------------
def test_connection_env_from_database_url() -> None:
    env = dbadmin.connection_env("postgresql+psycopg://wh:secret@db.local:6543/stock")
    assert (env["PGHOST"], env["PGPORT"], env["PGUSER"], env["PGDATABASE"]) == (
        "db.local",
        "6543",
        "wh",
        "stock",
    )
    assert env["PGPASSWORD"] == "secret"  # пароль — только в окружении, не в аргументах


def test_connection_env_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PGPASSWORD", raising=False)
    env = dbadmin.connection_env("postgresql+psycopg://wh@/stock")
    assert (env["PGHOST"], env["PGPORT"]) == ("localhost", "5432")
    assert "PGPASSWORD" not in env
    assert dbadmin.database_name("postgresql+psycopg://wh@/stock") == "stock"


def test_missing_postgresql_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dbadmin.shutil, "which", lambda name: None)
    with pytest.raises(dbadmin.BackupError) as error:
        dbadmin._program("pg_dump")
    assert "postgresql-client" in str(error.value)


def test_backup_list_is_sorted_and_filtered(tmp_path: Path) -> None:
    for name in (
        "warehouse-20261002-100000.dump",
        "warehouse-20261001-120000.dump",
        "before-restore-20261003-090000.dump",
        "notes.txt",
    ):
        (tmp_path / name).write_text("x")
    assert [path.name for path in dbadmin.list_backups(tmp_path)] == [
        "warehouse-20261001-120000.dump",
        "warehouse-20261002-100000.dump",
    ]
    assert dbadmin.list_backups(tmp_path / "нет-такого") == []


def test_check_archive_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(dbadmin.BackupError) as error:
        dbadmin.check_archive(tmp_path / "missing.dump")
    assert "не найден" in str(error.value)
