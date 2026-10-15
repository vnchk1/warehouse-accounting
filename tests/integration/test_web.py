"""Графический интерфейс: вход, экранные формы, сообщения об ошибках и права доступа."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.integration.conftest import PASSWORD, V1, add_item, login, new_material, new_receipt


def form(client: TestClient, path: str, **fields: object) -> Any:
    return client.post(path, data={k: str(v) for k, v in fields.items()}, follow_redirects=False)


# ---------------------------------------------------------------------------
# Вход и выход
# ---------------------------------------------------------------------------
def test_web_redirects_anonymous_to_login(client: TestClient) -> None:
    for path in ("/", "/receipts", "/materials", "/report", "/users"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 303, path
        assert response.headers["location"] == "/login"


def test_login_page(client: TestClient, data: dict[str, Any]) -> None:
    assert "Вход" in client.get("/login").text

    wrong = form(client, "/login", login="ivanov", password="неверный")
    assert wrong.status_code == 401
    assert "Неверный логин или пароль" in wrong.text
    assert 'value="ivanov"' in wrong.text  # введённый логин сохраняется

    entered = form(client, "/login", login="ivanov", password=PASSWORD)
    assert (entered.status_code, entered.headers["location"]) == (303, "/")
    assert "warehouse_session" in entered.headers["set-cookie"]
    assert client.get("/", follow_redirects=False).headers["location"] == "/receipts"
    assert client.get("/login", follow_redirects=False).headers["location"] == "/"

    out = form(client, "/logout")
    assert out.headers["location"].startswith("/login?msg=")
    assert client.get("/receipts", follow_redirects=False).status_code == 303


def test_web_pages_are_available(signed_in: TestClient) -> None:
    for path in ("/receipts", "/materials", "/suppliers", "/report"):
        response = signed_in.get(path)
        assert response.status_code == 200, path
        assert "Иванов И.И." in response.text  # шапка с пользователем
    assert "Пользователи" not in signed_in.get("/receipts").text  # пункт только для админа


def test_users_page_is_for_admin_only(signed_in: TestClient) -> None:
    response = signed_in.get("/users")
    assert response.status_code == 403
    assert "доступна только администратору" in response.text
    login(signed_in, "admin")
    page = signed_in.get("/users")
    assert page.status_code == 200
    assert "ivanov" in page.text


def test_error_pages_for_signed_in_user(signed_in: TestClient) -> None:
    """Ошибка, возникшая в обработке запроса, показывается страницей, а не сбоем 500."""
    for path in ("/receipts/9999", "/materials?edit=9999", "/suppliers?edit=9999"):
        response = signed_in.get(path)
        assert response.status_code == 404, path
        assert "не найден" in response.text
        assert "Иванов И.И." in response.text


def test_report_accepts_empty_filters(signed_in: TestClient) -> None:
    """Браузер отправляет незаполненные поля отбора пустой строкой — это не ошибка."""
    for path in (
        "/report?date_from=&date_to=&supplier_id=",
        "/report?date_from=&date_to=&supplier_id=1",
        "/receipts?date_from=&date_to=&supplier_id=",
        "/materials?edit=",
        "/suppliers?edit=",
    ):
        assert signed_in.get(path).status_code == 200, path


# ---------------------------------------------------------------------------
# Поставщики
# ---------------------------------------------------------------------------
def test_supplier_form(signed_in: TestClient, data: dict[str, Any]) -> None:
    created = form(signed_in, "/suppliers", name="АО «Астра»", inn="6371292619", phone="")
    assert created.headers["location"].startswith("/suppliers?msg=")
    page = signed_in.get("/suppliers")
    assert "АО «Астра»" in page.text and "6371292619" in page.text

    wrong = form(signed_in, "/suppliers", name="АО «Вега»", inn="6371292610")
    assert wrong.status_code == 422
    assert "контрольной суммы" in wrong.text
    assert 'value="АО «Вега»"' in wrong.text  # введённые значения сохраняются

    supplier_id = data["supplier"].id
    edit = signed_in.get(f"/suppliers?edit={supplier_id}")
    assert 'value="7707083893"' in edit.text
    changed = form(
        signed_in,
        "/suppliers",
        name="ООО «Лютик»",
        inn="7707083893",
        phone="+7 900 1",
        supplier_id=supplier_id,
    )
    assert changed.status_code == 303
    assert "ООО «Лютик»" in signed_in.get("/suppliers").text


def test_supplier_delete_form(signed_in: TestClient, data: dict[str, Any]) -> None:
    other = signed_in.post(f"{V1}/suppliers", json={"name": "АО «Астра»", "inn": "6371292619"})
    deleted = form(signed_in, f"/suppliers/{other.json()['id']}/delete")
    assert deleted.headers["location"].startswith("/suppliers?msg=")
    assert "АО «Астра»" not in signed_in.get("/suppliers").text

    new_receipt(signed_in, data["supplier"].id)
    used = form(signed_in, f"/suppliers/{data['supplier'].id}/delete")
    assert used.status_code == 422
    assert "нельзя удалить" in used.text


# ---------------------------------------------------------------------------
# Материалы
# ---------------------------------------------------------------------------
def test_material_form(signed_in: TestClient, data: dict[str, Any]) -> None:
    # Единица выбирается кодом ОКЕИ; если её ещё нет в справочнике — она добавляется.
    created = form(signed_in, "/materials", sku="grunt-10", name="Грунтовка", okei_code="112")
    assert created.status_code == 303
    material = signed_in.get(f"{V1}/materials").json()[0]
    assert (material["sku"], material["unit_code"]) == ("GRUNT-10", "л")
    same_unit = form(signed_in, "/materials", sku="LAK-1", name="Лак мебельный", okei_code="112")
    assert same_unit.status_code == 303
    assert [unit["code"] for unit in signed_in.get(f"{V1}/units").json()].count("л") == 1

    edit = signed_in.get(f"/materials?edit={material['id']}")
    assert 'value="GRUNT-10"' in edit.text
    changed = form(
        signed_in,
        "/materials",
        sku="GRUNT-10",
        name="Грунтовка глубокая",
        okei_code="166",
        material_id=material["id"],
    )
    assert changed.status_code == 303
    assert "Грунтовка глубокая" in signed_in.get("/materials").text


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"sku": "GR", "name": "Грунтовка", "okei_code": "112"}, "от 3 до 32 символов"),
        ({"sku": "GRUNT-10", "name": "Грунтовка", "okei_code": "999"}, "кодом ОКЕИ"),
        ({"sku": "GRUNT-10", "name": "Гр", "okei_code": "112"}, "не менее 3"),
        ({"sku": "GRUNT-10", "name": "Грунтовка", "okei_code": ""}, "okei_code"),
    ],
)
def test_material_form_errors(signed_in: TestClient, fields: dict[str, str], message: str) -> None:
    response = form(signed_in, "/materials", **fields)
    assert response.status_code == 422
    assert message in response.text


def test_material_search(signed_in: TestClient, data: dict[str, Any]) -> None:
    """Поиск по артикулу и наименованию.

    Регистр проверяется на латинском артикуле: свёртка регистра кириллицы в
    ILIKE зависит от локали кластера PostgreSQL, опираться на неё нельзя.
    """
    new_material(signed_in, data["kg"].id)
    new_material(signed_in, data["kg"].id, sku="KIR-RED", name="Кирпич рядовой")

    by_name = signed_in.get("/materials?q=Кирпич")
    assert "Кирпич рядовой" in by_name.text
    assert "Цемент М500" not in by_name.text

    by_sku = signed_in.get("/materials?q=cem")
    assert "Цемент М500" in by_sku.text
    assert "Кирпич рядовой" not in by_sku.text

    assert "ничего не найдено" in signed_in.get("/materials?q=отсутствующий").text
    everything = signed_in.get("/materials?q=")
    assert "Цемент М500" in everything.text and "Кирпич рядовой" in everything.text


def test_deleting_used_material_shows_message(signed_in: TestClient, data: dict[str, Any]) -> None:
    material = new_material(signed_in, data["kg"].id)
    receipt = new_receipt(signed_in, data["supplier"].id)
    add_item(signed_in, receipt["id"], material["id"])

    response = form(signed_in, f"/materials/{material['id']}/delete")
    assert response.status_code == 422
    assert "нельзя удалить" in response.text
    assert material["id"] in [item["id"] for item in signed_in.get(f"{V1}/materials").json()]

    unused = new_material(signed_in, data["kg"].id, sku="FREE-1", name="Свободный")
    assert form(signed_in, f"/materials/{unused['id']}/delete").status_code == 303


# ---------------------------------------------------------------------------
# Поступления
# ---------------------------------------------------------------------------
def test_receipt_forms(signed_in: TestClient, data: dict[str, Any]) -> None:
    material = new_material(signed_in, data["kg"].id)
    created = form(
        signed_in, "/receipts", receipt_date=date.today(), supplier_id=data["supplier"].id
    )
    assert created.status_code == 303
    card_url = created.headers["location"].split("?")[0]
    receipt_id = int(card_url.rsplit("/", 1)[1])

    added = form(
        signed_in, f"{card_url}/items", material_id=material["id"], quantity="2,5", price="10,10"
    )
    assert added.status_code == 303
    card = signed_in.get(card_url)
    assert card.status_code == 200
    assert "Цемент М500" in card.text and "25.25" in card.text

    wrong = form(
        signed_in, f"{card_url}/items", material_id=material["id"], quantity="1", price="1"
    )
    assert wrong.status_code == 422
    assert "уже есть в документе" in wrong.text
    no_material = form(signed_in, f"{card_url}/items", material_id="", quantity="1", price="1")
    assert no_material.status_code == 422
    not_chosen = form(signed_in, f"{card_url}/items", material_id="abc", quantity="1", price="1")
    assert not_chosen.status_code == 422
    assert "«Материал»: выберите" in not_chosen.text

    item_id = signed_in.get(f"{V1}/receipts/{receipt_id}").json()["items"][0]["id"]
    assert form(signed_in, f"{card_url}/items/{item_id}/delete").status_code == 303
    missing_item = form(signed_in, f"{card_url}/items/{item_id}/delete")
    assert missing_item.status_code == 422
    assert "не найдена" in missing_item.text

    assert form(signed_in, f"{card_url}/delete").headers["location"].startswith("/receipts?")
    assert signed_in.get(card_url).status_code == 404
    assert form(signed_in, f"{card_url}/delete").status_code == 404  # уже удалено


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"receipt_date": "", "supplier_id": "1"}, "обязательно"),
        ({"receipt_date": "2999-01-01", "supplier_id": "1"}, "позже сегодняшней"),
        ({"receipt_date": "01.01.2026", "supplier_id": "1"}, "ГГГГ-ММ-ДД"),
        ({"receipt_date": "2026-01-01", "supplier_id": ""}, "«Поставщик»: выберите"),
        ({"receipt_date": "2026-01-01", "supplier_id": "99"}, "не найден"),
    ],
)
def test_receipt_form_errors(
    signed_in: TestClient, data: dict[str, Any], fields: dict[str, str], message: str
) -> None:
    response = form(signed_in, "/receipts", **fields)
    assert response.status_code == 422
    assert message in response.text


def test_receipt_list_filters(signed_in: TestClient, data: dict[str, Any]) -> None:
    other = signed_in.post(f"{V1}/suppliers", json={"name": "АО «Астра»", "inn": "6371292619"})
    new_receipt(signed_in, data["supplier"].id, "2026-01-10")
    new_receipt(signed_in, other.json()["id"], "2026-02-10")

    filtered = signed_in.get("/receipts?date_from=2026-02-01&date_to=&supplier_id=")
    assert "10.02.2026" in filtered.text and "10.01.2026" not in filtered.text
    by_supplier = signed_in.get(f"/receipts?supplier_id={data['supplier'].id}")
    assert "10.01.2026" in by_supplier.text and "10.02.2026" not in by_supplier.text
    wrong = signed_in.get("/receipts?date_from=вчера")
    assert wrong.status_code == 422
    assert "ГГГГ-ММ-ДД" in wrong.text


# ---------------------------------------------------------------------------
# Отчёт
# ---------------------------------------------------------------------------
def test_report_page(signed_in: TestClient, data: dict[str, Any]) -> None:
    material = new_material(signed_in, data["kg"].id)
    receipt = new_receipt(signed_in, data["supplier"].id, "2026-01-10")
    add_item(signed_in, receipt["id"], material["id"], "3", "100")

    page = signed_in.get("/report?date_from=2026-01-01&date_to=2026-01-31")
    assert page.status_code == 200
    assert data["supplier"].name in page.text and "300.00" in page.text

    reversed_period = signed_in.get("/report?date_from=2026-02-01&date_to=2026-01-01")
    assert reversed_period.status_code == 422
    assert "позже даты окончания" in reversed_period.text
    assert signed_in.get("/report?date_to=31.01.2026").status_code == 422


def test_report_lists_supplier_without_receipts(
    signed_in: TestClient, data: dict[str, Any]
) -> None:
    report = signed_in.get("/report?date_from=&date_to=&supplier_id=")
    assert report.status_code == 200
    assert data["supplier"].name in report.text


# ---------------------------------------------------------------------------
# Пользователи
# ---------------------------------------------------------------------------
def test_user_forms(as_admin: TestClient, data: dict[str, Any]) -> None:
    created = form(
        as_admin, "/users", login="petrov", full_name="Петров П.П.", role="admin", password="x" * 8
    )
    assert created.headers["location"].startswith("/users?msg=")
    assert "petrov" in as_admin.get("/users").text

    short = form(
        as_admin, "/users", login="sidorov", full_name="Сидоров", role="storekeeper", password="123"
    )
    assert short.status_code == 422
    assert "не менее 8 символов" in short.text
    assert 'value="sidorov"' in short.text

    own = form(as_admin, f"/users/{data['admin'].id}/delete")
    assert own.status_code == 422
    assert "собственную учётную запись" in own.text

    petrov = next(u for u in as_admin.get(f"{V1}/users").json() if u["login"] == "petrov")
    assert form(as_admin, f"/users/{petrov['id']}/delete").status_code == 303
