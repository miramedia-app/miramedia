"""Add partial indexes for orphaned failed media files

Revision ID: n2b3c4d5e6f7
Revises: m1a2b3c4d5e6
Create Date: 2026-08-23 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "n2b3c4d5e6f7"
down_revision: str | None = "m1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ORPHANED_FAILED_WHERE = sa.text(
    "torrent_id IS NULL AND import_status IN "
    "('failed_io', 'failed_no_match')"
)


def upgrade() -> None:
    op.create_index(
        "ix_episode_file_orphaned_failed",
        "episode_file",
        ["import_status"],
        postgresql_where=_ORPHANED_FAILED_WHERE,
    )
    op.create_index(
        "ix_movie_file_orphaned_failed",
        "movie_file",
        ["import_status"],
        postgresql_where=_ORPHANED_FAILED_WHERE,
    )


def downgrade() -> None:
    op.drop_index("ix_movie_file_orphaned_failed", table_name="movie_file")
    op.drop_index("ix_episode_file_orphaned_failed", table_name="episode_file")
