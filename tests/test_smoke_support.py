"""DB-free tests for smoke harness helpers."""

from __future__ import annotations

import pytest

from miramedia.smoke.database import (
    admin_database_url,
    alembic_sync_url,
    assert_safe_smoke_parent_database,
    database_url_with_name,
    smoke_database_name,
)
from miramedia.smoke.health import SmokeStackReadyTimeoutError


def test_smoke_database_name_is_uniqueish() -> None:
    first = smoke_database_name("abc")
    second = smoke_database_name("def")
    assert first.startswith("miramedia_smoke_")
    assert first != second


def test_admin_database_url_targets_postgres_catalog() -> None:
    url = admin_database_url(
        "postgresql://test:secret@localhost:5432/miramedia_integration_test"
    )
    assert url.endswith("/postgres")
    assert "test:secret@localhost:5432" in url


def test_database_url_with_name_replaces_dbname() -> None:
    parent = "postgresql://test:secret@localhost:5432/miramedia_integration_test"
    child = database_url_with_name(parent, "miramedia_smoke_deadbeef")
    assert child.endswith("/miramedia_smoke_deadbeef")


def test_alembic_sync_url_uses_psycopg_driver() -> None:
    async_url = "postgresql+asyncpg://test:secret@localhost:5432/miramedia_smoke_x"
    assert alembic_sync_url(async_url).startswith("postgresql+psycopg://")


def test_assert_safe_smoke_parent_database_rejects_production_names() -> None:
    with pytest.raises(ValueError, match="Refusing smoke database creation"):
        assert_safe_smoke_parent_database(
            "postgresql://user:pass@localhost:5432/miramedia"
        )


def test_assert_safe_smoke_parent_database_allows_test_names() -> None:
    assert_safe_smoke_parent_database(
        "postgresql://test:secret@localhost:5432/miramedia_integration_test"
    )


def test_smoke_stack_ready_timeout_includes_base_url() -> None:
    err = SmokeStackReadyTimeoutError(
        "http://localhost:43219", RuntimeError("connection refused")
    )
    message = str(err)
    assert "http://localhost:43219" in message
    assert "connection refused" in message
