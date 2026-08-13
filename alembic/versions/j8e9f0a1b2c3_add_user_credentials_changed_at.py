"""Add user credentials_changed_at column

Revision ID: j8e9f0a1b2c3
Revises: h6d7e8f9a0b1
Create Date: 2026-08-08 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "j8e9f0a1b2c3"
down_revision: str | None = "h6d7e8f9a0b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user",
        sa.Column("credentials_changed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user", "credentials_changed_at")
