"""Add Jellyfin viewing-state dry-run tables (design 386 Slice A)

Revision ID: q5r6s7t8u9v0
Revises: p4q5r6s7t8u9
Create Date: 2026-08-24 00:00:00.000000

Persistent dry-run proposals, quarantine rows, poll cursor, and run metrics.
No playback or watched-state writes.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "q5r6s7t8u9v0"
down_revision: str | None = "p4q5r6s7t8u9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "viewing_sync_cursor",
        sa.Column("connector", sa.String(length=32), nullable=False),
        sa.Column("min_last_played_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("connector"),
    )

    op.create_table(
        "viewing_sync_run",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("connector", sa.String(length=32), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="running"),
        sa.Column("metrics", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("error_redacted", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_viewing_sync_run_started_at", "viewing_sync_run", ["started_at"])

    op.create_table(
        "viewing_sync_proposal",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("connector", sa.String(length=32), nullable=False),
        sa.Column("connector_user_id", sa.String(length=64), nullable=False),
        sa.Column("connector_item_id", sa.String(length=64), nullable=False),
        sa.Column("miramedia_user_id", sa.Uuid(), nullable=True),
        sa.Column("media_kind", sa.String(length=16), nullable=True),
        sa.Column("media_id", sa.Uuid(), nullable=True),
        sa.Column("file_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=True),
        sa.Column("match_confidence", sa.String(length=16), nullable=True),
        sa.Column("conflict_reason", sa.String(length=64), nullable=True),
        sa.Column("payload_digest", sa.String(length=64), nullable=False),
        sa.Column("position_ms", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("completed", sa.Boolean(), nullable=True),
        sa.Column("remote_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["run_id"], ["viewing_sync_run.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_viewing_sync_proposal_run_id", "viewing_sync_proposal", ["run_id"])
    op.create_index(
        "ix_viewing_sync_proposal_connector_item",
        "viewing_sync_proposal",
        ["connector", "connector_user_id", "connector_item_id"],
    )

    op.create_table(
        "viewing_sync_quarantine",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.Column("connector_user_id", sa.String(length=64), nullable=False),
        sa.Column("connector_item_id", sa.String(length=64), nullable=False),
        sa.Column("item_type", sa.String(length=16), nullable=False),
        sa.Column("provider_ids", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("candidate_mira_ids", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("title", sa.Text(), nullable=False, server_default=""),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("series_name", sa.Text(), nullable=True),
        sa.Column("season", sa.Integer(), nullable=True),
        sa.Column("episode", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["run_id"], ["viewing_sync_run.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_viewing_sync_quarantine_run_id", "viewing_sync_quarantine", ["run_id"]
    )
    op.create_index(
        "ix_viewing_sync_quarantine_created_at",
        "viewing_sync_quarantine",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_viewing_sync_quarantine_created_at",
        table_name="viewing_sync_quarantine",
        if_exists=True,
    )
    op.drop_index(
        "ix_viewing_sync_quarantine_run_id",
        table_name="viewing_sync_quarantine",
        if_exists=True,
    )
    op.drop_table("viewing_sync_quarantine", if_exists=True)
    op.drop_index(
        "ix_viewing_sync_proposal_connector_item",
        table_name="viewing_sync_proposal",
        if_exists=True,
    )
    op.drop_index(
        "ix_viewing_sync_proposal_run_id",
        table_name="viewing_sync_proposal",
        if_exists=True,
    )
    op.drop_table("viewing_sync_proposal", if_exists=True)
    op.drop_index("ix_viewing_sync_run_started_at", table_name="viewing_sync_run", if_exists=True)
    op.drop_table("viewing_sync_run", if_exists=True)
    op.drop_table("viewing_sync_cursor", if_exists=True)
