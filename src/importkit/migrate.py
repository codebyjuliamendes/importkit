"""Programmatic Alembic entry points (used by auto-migrate and tests)."""

from __future__ import annotations

from alembic import command
from alembic.config import Config


def alembic_config(database_url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", "importkit:migrations")
    # ConfigParser interpolation would choke on '%' in passwords.
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def upgrade(database_url: str, revision: str = "head") -> None:
    command.upgrade(alembic_config(database_url), revision)


def downgrade(database_url: str, revision: str) -> None:
    command.downgrade(alembic_config(database_url), revision)
