"""Add structured mirror list to indexer sites.

Replaces the flat ``available_urls`` list as the source of truth with a JSONB
``mirrors`` column of ``{url, enabled, source}`` entries. ``available_urls`` is
kept as a derived (enabled-only) view for the live search, the probe, and older
clients. Backfill classifies existing mirrors coarsely (preloaded → seeded,
else user); the startup seeder refines preloaded rows against the current code
mirror list on next boot.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "v0w1x2y3z4a5"
down_revision: str | None = "u9v0w1x2y3z4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "indexer_site",
        sa.Column(
            "mirrors",
            postgresql.JSONB(),
            nullable=False,
            server_default="[]",
        ),
    )
    # Backfill from available_urls, preserving order. Preloaded sites' mirrors
    # start as "seeded"; everything else as "user".
    op.execute(
        sa.text(
            "UPDATE indexer_site SET mirrors = COALESCE(("
            "  SELECT jsonb_agg("
            "    jsonb_build_object("
            "      'url', u, 'enabled', true,"
            "      'source', CASE WHEN is_preloaded THEN 'seeded' ELSE 'user' END"
            "    )"
            "  )"
            "  FROM unnest(available_urls) AS u"
            "), '[]'::jsonb)"
        )
    )


def downgrade() -> None:
    op.drop_column("indexer_site", "mirrors")
