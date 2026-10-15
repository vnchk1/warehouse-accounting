"""Миграции схемы данных: чистая база, база с данными, откат и совпадение с моделями.

Каждый тест работает с отдельной временной базой *_migrations_test, которая
создаётся заново и удаляется после теста, — рабочая и основная тестовая базы
не затрагиваются.
"""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine, make_url

from app import rules
from app.models import Base
from tests.conftest import ROOT
from tests.integration.conftest import alembic, create_database, drop_database

#: Данные в схеме первой версии (0001): так выглядит база, заполненная до изменений
SAMPLE_DATA = """
INSERT INTO unit (code, name, okei_code, is_integer)
    VALUES ('кг', 'Килограмм', '166', false), ('шт', 'Штука', '796', true);
INSERT INTO supplier (name, inn)
    VALUES ('ООО «Ромашка»', '7707083893'), ('АО «Астра»', '6371292619');
INSERT INTO material (sku, name, unit_id) VALUES ('CEM-500', 'Цемент', 1), ('BRK-01', 'Кирпич', 2);
INSERT INTO app_user (login, password_hash, full_name, role)
    VALUES ('admin', 'хеш', 'Администратор', 'admin');
INSERT INTO receipt (receipt_date, supplier_id, created_by)
    VALUES ('2026-01-10', 1, 1), ('2026-01-11', 1, 1), ('2026-01-12', 2, 1);
INSERT INTO receipt_item (receipt_id, material_id, quantity, price)
    VALUES (1, 1, 10.5, 345.90), (1, 2, 100, 12.00), (2, 1, 1, 50.00), (3, 2, 4, 0.01);
"""
TABLES = ("unit", "supplier", "material", "app_user", "receipt", "receipt_item")


def script() -> ScriptDirectory:
    return ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))


def revisions() -> list[str]:
    """Ревизии от первой к последней."""
    return [revision.revision for revision in reversed(list(script().walk_revisions()))]


@pytest.fixture
def scratch(database: str) -> Iterator[str]:
    """Пустая временная база для проверки миграций."""
    url = make_url(database)
    name = (url.database or "").removesuffix("_test") + "_migrations_test"
    scratch_url = url.set(database=name).render_as_string(hide_password=False)
    create_database(scratch_url, recreate=True)
    yield scratch_url
    drop_database(scratch_url)


def current_revision(engine: Engine) -> str | None:
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def counts(engine: Engine) -> dict[str, int]:
    with engine.connect() as connection:
        return {
            table: connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
            for table in TABLES
        }


def test_migrations_form_one_chain() -> None:
    """Последовательные изменения схемы: одна голова, у каждой ревизии один предок."""
    chain = list(script().walk_revisions())
    assert len(script().get_heads()) == 1
    for newer, older in zip(chain, chain[1:], strict=False):
        assert newer.down_revision == older.revision
    assert chain[-1].down_revision is None


def test_clean_database(scratch: str) -> None:
    """Чистая база: миграции создают схему, совпадающую с моделями приложения."""
    alembic(scratch, "upgrade", "head")
    engine = create_engine(scratch)
    try:
        assert current_revision(engine) == revisions()[-1]
        assert set(inspect(engine).get_table_names()) == set(TABLES) | {"alembic_version"}
        with engine.connect() as connection:
            context = MigrationContext.configure(
                connection, opts={"compare_type": True, "compare_server_default": True}
            )
            assert compare_metadata(context, Base.metadata) == []
            constraints = connection.execute(
                text(
                    "SELECT conname FROM pg_constraint "
                    "WHERE connamespace = 'public'::regnamespace AND contype IN ('c', 'u', 'f')"
                )
            ).scalars()
            # у каждого ограничения базы есть понятное пользователю сообщение (БП-08)
            assert set(constraints) <= set(rules.DB_ERRORS)
        assert counts(engine) == dict.fromkeys(TABLES, 0)
    finally:
        engine.dispose()


def test_every_migration_can_be_reverted(scratch: str) -> None:
    """Шаг за шагом вверх до последней версии и обратно до пустой базы."""
    engine = create_engine(scratch)
    try:
        for revision in revisions():
            alembic(scratch, "upgrade", revision)
            assert current_revision(engine) == revision
        for revision in reversed(revisions()[:-1]):
            alembic(scratch, "downgrade", "-1")
            assert current_revision(engine) == revision
        alembic(scratch, "downgrade", "base")
        assert current_revision(engine) is None
        assert set(inspect(engine).get_table_names()) == {"alembic_version"}
        alembic(scratch, "upgrade", "head")  # и снова вверх
        assert current_revision(engine) == revisions()[-1]
    finally:
        engine.dispose()


def test_upgrade_database_with_data(scratch: str) -> None:
    """База, заполненная в первой версии схемы, обновляется без потери данных."""
    alembic(scratch, "upgrade", revisions()[0])
    engine = create_engine(scratch)
    try:
        with engine.begin() as connection:
            connection.execute(text(SAMPLE_DATA))
        before = counts(engine)

        alembic(scratch, "upgrade", "head")
        assert current_revision(engine) == revisions()[-1]
        assert counts(engine) == before
        with engine.connect() as connection:
            amounts = connection.execute(
                text("SELECT amount FROM receipt_item ORDER BY id")
            ).scalars()
            assert list(amounts) == [
                Decimal("3631.95"),
                Decimal("1200.00"),
                Decimal("50.00"),
                Decimal("0.04"),
            ]
    finally:
        engine.dispose()
