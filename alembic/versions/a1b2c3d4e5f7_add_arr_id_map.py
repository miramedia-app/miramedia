"""Add arr_id_map

Revision ID: a1b2c3d4e5f7
Revises: f3a4b5c6d7e8
Create Date: 2026-07-23 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f7"
down_revision: str | None = "f3a4b5c6d7e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "arr_id_map",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("entity_type", sa.String(), nullable=False),
        sa.Column("entity_uuid", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "entity_type",
            "entity_uuid",
            name="uq_arr_id_map_entity",
        ),
    )


def downgrade() -> None:
    op.drop_table("arr_id_map")
