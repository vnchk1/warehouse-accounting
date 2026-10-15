"""Правила предметной области БП-01 … БП-10: корректные, ошибочные и граничные значения.

Это чистые функции, база данных не нужна. Набор проверяется мутационно
(make mutation): изменение любого условия в app/rules.py должно приводить к
падению хотя бы одного теста. Удалять и ослаблять эти тесты запрещено —
см. CONTRIBUTING.md.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app import rules

TODAY = date(2026, 9, 13)


def rule_of(call: Callable[..., object], *args: object, **kwargs: object) -> str | None:
    """Код правила, которое нарушает вызов; ошибка обязательна."""
    with pytest.raises(rules.AppError) as error:
        call(*args, **kwargs)
    return error.value.rule


# ---------------------------------------------------------------------------
# Ошибки приложения
# ---------------------------------------------------------------------------
def test_app_error_defaults_and_payload() -> None:
    error = rules.AppError("Текст")
    assert (error.status, error.code, error.rule, error.field) == (
        422,
        "VALIDATION_ERROR",
        None,
        None,
    )
    assert error.payload() == {"error": {"code": "VALIDATION_ERROR", "message": "Текст"}}
    assert str(error) == "Текст"

    full = rules.AppError("Текст", rule="БП-02", field="quantity")
    assert full.payload() == {
        "error": {
            "code": "VALIDATION_ERROR",
            "message": "Текст",
            "rule": "БП-02",
            "field": "quantity",
        }
    }


@pytest.mark.parametrize(
    ("factory", "status", "code", "rule"),
    [
        (rules.not_found, 404, "NOT_FOUND", None),
        (rules.conflict, 409, "CONFLICT", None),
        (rules.forbidden, 403, "FORBIDDEN", "БП-09"),
        (rules.unauthorized, 401, "UNAUTHORIZED", None),
    ],
)
def test_error_factories(
    factory: Callable[[str], rules.AppError], status: int, code: str, rule: str | None
) -> None:
    error = factory("Сообщение")
    assert (error.status, error.code, error.rule, error.message) == (
        status,
        code,
        rule,
        "Сообщение",
    )


def test_conflict_keeps_rule() -> None:
    assert rules.conflict("Повтор", rule="БП-07").rule == "БП-07"


# ---------------------------------------------------------------------------
# БП-01. Единица измерения — только из ОКЕИ
# ---------------------------------------------------------------------------
def test_bp01_unit_from_okei() -> None:
    assert rules.validate_okei("796") == ("796", "шт", "Штука", True)
    assert rules.validate_okei(" 166 ") == ("166", "кг", "Килограмм", False)


@pytest.mark.parametrize("wrong", ["мешок", "999", "", "79", "7966", None])
def test_bp01_arbitrary_value_rejected(wrong: str | None) -> None:
    with pytest.raises(rules.AppError) as error:
        rules.validate_okei(wrong)
    assert (error.value.rule, error.value.field) == ("БП-01", "okei_code")


def test_okei_table_is_consistent() -> None:
    """Код ОКЕИ и обозначение уникальны каждый по отдельности.

    Повтор обозначения нарушил бы уникальность столбца unit.code и сделал бы
    невозможным начальное наполнение справочника.
    """
    designations = [designation for designation, _name, _integer in rules.OKEI.values()]
    assert len(set(designations)) == len(designations), "повтор условного обозначения"
    for code, (designation, name, _integer) in rules.OKEI.items():
        assert len(code) == 3 and code.isdigit(), code
        assert 0 < len(designation) <= 20, designation
        assert 0 < len(name) <= 100, name


# ---------------------------------------------------------------------------
# Числа из форм и JSON
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("value", "expected"),
    [("2,5", "2.5"), (" 3 ", "3"), (5, "5"), ("0.125", "0.125"), (Decimal("1.10"), "1.10")],
)
def test_to_decimal(value: object, expected: str) -> None:
    assert rules.to_decimal(value, "quantity") == Decimal(expected)


def test_to_decimal_returns_decimal_unchanged() -> None:
    value = Decimal("7.000")
    assert rules.to_decimal(value, "price") is value


@pytest.mark.parametrize("wrong", ["abc", "", "1,2,3", None])
def test_to_decimal_rejects_non_numbers(wrong: object) -> None:
    with pytest.raises(rules.AppError) as error:
        rules.to_decimal(wrong, "price")
    assert error.value.field == "price"
    assert error.value.rule is None


# ---------------------------------------------------------------------------
# БП-02. Количество больше нуля; не более 3 знаков; для штучных — целое
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("value", ["0.001", "1", "2,5", "1.234", "99999"])
def test_bp02_valid_quantity(value: str) -> None:
    assert rules.validate_quantity(value, is_integer=False) == Decimal(value.replace(",", "."))


@pytest.mark.parametrize("value", ["0", "-0.001", "-1", "1.2345", "0.0001"])
def test_bp02_invalid_quantity(value: str) -> None:
    with pytest.raises(rules.AppError) as error:
        rules.validate_quantity(value, is_integer=False)
    assert (error.value.rule, error.value.field) == ("БП-02", "quantity")


def test_bp02_integer_units() -> None:
    assert rules.validate_quantity("3", is_integer=True, unit_code="шт") == Decimal("3")
    assert rules.validate_quantity("3.000", is_integer=True) == Decimal("3")
    with pytest.raises(rules.AppError) as error:
        rules.validate_quantity("1.5", is_integer=True, unit_code="шт")
    assert error.value.rule == "БП-02"
    assert "«шт»" in error.value.message
    # для нештучной единицы дробное количество допустимо
    assert rules.validate_quantity("1.5", is_integer=False) == Decimal("1.5")


# ---------------------------------------------------------------------------
# БП-03. Цена не отрицательна, не более 2 знаков; сумма = количество × цена
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("value", ["0", "0.01", "1.23", "345,90", "100"])
def test_bp03_valid_price(value: str) -> None:
    assert rules.validate_price(value) == Decimal(value.replace(",", "."))


@pytest.mark.parametrize("value", ["-0.01", "-1", "1.234", "0.001"])
def test_bp03_invalid_price(value: str) -> None:
    with pytest.raises(rules.AppError) as error:
        rules.validate_price(value)
    assert (error.value.rule, error.value.field) == ("БП-03", "price")


@pytest.mark.parametrize(
    ("quantity", "price", "expected"),
    [
        ("10.5", "345.90", "3631.95"),
        ("2.5", "0.01", "0.03"),  # 0.025 → 0.03: округление половины вверх
        ("0.333", "0.01", "0.00"),
        ("3", "0", "0.00"),
        ("1.001", "1000.00", "1001.00"),
    ],
)
def test_bp03_amount(quantity: str, price: str, expected: str) -> None:
    result = rules.amount(Decimal(quantity), Decimal(price))
    assert result == Decimal(expected)
    assert result.as_tuple().exponent == -2


# ---------------------------------------------------------------------------
# БП-04. Дата поступления не в будущем и не ранее 01.01.2000
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("value", [TODAY, rules.MIN_DATE, date(2000, 1, 2), date(2026, 1, 31)])
def test_bp04_valid_date(value: date) -> None:
    assert rules.validate_receipt_date(value, today=TODAY) == value


@pytest.mark.parametrize("value", [TODAY + timedelta(days=1), date(1999, 12, 31), date(1990, 1, 1)])
def test_bp04_invalid_date(value: date) -> None:
    with pytest.raises(rules.AppError) as error:
        rules.validate_receipt_date(value, today=TODAY)
    assert (error.value.rule, error.value.field) == ("БП-04", "receipt_date")


def test_bp04_minimum_date_is_2000_01_01() -> None:
    assert rules.MIN_DATE.isoformat() == "2000-01-01"


def test_bp04_today_by_default() -> None:
    """Без явной даты «сегодня» — текущая дата."""
    today = date.today()
    assert rules.validate_receipt_date(today) == today
    assert rule_of(rules.validate_receipt_date, today + timedelta(days=1)) == "БП-04"


# ---------------------------------------------------------------------------
# БП-05. ИНН: 10 или 12 цифр с верной контрольной суммой
# ---------------------------------------------------------------------------
VALID_INN = [
    "7707083893",  # ПАО Сбербанк
    "6371292619",  # все цифры ненулевые: проверяется каждый весовой коэффициент
    "7129666880",  # остаток 10 → контрольная цифра 0
    "500100732259",
    "225821585766",  # первые 11 цифр ненулевые
    "399311293707",  # 11-я цифра из остатка 10
    "278343797690",  # 12-я цифра из остатка 10
]


@pytest.mark.parametrize("inn", VALID_INN)
def test_bp05_valid_inn(inn: str) -> None:
    assert rules.validate_inn(inn) == inn
    assert rules.validate_inn(f" {inn} ") == inn


@pytest.mark.parametrize(
    "inn",
    [
        "7707083894",  # неверная контрольная цифра
        "6371292610",
        "7129666881",
        "225821585767",  # 11-я цифра верна, 12-я — нет
        "225821585702",  # 11-я цифра неверна, 12-я посчитана по ней
        "500100732258",
        "399311293717",
    ],
)
def test_bp05_wrong_checksum(inn: str) -> None:
    with pytest.raises(rules.AppError) as error:
        rules.validate_inn(inn)
    assert (error.value.rule, error.value.field) == ("БП-05", "inn")
    assert "контрольной суммы" in error.value.message


@pytest.mark.parametrize(
    "inn", ["770708389", "77070838931", "5001007322591", "77070838ab", "", None, "7707 08389"]
)
def test_bp05_wrong_format(inn: str | None) -> None:
    with pytest.raises(rules.AppError) as error:
        rules.validate_inn(inn)
    assert error.value.rule == "БП-05"
    assert "10 цифр" in error.value.message


def test_bp05_every_digit_matters() -> None:
    """Замена любой цифры в верном ИНН делает его неверным."""
    for inn in ("6371292619", "225821585766"):
        for position in range(len(inn)):
            digit = (int(inn[position]) + 1) % 10
            changed = inn[:position] + str(digit) + inn[position + 1 :]
            assert rule_of(rules.validate_inn, changed) == "БП-05", changed


# ---------------------------------------------------------------------------
# БП-06. Артикул материала
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("sku", "expected"),
    [(" cem-500 ", "CEM-500"), ("ABC", "ABC"), ("A" * 32, "A" * 32), ("123-45", "123-45")],
)
def test_bp06_valid_sku(sku: str, expected: str) -> None:
    assert rules.validate_sku(sku) == expected


@pytest.mark.parametrize("sku", ["AB", "A" * 33, "цемент", "CEM 500", "CEM_500", "", None])
def test_bp06_invalid_sku(sku: str | None) -> None:
    with pytest.raises(rules.AppError) as error:
        rules.validate_sku(sku)
    assert (error.value.rule, error.value.field) == ("БП-06", "sku")


# ---------------------------------------------------------------------------
# БП-07. Материал не повторяется в одном поступлении
# ---------------------------------------------------------------------------
def test_bp07_material_not_repeated() -> None:
    rules.ensure_material_not_repeated({1, 2}, 3)
    rules.ensure_material_not_repeated(set(), 1)
    with pytest.raises(rules.AppError) as error:
        rules.ensure_material_not_repeated({1, 2}, 2)
    assert (error.value.status, error.value.rule) == (409, "БП-07")


# ---------------------------------------------------------------------------
# БП-08. Ошибки целостности базы данных — понятным текстом
# ---------------------------------------------------------------------------
def test_bp08_database_error_is_readable() -> None:
    error = rules.database_error("material_unit_id_fkey")
    assert (error.status, error.code, error.rule) == (409, "CONFLICT", "БП-08")
    assert "используется" in error.message


def test_database_error_keeps_rule_of_constraint() -> None:
    assert rules.database_error("supplier_inn_key").rule == "БП-05"
    assert rules.database_error("app_user_login_key").rule is None


@pytest.mark.parametrize("constraint", [None, "", "unknown_constraint"])
def test_unknown_database_error(constraint: str | None) -> None:
    error = rules.database_error(constraint)
    assert (error.status, error.rule) == (409, None)
    assert "ограничения целостности" in error.message


# ---------------------------------------------------------------------------
# БП-09. Доступ по ролям
# ---------------------------------------------------------------------------
def test_bp09_only_admin() -> None:
    rules.require_admin(SimpleNamespace(role="admin"), "проверка")
    for account in (SimpleNamespace(role="storekeeper"), SimpleNamespace(), None):
        with pytest.raises(rules.AppError) as error:
            rules.require_admin(account, "проверка")
        assert (error.value.status, error.value.rule) == (403, "БП-09")
        assert "«проверка»" in error.value.message


# ---------------------------------------------------------------------------
# БП-10. Пароль и логин
# ---------------------------------------------------------------------------
def test_bp10_password_length() -> None:
    assert rules.validate_password("12345678") == "12345678"  # по умолчанию не короче 8
    assert rule_of(rules.validate_password, "1234567") == "БП-10"
    assert rules.validate_password("1234567890", min_length=10) == "1234567890"
    assert rule_of(rules.validate_password, "123456789", min_length=10) == "БП-10"
    assert rule_of(rules.validate_password, "") == "БП-10"
    assert rule_of(rules.validate_password, None) == "БП-10"


def test_bp10_password_message_names_length() -> None:
    with pytest.raises(rules.AppError) as error:
        rules.validate_password("short", min_length=12)
    assert "12" in error.value.message
    assert error.value.field == "password"


def test_password_is_stored_as_hash() -> None:
    from app.auth import hash_password, verify_password

    plain = "проверочный-пароль"
    stored = hash_password(plain)
    assert plain not in stored
    assert stored.startswith("$argon2")
    assert verify_password(stored, plain)
    assert not verify_password(stored, plain + "!")
    assert not verify_password("не хеш", plain)


@pytest.mark.parametrize(
    ("login", "expected"),
    [
        (" Ivanov ", "ivanov"),
        ("abc", "abc"),
        ("a" * 50, "a" * 50),
        ("p.petrov-2_x", "p.petrov-2_x"),
    ],
)
def test_valid_login(login: str, expected: str) -> None:
    assert rules.validate_login(login) == expected


@pytest.mark.parametrize("login", ["ab", "a" * 51, "иванов", "ivan ov", "ivan@ov", "", None])
def test_invalid_login(login: str | None) -> None:
    with pytest.raises(rules.AppError) as error:
        rules.validate_login(login)
    assert error.value.field == "login"


# ---------------------------------------------------------------------------
# Текстовые поля: обязательность и длина
# ---------------------------------------------------------------------------
def text(value: str | None, **kwargs: object) -> str | None:
    options: dict[str, object] = {"field": "name", "title": "Наименование", "max_length": 5}
    options.update(kwargs)
    return rules.validate_text(value, **options)  # type: ignore[arg-type]  # параметры из словаря


def test_text_boundaries() -> None:
    assert text("ab") == "ab"  # по умолчанию не короче 2 символов
    assert text("  abcde  ") == "abcde"  # пробелы по краям не считаются
    assert text("abc", min_length=3) == "abc"
    for wrong, options in (("a", {}), ("abcdef", {}), ("ab", {"min_length": 3})):
        with pytest.raises(rules.AppError) as error:
            text(wrong, **options)
        assert error.value.field == "name"


def test_text_required_and_optional() -> None:
    for empty in ("", "   ", None):
        with pytest.raises(rules.AppError) as error:
            text(empty)
        assert "обязательно" in error.value.message
        assert text(empty, required=False) is None
    assert text("abc", required=False) == "abc"


def test_text_messages_name_the_limits() -> None:
    with pytest.raises(rules.AppError) as short:
        text("a", min_length=2)
    assert "не менее 2" in short.value.message
    with pytest.raises(rules.AppError) as long:
        text("abcdef")
    assert "не более 5" in long.value.message
