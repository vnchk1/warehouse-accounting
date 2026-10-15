"""Номер накладной — шаг 3 из 3: правило БП-11 в базе данных.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-08

Одна накладная поставщика не оприходуется дважды: пара (поставщик, номер
накладной) уникальна. Номер не пустой и без пробелов по краям. Приложение
проверяет то же самое (rules.validate_invoice_number, services.create_receipt),
а ограничения базы защищают от одновременного ввода и обхода приложения.
"""

from __future__ import annotations

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint("uq_receipt_invoice", "receipt", ["supplier_id", "invoice_number"])
    op.create_check_constraint(
        "ck_receipt_invoice",
        "receipt",
        "invoice_number <> '' AND invoice_number = btrim(invoice_number)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_receipt_invoice", "receipt", type_="check")
    op.drop_constraint("uq_receipt_invoice", "receipt", type_="unique")
