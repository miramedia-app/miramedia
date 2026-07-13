"""OAuth provider migration SQL characterization and PostgreSQL integration tests."""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path
from typing import Any

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, text

from tests.oauth_test_helpers import KEY_A, KEY_B
from tests.pg_disposable import (
    assert_temporary_schema_name,
    disposable_database_sync_url,
    new_temporary_schema_name,
)

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "f3a4b5c6d7e8_normalize_oauth_provider_names.py"
)
_SCHEMA_PREFIX = "oauth_migration_test"
pytestmark = pytest.mark.postgresql


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
    url = disposable_database_sync_url()
    engine = create_engine(url, pool_pre_ping=True)
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return engine


def _set_search_path(conn, schema: str) -> None:
    conn.execute(text(f'SET search_path TO "{schema}"'))


def _run_migration(conn, migration, *, direction: str) -> None:
    ctx = MigrationContext.configure(connection=conn)
    with Operations.context(ctx):
        if direction == "upgrade":
            migration.upgrade()
        else:
            migration.downgrade()


def _create_oauth_tables(conn) -> None:
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


def _create_same_name_index(conn, ddl: str) -> None:
    conn.execute(text(ddl))


def _set_index_catalog_flag(
    conn,
    *,
    flag: str,
    value: bool,
) -> None:
    if flag == "indisvalid":
        stmt = text(
            """
            UPDATE pg_index AS idx
            SET indisvalid = :value
            FROM pg_class AS ix
            JOIN pg_namespace AS n ON n.oid = ix.relnamespace
            WHERE idx.indexrelid = ix.oid
              AND ix.relname = :index_name
              AND n.nspname = current_schema()
            """
        )
    elif flag == "indisready":
        stmt = text(
            """
            UPDATE pg_index AS idx
            SET indisready = :value
            FROM pg_class AS ix
            JOIN pg_namespace AS n ON n.oid = ix.relnamespace
            WHERE idx.indexrelid = ix.oid
              AND ix.relname = :index_name
              AND n.nspname = current_schema()
            """
        )
    else:
        msg = f"unsupported pg_index flag: {flag}"
        raise ValueError(msg)
    conn.execute(
        stmt,
        {"index_name": "uq_oauth_account_oauth_name_account_id", "value": value},
    )


def _assert_upgrade_rejects_incompatible_index(
    conn, migration, *, pattern: str
) -> None:
    savepoint = conn.begin_nested()
    with pytest.raises(RuntimeError, match=pattern):
        migration.ensure_unique_oauth_name_account_id(conn)
    savepoint.rollback()


@pytest.fixture
def pg_oauth_schema():
    engine = _pg_engine()
    migration = _load_migration()
    schema = new_temporary_schema_name(prefix=_SCHEMA_PREFIX)
    conn = engine.connect()
    conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    _set_search_path(conn, schema)
    _create_oauth_tables(conn)
    conn.commit()
    try:
        yield conn, migration, engine, schema
    finally:
        conn.close()
        with engine.connect() as cleanup:
            assert_temporary_schema_name(schema, prefix=_SCHEMA_PREFIX)
            cleanup.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            cleanup.commit()
        engine.dispose()


def test_migration_contains_uuid_safe_dedupe_and_exact_pair_preflight() -> None:
    source = _MIGRATION_PATH.read_text(encoding="utf-8")
    assert "ROW_NUMBER() OVER" in source
    assert "ORDER BY id::text ASC" in source
    assert "MIN(id)" not in source
    assert "assert_no_cross_user_provider_account_conflicts" in source
    assert "_oauth_account_unique_index_columns" in source
    assert "_assert_compatible_oauth_unique_index" in source
    assert "indisvalid" in source
    assert "indisready" in source
    assert "indpred" in source
    assert "indexprs" in source
    assert "uq_oauth_account_oauth_name_account_id" in source
    assert 'canonical="oidc"' not in source
    assert "rename_remaining_legacy_rows" not in source
    assert "DROP TABLE IF EXISTS oauth_account" not in source


def test_migration_sql_plans_on_postgresql_without_min_uuid() -> None:
    migration = _load_migration()
    engine = _pg_engine()
    schema = new_temporary_schema_name(prefix=_SCHEMA_PREFIX)
    with engine.connect() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        _set_search_path(conn, schema)
        _create_oauth_tables(conn)
        conn.commit()
        ctx = MigrationContext.configure(connection=conn)
        with Operations.context(ctx):
            migration.assert_no_cross_user_provider_account_conflicts(conn)
            migration.delete_duplicate_rows_same_user(conn)
            migration.ensure_unique_oauth_name_account_id(conn)
    with engine.connect() as cleanup:
        assert_temporary_schema_name(schema, prefix=_SCHEMA_PREFIX)
        cleanup.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        cleanup.commit()
    engine.dispose()


def test_upgrade_dedupes_exact_pair_same_user_deterministically(
    pg_oauth_schema,
) -> None:
    conn, migration, _engine, _schema = pg_oauth_schema
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
    conn, migration, _engine, _schema = pg_oauth_schema
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
    conn, migration, _engine, _schema = pg_oauth_schema
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
    conn, migration, _engine, _schema = pg_oauth_schema
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
    conn, migration, _engine, _schema = pg_oauth_schema
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
    conn, migration, _engine, _schema = pg_oauth_schema
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

    columns = migration._oauth_account_unique_index_columns(conn)
    assert columns is None


def test_upgrade_rejects_partial_same_name_index(pg_oauth_schema) -> None:
    conn, migration, _engine, _schema = pg_oauth_schema
    user_id = uuid.uuid4()
    _insert_user(conn, user_id)
    _insert_oauth(
        conn,
        row_id=uuid.uuid4(),
        user_id=user_id,
        oauth_name="OnlyProtected",
        account_id="acct-protected",
    )
    _create_same_name_index(
        conn,
        """
        CREATE UNIQUE INDEX uq_oauth_account_oauth_name_account_id
        ON oauth_account (oauth_name, account_id)
        WHERE oauth_name = 'OnlyProtected'
        """,
    )
    _assert_upgrade_rejects_incompatible_index(conn, migration, pattern="partial")

    _insert_oauth(
        conn,
        row_id=uuid.uuid4(),
        user_id=user_id,
        oauth_name="Unprotected",
        account_id="acct-dup",
    )
    _insert_oauth(
        conn,
        row_id=uuid.uuid4(),
        user_id=user_id,
        oauth_name="Unprotected",
        account_id="acct-dup",
    )
    rows = _fetch_accounts(conn)
    assert len(rows) == 3


def test_upgrade_rejects_expression_same_name_index(pg_oauth_schema) -> None:
    conn, migration, _engine, _schema = pg_oauth_schema
    _create_same_name_index(
        conn,
        """
        CREATE UNIQUE INDEX uq_oauth_account_oauth_name_account_id
        ON oauth_account ((lower(oauth_name)), account_id)
        """,
    )
    _assert_upgrade_rejects_incompatible_index(conn, migration, pattern="expression")


def test_upgrade_rejects_extra_key_column_index(pg_oauth_schema) -> None:
    conn, migration, _engine, _schema = pg_oauth_schema
    _create_same_name_index(
        conn,
        """
        CREATE UNIQUE INDEX uq_oauth_account_oauth_name_account_id
        ON oauth_account (oauth_name, account_id, user_id)
        """,
    )
    _assert_upgrade_rejects_incompatible_index(conn, migration, pattern="key columns")


def test_upgrade_rejects_same_name_index_on_wrong_table(pg_oauth_schema) -> None:
    conn, migration, _engine, _schema = pg_oauth_schema
    conn.execute(
        text(
            """
            CREATE TABLE oauth_index_decoy (
                oauth_name varchar(100) NOT NULL,
                account_id varchar(320) NOT NULL
            )
            """
        )
    )
    _create_same_name_index(
        conn,
        """
        CREATE UNIQUE INDEX uq_oauth_account_oauth_name_account_id
        ON oauth_index_decoy (oauth_name, account_id)
        """,
    )
    _assert_upgrade_rejects_incompatible_index(
        conn, migration, pattern="oauth_index_decoy"
    )


def test_upgrade_accepts_valid_exact_global_unique_index(pg_oauth_schema) -> None:
    conn, migration, _engine, _schema = pg_oauth_schema
    user_id = uuid.uuid4()
    _insert_user(conn, user_id)
    _insert_oauth(
        conn,
        row_id=uuid.uuid4(),
        user_id=user_id,
        oauth_name="Legacy",
        account_id="acct-1",
    )
    _create_same_name_index(
        conn,
        """
        CREATE UNIQUE INDEX uq_oauth_account_oauth_name_account_id
        ON oauth_account (oauth_name, account_id)
        """,
    )

    migration.ensure_unique_oauth_name_account_id(conn)

    with pytest.raises(Exception, match=r"unique|duplicate key"):
        _insert_oauth(
            conn,
            row_id=uuid.uuid4(),
            user_id=user_id,
            oauth_name="Legacy",
            account_id="acct-1",
        )


def test_upgrade_rejects_invalid_same_name_index(pg_oauth_schema) -> None:
    conn, migration, _engine, _schema = pg_oauth_schema
    _create_same_name_index(
        conn,
        """
        CREATE UNIQUE INDEX uq_oauth_account_oauth_name_account_id
        ON oauth_account (oauth_name, account_id)
        """,
    )
    try:
        _set_index_catalog_flag(conn, flag="indisvalid", value=False)
    except Exception as exc:
        pytest.skip(f"cannot mark index invalid in this PostgreSQL role: {exc}")
    _assert_upgrade_rejects_incompatible_index(
        conn, migration, pattern="indisvalid=false"
    )


def test_upgrade_rejects_not_ready_same_name_index(pg_oauth_schema) -> None:
    conn, migration, _engine, _schema = pg_oauth_schema
    _create_same_name_index(
        conn,
        """
        CREATE UNIQUE INDEX uq_oauth_account_oauth_name_account_id
        ON oauth_account (oauth_name, account_id)
        """,
    )
    try:
        _set_index_catalog_flag(conn, flag="indisready", value=False)
    except Exception as exc:
        pytest.skip(f"cannot mark index not-ready in this PostgreSQL role: {exc}")
    _assert_upgrade_rejects_incompatible_index(
        conn, migration, pattern="indisready=false"
    )
