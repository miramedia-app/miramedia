"""Guards for optional PostgreSQL integration tests.

Ordinary ``make test`` must remain DB-free. Integration cases may only run when
``MIRAMEDIA_TEST_DATABASE_URL`` points at an explicitly disposable database.
"""

from __future__ import annotations

import os
import re
import secrets
from collections.abc import Mapping

import pytest
from sqlalchemy.engine import make_url

DISPOSABLE_DATABASE_SKIP_REASON = (
    "PostgreSQL integration tests require MIRAMEDIA_TEST_DATABASE_URL pointing "
    "at a disposable database whose name contains 'test' or 'integration'. "
    "Example: postgresql+psycopg://miramedia:miramedia@127.0.0.1:5433/miramedia_test"
)

_SCHEMA_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


class DisposableDatabaseRejectedError(ValueError):
    """A configured database URL is not an allowed disposable test target."""


def database_name_from_url(url: str) -> str:
    database = make_url(url).database
    if not database:
        msg = "database URL must include a database name"
        raise DisposableDatabaseRejectedError(msg)
    return database


def validate_disposable_database_name(database_name: str) -> None:
    lowered = database_name.lower()
    if "test" in lowered or "integration" in lowered:
        return
    if lowered == "miramedia":
        msg = (
            "refusing PostgreSQL integration tests against the miramedia "
            "application database"
        )
        raise DisposableDatabaseRejectedError(msg)
    msg = (
        f"database name {database_name!r} is not disposable; it must contain "
        "'test' or 'integration'"
    )
    raise DisposableDatabaseRejectedError(msg)


def resolve_disposable_database_url(
    env: Mapping[str, str] | None = None,
) -> str | None:
    """Return a validated disposable URL or None when integration DB is not configured."""
    values = env if env is not None else os.environ
    url = values.get("MIRAMEDIA_TEST_DATABASE_URL", "").strip()
    if not url:
        return None
    validate_disposable_database_name(database_name_from_url(url))
    return url


def require_disposable_database_url(
    env: Mapping[str, str] | None = None,
) -> str:
    """Skip the current test when no disposable integration database is configured."""
    url = resolve_disposable_database_url(env)
    if url is None:
        pytest.skip(DISPOSABLE_DATABASE_SKIP_REASON)
    return url


def new_temporary_schema_name(*, prefix: str) -> str:
    safe_prefix = prefix.strip("_").lower()
    if not safe_prefix or not _SCHEMA_NAME_RE.match(safe_prefix):
        msg = f"invalid temporary schema prefix: {prefix!r}"
        raise ValueError(msg)
    return f"{safe_prefix}_{secrets.token_hex(8)}"


def assert_temporary_schema_name(schema: str, *, prefix: str) -> None:
    safe_prefix = prefix.strip("_").lower()
    if not schema.startswith(f"{safe_prefix}_"):
        msg = f"refusing cleanup for non-test schema {schema!r}"
        raise DisposableDatabaseRejectedError(msg)
    if not _SCHEMA_NAME_RE.match(schema):
        msg = f"invalid temporary schema name {schema!r}"
        raise DisposableDatabaseRejectedError(msg)
