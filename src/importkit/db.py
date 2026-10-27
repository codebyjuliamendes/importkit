"""Engine and session factory; SQLite gets WAL, FK enforcement and busy timeout."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

WRITE_OPTION = "importkit_write"


def _ensure_sqlite_dir(url: str) -> None:
    database = make_url(url).database
    if database and database != ":memory:" and not database.startswith("file:"):
        Path(database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)


def create_db_engine(url: str) -> Engine:
    if not url.startswith("sqlite"):
        return create_engine(url, pool_pre_ping=True)

    _ensure_sqlite_dir(url)
    engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 15})

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_connection: Any, _record: Any) -> None:
        # Let SQLAlchemy own transaction boundaries (pysqlite's implicit BEGIN is buggy).
        dbapi_connection.isolation_level = None
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=15000")
        cursor.close()

    @event.listens_for(engine, "begin")
    def _on_begin(connection: Any) -> None:
        # Write transactions take the lock up front (BEGIN IMMEDIATE) so concurrent
        # writers queue on busy_timeout instead of failing on a lock upgrade.
        immediate = connection.get_execution_options().get(WRITE_OPTION, False)
        connection.exec_driver_sql("BEGIN IMMEDIATE" if immediate else "BEGIN")

    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    with factory() as session:
        yield session


def begin_write(session: Session) -> None:
    """Start the session's transaction in write mode (no-op outside SQLite)."""
    session.connection(execution_options={WRITE_OPTION: True})
