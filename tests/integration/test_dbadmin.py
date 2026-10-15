"""Резервное копирование и восстановление: копия, намеренное повреждение, восстановление."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app import db, dbadmin
from tests.integration.conftest import add_item, new_material, new_receipt

TABLES = ("unit", "supplier", "material", "app_user", "receipt", "receipt_item")


def snapshot() -> dict[str, list[tuple[Any, ...]]]:
    """Полное содержимое таблиц — для сравнения «до» и «после»."""
    with db.engine().connect() as connection:
        return {
            table: [
                tuple(row) for row in connection.execute(text(f"SELECT * FROM {table} ORDER BY id"))
            ]
            for table in TABLES
        }


def damage() -> None:
    """Намеренная порча данных: удаление и изменение записей."""
    with db.engine().begin() as connection:
        connection.execute(text("DELETE FROM receipt_item WHERE receipt_id = 1"))
        connection.execute(text("DELETE FROM receipt WHERE id = 2"))
        connection.execute(text("UPDATE supplier SET name = 'испорчено'"))
        connection.execute(text("UPDATE receipt_item SET price = 0"))


@pytest.fixture
def filled(signed_in: TestClient, data: dict[str, Any]) -> dict[str, list[tuple[Any, ...]]]:
    material = new_material(signed_in, data["kg"].id)
    piece = new_material(signed_in, data["pcs"].id, sku="BRK-01", name="Кирпич")
    for day in ("2026-01-10", "2026-01-11"):
        receipt = new_receipt(signed_in, data["supplier"].id, day)
        add_item(signed_in, receipt["id"], material["id"], "10.5", "345.90")
        add_item(signed_in, receipt["id"], piece["id"], "100", "12")
    return snapshot()


def test_backup_damage_restore(filled: dict[str, Any], tmp_path: Path) -> None:
    backup = dbadmin.create_backup(tmp_path)
    assert backup.name.startswith("warehouse-") and backup.suffix == ".dump"
    assert backup.stat().st_mode & 0o777 == 0o600  # в копии хеши паролей
    assert dbadmin.check_archive(backup) > 10

    damage()
    damaged = snapshot()
    assert damaged != filled

    safety = dbadmin.restore_backup(backup, directory=tmp_path)
    assert snapshot() == filled  # данные восстановлены полностью
    assert safety.name.startswith("before-restore-")

    # копия, сделанная перед восстановлением, хранит повреждённое состояние
    dbadmin.restore_backup(safety, directory=tmp_path)
    assert snapshot() == damaged


def test_restore_keeps_application_working(
    filled: dict[str, Any], signed_in: TestClient, tmp_path: Path
) -> None:
    backup = dbadmin.create_backup(tmp_path)
    damage()
    dbadmin.restore_backup(backup, directory=tmp_path)
    receipts = signed_in.get("/api/v1/receipts").json()
    assert [receipt["total"] for receipt in receipts] == ["4831.95", "4831.95"]
    assert signed_in.get("/healthz").json()["database"] == "ok"


def test_invalid_archive_is_rejected_before_restore(filled: dict[str, Any], tmp_path: Path) -> None:
    broken = tmp_path / "warehouse-broken.dump"
    broken.write_bytes(b"not a pg_dump archive")
    with pytest.raises(dbadmin.BackupError):
        dbadmin.restore_backup(broken, directory=tmp_path)
    assert snapshot() == filled  # база не тронута
    assert not list(tmp_path.glob("before-restore-*"))


def test_failed_backup_leaves_no_file(tmp_path: Path) -> None:
    unreachable = "postgresql+psycopg://nobody:x@127.0.0.1:1/nothing_test"
    with pytest.raises(dbadmin.BackupError) as error:
        dbadmin.create_backup(tmp_path, database_url=unreachable)
    assert "pg_dump" in str(error.value)
    assert list(tmp_path.iterdir()) == []


def test_backup_names_do_not_collide(filled: dict[str, Any], tmp_path: Path) -> None:
    names = {dbadmin.create_backup(tmp_path).name for _ in range(3)}
    assert len(names) == 3
    assert [path.name for path in dbadmin.list_backups(tmp_path)] == sorted(names)


def test_table_counts(filled: dict[str, Any]) -> None:
    assert dbadmin.table_counts() == {
        "unit": 2,
        "supplier": 1,
        "material": 2,
        "app_user": 2,
        "receipt": 2,
        "receipt_item": 4,
    }


# ---------------------------------------------------------------------------
# Консольная команда: make backup / make restore / make backups / make psql
# ---------------------------------------------------------------------------
def test_command_line(
    filled: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    folder = ["--dir", str(tmp_path)]
    assert dbadmin.main([*folder, "list"]) == 0
    assert "резервных копий нет" in capsys.readouterr().out
    assert dbadmin.main([*folder, "restore", "--yes"]) == 1
    assert "Сначала: make backup" in capsys.readouterr().err

    assert dbadmin.main([*folder, "backup"]) == 0
    output = capsys.readouterr().out
    assert "Резервная копия создана" in output and "receipt_item 4" in output
    assert dbadmin.main([*folder, "list"]) == 0
    assert "warehouse-" in capsys.readouterr().out

    damage()
    monkeypatch.setattr("builtins.input", lambda prompt: "другая_база")
    assert dbadmin.main([*folder, "restore"]) == 1  # подтверждение не совпало
    assert "отменено" in capsys.readouterr().out
    assert snapshot() != filled

    name = os.environ["DATABASE_URL"].rsplit("/", 1)[1]
    monkeypatch.setattr("builtins.input", lambda prompt: name)
    assert dbadmin.main([*folder, "restore"]) == 0  # последняя копия
    assert "восстановлена" in capsys.readouterr().out
    assert snapshot() == filled

    missing = tmp_path / "missing.dump"
    assert dbadmin.main([*folder, "restore", str(missing), "--yes"]) == 1


def test_psql_command(filled: dict[str, Any], capfd: pytest.CaptureFixture[str]) -> None:
    assert dbadmin.main(["psql", "-c", "SELECT count(*) AS receipts FROM receipt"]) == 0
    assert "receipts" in capfd.readouterr().out
    assert dbadmin.main(["psql", "-c", "SELECT * FROM no_such_table"]) != 0
