"""Проверка правил предметной области БП-01 … БП-10.

Это чистые функции, база данных не нужна. Удалять эти тесты запрещено —
см. CONTRIBUTING.md.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app import rules

TODAY = date(2026, 9, 13)


def test_bp01_unit_only_from_okei() -> None:
    """Единицей измерения не может быть произвольное слово."""
    assert rules.validate_okei("796") == ("796", "шт", "Штука", True)
    for wrong in ("мешок", "999", "", "79"):
        with pytest.raises(rules.AppError) as error:
            rules.validate_okei(wrong)
        assert error.value.rule == "БП-01"


def test_bp02_quantity_must_be_positive() -> None:
    assert rules.validate_quantity("2,5", is_integer=False) == Decimal("2.5")
    for wrong in ("0", "-1"):
        with pytest.raises(rules.AppError) as error:
            rules.validate_quantity(wrong, is_integer=False)
        assert error.value.rule == "БП-02"


def test_bp02_integer_units_reject_fractions() -> None:
    assert rules.validate_quantity("3", is_integer=True, unit_code="шт") == Decimal("3")
    with pytest.raises(rules.AppError) as error:
        rules.validate_quantity("1.5", is_integer=True, unit_code="шт")
    assert error.value.rule == "БП-02"


def test_bp03_price_and_amount() -> None:
    assert rules.validate_price("0") == Decimal("0")
    with pytest.raises(rules.AppError) as error:
        rules.validate_price("-0.01")
    assert error.value.rule == "БП-03"
    assert rules.amount(Decimal("10.5"), Decimal("345.90")) == Decimal("3631.95")


def test_bp04_receipt_date() -> None:
    assert rules.validate_receipt_date(TODAY, today=TODAY) == TODAY
    for wrong in (TODAY + timedelta(days=1), date(1999, 12, 31)):
        with pytest.raises(rules.AppError) as error:
            rules.validate_receipt_date(wrong, today=TODAY)
        assert error.value.rule == "БП-04"


@pytest.mark.parametrize("inn", ["7707083893", "500100732259"])
def test_bp05_valid_inn(inn: str) -> None:
    assert rules.validate_inn(inn) == inn


@pytest.mark.parametrize("inn", ["7707083894", "770708389", "77070838ab", ""])
def test_bp05_invalid_inn(inn: str) -> None:
    with pytest.raises(rules.AppError) as error:
        rules.validate_inn(inn)
    assert error.value.rule == "БП-05"


def test_bp06_sku_format() -> None:
    assert rules.validate_sku(" cem-500 ") == "CEM-500"
    for wrong in ("AB", "цемент", "CEM 500"):
        with pytest.raises(rules.AppError) as error:
            rules.validate_sku(wrong)
        assert error.value.rule == "БП-06"


def test_bp07_material_not_repeated() -> None:
    rules.ensure_material_not_repeated({1, 2}, 3)
    with pytest.raises(rules.AppError) as error:
        rules.ensure_material_not_repeated({1, 2}, 2)
    assert error.value.rule == "БП-07"


def test_bp08_database_error_is_readable() -> None:
    error = rules.database_error("material_unit_id_fkey")
    assert error.status == 409
    assert error.rule == "БП-08"
    assert "используется" in error.message


def test_bp09_only_admin() -> None:
    class Account:
        def __init__(self, role: str) -> None:
            self.role = role

    rules.require_admin(Account("admin"), "проверка")
    with pytest.raises(rules.AppError) as error:
        rules.require_admin(Account("storekeeper"), "проверка")
    assert error.value.status == 403
    assert error.value.rule == "БП-09"


def test_bp10_password_length() -> None:
    assert rules.validate_password("12345678", min_length=8) == "12345678"
    with pytest.raises(rules.AppError) as error:
        rules.validate_password("1234567", min_length=8)
    assert error.value.rule == "БП-10"


def test_password_is_stored_as_hash() -> None:
    from app.auth import hash_password, verify_password

    plain = "проверочный-пароль"
    stored = hash_password(plain)
    assert plain not in stored
    assert verify_password(stored, plain)
    assert not verify_password(stored, plain + "!")


def test_okei_table_is_consistent() -> None:
    """Код ОКЕИ и условное обозначение уникальны каждый по отдельности.

    Повтор обозначения нарушил бы ограничение уникальности столбца unit.code и
    сделал бы невозможным начальное наполнение справочника.
    """
    designations = [designation for designation, _name, _integer in rules.OKEI.values()]
    assert len(set(designations)) == len(designations), "повтор условного обозначения"
    for code, (designation, name, _integer) in rules.OKEI.items():
        assert len(code) == 3 and code.isdigit(), code
        assert 0 < len(designation) <= 20, designation
        assert 0 < len(name) <= 100, name
