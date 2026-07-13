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
_INDEX_INCOMPATIBLE_MSG = (
    "OAuth provider migration blocked: index {index_name} in schema {schema_name} "
    "does not match the required global UNIQUE (oauth_name, account_id) invariant "
    "on oauth_account ({reason}). Drop or rename the incompatible index manually, "
    "then rerun alembic upgrade head."
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


def _oauth_account_unique_index_state(conn) -> dict[str, object] | None:
    row = conn.execute(
        text(
            """
            SELECT t.relname AS table_name,
                   n.nspname AS schema_name,
                   array_agg(a.attname ORDER BY x.ordinality)
                     FILTER (WHERE a.attname IS NOT NULL) AS columns,
                   i.indisunique,
                   i.indisvalid,
                   i.indisready,
                   (i.indpred IS NOT NULL) AS is_partial,
                   (i.indexprs IS NOT NULL) AS is_expression,
                   COALESCE(array_length(i.indkey, 1), 0) AS key_count
            FROM pg_class ix
            JOIN pg_namespace n ON n.oid = ix.relnamespace
            JOIN pg_index i ON i.indexrelid = ix.oid
            JOIN pg_class t ON t.oid = i.indrelid
            LEFT JOIN LATERAL unnest(i.indkey) WITH ORDINALITY AS x(attnum, ordinality)
              ON true
            LEFT JOIN pg_attribute a
              ON a.attrelid = t.oid
             AND a.attnum = x.attnum
             AND a.attnum > 0
             AND NOT a.attisdropped
            WHERE ix.relname = :index_name
              AND n.nspname = current_schema()
              AND ix.relkind = 'i'
            GROUP BY t.relname,
                     n.nspname,
                     i.indisunique,
                     i.indisvalid,
                     i.indisready,
                     i.indpred,
                     i.indexprs,
                     i.indkey
            """
        ),
        {"index_name": _UNIQUE_INDEX},
    ).fetchone()
    if row is None:
        return None
    columns = row[2]
    return {
        "table_name": row[0],
        "schema_name": row[1],
        "columns": tuple(columns) if columns is not None else tuple(),
        "indisunique": bool(row[3]),
        "indisvalid": bool(row[4]),
        "indisready": bool(row[5]),
        "is_partial": bool(row[6]),
        "is_expression": bool(row[7]),
        "key_count": int(row[8]),
    }


def _oauth_account_unique_index_incompatibility(
    state: dict[str, object],
) -> str | None:
    reasons: list[str] = []
    if state["table_name"] != "oauth_account":
        reasons.append(
            f"index is attached to table {state['table_name']!r}, expected 'oauth_account'"
        )
    if not state["indisunique"]:
        reasons.append("index is not UNIQUE")
    if not state["indisvalid"]:
        reasons.append("index is not valid (indisvalid=false)")
    if not state["indisready"]:
        reasons.append("index is not ready (indisready=false)")
    if state["is_partial"]:
        reasons.append("index is partial (has a WHERE clause)")
    if state["is_expression"]:
        reasons.append("index uses expressions instead of plain key columns")
    if state["key_count"] != len(_EXPECTED_COLUMNS):
        reasons.append(
            f"index has {state['key_count']} key columns, expected "
            f"{len(_EXPECTED_COLUMNS)}"
        )
    columns = state["columns"]
    if columns != _EXPECTED_COLUMNS:
        reasons.append(f"index columns are {columns!r}, expected {_EXPECTED_COLUMNS!r}")
    if not reasons:
        return None
    return "; ".join(reasons)


def _oauth_account_unique_index_columns(conn) -> tuple[str, ...] | None:
    state = _oauth_account_unique_index_state(conn)
    if state is None:
        return None
    if _oauth_account_unique_index_incompatibility(state) is not None:
        return tuple(state["columns"])
    return tuple(state["columns"])


def _assert_compatible_oauth_unique_index(conn) -> bool:
    """Return True when a valid invariant index already exists."""
    state = _oauth_account_unique_index_state(conn)
    if state is None:
        return False
    reason = _oauth_account_unique_index_incompatibility(state)
    if reason is not None:
        msg = _INDEX_INCOMPATIBLE_MSG.format(
            index_name=_UNIQUE_INDEX,
            schema_name=state["schema_name"],
            reason=reason,
        )
        raise RuntimeError(msg)
    return True


def ensure_unique_oauth_name_account_id(conn) -> None:
    if _assert_compatible_oauth_unique_index(conn):
        return
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
    state = _oauth_account_unique_index_state(conn)
    if state is None:
        return
    reason = _oauth_account_unique_index_incompatibility(state)
    if reason is not None:
        msg = _INDEX_INCOMPATIBLE_MSG.format(
            index_name=_UNIQUE_INDEX,
            schema_name=state["schema_name"],
            reason=reason,
        )
        raise RuntimeError(msg)
    conn.execute(text(f"DROP INDEX {_UNIQUE_INDEX}"))
