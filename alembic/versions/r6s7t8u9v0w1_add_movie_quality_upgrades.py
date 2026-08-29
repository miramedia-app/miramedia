"""Add movie quality upgrade policy columns (design 309 Slice A)

Revision ID: r6s7t8u9v0w1
Revises: q5r6s7t8u9v0
Create Date: 2026-08-24 00:00:00.000000

Nullable per-title overrides; global default remains off in config.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "r6s7t8u9v0w1"
down_revision: str | None = "q5r6s7t8u9v0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "movie",
        sa.Column("quality_upgrades", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "movie",
        sa.Column("upgrade_until_quality", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("movie", "upgrade_until_quality")
    op.drop_column("movie", "quality_upgrades")
