"""Начальное и демонстрационное наполнение базы, запуск приложения."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import demo_seed, rules, seed
from app.auth import verify_password
from app.models import Material, Receipt, ReceiptItem, Supplier, Unit, User


def count(session: Session, model: type) -> int:
    return int(session.execute(select(func.count()).select_from(model)).scalar_one())


def test_seed_is_idempotent(session: Session, capsys: pytest.CaptureFixture[str]) -> None:
    seed.main()
    assert "добавлено 43" in capsys.readouterr().out
    seed.main()  # повторный запуск ничего не дублирует
    assert "добавлено 0, всего 43" in capsys.readouterr().out
    assert count(session, Unit) == len(rules.OKEI)

    admin = session.execute(select(User)).scalar_one()
    assert (admin.login, admin.role) == ("admin", "admin")
    assert verify_password(admin.password_hash, "admin-password")  # ADMIN_PASSWORD


def test_seed_skips_existing_designation(session: Session) -> None:
    session.add(Unit(code="кг", name="Килограмм", okei_code="166", is_integer=False))
    session.commit()
    seed.main()
    assert count(session, Unit) == len(rules.OKEI)


def test_demo_seed_requires_admin(capsys: pytest.CaptureFixture[str]) -> None:
    assert demo_seed.main() == 1
    assert "make seed" in capsys.readouterr().out


def test_demo_seed(session: Session, capsys: pytest.CaptureFixture[str]) -> None:
    seed.main()
    assert demo_seed.main() == 0
    totals = {model: count(session, model) for model in (Supplier, Material, Receipt)}
    assert totals == {Supplier: 10, Material: 26, Receipt: 28}
    items = count(session, ReceiptItem)
    assert items >= 28 * 2

    assert demo_seed.main() == 0  # повторно ничего не добавляется
    assert "повторно не добавляются" in capsys.readouterr().out
    assert {model: count(session, model) for model in (Supplier, Material, Receipt)} == totals
    assert count(session, ReceiptItem) == items


def test_demo_data_follows_rules() -> None:
    """Демонстрационные данные сами проходят правила БП-01, БП-05, БП-06."""
    for _name, inn, _phone in demo_seed.SUPPLIERS:
        assert rules.validate_inn(inn) == inn
    for sku, _name, okei_code in demo_seed.MATERIALS:
        assert rules.validate_sku(sku) == sku
        assert rules.validate_okei(okei_code)[0] == okei_code


def test_run_command(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """make run: адрес и порт из настроек, пароль базы на экран не выводится."""
    import uvicorn

    from app import main

    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **options: calls.append(options))
    main.main()
    assert calls == [{"host": "127.0.0.1", "port": 8000, "reload": True}]
    output = capsys.readouterr().out
    assert "База данных:" in output and ":***@" in output


def test_application_connects_on_start(monkeypatch: pytest.MonkeyPatch) -> None:
    """Без заранее настроенного подключения приложение создаёт его при запуске."""
    from fastapi.testclient import TestClient

    from app import db
    from app.main import app

    monkeypatch.setattr(db, "_connection", None)
    with TestClient(app) as client:
        assert db.is_configured()
        assert client.get("/healthz").json()["database"] == "ok"
    db.engine().dispose()


def test_database_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    from sqlalchemy import create_engine

    from app import db

    unreachable = create_engine("postgresql+psycopg://nobody@127.0.0.1:1/nothing_test")
    monkeypatch.setattr(db, "_connection", (unreachable, None))
    assert db.database_is_available() is False


def test_database_failure_rolls_back(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """Сбой базы данных (не нарушение правил) откатывает транзакцию и передаётся дальше."""
    from sqlalchemy.exc import OperationalError

    from app import services

    def failing_flush() -> None:
        raise OperationalError("INSERT", {}, Exception("соединение разорвано"))

    rolled_back: list[bool] = []
    monkeypatch.setattr(session, "flush", failing_flush)
    monkeypatch.setattr(session, "rollback", lambda: rolled_back.append(True))
    with pytest.raises(OperationalError):
        services.save_supplier(session, name="ООО «Тест»", inn="7707083893")
    assert rolled_back == [True]
