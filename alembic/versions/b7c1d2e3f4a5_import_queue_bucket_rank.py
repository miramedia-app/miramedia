"""import_queue_item bucket_rank ordering

Add ``bucket_rank`` so the ``all`` imports tab lists action-needed rows
(Review, then Retry) ahead of Done rows. Without it the tab ordered purely by
``sort_at``, interleaving reviewable scan + torrent items with already-imported
rows and scattering them across pages. Rebuilds the queue (rows self-heal via
``_ensure_queue_populated``) so existing rows pick up a correct rank.

Revision ID: b7c1d2e3f4a5
Revises: 3ae9e0afdc49
Create Date: 2026-06-22 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7c1d2e3f4a5"
down_revision: str | None = "3ae9e0afdc49"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "import_queue_item",
        sa.Column(
            "bucket_rank",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.drop_index("ix_import_queue_item_tab_sort", table_name="import_queue_item")
    op.create_index(
        "ix_import_queue_item_tab_sort",
        "import_queue_item",
        ["tab", "bucket_rank", "sort_at"],
    )
    # Drop cached rows so the next list/counts call rebuilds with correct ranks.
    op.execute("DELETE FROM import_queue_item")


def downgrade() -> None:
    op.drop_index("ix_import_queue_item_tab_sort", table_name="import_queue_item")
    op.create_index(
        "ix_import_queue_item_tab_sort",
        "import_queue_item",
        ["tab", "sort_at"],
    )
    op.drop_column("import_queue_item", "bucket_rank")
