"""persist specials (Season 0) skipped state

Specials used to be force-skipped at display/auto-download time whenever
``download_specials`` was off — a derived override that masked the persisted
``skipped`` flags. That made marking an individual special "wanted" a no-op
(the override re-skipped it on the next read). The override is gone; the
``skipped`` flag is now the single source of truth.

This backfill brings existing rows in line with their previously-displayed
state: when specials auto-download is off, persist Season 0 (and its
not-yet-downloaded episodes) as skipped. When it is on, specials were already
treated as wanted, so nothing changes.

Revision ID: c8d2e3f4a5b6
Revises: b7c1d2e3f4a5
Create Date: 2026-06-29 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c8d2e3f4a5b6"
down_revision: str | None = "b7c1d2e3f4a5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _specials_enabled() -> bool:
    """Read the live ``download_specials`` setting.

    This backfill's data effect depends on app config. If config cannot be
    loaded, abort the migration loudly rather than silently taking the
    destructive branch (marking all specials skipped is irreversible —
    ``downgrade()`` is a no-op).
    """
    try:
        from miramedia.config import MiraMediaConfig

        return bool(MiraMediaConfig().misc.download_specials)
    except Exception as exc:
        raise RuntimeError(
            "persist-specials-skip migration could not load app config to "
            "read misc.download_specials. Fix the config (or set "
            "MIRAMEDIA_MISC__DOWNLOAD_SPECIALS explicitly) and re-run "
            "'alembic upgrade head'."
        ) from exc


def upgrade() -> None:
    if _specials_enabled():
        # Specials were already treated as wanted — leave skipped flags as-is.
        return

    # Mark Season 0 rows skipped, matching the old display/auto-download override.
    op.execute("UPDATE season SET skipped = true WHERE number = 0")
    # Mark their not-yet-downloaded episodes skipped. Downloaded specials keep
    # their flag — the file on disk represents an explicit user choice.
    op.execute(
        """
        UPDATE episode
        SET skipped = true
        WHERE downloaded = false
          AND season_id IN (SELECT id FROM season WHERE number = 0)
        """
    )


def downgrade() -> None:
    # The override-based behaviour was non-destructive; nothing to revert.
    pass
