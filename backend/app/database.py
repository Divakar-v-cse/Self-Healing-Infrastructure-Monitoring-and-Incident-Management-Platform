"""Database engine, session management and schema bootstrap."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .config import settings

logger = logging.getLogger(__name__)

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def _prepare_sqlite_path(url: str) -> None:
    if not url.startswith("sqlite"):
        return
    raw = url.split("sqlite:///", 1)[-1]
    if not raw or raw == ":memory:":
        return
    path = Path(raw)
    if not path.is_absolute():
        path = Path.cwd() / path
    path.parent.mkdir(parents=True, exist_ok=True)


def build_engine(url: str | None = None) -> Engine:
    url = url or settings.database_url
    _prepare_sqlite_path(url)
    kwargs: dict = {"pool_pre_ping": True, "future": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    engine = create_engine(url, **kwargs)

    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, _connection_record):  # pragma: no cover
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def configure_database(url: str | None = None) -> Engine:
    """(Re)build the global engine. Used at startup and by the test suite."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = build_engine(url)
    _session_factory = sessionmaker(bind=_engine, autoflush=False, autocommit=False, expire_on_commit=False)
    logger.info("database engine configured")
    return _engine


def get_engine() -> Engine:
    if _engine is None:
        return configure_database()
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    if _session_factory is None:
        configure_database()
    assert _session_factory is not None
    return _session_factory


def get_db() -> Iterator[Session]:
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


def init_db() -> None:
    from . import models  # noqa: F401  (metadata registration)

    models.Base.metadata.create_all(bind=get_engine())


def reset_database() -> None:
    """Drop and recreate every table (tests only)."""
    from . import models  # noqa: F401

    engine = get_engine()
    models.Base.metadata.drop_all(bind=engine)
    models.Base.metadata.create_all(bind=engine)


def session_scope() -> Session:
    """Explicit session for background workers (caller closes)."""
    return get_session_factory()()
