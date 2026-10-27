"""Alembic environment; the URL is taken from importkit settings unless preset."""

from __future__ import annotations

from alembic import context
from sqlalchemy import Connection

from importkit.config import get_settings
from importkit.db import create_db_engine
from importkit.models import Base

config = context.config
target_metadata = Base.metadata


def _url() -> str:
    preset = config.get_main_option("sqlalchemy.url")
    return preset or get_settings().database_url


def _run(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=connection.dialect.name == "sqlite",
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        _run(connection)
        return
    engine = create_db_engine(_url())
    try:
        with engine.connect() as conn:
            _run(conn)
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
