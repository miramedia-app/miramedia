"""add settings revision for optimistic concurrency

Revision ID: e1f2a3b4c5d6
Revises: c8d2e3f4a5b6
Create Date: 2026-07-13 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e1f2a3b4c5d6"
down_revision: str | None = "c8d2e3f4a5b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "system_config_override",
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("system_config_override", "revision")
