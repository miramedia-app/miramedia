"""Retain source torrent hashes on imported media files."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "u9v0w1x2y3z4"
down_revision: str | None = "t8u9v0w1x2y3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ("movie_file", "episode_file"):
        op.add_column(table, sa.Column("source_info_hash", sa.String(40), nullable=True))
        op.execute(
            sa.text(
                f'UPDATE "{table}" AS media_file '
                'SET source_info_hash = torrent.hash '
                'FROM torrent WHERE media_file.torrent_id = torrent.id'
            )
        )
        op.create_index(f"ix_{table}_source_info_hash", table, ["source_info_hash"])


def downgrade() -> None:
    for table in ("episode_file", "movie_file"):
        op.drop_index(f"ix_{table}_source_info_hash", table_name=table)
        op.drop_column(table, "source_info_hash")
