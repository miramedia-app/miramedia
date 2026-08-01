"""Optional filesystem watcher that invalidates denormalized download state.

Enabled when ``MIRAMEDIA_LIBRARY_WATCHER=true``. Uses a lightweight polling loop
so we do not add a hard dependency on ``watchdog`` in the default image.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)


async def run_library_watcher() -> None:
    interval = max(30, int(os.getenv("MIRAMEDIA_LIBRARY_WATCHER_INTERVAL", "120")))
    from miramedia.config import MiraMediaConfig

    cfg = MiraMediaConfig()
    roots: list[Path] = [
        Path(lib.path)
        for lib in cfg.misc.movie_libraries + cfg.misc.show_libraries
        if lib.path
    ]

    if not roots:
        log.info("Library watcher: no library roots configured")
        return

    log.info("Library watcher polling %d roots every %ds", len(roots), interval)
    while True:
        await asyncio.sleep(interval)
        try:
            from miramedia.database import SessionLocalBackground
            from miramedia.disk_scan import invalidate_disk_scan_cache
            from miramedia.media_state import refresh_media_state

            async with SessionLocalBackground() as db:
                await refresh_media_state(db)
                await db.commit()
            invalidate_disk_scan_cache()
        except Exception:
            log.exception("Library watcher refresh failed")
