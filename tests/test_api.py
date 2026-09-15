"""Проверка HTTP API, авторизации и обработки некорректных запросов."""

from __future__ import annotations

from datetime import date

from tests.conftest import PASSWORD

V1 = "/api/v1"


def test_healthz(client) -> None:  # type: ignore[no-untyped-def]
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["database"] == "ok"


def test_login_required(client) -> None:  # type: ignore[no-untyped-def]
    response = client.get(f"{V1}/materials")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_login_and_me(client, data) -> None:  # type: ignore[no-untyped-def]
    assert (
        client.post(
            f"{V1}/auth/login", json={"login": "ivanov", "password": "неверный"}
        ).status_code
        == 401
    )

    response = client.post(f"{V1}/auth/login", json={"login": "ivanov", "password": PASSWORD})
    assert response.status_code == 200
    assert client.get(f"{V1}/auth/me").json()["role"] == "storekeeper"

    assert client.post(f"{V1}/auth/logout").status_code == 204
    assert client.get(f"{V1}/auth/me").status_code == 401


def test_unit_must_come_from_okei(signed_in) -> None:  # type: ignore[no-untyped-def]
    """Правило БП-01 через API: произвольное значение не принимается."""
    bad = signed_in.post(f"{V1}/units", json={"okei_code": "мешок"})
    assert bad.status_code == 403  # кладовщику справочник ЕИ недоступен (БП-09)

    signed_in.post(f"{V1}/auth/logout")
    signed_in.post(f"{V1}/auth/login", json={"login": "admin", "password": PASSWORD})
    bad = signed_in.post(f"{V1}/units", json={"okei_code": "мешок"})
    assert bad.status_code == 422
    assert bad.json()["error"]["rule"] == "БП-01"

    good = signed_in.post(f"{V1}/units", json={"okei_code": "112"})
    assert good.status_code == 201
    assert good.json()["code"] == "л"


def test_supplier_inn_checksum(signed_in) -> None:  # type: ignore[no-untyped-def]
    bad = signed_in.post(f"{V1}/suppliers", json={"name": "ООО «Тест»", "inn": "7707083894"})
    assert bad.status_code == 422
    assert bad.json()["error"]["rule"] == "БП-05"

    good = signed_in.post(f"{V1}/suppliers", json={"name": "ООО «Тест»", "inn": "500100732259"})
    assert good.status_code == 201


def test_unknown_field_is_rejected(signed_in, data) -> None:  # type: ignore[no-untyped-def]
    response = signed_in.post(
        f"{V1}/materials",
        json={"sku": "CEM-500", "name": "Цемент", "unit_id": data["kg"].id, "lишнее": 1},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "BAD_REQUEST"


def test_not_found(signed_in) -> None:  # type: ignore[no-untyped-def]
    response = signed_in.get(f"{V1}/receipts/9999")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_receipt_flow(signed_in, data) -> None:  # type: ignore[no-untyped-def]
    """Создание поступления, строки, отчёт и правила БП-02, БП-07."""
    material = signed_in.post(
        f"{V1}/materials",
        json={"sku": "CEM-500", "name": "Цемент М500", "unit_id": data["kg"].id},
    ).json()
    piece_material = signed_in.post(
        f"{V1}/materials",
        json={"sku": "BRK-01", "name": "Кирпич", "unit_id": data["pcs"].id},
    ).json()

    receipt = signed_in.post(
        f"{V1}/receipts",
        json={"receipt_date": date.today().isoformat(), "supplier_id": data["supplier"].id},
    )
    assert receipt.status_code == 201
    receipt_id = receipt.json()["id"]

    item = signed_in.post(
        f"{V1}/receipts/{receipt_id}/items",
        json={"material_id": material["id"], "quantity": "10.5", "price": "345.90"},
    )
    assert item.status_code == 201
    assert item.json()["amount"] == "3631.95"

    # БП-07: тот же материал второй раз
    again = signed_in.post(
        f"{V1}/receipts/{receipt_id}/items",
        json={"material_id": material["id"], "quantity": "1", "price": "1"},
    )
    assert again.status_code == 409
    assert again.json()["error"]["rule"] == "БП-07"

    # БП-02: дробное количество для штучной единицы измерения
    fractional = signed_in.post(
        f"{V1}/receipts/{receipt_id}/items",
        json={"material_id": piece_material["id"], "quantity": "1.5", "price": "10"},
    )
    assert fractional.status_code == 422
    assert fractional.json()["error"]["rule"] == "БП-02"

    report = signed_in.get(f"{V1}/reports/receipts").json()
    assert report["total"] == "3631.95"
    assert report["rows"][0]["receipts"] == 1


def test_receipt_date_in_future_is_rejected(  # type: ignore[no-untyped-def]
    signed_in, data
) -> None:
    response = signed_in.post(
        f"{V1}/receipts", json={"receipt_date": "2999-01-01", "supplier_id": data["supplier"].id}
    )
    assert response.status_code == 422
    assert response.json()["error"]["rule"] == "БП-04"


def test_referenced_record_cannot_be_deleted(  # type: ignore[no-untyped-def]
    signed_in, data
) -> None:
    """Правило БП-08: единица измерения используется материалом."""
    signed_in.post(
        f"{V1}/materials",
        json={"sku": "CEM-500", "name": "Цемент М500", "unit_id": data["kg"].id},
    )
    signed_in.post(f"{V1}/auth/logout")
    signed_in.post(f"{V1}/auth/login", json={"login": "admin", "password": PASSWORD})

    response = signed_in.delete(f"{V1}/units/{data['kg'].id}")
    assert response.status_code == 409
    assert response.json()["error"]["rule"] == "БП-08"


def test_web_pages_are_available(signed_in) -> None:  # type: ignore[no-untyped-def]
    """Графический интерфейс отвечает на основных страницах."""
    for path in ("/receipts", "/materials", "/suppliers", "/report"):
        assert signed_in.get(path).status_code == 200, path


def test_web_redirects_anonymous_to_login(client) -> None:  # type: ignore[no-untyped-def]
    response = client.get("/receipts", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_report_accepts_empty_filters(signed_in) -> None:  # type: ignore[no-untyped-def]
    """Отчёт по всем поставщикам: браузер отправляет незаполненные поля отбора
    пустой строкой, и это не должно приводить к отказу."""
    for path in (
        "/report?date_from=&date_to=&supplier_id=",
        "/report?date_from=&date_to=&supplier_id=1",
        "/receipts?date_from=&date_to=&supplier_id=",
        "/materials?edit=",
        "/suppliers?edit=",
    ):
        assert signed_in.get(path).status_code == 200, path


def test_report_lists_supplier_without_receipts(  # type: ignore[no-untyped-def]
    signed_in, data
) -> None:
    """Поставщик без поступлений виден в отчёте с нулями, а не пропадает."""
    report = signed_in.get("/report?date_from=&date_to=&supplier_id=")
    assert report.status_code == 200
    assert data["supplier"].name in report.text


def test_deleting_used_material_shows_message(  # type: ignore[no-untyped-def]
    signed_in, data
) -> None:
    """Правило БП-08 в интерфейсе: страница материалов открывается с понятным
    сообщением, а не отвечает внутренней ошибкой."""
    material = signed_in.post(
        f"{V1}/materials",
        json={"sku": "CEM-500", "name": "Цемент М500", "unit_id": data["kg"].id},
    ).json()
    receipt = signed_in.post(
        f"{V1}/receipts",
        json={"receipt_date": "2026-09-01", "supplier_id": data["supplier"].id},
    ).json()
    signed_in.post(
        f"{V1}/receipts/{receipt['id']}/items",
        json={"material_id": material["id"], "quantity": "1", "price": "10"},
    )

    response = signed_in.post(f"/materials/{material['id']}/delete", follow_redirects=False)
    assert response.status_code != 500
    assert "нельзя удалить" in response.text
    # материал остался на месте
    remaining = signed_in.get(f"{V1}/materials").json()
    assert material["id"] in [item["id"] for item in remaining]


def test_deleting_used_supplier_shows_message(  # type: ignore[no-untyped-def]
    signed_in, data
) -> None:
    signed_in.post(
        f"{V1}/receipts",
        json={"receipt_date": "2026-09-01", "supplier_id": data["supplier"].id},
    )
    response = signed_in.post(f"/suppliers/{data['supplier'].id}/delete", follow_redirects=False)
    assert response.status_code != 500
    assert "нельзя удалить" in response.text


def test_material_search(signed_in, data) -> None:  # type: ignore[no-untyped-def]
    """Поиск материалов по артикулу и по наименованию.

    Совпадение по латинскому артикулу проверяется в другом регистре; для
    наименования регистр не меняется, потому что свёртка регистра кириллицы
    зависит от локали кластера PostgreSQL и в тесте на неё опираться нельзя.
    """
    for sku, name in (("CEM-500", "Цемент М500"), ("KIR-RED", "Кирпич рядовой")):
        signed_in.post(
            f"{V1}/materials",
            json={"sku": sku, "name": name, "unit_id": data["kg"].id},
        )

    by_name = signed_in.get("/materials?q=Кирпич")
    assert by_name.status_code == 200
    assert "Кирпич рядовой" in by_name.text
    assert "Цемент М500" not in by_name.text

    by_sku = signed_in.get("/materials?q=cem")  # артикул CEM-500, регистр другой
    assert "Цемент М500" in by_sku.text
    assert "Кирпич рядовой" not in by_sku.text

    nothing = signed_in.get("/materials?q=отсутствующий-материал")
    assert "ничего не найдено" in nothing.text

    everything = signed_in.get("/materials?q=")  # пустой запрос отбор не применяет
    assert "Цемент М500" in everything.text and "Кирпич рядовой" in everything.text
