"""Номер накладной поставщика в поступлении — шаг 1 из 3: новый столбец.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-08

Столбец добавляется необязательным: в заполненной базе уже есть поступления
без номера, а обязательный столбец без значения к ним добавить нельзя.
Значения заполняет миграция 0003, правило БП-11 в базе вводит миграция 0004.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("receipt", sa.Column("invoice_number", sa.String(30), nullable=True))


def downgrade() -> None:
    op.drop_column("receipt", "invoice_number")
