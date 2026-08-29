"""Add observe-only release feed tables (design 385 Slice A)

Revision ID: p4q5r6s7t8u9
Revises: o3p4q5r6s7t8
Create Date: 2026-08-24 00:00:00.000000

feed_source cursor rows and feed_item observation/decision rows for Torznab/Newznab
observe-only polling. No automatic downloads.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "p4q5r6s7t8u9"
down_revision: str | None = "o3p4q5r6s7t8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "feed_source",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("backend", sa.String(length=32), nullable=False),
        sa.Column("indexer_key", sa.String(length=128), nullable=False),
        sa.Column("protocol", sa.String(length=16), nullable=False, server_default="torznab"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("watermark_pub_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("watermark_guid", sa.String(length=512), nullable=True),
        sa.Column("lease_owner", sa.String(length=64), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "consecutive_failures",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("backend", "indexer_key", name="uq_feed_source_backend_indexer"),
    )
    op.create_index("ix_feed_source_lease_until", "feed_source", ["lease_until"])

    op.create_table(
        "feed_item",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("provider_guid", sa.String(length=512), nullable=True),
        sa.Column("info_hash", sa.String(length=64), nullable=True),
        sa.Column("download_url_redacted", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("indexer", sa.String(length=128), nullable=True),
        sa.Column("usenet", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("seeders", sa.Integer(), nullable=True),
        sa.Column("age", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("imdb_id", sa.String(length=32), nullable=True),
        sa.Column("tmdb_id", sa.String(length=32), nullable=True),
        sa.Column("tvdb_id", sa.String(length=32), nullable=True),
        sa.Column("bound_media_type", sa.String(length=16), nullable=True),
        sa.Column("bound_media_id", sa.Uuid(), nullable=True),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        sa.ForeignKeyConstraint(["source_id"], ["feed_source.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_feed_item_source_first_seen",
        "feed_item",
        ["source_id", "first_seen_at"],
    )
    op.create_index(
        "uq_feed_item_source_guid",
        "feed_item",
        ["source_id", "provider_guid"],
        unique=True,
        postgresql_where=sa.text("provider_guid IS NOT NULL"),
    )
    op.create_index(
        "uq_feed_item_source_info_hash",
        "feed_item",
        ["source_id", "info_hash"],
        unique=True,
        postgresql_where=sa.text("info_hash IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_feed_item_source_info_hash",
        table_name="feed_item",
        if_exists=True,
        postgresql_where=sa.text("info_hash IS NOT NULL"),
    )
    op.drop_index(
        "uq_feed_item_source_guid",
        table_name="feed_item",
        if_exists=True,
        postgresql_where=sa.text("provider_guid IS NOT NULL"),
    )
    op.drop_index(
        "ix_feed_item_source_first_seen",
        table_name="feed_item",
        if_exists=True,
    )
    op.drop_table("feed_item", if_exists=True)
    op.drop_index("ix_feed_source_lease_until", table_name="feed_source", if_exists=True)
    op.drop_table("feed_source", if_exists=True)
