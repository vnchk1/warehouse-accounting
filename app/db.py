"""Подключение к PostgreSQL и управление транзакциями."""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def setup(database_url: str | None = None) -> Engine:
    """Создаёт подключение (вызывается при старте приложения и в тестах)."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = create_engine(database_url or get_settings().database_url, pool_pre_ping=True)
    _session_factory = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def is_configured() -> bool:
    """Подключение уже настроено (в тестах оно задаётся заранее)."""
    return _engine is not None


def engine() -> Engine:
    if _engine is None:
        setup()
    assert _engine is not None
    return _engine


def get_session() -> Iterator[Session]:
    """Зависимость FastAPI: одна транзакция на один запрос.

    При ошибке транзакция откатывается целиком — частично сохранённых
    документов не остаётся.
    """
    if _session_factory is None:
        setup()
    assert _session_factory is not None
    session = _session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def database_is_available() -> bool:
    """Контрольный запрос к базе данных для служебного адреса /healthz."""
    try:
        with engine().connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
