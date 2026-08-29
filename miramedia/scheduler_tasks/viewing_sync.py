"""Scheduler task for Jellyfin viewing-state dry-run."""

from __future__ import annotations

import logging

from miramedia.config import MiraMediaConfig

log = logging.getLogger(__name__)


async def jellyfin_viewing_state_dry_run() -> None:
    if not MiraMediaConfig().viewing_sync.enabled:
        return

    from miramedia.database import SessionLocalBackground
    from miramedia.viewing_sync.service import ViewingSyncDryRunService

    if SessionLocalBackground is None:
        log.warning("SessionLocalBackground unavailable — skipping viewing-sync poll")
        return

    async with SessionLocalBackground() as db:
        service = ViewingSyncDryRunService(db)
        try:
            await service.poll_once()
            await db.commit()
        except Exception:
            await db.rollback()
            log.exception("Jellyfin viewing-state dry-run failed")
