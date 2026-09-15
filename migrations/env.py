"""Среда выполнения миграций Alembic."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models import Base  # noqa: E402

target_metadata = Base.metadata


def database_url() -> str:
    """ALEMBIC_DATABASE_URL нужен, чтобы прогонять миграции на тестовой базе."""
    url = os.getenv("ALEMBIC_DATABASE_URL")
    if url:
        return url
    from app.config import get_settings

    return get_settings().database_url


def run() -> None:
    engine = create_engine(database_url())
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    context.configure(url=database_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    run()
