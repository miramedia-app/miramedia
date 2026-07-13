"""Safety guards for optional PostgreSQL integration tests."""

from __future__ import annotations

import pytest

from tests.pg_disposable import (
    DISPOSABLE_DATABASE_SKIP_REASON,
    DisposableDatabaseRejectedError,
    assert_temporary_schema_name,
    database_name_from_url,
    disposable_database_sync_url,
    new_temporary_schema_name,
    require_disposable_database_url,
    resolve_disposable_database_url,
    validate_disposable_database_name,
)


def test_missing_env_skips_integration_database() -> None:
    with pytest.raises(pytest.skip.Exception, match="MIRAMEDIA_TEST_DATABASE_URL"):
        require_disposable_database_url(env={})


def test_database_url_alone_is_ignored() -> None:
    assert (
        resolve_disposable_database_url(
            env={
                "DATABASE_URL": "postgresql+psycopg://miramedia:miramedia@127.0.0.1:5433/miramedia"
            }
        )
        is None
    )
    with pytest.raises(pytest.skip.Exception, match="MIRAMEDIA_TEST_DATABASE_URL"):
        require_disposable_database_url(
            env={
                "DATABASE_URL": "postgresql+psycopg://miramedia:miramedia@127.0.0.1:5433/miramedia"
            }
        )


def test_rejects_known_miramedia_application_database() -> None:
    with pytest.raises(DisposableDatabaseRejectedError, match="miramedia"):
        validate_disposable_database_name("miramedia")


def test_accepts_disposable_database_names() -> None:
    validate_disposable_database_name("miramedia_test")
    validate_disposable_database_name("miramedia_integration")


def test_resolve_disposable_database_url_validates_explicit_target() -> None:
    url = resolve_disposable_database_url(
        env={
            "MIRAMEDIA_TEST_DATABASE_URL": (
                "postgresql+psycopg://miramedia:miramedia@127.0.0.1:5433/miramedia_test"
            )
        }
    )
    assert url is not None
    assert database_name_from_url(url) == "miramedia_test"


def test_cleanup_only_targets_generated_schema_prefix() -> None:
    schema = new_temporary_schema_name(prefix="oauth_migration_test")
    assert_temporary_schema_name(schema, prefix="oauth_migration_test")
    with pytest.raises(DisposableDatabaseRejectedError, match="refusing cleanup"):
        assert_temporary_schema_name("public", prefix="oauth_migration_test")


def test_skip_reason_is_actionable() -> None:
    assert "MIRAMEDIA_TEST_DATABASE_URL" in DISPOSABLE_DATABASE_SKIP_REASON
    assert "miramedia_test" in DISPOSABLE_DATABASE_SKIP_REASON


def test_disposable_database_sync_url_converts_asyncpg_driver() -> None:
    sync_url = disposable_database_sync_url(
        env={
            "MIRAMEDIA_TEST_DATABASE_URL": (
                "postgresql+asyncpg://test:test@127.0.0.1:55432/miramedia_integration_test"
            )
        }
    )
    assert sync_url.startswith("postgresql+psycopg://")
    assert "miramedia_integration_test" in sync_url
