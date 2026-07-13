"""normalize legacy oauth_account provider names to canonical route key

Revision ID: f3a4b5c6d7e8
Revises: e1f2a3b4c5d6
Create Date: 2026-07-13 11:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "f3a4b5c6d7e8"
down_revision: str | None = "e1f2a3b4c5d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CANONICAL = "oidc"


def upgrade() -> None:
    # Drop legacy duplicates when canonical already exists for same account+user.
    op.execute(
        f"""
        DELETE FROM oauth_account AS legacy
        WHERE legacy.oauth_name <> '{_CANONICAL}'
          AND EXISTS (
            SELECT 1
            FROM oauth_account AS canonical
            WHERE canonical.oauth_name = '{_CANONICAL}'
              AND canonical.account_id = legacy.account_id
              AND canonical.user_id = legacy.user_id
          )
        """
    )
    # Keep one deterministic legacy row per account+user before rename.
    op.execute(
        f"""
        DELETE FROM oauth_account AS duplicate
        WHERE duplicate.oauth_name <> '{_CANONICAL}'
          AND duplicate.id NOT IN (
            SELECT MIN(id)
            FROM oauth_account
            WHERE oauth_name <> '{_CANONICAL}'
            GROUP BY account_id, user_id
          )
        """
    )
    # Rename remaining legacy rows only when no cross-user canonical conflict exists.
    op.execute(
        f"""
        UPDATE oauth_account AS legacy
        SET oauth_name = '{_CANONICAL}'
        WHERE legacy.oauth_name <> '{_CANONICAL}'
          AND NOT EXISTS (
            SELECT 1
            FROM oauth_account AS canonical
            WHERE canonical.oauth_name = '{_CANONICAL}'
              AND canonical.account_id = legacy.account_id
              AND canonical.user_id <> legacy.user_id
          )
        """
    )


def downgrade() -> None:
    # Provider display names are not recoverable; downgrade is a no-op.
    pass
