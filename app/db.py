"""Подключение к PostgreSQL и управление транзакциями."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

Connection = tuple[Engine, sessionmaker[Session]]

_connection: Connection | None = None


def setup(database_url: str | None = None) -> Connection:
    """Создаёт подключение (вызывается при старте приложения и в тестах)."""
    global _connection
    if _connection is not None:
        _connection[0].dispose()
    bind = create_engine(database_url or get_settings().database_url, pool_pre_ping=True)
    _connection = (bind, sessionmaker(bind=bind, expire_on_commit=False))
    return _connection


def _current() -> Connection:
    return _connection if _connection is not None else setup()


def is_configured() -> bool:
    """Подключение уже настроено (в тестах оно задаётся заранее)."""
    return _connection is not None


def engine() -> Engine:
    return _current()[0]


@contextmanager
def session_scope() -> Iterator[Session]:
    """Одна транзакция: фиксируется при успехе, откатывается при ошибке.

    При ошибке транзакция откатывается целиком — частично сохранённых
    документов не остаётся. Сеанс закрывается в любом случае.
    """
    session = _current()[1]()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Iterator[Session]:
    """Зависимость FastAPI: одна транзакция на один запрос."""
    with session_scope() as session:
        yield session


def database_is_available() -> bool:
    """Контрольный запрос к базе данных для служебного адреса /healthz."""
    try:
        with engine().connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
