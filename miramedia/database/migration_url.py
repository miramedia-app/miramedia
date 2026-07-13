"""Alembic migration URL resolution (testable without a live database)."""

from __future__ import annotations

import os

from miramedia.config import MiraMediaConfig
from miramedia.database import render_db_url


def resolve_migration_url_raw() -> str:
    """Return the migration DSN before Alembic ConfigParser escaping."""
    explicit = os.environ.get("DATABASE_URL")
    if explicit:
        return explicit
    db_config = MiraMediaConfig().database
    return render_db_url(
        db_config.user,
        db_config.password,
        db_config.host,
        db_config.port,
        db_config.dbname,
        driver="psycopg",
    )


def migration_url_for_alembic_config(raw_url: str | None = None) -> str:
    """Escape ``%`` for Alembic ``set_main_option`` / ConfigParser."""
    url = raw_url if raw_url is not None else resolve_migration_url_raw()
    return url.replace("%", "%%")
