"""Модели данных. Схема создаётся миграцией migrations/versions/0001_initial.py."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

ADMIN = "admin"
STOREKEEPER = "storekeeper"
ROLE_NAMES = {ADMIN: "Администратор", STOREKEEPER: "Кладовщик"}


class Base(DeclarativeBase):
    pass


class Unit(Base):
    """Единица измерения. Берётся из ОКЕИ, произвольное слово недопустимо."""

    __tablename__ = "unit"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    okei_code: Mapped[str] = mapped_column(String(3), unique=True)
    # True для штучных единиц: количество в документах должно быть целым
    is_integer: Mapped[bool] = mapped_column(Boolean, default=False)

    # passive_deletes: удаление проверяет база данных (ondelete=RESTRICT),
    # ссылки на удаляемую запись не обнуляются.
    materials: Mapped[list[Material]] = relationship(back_populates="unit", passive_deletes="all")

    __table_args__ = (CheckConstraint("okei_code ~ '^[0-9]{3}$'", name="ck_unit_okei"),)


class Supplier(Base):
    """Поставщик материалов."""

    __tablename__ = "supplier"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    inn: Mapped[str] = mapped_column(String(12), unique=True)
    phone: Mapped[str | None] = mapped_column(String(20))

    receipts: Mapped[list[Receipt]] = relationship(back_populates="supplier", passive_deletes="all")

    __table_args__ = (
        CheckConstraint("inn ~ '^[0-9]{10}$' OR inn ~ '^[0-9]{12}$'", name="ck_supplier_inn"),
    )


class Material(Base):
    """Материал, учитываемый на складе."""

    __tablename__ = "material"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sku: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    unit_id: Mapped[int] = mapped_column(ForeignKey("unit.id", ondelete="RESTRICT"))

    unit: Mapped[Unit] = relationship(back_populates="materials", lazy="joined")
    items: Mapped[list[ReceiptItem]] = relationship(
        back_populates="material", passive_deletes="all"
    )

    __table_args__ = (CheckConstraint("sku ~ '^[A-Z0-9-]{3,32}$'", name="ck_material_sku"),)


class User(Base):
    """Пользователь Системы. Пароль хранится только в виде хеша."""

    __tablename__ = "app_user"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    login: Mapped[str] = mapped_column(String(50), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(150))
    role: Mapped[str] = mapped_column(String(20))

    __table_args__ = (
        CheckConstraint("role IN ('admin', 'storekeeper')", name="ck_user_role"),
        CheckConstraint("login ~ '^[a-z0-9_.-]{3,50}$'", name="ck_user_login"),
    )

    @property
    def role_name(self) -> str:
        return ROLE_NAMES[self.role]

    @property
    def is_admin(self) -> bool:
        return self.role == ADMIN


class Receipt(Base):
    """Поступление материалов на склад — документ со строками."""

    __tablename__ = "receipt"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    receipt_date: Mapped[date] = mapped_column(Date)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("supplier.id", ondelete="RESTRICT"))
    created_by: Mapped[int] = mapped_column(ForeignKey("app_user.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    supplier: Mapped[Supplier] = relationship(back_populates="receipts", lazy="joined")
    author: Mapped[User] = relationship(lazy="joined")
    items: Mapped[list[ReceiptItem]] = relationship(
        back_populates="receipt", cascade="all, delete-orphan", order_by="ReceiptItem.id"
    )

    __table_args__ = (CheckConstraint("receipt_date >= DATE '2000-01-01'", name="ck_receipt_date"),)

    @property
    def total(self) -> Decimal:
        return sum((item.amount or Decimal("0") for item in self.items), Decimal("0"))


class ReceiptItem(Base):
    """Строка поступления. Сумма вычисляется базой данных."""

    __tablename__ = "receipt_item"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    receipt_id: Mapped[int] = mapped_column(ForeignKey("receipt.id", ondelete="CASCADE"))
    material_id: Mapped[int] = mapped_column(ForeignKey("material.id", ondelete="RESTRICT"))
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    amount: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), Computed("round(quantity * price, 2)", persisted=True)
    )

    receipt: Mapped[Receipt] = relationship(back_populates="items")
    material: Mapped[Material] = relationship(back_populates="items", lazy="joined")

    __table_args__ = (
        UniqueConstraint("receipt_id", "material_id", name="uq_receipt_item"),
        CheckConstraint("quantity > 0", name="ck_item_quantity"),
        CheckConstraint("price >= 0", name="ck_item_price"),
    )
