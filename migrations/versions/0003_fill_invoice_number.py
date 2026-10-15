"""Номер накладной — шаг 2 из 3: заполнение у существующих поступлений.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-08

Поступлениям, внесённым до появления номера, присваивается условный номер
«БН-<номер поступления>» (без номера). Он заведомо уникален — не нарушит
ограничение из 0004 — и отличим от настоящих номеров. После заполнения
столбец становится обязательным.

Откат возвращает пустые значения только условным номерам: номера, введённые
пользователями после обновления, сохраняются.
"""

from __future__ import annotations

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE receipt SET invoice_number = 'БН-' || id WHERE invoice_number IS NULL")
    op.alter_column("receipt", "invoice_number", nullable=False)


def downgrade() -> None:
    op.alter_column("receipt", "invoice_number", nullable=True)
    op.execute("UPDATE receipt SET invoice_number = NULL WHERE invoice_number = 'БН-' || id")
