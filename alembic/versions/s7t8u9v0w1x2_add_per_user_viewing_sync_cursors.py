"""Add per-user viewing-sync cursor keys (plan 444)

Revision ID: s7t8u9v0w1x2
Revises: r6s7t8u9v0w1
Create Date: 2026-08-25 00:00:00.000000

Replace connector-wide poll cursor with composite (connector, connector_user_id).
Upgrade copies the legacy singleton cursor onto currently configured Jellyfin
user_map keys. Downgrade collapses per-user rows back to MAX(min_last_played_date).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "s7t8u9v0w1x2"
down_revision: str | None = "r6s7t8u9v0w1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONNECTOR = "jellyfin"


def _jellyfin_user_map_keys() -> list[str]:
    try:
        from miramedia.config import MiraMediaConfig

        user_map = MiraMediaConfig().viewing_sync.jellyfin.user_map
    except Exception as exc:
        raise RuntimeError(
            "per-user viewing-sync cursor migration could not load app config to "
            "read viewing_sync.jellyfin.user_map. Fix the config (or set "
            "MIRAMEDIA_CONFIG_FILE explicitly) and re-run 'alembic upgrade head'."
        ) from exc
    return [key.strip() for key in user_map if key.strip()]


def upgrade() -> None:
    op.add_column(
        "viewing_sync_cursor",
        sa.Column("connector_user_id", sa.String(length=64), nullable=True),
    )

    op.drop_constraint(
        "viewing_sync_cursor_pkey", "viewing_sync_cursor", type_="primary"
    )

    conn = op.get_bind()
    legacy_row = conn.execute(
        sa.text(
            """
            SELECT min_last_played_date
            FROM viewing_sync_cursor
            WHERE connector = :connector
            """
        ),
        {"connector": _CONNECTOR},
    ).first()
    legacy_min_last_played = legacy_row[0] if legacy_row is not None else None

    user_ids = _jellyfin_user_map_keys()
    if user_ids:
        for user_id in user_ids:
            conn.execute(
                sa.text(
                    """
                    INSERT INTO viewing_sync_cursor (
                        connector,
                        connector_user_id,
                        min_last_played_date,
                        updated_at
                    )
                    VALUES (:connector, :user_id, :min_date, now())
                    """
                ),
                {
                    "connector": _CONNECTOR,
                    "user_id": user_id,
                    "min_date": legacy_min_last_played,
                },
            )
        conn.execute(
            sa.text(
                """
                DELETE FROM viewing_sync_cursor
                WHERE connector = :connector
                  AND connector_user_id IS NULL
                """
            ),
            {"connector": _CONNECTOR},
        )
    elif legacy_row is not None:
        conn.execute(
            sa.text(
                """
                DELETE FROM viewing_sync_cursor
                WHERE connector = :connector
                """
            ),
            {"connector": _CONNECTOR},
        )

    op.alter_column(
        "viewing_sync_cursor",
        "connector_user_id",
        existing_type=sa.String(length=64),
        nullable=False,
    )
    op.create_primary_key(
        "viewing_sync_cursor_pkey",
        "viewing_sync_cursor",
        ["connector", "connector_user_id"],
    )


def downgrade() -> None:
    conn = op.get_bind()
    max_row = conn.execute(
        sa.text(
            """
            SELECT MAX(min_last_played_date)
            FROM viewing_sync_cursor
            WHERE connector = :connector
            """
        ),
        {"connector": _CONNECTOR},
    ).first()
    max_min_last_played = max_row[0] if max_row is not None else None

    op.drop_constraint(
        "viewing_sync_cursor_pkey", "viewing_sync_cursor", type_="primary"
    )
    conn.execute(
        sa.text(
            """
            DELETE FROM viewing_sync_cursor
            WHERE connector = :connector
            """
        ),
        {"connector": _CONNECTOR},
    )
    # Drop the NOT NULL per-user column before re-inserting the collapsed
    # singleton row; inserting first would violate the connector_user_id
    # not-null constraint that is still in force at this point.
    op.drop_column("viewing_sync_cursor", "connector_user_id")
    conn.execute(
        sa.text(
            """
            INSERT INTO viewing_sync_cursor (
                connector,
                min_last_played_date,
                updated_at
            )
            VALUES (:connector, :min_date, now())
            """
        ),
        {"connector": _CONNECTOR, "min_date": max_min_last_played},
    )
    op.create_primary_key(
        "viewing_sync_cursor_pkey", "viewing_sync_cursor", ["connector"]
    )
