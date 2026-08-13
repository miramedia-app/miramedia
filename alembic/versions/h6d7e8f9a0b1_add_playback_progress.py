"""Add playback_progress

Revision ID: h6d7e8f9a0b1
Revises: g5c6d7e8f9a0
Create Date: 2026-08-07 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "h6d7e8f9a0b1"
down_revision: str | None = "g5c6d7e8f9a0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "playback_progress",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("movie_file_id", sa.Uuid(), nullable=True),
        sa.Column("episode_file_id", sa.Uuid(), nullable=True),
        sa.Column("position_ms", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("completed", sa.Boolean(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(movie_file_id IS NOT NULL AND episode_file_id IS NULL) "
            "OR (movie_file_id IS NULL AND episode_file_id IS NOT NULL)",
            name="playback_progress_file_xor",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["movie_file_id"], ["movie_file.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["episode_file_id"], ["episode_file.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_playback_progress_user_updated_at",
        "playback_progress",
        ["user_id", "updated_at"],
        unique=False,
        postgresql_ops={"updated_at": "DESC"},
    )
    op.create_index(
        "uq_playback_progress_user_episode_file",
        "playback_progress",
        ["user_id", "episode_file_id"],
        unique=True,
        postgresql_where=sa.text("episode_file_id IS NOT NULL"),
    )
    op.create_index(
        "uq_playback_progress_user_movie_file",
        "playback_progress",
        ["user_id", "movie_file_id"],
        unique=True,
        postgresql_where=sa.text("movie_file_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_playback_progress_user_movie_file",
        table_name="playback_progress",
        postgresql_where=sa.text("movie_file_id IS NOT NULL"),
    )
    op.drop_index(
        "uq_playback_progress_user_episode_file",
        table_name="playback_progress",
        postgresql_where=sa.text("episode_file_id IS NOT NULL"),
    )
    op.drop_index(
        "ix_playback_progress_user_updated_at",
        table_name="playback_progress",
        postgresql_ops={"updated_at": "DESC"},
    )
    op.drop_table("playback_progress")
