"""HTTP API. Общий префикс /api/v1, формат обмена — JSON в кодировке UTF-8."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field

from app import rules, services
from app.auth import CurrentUser, SessionDep, SettingsDep, end_session, start_session
from app.models import Material, Receipt, ReceiptItem, Supplier, Unit, User

router = APIRouter(prefix="/api/v1")


class Schema(BaseModel):
    """Базовая схема: поля, не предусмотренные схемой, отклоняются."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# ---------------------------------------------------------------------------
# Схемы запросов и ответов
# ---------------------------------------------------------------------------
class LoginIn(Schema):
    login: str
    password: str


class UnitIn(Schema):
    okei_code: str = Field(examples=["796"])


class SupplierIn(Schema):
    name: str
    inn: str = Field(examples=["7707083893"])
    phone: str | None = None


class MaterialIn(Schema):
    sku: str = Field(examples=["CEM-500"])
    name: str
    unit_id: int


class ReceiptIn(Schema):
    receipt_date: date
    supplier_id: int


class ItemIn(Schema):
    material_id: int
    quantity: Decimal
    price: Decimal


class UserIn(Schema):
    login: str
    full_name: str
    role: str = Field(examples=["storekeeper"])
    password: str


# ---------------------------------------------------------------------------
# Преобразование моделей в ответы
# ---------------------------------------------------------------------------
def unit_json(unit: Unit) -> dict[str, Any]:
    return {
        "id": unit.id,
        "code": unit.code,
        "name": unit.name,
        "okei_code": unit.okei_code,
        "is_integer": unit.is_integer,
    }


def supplier_json(supplier: Supplier) -> dict[str, Any]:
    return {
        "id": supplier.id,
        "name": supplier.name,
        "inn": supplier.inn,
        "phone": supplier.phone,
    }


def material_json(material: Material) -> dict[str, Any]:
    return {
        "id": material.id,
        "sku": material.sku,
        "name": material.name,
        "unit_id": material.unit_id,
        "unit_code": material.unit.code,
    }


def item_json(item: ReceiptItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "material_id": item.material_id,
        "material_name": item.material.name,
        "unit_code": item.material.unit.code,
        "quantity": format(item.quantity, "f"),
        "price": format(item.price, "f"),
        "amount": format(item.amount, "f"),
    }


def receipt_json(receipt: Receipt, *, with_items: bool = True) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": receipt.id,
        "receipt_date": receipt.receipt_date.isoformat(),
        "supplier_id": receipt.supplier_id,
        "supplier_name": receipt.supplier.name,
        "total": format(receipt.total, "f"),
    }
    if with_items:
        data["items"] = [item_json(item) for item in receipt.items]
    return data


def user_json(user: User) -> dict[str, Any]:
    return {"id": user.id, "login": user.login, "full_name": user.full_name, "role": user.role}


# ---------------------------------------------------------------------------
# Вход в Систему
# ---------------------------------------------------------------------------
@router.post("/auth/login", summary="Вход по логину и паролю")
def login(payload: LoginIn, response: Response, db: SessionDep, settings: SettingsDep) -> Any:
    user = services.authenticate(db, payload.login, payload.password)
    start_session(response, user, settings)
    return user_json(user)


@router.post("/auth/logout", status_code=204, summary="Завершение сеанса")
def logout(user: CurrentUser) -> Response:
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    end_session(response)
    return response


@router.get("/auth/me", summary="Текущий пользователь")
def me(user: CurrentUser) -> Any:
    return user_json(user)


# ---------------------------------------------------------------------------
# Единицы измерения
# ---------------------------------------------------------------------------
@router.get("/units", summary="Справочник единиц измерения")
def get_units(db: SessionDep, user: CurrentUser) -> Any:
    return [unit_json(unit) for unit in services.list_units(db)]


@router.get("/units/okei", summary="Доступные коды ОКЕИ")
def get_okei(user: CurrentUser) -> Any:
    return [
        {"okei_code": code, "code": value[0], "name": value[1], "is_integer": value[2]}
        for code, value in sorted(rules.OKEI.items())
    ]


@router.post("/units", status_code=201, summary="Добавление единицы измерения из ОКЕИ")
def post_unit(payload: UnitIn, db: SessionDep, user: CurrentUser) -> Any:
    return unit_json(services.create_unit(db, user, payload.okei_code))


@router.delete("/units/{unit_id}", status_code=204, summary="Удаление единицы измерения")
def remove_unit(unit_id: int, db: SessionDep, user: CurrentUser) -> Response:
    services.delete_unit(db, user, unit_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Поставщики
# ---------------------------------------------------------------------------
@router.get("/suppliers", summary="Перечень поставщиков")
def get_suppliers(db: SessionDep, user: CurrentUser) -> Any:
    return [supplier_json(item) for item in services.list_suppliers(db)]


@router.post("/suppliers", status_code=201, summary="Добавление поставщика")
def post_supplier(payload: SupplierIn, db: SessionDep, user: CurrentUser) -> Any:
    supplier = services.save_supplier(db, name=payload.name, inn=payload.inn, phone=payload.phone)
    return supplier_json(supplier)


@router.put("/suppliers/{supplier_id}", summary="Изменение поставщика")
def put_supplier(supplier_id: int, payload: SupplierIn, db: SessionDep, user: CurrentUser) -> Any:
    supplier = services.save_supplier(
        db, name=payload.name, inn=payload.inn, phone=payload.phone, supplier_id=supplier_id
    )
    return supplier_json(supplier)


@router.delete("/suppliers/{supplier_id}", status_code=204, summary="Удаление поставщика")
def remove_supplier(supplier_id: int, db: SessionDep, user: CurrentUser) -> Response:
    services.delete_supplier(db, supplier_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Материалы
# ---------------------------------------------------------------------------
@router.get("/materials", summary="Перечень материалов")
def get_materials(db: SessionDep, user: CurrentUser) -> Any:
    return [material_json(item) for item in services.list_materials(db)]


@router.post("/materials", status_code=201, summary="Добавление материала")
def post_material(payload: MaterialIn, db: SessionDep, user: CurrentUser) -> Any:
    material = services.save_material(
        db, sku=payload.sku, name=payload.name, unit_id=payload.unit_id
    )
    return material_json(material)


@router.put("/materials/{material_id}", summary="Изменение материала")
def put_material(material_id: int, payload: MaterialIn, db: SessionDep, user: CurrentUser) -> Any:
    material = services.save_material(
        db,
        sku=payload.sku,
        name=payload.name,
        unit_id=payload.unit_id,
        material_id=material_id,
    )
    return material_json(material)


@router.delete("/materials/{material_id}", status_code=204, summary="Удаление материала")
def remove_material(material_id: int, db: SessionDep, user: CurrentUser) -> Response:
    services.delete_material(db, material_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Поступления
# ---------------------------------------------------------------------------
@router.get("/receipts", summary="Перечень поступлений")
def get_receipts(
    db: SessionDep,
    user: CurrentUser,
    date_from: Annotated[date | None, Query()] = None,
    date_to: Annotated[date | None, Query()] = None,
    supplier_id: Annotated[int | None, Query(ge=1)] = None,
) -> Any:
    receipts = services.list_receipts(
        db, date_from=date_from, date_to=date_to, supplier_id=supplier_id
    )
    return [receipt_json(receipt, with_items=False) for receipt in receipts]


@router.post("/receipts", status_code=201, summary="Создание поступления")
def post_receipt(payload: ReceiptIn, db: SessionDep, user: CurrentUser) -> Any:
    receipt = services.create_receipt(
        db, user, receipt_date=payload.receipt_date, supplier_id=payload.supplier_id
    )
    return receipt_json(receipt)


@router.get("/receipts/{receipt_id}", summary="Поступление со строками")
def get_receipt(receipt_id: int, db: SessionDep, user: CurrentUser) -> Any:
    return receipt_json(services.get_receipt(db, receipt_id))


@router.delete("/receipts/{receipt_id}", status_code=204, summary="Удаление поступления")
def remove_receipt(receipt_id: int, db: SessionDep, user: CurrentUser) -> Response:
    services.delete_receipt(db, receipt_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/receipts/{receipt_id}/items", status_code=201, summary="Добавление строки")
def post_item(receipt_id: int, payload: ItemIn, db: SessionDep, user: CurrentUser) -> Any:
    item = services.add_item(
        db,
        receipt_id=receipt_id,
        material_id=payload.material_id,
        quantity=payload.quantity,
        price=payload.price,
    )
    return item_json(item)


@router.delete("/receipts/{receipt_id}/items/{item_id}", status_code=204, summary="Удаление строки")
def remove_item(receipt_id: int, item_id: int, db: SessionDep, user: CurrentUser) -> Response:
    services.delete_item(db, receipt_id=receipt_id, item_id=item_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Отчёт по поставкам
# ---------------------------------------------------------------------------
@router.get("/reports/receipts", summary="Отчёт по поставкам за период")
def get_report(
    db: SessionDep,
    user: CurrentUser,
    date_from: Annotated[date | None, Query()] = None,
    date_to: Annotated[date | None, Query()] = None,
    supplier_id: Annotated[int | None, Query(ge=1)] = None,
) -> Any:
    report = services.receipts_report(
        db, date_from=date_from, date_to=date_to, supplier_id=supplier_id
    )
    return {
        "date_from": report["date_from"].isoformat() if report["date_from"] else None,
        "date_to": report["date_to"].isoformat() if report["date_to"] else None,
        "rows": [
            {
                "supplier_id": row["supplier_id"],
                "supplier_name": row["supplier_name"],
                "receipts": row["receipts"],
                "total": format(row["total"], "f"),
            }
            for row in report["rows"]
        ],
        "total": format(report["total"], "f"),
    }


# ---------------------------------------------------------------------------
# Пользователи
# ---------------------------------------------------------------------------
@router.get("/users", summary="Перечень учётных записей")
def get_users(db: SessionDep, user: CurrentUser) -> Any:
    return [user_json(item) for item in services.list_users(db, user)]


@router.post("/users", status_code=201, summary="Создание учётной записи")
def post_user(payload: UserIn, db: SessionDep, user: CurrentUser, settings: SettingsDep) -> Any:
    created = services.create_user(
        db,
        user,
        login=payload.login,
        full_name=payload.full_name,
        role=payload.role,
        password=payload.password,
        password_min_length=settings.password_min_length,
    )
    return user_json(created)


@router.delete("/users/{user_id}", status_code=204, summary="Удаление учётной записи")
def remove_user(user_id: int, db: SessionDep, user: CurrentUser) -> Response:
    services.delete_user(db, user, user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
