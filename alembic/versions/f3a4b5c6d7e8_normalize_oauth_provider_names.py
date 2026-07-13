"""normalize legacy oauth_account provider names to canonical route key

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

_CANONICAL = "oidc"
_UNIQUE_INDEX = "uq_oauth_account_oauth_name_account_id"
_CROSS_USER_CONFLICT_MSG = (
    "OAuth provider migration blocked: one or more account_id values are bound to "
    "multiple users. Resolve the conflicting oauth_account rows manually, then rerun "
    "alembic upgrade head. Conflicts: {details}"
)


def _cross_user_conflicts(conn) -> list[tuple[str, list[str]]]:
    rows = conn.execute(
        text(
            """
            SELECT account_id::text,
                   array_agg(DISTINCT user_id::text ORDER BY user_id::text) AS user_ids
            FROM oauth_account
            GROUP BY account_id
            HAVING COUNT(DISTINCT user_id) > 1
            """
        )
    ).fetchall()
    return [(row[0], list(row[1])) for row in rows]


def assert_no_cross_user_account_conflicts(conn) -> None:
    """Abort before destructive work when account_id spans multiple users."""
    conflicts = _cross_user_conflicts(conn)
    if not conflicts:
        return
    details = "; ".join(
        f"account_id={account_id} user_ids={user_ids}"
        for account_id, user_ids in conflicts
    )
    msg = _CROSS_USER_CONFLICT_MSG.format(details=details)
    raise RuntimeError(msg)


def delete_legacy_when_canonical_same_user(conn, *, canonical: str) -> None:
    conn.execute(
        text(
            f"""
            DELETE FROM oauth_account AS legacy
            WHERE legacy.oauth_name <> :canonical
              AND EXISTS (
                SELECT 1
                FROM oauth_account AS canonical_row
                WHERE canonical_row.oauth_name = :canonical
                  AND canonical_row.account_id = legacy.account_id
                  AND canonical_row.user_id = legacy.user_id
              )
            """
        ),
        {"canonical": canonical},
    )


def delete_duplicate_legacy_rows(conn, *, canonical: str) -> None:
    conn.execute(
        text(
            f"""
            DELETE FROM oauth_account AS duplicate
            WHERE duplicate.oauth_name <> :canonical
              AND duplicate.id IN (
                SELECT ranked.id
                FROM (
                  SELECT id,
                         ROW_NUMBER() OVER (
                           PARTITION BY account_id, user_id
                           ORDER BY id::text ASC
                         ) AS rn
                  FROM oauth_account
                  WHERE oauth_name <> :canonical
                ) AS ranked
                WHERE ranked.rn > 1
              )
            """
        ),
        {"canonical": canonical},
    )


def rename_remaining_legacy_rows(conn, *, canonical: str) -> None:
    conn.execute(
        text(
            """
            UPDATE oauth_account
            SET oauth_name = :canonical
            WHERE oauth_name <> :canonical
            """
        ),
        {"canonical": canonical},
    )


def ensure_unique_oauth_name_account_id(conn) -> None:
    exists = conn.execute(
        text(
            """
            SELECT 1
            FROM pg_class
            WHERE relname = :index_name
              AND relkind = 'i'
            """
        ),
        {"index_name": _UNIQUE_INDEX},
    ).scalar()
    if exists:
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
    assert_no_cross_user_account_conflicts(conn)
    delete_legacy_when_canonical_same_user(conn, canonical=_CANONICAL)
    delete_duplicate_legacy_rows(conn, canonical=_CANONICAL)
    rename_remaining_legacy_rows(conn, canonical=_CANONICAL)
    ensure_unique_oauth_name_account_id(conn)


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(text(f"DROP INDEX IF EXISTS {_UNIQUE_INDEX}"))
