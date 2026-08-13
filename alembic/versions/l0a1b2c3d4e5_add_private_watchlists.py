"""Add private watchlists and media watch state

Revision ID: l0a1b2c3d4e5
Revises: k9f0a1b2c3d4
Create Date: 2026-08-10 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "l0a1b2c3d4e5"
down_revision: str | None = "k9f0a1b2c3d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "media_watch_state",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("movie_id", sa.Uuid(), nullable=True),
        sa.Column("episode_id", sa.Uuid(), nullable=True),
        sa.Column("watched", sa.Boolean(), nullable=False),
        sa.Column("source", sa.String(length=10), nullable=False),
        sa.Column("watched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(movie_id IS NOT NULL AND episode_id IS NULL) "
            "OR (movie_id IS NULL AND episode_id IS NOT NULL)",
            name="media_watch_state_media_xor",
        ),
        sa.CheckConstraint(
            "source IN ('derived', 'manual')",
            name="media_watch_state_source_valid",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["movie_id"], ["movie.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["episode_id"], ["episode.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_media_watch_state_user_episode",
        "media_watch_state",
        ["user_id", "episode_id"],
        unique=True,
        postgresql_where=sa.text("episode_id IS NOT NULL"),
    )
    op.create_index(
        "uq_media_watch_state_user_movie",
        "media_watch_state",
        ["user_id", "movie_id"],
        unique=True,
        postgresql_where=sa.text("movie_id IS NOT NULL"),
    )

    op.create_table(
        "watchlist",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_watchlist_user_name_lower",
        "watchlist",
        ["user_id", sa.text("lower(name)")],
        unique=True,
    )

    op.create_table(
        "watchlist_item",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("watchlist_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("movie_id", sa.Uuid(), nullable=True),
        sa.Column("show_id", sa.Uuid(), nullable=True),
        sa.Column("episode_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(movie_id IS NOT NULL AND show_id IS NULL AND episode_id IS NULL) "
            "OR (movie_id IS NULL AND show_id IS NOT NULL AND episode_id IS NULL) "
            "OR (movie_id IS NULL AND show_id IS NULL AND episode_id IS NOT NULL)",
            name="watchlist_item_media_xor",
        ),
        sa.ForeignKeyConstraint(["watchlist_id"], ["watchlist.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["movie_id"], ["movie.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["show_id"], ["show.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["episode_id"], ["episode.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_watchlist_item_list_episode",
        "watchlist_item",
        ["watchlist_id", "episode_id"],
        unique=True,
        postgresql_where=sa.text("episode_id IS NOT NULL"),
    )
    op.create_index(
        "uq_watchlist_item_list_movie",
        "watchlist_item",
        ["watchlist_id", "movie_id"],
        unique=True,
        postgresql_where=sa.text("movie_id IS NOT NULL"),
    )
    op.create_index(
        "uq_watchlist_item_list_position",
        "watchlist_item",
        ["watchlist_id", "position"],
        unique=True,
    )
    op.create_index(
        "uq_watchlist_item_list_show",
        "watchlist_item",
        ["watchlist_id", "show_id"],
        unique=True,
        postgresql_where=sa.text("show_id IS NOT NULL"),
    )

    op.execute(
        """
        INSERT INTO media_watch_state (
            id,
            user_id,
            movie_id,
            episode_id,
            watched,
            source,
            watched_at,
            updated_at
        )
        SELECT
            gen_random_uuid(),
            user_id,
            movie_id,
            NULL,
            true,
            'derived',
            watched_at,
            updated_at
        FROM (
            SELECT DISTINCT ON (pp.user_id, mf.movie_id)
                pp.user_id,
                mf.movie_id,
                pp.updated_at AS watched_at,
                pp.updated_at AS updated_at
            FROM playback_progress pp
            INNER JOIN movie_file mf ON mf.id = pp.movie_file_id
            WHERE pp.completed = true
              AND pp.movie_file_id IS NOT NULL
            ORDER BY pp.user_id, mf.movie_id, pp.updated_at DESC
        ) movie_rows
        """
    )
    op.execute(
        """
        INSERT INTO media_watch_state (
            id,
            user_id,
            movie_id,
            episode_id,
            watched,
            source,
            watched_at,
            updated_at
        )
        SELECT
            gen_random_uuid(),
            user_id,
            NULL,
            episode_id,
            true,
            'derived',
            watched_at,
            updated_at
        FROM (
            SELECT DISTINCT ON (pp.user_id, ef.episode_id)
                pp.user_id,
                ef.episode_id,
                pp.updated_at AS watched_at,
                pp.updated_at AS updated_at
            FROM playback_progress pp
            INNER JOIN episode_file ef ON ef.id = pp.episode_file_id
            WHERE pp.completed = true
              AND pp.episode_file_id IS NOT NULL
            ORDER BY pp.user_id, ef.episode_id, pp.updated_at DESC
        ) episode_rows
        """
    )


def downgrade() -> None:
    op.drop_index(
        "uq_watchlist_item_list_show",
        table_name="watchlist_item",
        postgresql_where=sa.text("show_id IS NOT NULL"),
    )
    op.drop_index(
        "uq_watchlist_item_list_position",
        table_name="watchlist_item",
    )
    op.drop_index(
        "uq_watchlist_item_list_movie",
        table_name="watchlist_item",
        postgresql_where=sa.text("movie_id IS NOT NULL"),
    )
    op.drop_index(
        "uq_watchlist_item_list_episode",
        table_name="watchlist_item",
        postgresql_where=sa.text("episode_id IS NOT NULL"),
    )
    op.drop_table("watchlist_item")

    op.drop_index("uq_watchlist_user_name_lower", table_name="watchlist")
    op.drop_table("watchlist")

    op.drop_index(
        "uq_media_watch_state_user_movie",
        table_name="media_watch_state",
        postgresql_where=sa.text("movie_id IS NOT NULL"),
    )
    op.drop_index(
        "uq_media_watch_state_user_episode",
        table_name="media_watch_state",
        postgresql_where=sa.text("episode_id IS NOT NULL"),
    )
    op.drop_table("media_watch_state")
