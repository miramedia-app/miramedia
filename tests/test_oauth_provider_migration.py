"""OAuth provider migration SQL characterization and PostgreSQL integration tests."""

from __future__ import annotations

import importlib.util
import os
import uuid
from pathlib import Path
from typing import Any

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, text

from tests.oauth_test_helpers import KEY_A, KEY_B

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "f3a4b5c6d7e8_normalize_oauth_provider_names.py"
)
_PG_TEST_URL = os.getenv(
    "MIRAMEDIA_PG_TEST_URL",
    "postgresql+psycopg://miramedia:miramedia@127.0.0.1:5433/miramedia",
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "normalize_oauth_provider_names", _MIGRATION_PATH
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _pg_engine():
    try:
        engine = create_engine(_PG_TEST_URL, pool_pre_ping=True)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        pytest.skip(f"PostgreSQL not available at {_PG_TEST_URL}: {exc}")
    return engine


def _run_migration(conn, migration, *, direction: str) -> None:
    ctx = MigrationContext.configure(connection=conn)
    with Operations.context(ctx):
        if direction == "upgrade":
            migration.upgrade()
        else:
            migration.downgrade()


def _create_oauth_tables(conn) -> None:
    conn.execute(text("DROP TABLE IF EXISTS oauth_account CASCADE"))
    conn.execute(text('DROP TABLE IF EXISTS "user" CASCADE'))
    conn.execute(
        text(
            """
            CREATE TABLE "user" (
                id uuid PRIMARY KEY,
                email varchar(320) NOT NULL,
                hashed_password varchar(1024) NOT NULL,
                is_active boolean NOT NULL,
                is_superuser boolean NOT NULL,
                is_verified boolean NOT NULL
            )
            """
        )
    )
    conn.execute(
        text(
            """
            CREATE TABLE oauth_account (
                id uuid PRIMARY KEY,
                user_id uuid NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
                oauth_name varchar(100) NOT NULL,
                account_id varchar(320) NOT NULL,
                account_email varchar(320) NOT NULL,
                access_token varchar(4096) NOT NULL,
                refresh_token varchar(4096),
                expires_at integer
            )
            """
        )
    )


def _insert_user(conn, user_id: uuid.UUID) -> None:
    conn.execute(
        text(
            """
            INSERT INTO "user" (
                id, email, hashed_password, is_active, is_superuser, is_verified
            ) VALUES (
                :id, :email, 'hash', true, false, true
            )
            """
        ),
        {"id": user_id, "email": f"{user_id}@example.com"},
    )


def _insert_oauth(
    conn,
    *,
    row_id: uuid.UUID,
    user_id: uuid.UUID,
    oauth_name: str,
    account_id: str,
) -> None:
    conn.execute(
        text(
            """
            INSERT INTO oauth_account (
                id, user_id, oauth_name, account_id, account_email, access_token
            ) VALUES (
                :id, :user_id, :oauth_name, :account_id, :email, 'token'
            )
            """
        ),
        {
            "id": row_id,
            "user_id": user_id,
            "oauth_name": oauth_name,
            "account_id": account_id,
            "email": f"{account_id}@example.com",
        },
    )


def _fetch_accounts(conn) -> list[dict[str, Any]]:
    rows = conn.execute(
        text(
            """
            SELECT id::text, user_id::text, oauth_name, account_id
            FROM oauth_account
            ORDER BY account_id, oauth_name, id::text
            """
        )
    ).mappings()
    return [dict(row) for row in rows]


@pytest.fixture
def pg_oauth_schema():
    engine = _pg_engine()
    migration = _load_migration()
    conn = engine.connect()
    trans = conn.begin()
    _create_oauth_tables(conn)
    try:
        yield conn, migration, engine
    finally:
        trans.rollback()
        conn.close()
        engine.dispose()


def test_migration_contains_uuid_safe_dedupe_and_exact_pair_preflight() -> None:
    source = _MIGRATION_PATH.read_text(encoding="utf-8")
    assert "ROW_NUMBER() OVER" in source
    assert "ORDER BY id::text ASC" in source
    assert "MIN(id)" not in source
    assert "assert_no_cross_user_provider_account_conflicts" in source
    assert "uq_oauth_account_oauth_name_account_id" in source
    assert 'canonical="oidc"' not in source
    assert "rename_remaining_legacy_rows" not in source


def test_migration_sql_plans_on_postgresql_without_min_uuid() -> None:
    migration = _load_migration()
    engine = _pg_engine()
    with engine.connect() as conn:
        _create_oauth_tables(conn)
        conn.commit()
        ctx = MigrationContext.configure(connection=conn)
        with Operations.context(ctx):
            migration.assert_no_cross_user_provider_account_conflicts(conn)
            migration.delete_duplicate_rows_same_user(conn)
            migration.ensure_unique_oauth_name_account_id(conn)
        conn.rollback()
    engine.dispose()


def test_upgrade_dedupes_exact_pair_same_user_deterministically(
    pg_oauth_schema,
) -> None:
    conn, migration, _engine = pg_oauth_schema
    user_id = uuid.uuid4()
    keep_id = uuid.UUID("00000000-0000-4000-8000-000000000001")
    drop_id = uuid.UUID("00000000-0000-4000-8000-000000000002")
    _insert_user(conn, user_id)
    _insert_oauth(
        conn,
        row_id=keep_id,
        user_id=user_id,
        oauth_name="LegacyA",
        account_id="acct-1",
    )
    _insert_oauth(
        conn,
        row_id=drop_id,
        user_id=user_id,
        oauth_name="LegacyA",
        account_id="acct-1",
    )

    _run_migration(conn, migration, direction="upgrade")

    rows = _fetch_accounts(conn)
    assert len(rows) == 1
    assert rows[0]["id"] == str(keep_id)
    assert rows[0]["oauth_name"] == "LegacyA"


def test_upgrade_allows_same_account_id_across_provider_namespaces(
    pg_oauth_schema,
) -> None:
    conn, migration, _engine = pg_oauth_schema
    user_id = uuid.uuid4()
    _insert_user(conn, user_id)
    _insert_oauth(
        conn,
        row_id=uuid.uuid4(),
        user_id=user_id,
        oauth_name=KEY_A,
        account_id="shared-sub",
    )
    _insert_oauth(
        conn,
        row_id=uuid.uuid4(),
        user_id=user_id,
        oauth_name=KEY_B,
        account_id="shared-sub",
    )

    _run_migration(conn, migration, direction="upgrade")

    rows = _fetch_accounts(conn)
    assert len(rows) == 2
    assert {row["oauth_name"] for row in rows} == {KEY_A, KEY_B}


def test_upgrade_fails_closed_on_cross_user_exact_pair(pg_oauth_schema) -> None:
    conn, migration, _engine = pg_oauth_schema
    user_a = uuid.uuid4()
    user_b = uuid.uuid4()
    _insert_user(conn, user_a)
    _insert_user(conn, user_b)
    _insert_oauth(
        conn,
        row_id=uuid.uuid4(),
        user_id=user_a,
        oauth_name="LegacyA",
        account_id="shared-acct",
    )
    _insert_oauth(
        conn,
        row_id=uuid.uuid4(),
        user_id=user_b,
        oauth_name="LegacyA",
        account_id="shared-acct",
    )

    savepoint = conn.begin_nested()
    with pytest.raises(RuntimeError, match="oauth_name, account_id"):
        _run_migration(conn, migration, direction="upgrade")
    savepoint.rollback()

    rows = _fetch_accounts(conn)
    assert len(rows) == 2
    assert all(row["oauth_name"] == "LegacyA" for row in rows)


def test_upgrade_preserves_legacy_display_name_keys(pg_oauth_schema) -> None:
    conn, migration, _engine = pg_oauth_schema
    user_id = uuid.uuid4()
    _insert_user(conn, user_id)
    _insert_oauth(
        conn,
        row_id=uuid.uuid4(),
        user_id=user_id,
        oauth_name="OldProvider",
        account_id="acct-1",
    )

    _run_migration(conn, migration, direction="upgrade")

    rows = _fetch_accounts(conn)
    assert len(rows) == 1
    assert rows[0]["oauth_name"] == "OldProvider"


def test_upgrade_is_idempotent_and_enforces_unique_index(pg_oauth_schema) -> None:
    conn, migration, _engine = pg_oauth_schema
    user_id = uuid.uuid4()
    _insert_user(conn, user_id)
    _insert_oauth(
        conn,
        row_id=uuid.uuid4(),
        user_id=user_id,
        oauth_name="Legacy",
        account_id="acct-1",
    )

    _run_migration(conn, migration, direction="upgrade")
    _run_migration(conn, migration, direction="upgrade")

    rows = _fetch_accounts(conn)
    assert len(rows) == 1
    assert rows[0]["oauth_name"] == "Legacy"

    with pytest.raises(Exception, match=r"unique|duplicate key"):
        conn.execute(
            text(
                """
                INSERT INTO oauth_account (
                    id, user_id, oauth_name, account_id, account_email, access_token
                ) VALUES (
                    :id, :user_id, 'Legacy', 'acct-1', 'dup@example.com', 'token'
                )
                """
            ),
            {"id": uuid.uuid4(), "user_id": user_id},
        )


def test_downgrade_drops_unique_index(pg_oauth_schema) -> None:
    conn, migration, _engine = pg_oauth_schema
    user_id = uuid.uuid4()
    _insert_user(conn, user_id)
    _insert_oauth(
        conn,
        row_id=uuid.uuid4(),
        user_id=user_id,
        oauth_name="Legacy",
        account_id="acct-1",
    )

    _run_migration(conn, migration, direction="upgrade")
    _run_migration(conn, migration, direction="downgrade")

    exists = conn.execute(
        text(
            """
            SELECT 1
            FROM pg_class
            WHERE relname = 'uq_oauth_account_oauth_name_account_id'
              AND relkind = 'i'
            """
        )
    ).scalar()
    assert exists is None
