"""Add episode air_time column

Revision ID: k9f0a1b2c3d4
Revises: j8e9f0a1b2c3
Create Date: 2026-08-09 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "k9f0a1b2c3d4"
down_revision: str | None = "j8e9f0a1b2c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "episode",
        sa.Column("air_time", sa.Time(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("episode", "air_time")
