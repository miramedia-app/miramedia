"""Add unique constraint on media_request.seerr_request_id

Revision ID: t8u9v0w1x2y3
Revises: s7t8u9v0w1x2
Create Date: 2026-08-31 00:00:00.000000

The ORM model marks ``seerr_request_id`` as ``unique=True`` but the initial
schema created the column without a unique constraint, leaving the ORM and
migration head out of sync. Add the missing constraint. NULLs are exempt from
the uniqueness rule in Postgres, so existing native (non-seerr) rows are
unaffected.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "t8u9v0w1x2y3"
down_revision: str | None = "s7t8u9v0w1x2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONSTRAINT = "uq_media_request_seerr_request_id"


def upgrade() -> None:
    op.create_unique_constraint(_CONSTRAINT, "media_request", ["seerr_request_id"])


def downgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "media_request", type_="unique")
