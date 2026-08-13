"""audit specials skip migration limits and operator reporting

Revision ``c8d2e3f4a5b6`` backfilled Season 0 ``skipped`` flags using live
``misc.download_specials`` application config. Two databases stamped at that
revision can therefore disagree, and later user skip edits cannot be
distinguished from the one-time backfill. ``c8d2e3f4a5b6`` documents that the
destructive branch is irreversible; this revision records the audit boundary:
no mass-rewrite of ``skipped`` flags. Operators review ``specials_skip_audit``
instead.

Revision ID: g5c6d7e8f9a0
Revises: a1b2c3d4e5f7
Create Date: 2026-08-07 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "g5c6d7e8f9a0"
down_revision: str | None = "a1b2c3d4e5f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_AUDIT_VIEW = "specials_skip_audit"

# Decision (plan 247): persisted rows carry no marker for c8d2 backfill vs user
# edits — do not infer or mass-rewrite ``skipped`` here. Future migrations must
# operate only on persisted facts; deployment policy stays in app config or
# explicit operator commands, never implicit config reads during traversal.
_VIEW_SQL = f"""
CREATE OR REPLACE VIEW {_AUDIT_VIEW} AS
SELECT
    sh.id AS show_id,
    sh.name AS show_name,
    se.id AS season_id,
    se.skipped AS season_skipped,
    e.id AS episode_id,
    e.number AS episode_number,
    e.title AS episode_title,
    e.skipped AS episode_skipped,
    e.downloaded AS episode_downloaded,
    CASE
        WHEN e.downloaded THEN 'downloaded'
        WHEN NOT e.skipped THEN 'wanted_undownloaded'
        ELSE 'skipped_undownloaded'
    END AS skip_category
FROM season se
JOIN show sh ON sh.id = se.show_id
JOIN episode e ON e.season_id = se.id
WHERE se.number = 0
"""


def upgrade() -> None:
    op.execute(_VIEW_SQL)


def downgrade() -> None:
    op.execute(f"DROP VIEW IF EXISTS {_AUDIT_VIEW}")
