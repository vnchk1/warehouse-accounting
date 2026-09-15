"""Начальная схема данных.

Revision ID: 0001
Revises:
Create Date: 2026-09-13

Ограничения записаны явно: правила, которые можно выразить средствами СУБД,
проверяются и в приложении, и в базе данных.
"""

from __future__ import annotations

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

TABLES: list[str] = [
    """
    CREATE TABLE unit (
        id          serial PRIMARY KEY,
        code        varchar(20)  NOT NULL UNIQUE,
        name        varchar(100) NOT NULL,
        okei_code   varchar(3)   NOT NULL UNIQUE,
        is_integer  boolean      NOT NULL DEFAULT false,
        CONSTRAINT ck_unit_okei CHECK (okei_code ~ '^[0-9]{3}$')
    )
    """,
    """
    CREATE TABLE supplier (
        id     serial PRIMARY KEY,
        name   varchar(200) NOT NULL,
        inn    varchar(12)  NOT NULL UNIQUE,
        phone  varchar(20),
        CONSTRAINT ck_supplier_inn CHECK (inn ~ '^[0-9]{10}$' OR inn ~ '^[0-9]{12}$')
    )
    """,
    """
    CREATE TABLE material (
        id       serial PRIMARY KEY,
        sku      varchar(32)  NOT NULL UNIQUE,
        name     varchar(200) NOT NULL,
        unit_id  integer      NOT NULL REFERENCES unit (id) ON DELETE RESTRICT,
        CONSTRAINT ck_material_sku CHECK (sku ~ '^[A-Z0-9-]{3,32}$')
    )
    """,
    "CREATE INDEX ix_material_unit ON material (unit_id)",
    """
    CREATE TABLE app_user (
        id             serial PRIMARY KEY,
        login          varchar(50)  NOT NULL UNIQUE,
        password_hash  varchar(255) NOT NULL,
        full_name      varchar(150) NOT NULL,
        role           varchar(20)  NOT NULL,
        CONSTRAINT ck_user_role  CHECK (role IN ('admin', 'storekeeper')),
        CONSTRAINT ck_user_login CHECK (login ~ '^[a-z0-9_.-]{3,50}$')
    )
    """,
    """
    CREATE TABLE receipt (
        id            serial PRIMARY KEY,
        receipt_date  date        NOT NULL,
        supplier_id   integer     NOT NULL REFERENCES supplier (id) ON DELETE RESTRICT,
        created_by    integer     NOT NULL REFERENCES app_user (id) ON DELETE RESTRICT,
        created_at    timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT ck_receipt_date CHECK (receipt_date >= DATE '2000-01-01')
    )
    """,
    "CREATE INDEX ix_receipt_date ON receipt (receipt_date)",
    "CREATE INDEX ix_receipt_supplier ON receipt (supplier_id)",
    """
    CREATE TABLE receipt_item (
        id           serial PRIMARY KEY,
        receipt_id   integer       NOT NULL REFERENCES receipt (id) ON DELETE CASCADE,
        material_id  integer       NOT NULL REFERENCES material (id) ON DELETE RESTRICT,
        quantity     numeric(12,3) NOT NULL,
        price        numeric(12,2) NOT NULL,
        amount       numeric(14,2) GENERATED ALWAYS AS (round(quantity * price, 2)) STORED,
        CONSTRAINT uq_receipt_item     UNIQUE (receipt_id, material_id),
        CONSTRAINT ck_item_quantity    CHECK (quantity > 0),
        CONSTRAINT ck_item_price       CHECK (price >= 0)
    )
    """,
    "CREATE INDEX ix_item_receipt ON receipt_item (receipt_id)",
    "CREATE INDEX ix_item_material ON receipt_item (material_id)",
]

DROPS: list[str] = [
    "DROP TABLE IF EXISTS receipt_item",
    "DROP TABLE IF EXISTS receipt",
    "DROP TABLE IF EXISTS material",
    "DROP TABLE IF EXISTS app_user",
    "DROP TABLE IF EXISTS supplier",
    "DROP TABLE IF EXISTS unit",
]


def upgrade() -> None:
    for statement in TABLES:
        op.execute(statement)


def downgrade() -> None:
    for statement in DROPS:
        op.execute(statement)
