"""Операции предметной области.

Этот слой используют и графический интерфейс, и HTTP API, поэтому правила
проверяются одинаково независимо от способа обращения к Системе.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, selectinload

from app import rules
from app.auth import hash_password, verify_password
from app.models import Material, Receipt, ReceiptItem, Supplier, Unit, User


def _save(db: Session) -> None:
    """Сброс изменений в базу данных с переводом ошибок целостности (БП-08).

    После отказа транзакция откатывается: иначе любой следующий запрос к базе
    данных в этом же обращении завершился бы отказом, и вместо понятного
    сообщения пользователь увидел бы страницу внутренней ошибки.
    """
    try:
        db.flush()
    except IntegrityError as error:
        constraint = getattr(getattr(error.orig, "diag", None), "constraint_name", None)
        db.rollback()
        raise rules.database_error(constraint) from error
    except SQLAlchemyError:
        # Причина не в правилах предметной области: откатываем транзакцию,
        # чтобы сеанс работы с базой данных остался пригодным, и передаём
        # ошибку дальше — её запишет в журнал обработчик в app/main.py.
        db.rollback()
        raise


def _used_by(db: Session, model: type, column: Any, value: int) -> int:
    """Сколько записей ссылается на запись справочника (БП-08)."""
    return int(
        db.execute(select(func.count()).select_from(model).where(column == value)).scalar_one()
    )


def _get(db: Session, model: type, obj_id: int, title: str) -> Any:
    obj = db.get(model, obj_id)
    if obj is None:
        raise rules.not_found(f"{title} № {obj_id} не найден")
    return obj


# ---------------------------------------------------------------------------
# Вход в Систему
# ---------------------------------------------------------------------------
def authenticate(db: Session, login: str, password: str) -> User:
    user = db.execute(
        select(User).where(User.login == (login or "").strip().lower())
    ).scalar_one_or_none()
    if user is None or not verify_password(user.password_hash, password or ""):
        raise rules.unauthorized("Неверный логин или пароль")
    return user


# ---------------------------------------------------------------------------
# Единицы измерения (БП-01)
# ---------------------------------------------------------------------------
def list_units(db: Session) -> list[Unit]:
    return list(db.execute(select(Unit).order_by(Unit.code)).scalars())


def create_unit(db: Session, user: User, okei_code: str) -> Unit:
    rules.require_admin(user, "ведение справочника единиц измерения")
    code_okei, code, name, is_integer = rules.validate_okei(okei_code)
    unit = Unit(code=code, name=name, okei_code=code_okei, is_integer=is_integer)
    db.add(unit)
    _save(db)
    return unit


def delete_unit(db: Session, user: User, unit_id: int) -> None:
    rules.require_admin(user, "удаление единицы измерения")
    unit = _get(db, Unit, unit_id, "Единица измерения")
    used = _used_by(db, Material, Material.unit_id, unit.id)
    if used:
        raise rules.conflict(
            f"Единицу измерения «{unit.code}» нельзя удалить: она указана "
            f"у материалов ({used} шт.)",
            rule="БП-08",
        )
    db.delete(unit)
    _save(db)


# ---------------------------------------------------------------------------
# Поставщики (БП-05)
# ---------------------------------------------------------------------------
def list_suppliers(db: Session) -> list[Supplier]:
    return list(db.execute(select(Supplier).order_by(Supplier.name)).scalars())


def get_supplier(db: Session, supplier_id: int) -> Supplier:
    return _get(db, Supplier, supplier_id, "Поставщик")


def save_supplier(
    db: Session,
    *,
    name: str,
    inn: str,
    phone: str | None = None,
    supplier_id: int | None = None,
) -> Supplier:
    supplier = get_supplier(db, supplier_id) if supplier_id else Supplier()
    supplier.name = str(
        rules.validate_text(name, field="name", title="Наименование", max_length=200, min_length=3)
    )
    supplier.inn = rules.validate_inn(inn)
    supplier.phone = rules.validate_text(
        phone, field="phone", title="Телефон", max_length=20, min_length=5, required=False
    )
    if supplier_id is None:
        db.add(supplier)
    _save(db)
    return supplier


def delete_supplier(db: Session, supplier_id: int) -> None:
    supplier = get_supplier(db, supplier_id)
    used = _used_by(db, Receipt, Receipt.supplier_id, supplier.id)
    if used:
        raise rules.conflict(
            f"Поставщика «{supplier.name}» нельзя удалить: на него ссылаются "
            f"поступления ({used} шт.). Сначала удалите эти поступления",
            rule="БП-08",
        )
    db.delete(supplier)
    _save(db)


# ---------------------------------------------------------------------------
# Материалы (БП-06)
# ---------------------------------------------------------------------------
def list_materials(db: Session) -> list[Material]:
    return list(
        db.execute(
            select(Material).options(selectinload(Material.unit)).order_by(Material.name)
        ).scalars()
    )


def get_material(db: Session, material_id: int) -> Material:
    return _get(db, Material, material_id, "Материал")


def save_material(
    db: Session,
    *,
    sku: str,
    name: str,
    okei_code: str = "",
    unit_id: int | None = None,
    material_id: int | None = None,
) -> Material:
    """Сохранить материал. Можно передать либо okei_code (для GUI), либо unit_id (для API)."""
    material = get_material(db, material_id) if material_id else Material()
    material.sku = rules.validate_sku(sku)
    material.name = str(
        rules.validate_text(name, field="name", title="Наименование", max_length=200, min_length=3)
    )

    # Если передан okei_code (из GUI), найти или создать единицу измерения
    if okei_code:
        code_okei, code, unit_name, is_integer = rules.validate_okei(okei_code)
        unit = db.execute(select(Unit).where(Unit.okei_code == code_okei)).scalar_one_or_none()
        if unit is None:
            unit = Unit(code=code, name=unit_name, okei_code=code_okei, is_integer=is_integer)
            db.add(unit)
            db.flush()
        material.unit_id = unit.id
    elif unit_id:
        # Если передан unit_id (из API)
        unit = db.get(Unit, unit_id)
        if unit is None:
            raise rules.AppError("Единица измерения не найдена", rule="БП-01", field="unit_id")
        material.unit_id = unit.id
    else:
        raise rules.AppError("Единица измерения обязательна", rule="БП-01", field="okei_code")

    if material_id is None:
        db.add(material)
    _save(db)
    return material


def delete_material(db: Session, material_id: int) -> None:
    material = get_material(db, material_id)
    used = _used_by(db, ReceiptItem, ReceiptItem.material_id, material.id)
    if used:
        raise rules.conflict(
            f"Материал «{material.name}» нельзя удалить: он указан в строках "
            f"поступлений ({used} шт.). Сначала удалите эти строки",
            rule="БП-08",
        )
    db.delete(material)
    _save(db)


# ---------------------------------------------------------------------------
# Поступления (БП-02, БП-03, БП-04, БП-07)
# ---------------------------------------------------------------------------
def list_receipts(
    db: Session,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    supplier_id: int | None = None,
) -> list[Receipt]:
    statement = (
        select(Receipt)
        .options(selectinload(Receipt.items), selectinload(Receipt.supplier))
        .order_by(Receipt.receipt_date.desc(), Receipt.id.desc())
    )
    if date_from:
        statement = statement.where(Receipt.receipt_date >= date_from)
    if date_to:
        statement = statement.where(Receipt.receipt_date <= date_to)
    if supplier_id:
        statement = statement.where(Receipt.supplier_id == supplier_id)
    return list(db.execute(statement).unique().scalars())


def get_receipt(db: Session, receipt_id: int) -> Receipt:
    receipt = (
        db.execute(
            select(Receipt)
            .options(selectinload(Receipt.items).selectinload(ReceiptItem.material))
            .where(Receipt.id == receipt_id)
        )
        .unique()
        .scalar_one_or_none()
    )
    if receipt is None:
        raise rules.not_found(f"Поступление № {receipt_id} не найдено")
    return receipt


def create_receipt(db: Session, user: User, *, receipt_date: date, supplier_id: int) -> Receipt:
    supplier = get_supplier(db, supplier_id)
    receipt = Receipt(
        receipt_date=rules.validate_receipt_date(receipt_date),
        supplier_id=supplier.id,
        created_by=user.id,
    )
    db.add(receipt)
    _save(db)
    return receipt


def delete_receipt(db: Session, receipt_id: int) -> None:
    """Удаление поступления вместе со строками."""
    db.delete(get_receipt(db, receipt_id))
    _save(db)


def add_item(
    db: Session, *, receipt_id: int, material_id: int, quantity: Any, price: Any
) -> ReceiptItem:
    receipt = get_receipt(db, receipt_id)
    material = get_material(db, material_id)
    rules.ensure_material_not_repeated({item.material_id for item in receipt.items}, material.id)
    item = ReceiptItem(
        receipt_id=receipt.id,
        material_id=material.id,
        quantity=rules.validate_quantity(
            quantity, is_integer=material.unit.is_integer, unit_code=material.unit.code
        ),
        price=rules.validate_price(price),
    )
    db.add(item)
    _save(db)
    db.refresh(item)
    return item


def delete_item(db: Session, *, receipt_id: int, item_id: int) -> None:
    item = db.get(ReceiptItem, item_id)
    if item is None or item.receipt_id != receipt_id:
        raise rules.not_found(f"Строка № {item_id} не найдена")
    db.delete(item)
    _save(db)


# ---------------------------------------------------------------------------
# Отчёт по поставкам
# ---------------------------------------------------------------------------
def receipts_report(
    db: Session,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    supplier_id: int | None = None,
) -> dict[str, Any]:
    """Свод по поставщикам за период: количество поступлений и сумма.

    Отбор по периоду задаётся условием присоединения, а не условием выборки:
    иначе из отчёта пропали бы поставщики, у которых за период поступлений не
    было, — а в отчёте «по всем поставщикам» они должны быть видны с нулями.
    Присоединение внешнее по той же причине, а также чтобы поступление без
    строк попадало в число поступлений.
    """
    if date_from and date_to and date_from > date_to:
        raise rules.AppError("Дата начала периода позже даты окончания", field="date_from")

    period = [Receipt.supplier_id == Supplier.id]
    if date_from:
        period.append(Receipt.receipt_date >= date_from)
    if date_to:
        period.append(Receipt.receipt_date <= date_to)

    statement = (
        select(
            Supplier.id,
            Supplier.name,
            func.count(func.distinct(Receipt.id)),
            func.coalesce(func.sum(ReceiptItem.amount), 0),
        )
        .select_from(Supplier)
        .outerjoin(Receipt, and_(*period))
        .outerjoin(ReceiptItem, ReceiptItem.receipt_id == Receipt.id)
        .group_by(Supplier.id, Supplier.name)
        .order_by(Supplier.name)
    )
    if supplier_id:
        statement = statement.where(Supplier.id == supplier_id)

    rows = [
        {
            "supplier_id": row[0],
            "supplier_name": row[1],
            "receipts": int(row[2] or 0),
            "total": Decimal(str(row[3] or 0)),
        }
        for row in db.execute(statement).all()
    ]
    return {
        "date_from": date_from,
        "date_to": date_to,
        "rows": rows,
        "total": sum((row["total"] for row in rows), Decimal("0")),
    }


# ---------------------------------------------------------------------------
# Пользователи (БП-09, БП-10)
# ---------------------------------------------------------------------------
def list_users(db: Session, user: User) -> list[User]:
    rules.require_admin(user, "просмотр списка пользователей")
    return list(db.execute(select(User).order_by(User.login)).scalars())


def create_user(
    db: Session,
    actor: User,
    *,
    login: str,
    full_name: str,
    role: str,
    password: str,
    password_min_length: int = 8,
) -> User:
    rules.require_admin(actor, "создание учётной записи")
    if role not in ("admin", "storekeeper"):
        raise rules.AppError("Роль: администратор или кладовщик", rule="БП-09", field="role")
    user = User(
        login=rules.validate_login(login),
        full_name=str(
            rules.validate_text(
                full_name, field="full_name", title="ФИО", max_length=150, min_length=3
            )
        ),
        role=role,
        password_hash=hash_password(
            rules.validate_password(password, min_length=password_min_length)
        ),
    )
    db.add(user)
    _save(db)
    return user


def delete_user(db: Session, actor: User, user_id: int) -> None:
    rules.require_admin(actor, "удаление учётной записи")
    if actor.id == user_id:
        raise rules.conflict("Нельзя удалить собственную учётную запись")
    user = _get(db, User, user_id, "Пользователь")
    used = _used_by(db, Receipt, Receipt.created_by, user.id)
    if used:
        raise rules.conflict(
            f"Учётную запись «{user.login}» нельзя удалить: она указана автором "
            f"поступлений ({used} шт.)",
            rule="БП-08",
        )
    db.delete(user)
    _save(db)
