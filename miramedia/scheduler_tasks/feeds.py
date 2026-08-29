"""Scheduler task for observe-only release feeds."""

from __future__ import annotations

import logging

from miramedia.config import MiraMediaConfig

log = logging.getLogger(__name__)


async def observe_release_feeds() -> None:
    if not MiraMediaConfig().misc.release_feeds_enabled:
        return

    from miramedia.database import SessionLocalBackground
    from miramedia.feeds.service import FeedObserveService

    if SessionLocalBackground is None:
        log.warning("SessionLocalBackground unavailable — skipping feed poll")
        return

    async with SessionLocalBackground() as db:
        service = FeedObserveService(db)
        try:
            await service.poll_once()
            # poll_once commits the lease before external I/O; this commit
            # persists post-poll observations and terminal lease writes.
            await db.commit()
        except Exception:
            await db.rollback()
            log.exception("Observe release feed poll failed")
