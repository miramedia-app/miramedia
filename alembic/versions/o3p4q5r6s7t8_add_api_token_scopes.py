"""Add scopes column to user_api_tokens (forced reissue / default-deny)

Revision ID: o3p4q5r6s7t8
Revises: n2b3c4d5e6f7
Create Date: 2026-08-24 00:00:00.000000

Existing rows receive an empty scopes array — tokens authenticate but fail every
scoped route until operators mint replacements from a browser session.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "o3p4q5r6s7t8"
down_revision: str | None = "n2b3c4d5e6f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user_api_tokens",
        sa.Column(
            "scopes",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("user_api_tokens", "scopes")
