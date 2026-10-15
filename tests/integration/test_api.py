"""HTTP API: операции, правила БП-01 … БП-10 на настоящей базе, ошибки и права доступа."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.integration.conftest import PASSWORD, V1, add_item, login, new_material, new_receipt


def error_of(response: Any) -> dict[str, Any]:
    return response.json()["error"]


# ---------------------------------------------------------------------------
# Служебный адрес и вход
# ---------------------------------------------------------------------------
def test_healthz(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "0.1.0", "database": "ok"}


def test_healthz_without_database(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.db.database_is_available", lambda: False)
    response = client.get("/healthz")
    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
    assert response.json()["database"] == "unavailable"


@pytest.mark.parametrize(
    "path",
    ["/auth/me", "/units", "/units/okei", "/suppliers", "/materials", "/receipts", "/users"],
)
def test_login_required(client: TestClient, path: str) -> None:
    response = client.get(V1 + path)
    assert response.status_code == 401
    assert error_of(response)["code"] == "UNAUTHORIZED"


def test_login_and_me(client: TestClient, data: dict[str, Any]) -> None:
    wrong = client.post(f"{V1}/auth/login", json={"login": "ivanov", "password": "неверный"})
    assert wrong.status_code == 401
    unknown = client.post(f"{V1}/auth/login", json={"login": "nobody", "password": PASSWORD})
    assert unknown.status_code == 401
    assert error_of(unknown)["message"] == error_of(wrong)["message"]  # не подсказывает логины

    response = client.post(f"{V1}/auth/login", json={"login": " IVANOV ", "password": PASSWORD})
    assert response.status_code == 200
    assert response.json()["login"] == "ivanov"
    assert "password_hash" not in response.json()
    assert client.get(f"{V1}/auth/me").json()["role"] == "storekeeper"

    assert client.post(f"{V1}/auth/logout").status_code == 204
    assert client.get(f"{V1}/auth/me").status_code == 401


def test_forged_cookie_is_rejected(client: TestClient, data: dict[str, Any]) -> None:
    client.cookies.set("warehouse_session", "eyJ1aWQiOjF9.forged-signature")
    assert client.get(f"{V1}/auth/me").status_code == 401


def test_session_of_deleted_user_is_invalid(as_admin: TestClient, data: dict[str, Any]) -> None:
    login(as_admin, "ivanov")
    cookie = as_admin.cookies.get("warehouse_session")
    login(as_admin, "admin")
    assert as_admin.delete(f"{V1}/users/{data['storekeeper'].id}").status_code == 204
    as_admin.cookies.clear()
    as_admin.cookies.set("warehouse_session", str(cookie))
    assert as_admin.get(f"{V1}/auth/me").status_code == 401


# ---------------------------------------------------------------------------
# Единицы измерения (БП-01, БП-08, БП-09)
# ---------------------------------------------------------------------------
def test_unit_must_come_from_okei(signed_in: TestClient) -> None:
    denied = signed_in.post(f"{V1}/units", json={"okei_code": "112"})
    assert denied.status_code == 403  # кладовщику справочник ЕИ недоступен (БП-09)
    assert error_of(denied)["rule"] == "БП-09"

    login(signed_in, "admin")
    bad = signed_in.post(f"{V1}/units", json={"okei_code": "мешок"})
    assert bad.status_code == 422
    assert error_of(bad)["rule"] == "БП-01"

    good = signed_in.post(f"{V1}/units", json={"okei_code": "112"})
    assert good.status_code == 201
    assert good.json() == {
        "id": good.json()["id"],
        "code": "л",
        "name": "Литр; кубический дециметр",
        "okei_code": "112",
        "is_integer": False,
    }
    again = signed_in.post(f"{V1}/units", json={"okei_code": "112"})
    assert again.status_code == 409
    assert error_of(again)["rule"] == "БП-01"


def test_unit_lists(signed_in: TestClient) -> None:
    units = signed_in.get(f"{V1}/units").json()
    assert [unit["code"] for unit in units] == ["кг", "шт"]  # упорядочены по обозначению
    okei = signed_in.get(f"{V1}/units/okei").json()
    assert len(okei) == 43
    assert {"okei_code": "796", "code": "шт", "name": "Штука", "is_integer": True} in okei


def test_unit_deletion(as_admin: TestClient, data: dict[str, Any]) -> None:
    new_material(as_admin, data["kg"].id)
    used = as_admin.delete(f"{V1}/units/{data['kg'].id}")
    assert used.status_code == 409
    assert error_of(used)["rule"] == "БП-08"
    assert "(1 шт.)" in error_of(used)["message"]

    assert as_admin.delete(f"{V1}/units/{data['pcs'].id}").status_code == 204
    assert as_admin.delete(f"{V1}/units/{data['pcs'].id}").status_code == 404
    assert [unit["code"] for unit in as_admin.get(f"{V1}/units").json()] == ["кг"]

    login(as_admin, "ivanov")
    assert as_admin.delete(f"{V1}/units/{data['kg'].id}").status_code == 403


# ---------------------------------------------------------------------------
# Поставщики (БП-05, БП-08)
# ---------------------------------------------------------------------------
def test_supplier_inn_checksum(signed_in: TestClient) -> None:
    bad = signed_in.post(f"{V1}/suppliers", json={"name": "ООО «Тест»", "inn": "7707083894"})
    assert bad.status_code == 422
    assert error_of(bad)["rule"] == "БП-05"
    assert error_of(bad)["field"] == "inn"

    good = signed_in.post(f"{V1}/suppliers", json={"name": "ООО «Тест»", "inn": "500100732259"})
    assert good.status_code == 201
    assert good.json()["phone"] is None


def test_supplier_inn_is_unique(signed_in: TestClient, data: dict[str, Any]) -> None:
    response = signed_in.post(f"{V1}/suppliers", json={"name": "Другой", "inn": "7707083893"})
    assert response.status_code == 409
    assert error_of(response)["rule"] == "БП-05"
    # после отказа сеанс работы с базой исправен: следующий запрос выполняется
    assert signed_in.get(f"{V1}/suppliers").status_code == 200


@pytest.mark.parametrize(
    ("payload", "field"),
    [
        ({"name": "АБ", "inn": "500100732259"}, "name"),
        ({"name": "ООО «Тест»", "inn": "500100732259", "phone": "123"}, "phone"),
        ({"name": "  ", "inn": "500100732259"}, "name"),
    ],
)
def test_supplier_fields(signed_in: TestClient, payload: dict[str, str], field: str) -> None:
    response = signed_in.post(f"{V1}/suppliers", json=payload)
    assert response.status_code == 422
    assert error_of(response)["field"] == field


@pytest.mark.parametrize(
    ("path", "field", "accepted", "rejected"),
    [
        ("suppliers", "name", ["ООО", "Н" * 200], ["ОО", "Н" * 201]),
        ("suppliers", "phone", ["12345", "1" * 20], ["1234", "1" * 21]),
        ("materials", "name", ["Лак", "Н" * 200], ["Ла", "Н" * 201]),
        ("users", "full_name", ["Ван", "Ф" * 150], ["Ли", "Ф" * 151]),
    ],
)
def test_text_length_limits(
    as_admin: TestClient,
    data: dict[str, Any],
    path: str,
    field: str,
    accepted: list[str],
    rejected: list[str],
) -> None:
    """Границы длины текстовых полей: на границе — принимается, за ней — отказ 422."""
    numbers = iter(["6371292619", "500100732259", "7129666880", "225821585766"])

    def payload(value: str, index: int) -> dict[str, Any]:
        if path == "suppliers":
            base: dict[str, Any] = {"name": "ООО «Тест»", "inn": next(numbers)}
        elif path == "materials":
            base = {"sku": f"SKU-{index}", "name": "Материал", "unit_id": data["kg"].id}
        else:
            base = {"login": f"user{index}", "full_name": "Пользователь", "role": "storekeeper"}
            base["password"] = "12345678"
        return {**base, field: value}

    for index, value in enumerate(accepted):
        response = as_admin.post(f"{V1}/{path}", json=payload(value, index))
        assert response.status_code == 201, (len(value), response.text)
    for index, value in enumerate(rejected, start=10):
        response = as_admin.post(f"{V1}/{path}", json=payload(value, index))
        assert response.status_code == 422, len(value)
        assert error_of(response)["field"] == field


def test_supplier_update_and_delete(signed_in: TestClient, data: dict[str, Any]) -> None:
    supplier_id = data["supplier"].id
    changed = signed_in.put(
        f"{V1}/suppliers/{supplier_id}",
        json={"name": "ООО «Лютик»", "inn": "500100732259", "phone": "+7 900 000-00-00"},
    )
    assert changed.status_code == 200
    assert changed.json() == {
        "id": supplier_id,
        "name": "ООО «Лютик»",
        "inn": "500100732259",
        "phone": "+7 900 000-00-00",
    }
    missing = signed_in.put(
        f"{V1}/suppliers/9999", json={"name": "ООО «Лютик»", "inn": "500100732259"}
    )
    assert missing.status_code == 404

    signed_in.post(f"{V1}/suppliers", json={"name": "АО «Астра»", "inn": "6371292619"})
    names = [item["name"] for item in signed_in.get(f"{V1}/suppliers").json()]
    assert names == ["АО «Астра»", "ООО «Лютик»"]  # упорядочены по наименованию

    assert signed_in.delete(f"{V1}/suppliers/{supplier_id}").status_code == 204
    assert signed_in.delete(f"{V1}/suppliers/{supplier_id}").status_code == 404


def test_used_supplier_is_not_deleted(signed_in: TestClient, data: dict[str, Any]) -> None:
    new_receipt(signed_in, data["supplier"].id)
    new_receipt(signed_in, data["supplier"].id)
    response = signed_in.delete(f"{V1}/suppliers/{data['supplier'].id}")
    assert response.status_code == 409
    assert error_of(response)["rule"] == "БП-08"
    assert "(2 шт.)" in error_of(response)["message"]


# ---------------------------------------------------------------------------
# Материалы (БП-01, БП-06, БП-08)
# ---------------------------------------------------------------------------
def test_material_crud(signed_in: TestClient, data: dict[str, Any]) -> None:
    material = new_material(signed_in, data["kg"].id, sku=" cem-500 ")
    assert material["sku"] == "CEM-500"  # приводится к верхнему регистру
    assert material["unit_code"] == "кг"

    changed = signed_in.put(
        f"{V1}/materials/{material['id']}",
        json={"sku": "CEM-400", "name": "Цемент М400", "unit_id": data["pcs"].id},
    )
    assert changed.status_code == 200
    assert (changed.json()["sku"], changed.json()["unit_code"]) == ("CEM-400", "шт")

    assert signed_in.delete(f"{V1}/materials/{material['id']}").status_code == 204
    assert signed_in.get(f"{V1}/materials").json() == []
    assert signed_in.delete(f"{V1}/materials/{material['id']}").status_code == 404


def test_material_rules(signed_in: TestClient, data: dict[str, Any]) -> None:
    new_material(signed_in, data["kg"].id)
    duplicate = signed_in.post(
        f"{V1}/materials", json={"sku": "cem-500", "name": "Повтор", "unit_id": data["kg"].id}
    )
    assert duplicate.status_code == 409
    assert error_of(duplicate)["rule"] == "БП-06"

    wrong_sku = signed_in.post(
        f"{V1}/materials", json={"sku": "ЦЕМ", "name": "Цемент", "unit_id": data["kg"].id}
    )
    assert error_of(wrong_sku)["rule"] == "БП-06"

    no_unit = signed_in.post(
        f"{V1}/materials", json={"sku": "BRK-01", "name": "Кирпич", "unit_id": 9999}
    )
    assert no_unit.status_code == 422
    assert error_of(no_unit)["rule"] == "БП-01"

    zero_unit = signed_in.post(
        f"{V1}/materials", json={"sku": "BRK-01", "name": "Кирпич", "unit_id": 0}
    )
    assert error_of(zero_unit)["rule"] == "БП-01"


def test_unknown_field_is_rejected(signed_in: TestClient, data: dict[str, Any]) -> None:
    response = signed_in.post(
        f"{V1}/materials",
        json={"sku": "CEM-500", "name": "Цемент", "unit_id": data["kg"].id, "лишнее": 1},
    )
    assert response.status_code == 422
    assert error_of(response)["code"] == "BAD_REQUEST"
    assert "лишнее" in error_of(response)["message"]


def test_referenced_record_cannot_be_deleted(signed_in: TestClient, data: dict[str, Any]) -> None:
    material = new_material(signed_in, data["kg"].id)
    receipt = new_receipt(signed_in, data["supplier"].id)
    add_item(signed_in, receipt["id"], material["id"])
    response = signed_in.delete(f"{V1}/materials/{material['id']}")
    assert response.status_code == 409
    assert error_of(response)["rule"] == "БП-08"


# ---------------------------------------------------------------------------
# Поступления (БП-02, БП-03, БП-04, БП-07)
# ---------------------------------------------------------------------------
def test_receipt_flow(signed_in: TestClient, data: dict[str, Any]) -> None:
    material = new_material(signed_in, data["kg"].id)
    piece = new_material(signed_in, data["pcs"].id, sku="BRK-01", name="Кирпич")
    receipt = new_receipt(signed_in, data["supplier"].id)
    assert receipt["items"] == [] and receipt["total"] == "0"

    item = add_item(signed_in, receipt["id"], material["id"], "10.5", "345.90")
    assert item.status_code == 201
    assert item.json()["amount"] == "3631.95"
    assert add_item(signed_in, receipt["id"], piece["id"], "4", "12.50").status_code == 201

    again = add_item(signed_in, receipt["id"], material["id"])
    assert again.status_code == 409
    assert error_of(again)["rule"] == "БП-07"

    card = signed_in.get(f"{V1}/receipts/{receipt['id']}").json()
    assert card["total"] == "3681.95"
    assert [line["material_name"] for line in card["items"]] == ["Цемент М500", "Кирпич"]
    assert card["supplier_name"] == data["supplier"].name


@pytest.mark.parametrize(
    ("quantity", "price", "rule"),
    [
        ("0", "10", "БП-02"),
        ("-1", "10", "БП-02"),
        ("1.2345", "10", "БП-02"),
        ("1.5", "10", "БП-02"),  # штучная единица
        ("1", "-0.01", "БП-03"),
        ("1", "1.001", "БП-03"),
    ],
)
def test_item_rules(
    signed_in: TestClient, data: dict[str, Any], quantity: str, price: str, rule: str
) -> None:
    piece = new_material(signed_in, data["pcs"].id, sku="BRK-01", name="Кирпич")
    receipt = new_receipt(signed_in, data["supplier"].id)
    response = add_item(signed_in, receipt["id"], piece["id"], quantity, price)
    assert response.status_code == 422
    assert error_of(response)["rule"] == rule
    assert signed_in.get(f"{V1}/receipts/{receipt['id']}").json()["items"] == []


def test_item_boundaries_are_accepted(signed_in: TestClient, data: dict[str, Any]) -> None:
    material = new_material(signed_in, data["kg"].id)
    receipt = new_receipt(signed_in, data["supplier"].id)
    response = add_item(signed_in, receipt["id"], material["id"], "0.001", "0")
    assert response.status_code == 201
    assert (response.json()["quantity"], response.json()["amount"]) == ("0.001", "0.00")


def test_item_for_unknown_material_or_receipt(signed_in: TestClient, data: dict[str, Any]) -> None:
    material = new_material(signed_in, data["kg"].id)
    receipt = new_receipt(signed_in, data["supplier"].id)
    assert add_item(signed_in, receipt["id"], 9999).status_code == 404
    assert add_item(signed_in, 9999, material["id"]).status_code == 404


@pytest.mark.parametrize(
    "day", [date.today() + timedelta(days=1), date(1999, 12, 31), date(2999, 1, 1)]
)
def test_receipt_date_is_checked(signed_in: TestClient, data: dict[str, Any], day: date) -> None:
    response = signed_in.post(
        f"{V1}/receipts", json={"receipt_date": str(day), "supplier_id": data["supplier"].id}
    )
    assert response.status_code == 422
    assert error_of(response)["rule"] == "БП-04"


def test_receipt_date_boundaries_are_accepted(signed_in: TestClient, data: dict[str, Any]) -> None:
    for day in (date.today(), date(2000, 1, 1)):
        assert new_receipt(signed_in, data["supplier"].id, day)["receipt_date"] == str(day)


def test_receipt_for_unknown_supplier(signed_in: TestClient) -> None:
    response = signed_in.post(
        f"{V1}/receipts", json={"receipt_date": "2026-01-01", "supplier_id": 99}
    )
    assert response.status_code == 404


def test_receipt_list_filters(signed_in: TestClient, data: dict[str, Any]) -> None:
    other = signed_in.post(f"{V1}/suppliers", json={"name": "АО «Астра»", "inn": "6371292619"})
    first = new_receipt(signed_in, data["supplier"].id, "2026-01-10")
    second = new_receipt(signed_in, other.json()["id"], "2026-01-20")
    third = new_receipt(signed_in, data["supplier"].id, "2026-01-31")

    def ids(query: str = "") -> list[int]:
        return [item["id"] for item in signed_in.get(f"{V1}/receipts{query}").json()]

    assert ids() == [third["id"], second["id"], first["id"]]  # новые сверху
    assert ids("?date_from=2026-01-20") == [third["id"], second["id"]]  # граница включена
    assert ids("?date_to=2026-01-20") == [second["id"], first["id"]]
    assert ids("?date_from=2026-01-11&date_to=2026-01-30") == [second["id"]]
    assert ids(f"?supplier_id={data['supplier'].id}") == [third["id"], first["id"]]
    assert signed_in.get(f"{V1}/receipts?supplier_id=0").status_code == 422
    assert "items" not in signed_in.get(f"{V1}/receipts").json()[0]


def test_receipt_and_item_deletion(signed_in: TestClient, data: dict[str, Any]) -> None:
    material = new_material(signed_in, data["kg"].id)
    receipt = new_receipt(signed_in, data["supplier"].id)
    other = new_receipt(signed_in, data["supplier"].id)
    item = add_item(signed_in, receipt["id"], material["id"]).json()

    # строка чужого поступления не найдена
    assert signed_in.delete(f"{V1}/receipts/{other['id']}/items/{item['id']}").status_code == 404
    assert signed_in.delete(f"{V1}/receipts/{receipt['id']}/items/9999").status_code == 404
    assert signed_in.delete(f"{V1}/receipts/{receipt['id']}/items/{item['id']}").status_code == 204
    assert signed_in.get(f"{V1}/receipts/{receipt['id']}").json()["items"] == []

    add_item(signed_in, receipt["id"], material["id"])
    assert signed_in.delete(f"{V1}/receipts/{receipt['id']}").status_code == 204  # со строками
    assert signed_in.get(f"{V1}/receipts/{receipt['id']}").status_code == 404
    assert signed_in.delete(f"{V1}/receipts/{receipt['id']}").status_code == 404


def test_not_found(signed_in: TestClient) -> None:
    response = signed_in.get(f"{V1}/receipts/9999")
    assert response.status_code == 404
    assert error_of(response)["code"] == "NOT_FOUND"


# ---------------------------------------------------------------------------
# Отчёт по поставкам
# ---------------------------------------------------------------------------
def test_report(signed_in: TestClient, data: dict[str, Any]) -> None:
    other = signed_in.post(f"{V1}/suppliers", json={"name": "АО «Астра»", "inn": "6371292619"})
    idle = signed_in.post(
        f"{V1}/suppliers", json={"name": "ИП Без Поставок", "inn": "500100732259"}
    )
    material = new_material(signed_in, data["kg"].id)
    january = new_receipt(signed_in, data["supplier"].id, "2026-01-10")
    add_item(signed_in, january["id"], material["id"], "2", "100")
    february = new_receipt(signed_in, data["supplier"].id, "2026-02-10")
    add_item(signed_in, february["id"], material["id"], "1", "50.50")
    new_receipt(signed_in, other.json()["id"], "2026-02-15")  # поступление без строк

    def report(query: str = "") -> dict[str, Any]:
        response = signed_in.get(f"{V1}/reports/receipts{query}")
        assert response.status_code == 200, response.text
        return response.json()

    def rows(result: dict[str, Any]) -> dict[str, tuple[int, str]]:
        return {row["supplier_name"]: (row["receipts"], row["total"]) for row in result["rows"]}

    full = report()
    assert rows(full) == {
        "АО «Астра»": (1, "0"),  # поступление без строк учтено в числе поступлений
        "ИП Без Поставок": (0, "0"),  # поставщик без поступлений виден с нулями
        "ООО «Ромашка»": (2, "250.50"),
    }
    assert full["total"] == "250.50"
    assert (full["date_from"], full["date_to"]) == (None, None)

    period = report("?date_from=2026-02-10&date_to=2026-02-15")  # границы включены
    assert rows(period)["ООО «Ромашка»"] == (1, "50.50")
    assert rows(period)["АО «Астра»"] == (1, "0")
    assert (period["date_from"], period["date_to"]) == ("2026-02-10", "2026-02-15")
    assert rows(report("?date_to=2026-02-09"))["ООО «Ромашка»"] == (1, "200.00")
    assert rows(report("?date_from=2026-02-16"))["ООО «Ромашка»"] == (0, "0")

    one = report(f"?supplier_id={idle.json()['id']}")
    assert list(rows(one)) == ["ИП Без Поставок"]
    assert one["total"] == "0"


def test_report_period_must_be_ordered(signed_in: TestClient) -> None:
    response = signed_in.get(f"{V1}/reports/receipts?date_from=2026-02-02&date_to=2026-02-01")
    assert response.status_code == 422
    assert error_of(response)["field"] == "date_from"
    same_day = signed_in.get(f"{V1}/reports/receipts?date_from=2026-02-01&date_to=2026-02-01")
    assert same_day.status_code == 200


# ---------------------------------------------------------------------------
# Пользователи (БП-08, БП-09, БП-10)
# ---------------------------------------------------------------------------
def test_users_are_managed_by_admin_only(signed_in: TestClient) -> None:
    assert signed_in.get(f"{V1}/users").status_code == 403
    response = signed_in.post(
        f"{V1}/users",
        json={"login": "petrov", "full_name": "Петров П.П.", "role": "admin", "password": "x" * 8},
    )
    assert response.status_code == 403
    assert error_of(response)["rule"] == "БП-09"


def test_user_creation(as_admin: TestClient) -> None:
    payload = {"login": "Petrov", "full_name": "Петров П.П.", "role": "storekeeper"}
    created = as_admin.post(f"{V1}/users", json={**payload, "password": "12345678"})
    assert created.status_code == 201
    assert created.json()["login"] == "petrov"
    assert [user["login"] for user in as_admin.get(f"{V1}/users").json()] == [
        "admin",
        "ivanov",
        "petrov",
    ]
    login(as_admin, "admin")
    as_admin.cookies.clear()
    entered = as_admin.post(f"{V1}/auth/login", json={"login": "petrov", "password": "12345678"})
    assert entered.status_code == 200

    login(as_admin, "admin")
    duplicate = as_admin.post(f"{V1}/users", json={**payload, "password": "12345678"})
    assert duplicate.status_code == 409


@pytest.mark.parametrize(
    ("changes", "rule", "field"),
    [
        ({"password": "1234567"}, "БП-10", "password"),
        ({"role": "viewer"}, "БП-09", "role"),
        ({"login": "пётр"}, None, "login"),
        ({"full_name": "П."}, None, "full_name"),
    ],
)
def test_user_rules(
    as_admin: TestClient, changes: dict[str, str], rule: str | None, field: str
) -> None:
    payload = {"login": "petrov", "full_name": "Петров П.П.", "role": "storekeeper"}
    payload["password"] = "12345678"
    response = as_admin.post(f"{V1}/users", json={**payload, **changes})
    assert response.status_code == 422
    assert error_of(response).get("rule") == rule
    assert error_of(response)["field"] == field


def test_user_deletion(as_admin: TestClient, data: dict[str, Any]) -> None:
    own = as_admin.delete(f"{V1}/users/{data['admin'].id}")
    assert own.status_code == 409  # собственную учётную запись удалить нельзя

    login(as_admin, "ivanov")
    new_receipt(as_admin, data["supplier"].id)
    login(as_admin, "admin")
    author = as_admin.delete(f"{V1}/users/{data['storekeeper'].id}")
    assert author.status_code == 409
    assert error_of(author)["rule"] == "БП-08"

    created = as_admin.post(
        f"{V1}/users",
        json={"login": "temp", "full_name": "Временный", "role": "admin", "password": "x" * 8},
    ).json()
    assert as_admin.delete(f"{V1}/users/{created['id']}").status_code == 204
    assert as_admin.delete(f"{V1}/users/{created['id']}").status_code == 404

    login(as_admin, "ivanov")
    assert as_admin.delete(f"{V1}/users/{data['admin'].id}").status_code == 403


# ---------------------------------------------------------------------------
# Непредвиденная ошибка
# ---------------------------------------------------------------------------
def test_unexpected_error_is_hidden(
    data: dict[str, Any], monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from app import services
    from app.main import app

    def broken(*args: object, **kwargs: object) -> None:
        raise RuntimeError("подробности сбоя")

    monkeypatch.setattr(services, "list_suppliers", broken)
    with TestClient(app, raise_server_exceptions=False) as client:
        login(client, "ivanov")
        response = client.get(f"{V1}/suppliers")
        assert response.status_code == 500
        assert error_of(response)["code"] == "INTERNAL_ERROR"
        assert "подробности" not in response.text  # пользователю — общее сообщение
        page = client.get("/suppliers")
        assert page.status_code == 500
        assert "Внутренняя ошибка" in page.text
    assert "подробности сбоя" in caplog.text  # подробности — в журнал
