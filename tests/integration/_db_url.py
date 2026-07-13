"""Shared helpers for PostgreSQL integration tests."""

from __future__ import annotations

import os
import re
import uuid
from datetime import UTC, datetime
from urllib.parse import urlparse

import pytest
from sqlalchemy.engine.url import make_url

_ASYNCPG_SCHEME = re.compile(r"^postgresql(\+asyncpg)?://", re.IGNORECASE)
_TEST_DB_NAME = re.compile(r"(test|integration)", re.IGNORECASE)


def integration_database_url() -> str:
    """Return a PostgreSQL async URL or fail with setup instructions."""
    raw = os.environ.get("MIRAMEDIA_TEST_DATABASE_URL") or os.environ.get(
        "DATABASE_URL"
    )
    if not raw:
        pytest.fail(
            "PostgreSQL integration tests require MIRAMEDIA_TEST_DATABASE_URL "
            "(preferred) or DATABASE_URL pointing at a disposable test database. "
            "Example: MIRAMEDIA_TEST_DATABASE_URL="
            "postgresql+asyncpg://test:test@127.0.0.1:5432/miramedia_integration_test"
        )
    if not _ASYNCPG_SCHEME.match(raw):
        pytest.fail(
            "Integration database URL must use postgresql:// or "
            "postgresql+asyncpg:// — got a non-PostgreSQL scheme"
        )
    url = make_url(raw)
    if url.drivername == "postgresql":
        url = url.set(drivername="postgresql+asyncpg")
    return url.render_as_string(hide_password=False)


def assert_safe_integration_database(url: str) -> None:
    """Refuse destructive cleanup against non-test database names."""
    if os.environ.get("MIRAMEDIA_INTEGRATION_ALLOW_ANY_DATABASE") == "1":
        return
    dbname = urlparse(url.replace("+asyncpg", "")).path.lstrip("/")
    if not dbname or not _TEST_DB_NAME.search(dbname):
        pytest.fail(
            f"Refusing integration cleanup on database {dbname!r}. "
            "Use a disposable name containing 'test' or 'integration', or set "
            "MIRAMEDIA_INTEGRATION_ALLOW_ANY_DATABASE=1 to opt in explicitly."
        )


def alembic_sync_url(async_url: str) -> str:
    """Convert an asyncpg URL to psycopg for Alembic migrations."""
    url = make_url(async_url)
    return url.set(drivername="postgresql+psycopg").render_as_string(
        hide_password=False
    )


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_uuid() -> uuid.UUID:
    return uuid.uuid4()
