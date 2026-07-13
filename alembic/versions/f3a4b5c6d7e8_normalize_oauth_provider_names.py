"""normalize oauth_account duplicate rows and enforce provider/account uniqueness

Revision ID: f3a4b5c6d7e8
Revises: e1f2a3b4c5d6
Create Date: 2026-07-13 11:00:00.000000

"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "f3a4b5c6d7e8"
down_revision: str | None = "e1f2a3b4c5d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UNIQUE_INDEX = "uq_oauth_account_oauth_name_account_id"
_EXPECTED_COLUMNS = ("oauth_name", "account_id")
_CROSS_USER_CONFLICT_MSG = (
    "OAuth provider migration blocked: one or more (oauth_name, account_id) pairs are "
    "bound to multiple users. Resolve the conflicting oauth_account rows manually, "
    "then rerun alembic upgrade head. Conflicts: {details}"
)


def _cross_user_conflicts(conn) -> list[tuple[str, str, list[str]]]:
    rows = conn.execute(
        text(
            """
            SELECT oauth_name,
                   account_id::text,
                   array_agg(DISTINCT user_id::text ORDER BY user_id::text) AS user_ids
            FROM oauth_account
            GROUP BY oauth_name, account_id
            HAVING COUNT(DISTINCT user_id) > 1
            """
        )
    ).fetchall()
    return [(row[0], row[1], list(row[2])) for row in rows]


def assert_no_cross_user_provider_account_conflicts(conn) -> None:
    conflicts = _cross_user_conflicts(conn)
    if not conflicts:
        return
    details = "; ".join(
        f"oauth_name={oauth_name!r} account_id={account_id} user_ids={user_ids}"
        for oauth_name, account_id, user_ids in conflicts
    )
    msg = _CROSS_USER_CONFLICT_MSG.format(details=details)
    raise RuntimeError(msg)


def delete_duplicate_rows_same_user(conn) -> None:
    conn.execute(
        text(
            """
            DELETE FROM oauth_account AS duplicate
            WHERE duplicate.id IN (
                SELECT ranked.id
                FROM (
                  SELECT id,
                         ROW_NUMBER() OVER (
                           PARTITION BY oauth_name, account_id, user_id
                           ORDER BY id::text ASC
                         ) AS rn
                  FROM oauth_account
                ) AS ranked
                WHERE ranked.rn > 1
            )
            """
        )
    )


def _oauth_account_unique_index_columns(conn) -> tuple[str, ...] | None:
    row = conn.execute(
        text(
            """
            SELECT array_agg(a.attname ORDER BY x.ordinality) AS columns
            FROM pg_index i
            JOIN pg_class t ON t.oid = i.indrelid
            JOIN pg_namespace n ON n.oid = t.relnamespace
            JOIN pg_class ix ON ix.oid = i.indexrelid
            JOIN LATERAL unnest(i.indkey) WITH ORDINALITY AS x(attnum, ordinality) ON true
            JOIN pg_attribute a
              ON a.attrelid = t.oid
             AND a.attnum = x.attnum
             AND a.attnum > 0
            WHERE t.relname = 'oauth_account'
              AND n.nspname = current_schema()
              AND ix.relname = :index_name
              AND i.indisunique
            GROUP BY ix.relname, i.indisunique
            """
        ),
        {"index_name": _UNIQUE_INDEX},
    ).fetchone()
    if row is None or row[0] is None:
        return None
    return tuple(row[0])


def ensure_unique_oauth_name_account_id(conn) -> None:
    columns = _oauth_account_unique_index_columns(conn)
    if columns == _EXPECTED_COLUMNS:
        return
    if columns is not None:
        msg = (
            f"Existing index {_UNIQUE_INDEX} on oauth_account has unexpected "
            f"columns {columns}; expected {_EXPECTED_COLUMNS}"
        )
        raise RuntimeError(msg)
    conn.execute(
        text(
            f"""
            CREATE UNIQUE INDEX {_UNIQUE_INDEX}
            ON oauth_account (oauth_name, account_id)
            """
        )
    )


def upgrade() -> None:
    conn = op.get_bind()
    assert_no_cross_user_provider_account_conflicts(conn)
    delete_duplicate_rows_same_user(conn)
    ensure_unique_oauth_name_account_id(conn)


def downgrade() -> None:
    conn = op.get_bind()
    columns = _oauth_account_unique_index_columns(conn)
    if columns is None:
        return
    if columns != _EXPECTED_COLUMNS:
        msg = (
            f"Refusing to drop index {_UNIQUE_INDEX}: oauth_account index has "
            f"unexpected columns {columns}"
        )
        raise RuntimeError(msg)
    conn.execute(text(f"DROP INDEX {_UNIQUE_INDEX}"))
