"""Dedicated Taskiq scheduler process (no HTTP server).

Run via::

    python -m miramedia.scheduler_entrypoint

Pair with ``MIRAMEDIA_SCHEDULER_DISABLED=true`` on API workers so cron tasks
are not duplicated across Uvicorn children.
"""

from __future__ import annotations

import asyncio
import logging

from miramedia.logging import setup_logging

setup_logging()
log = logging.getLogger(__name__)


async def _run() -> None:
    from sqlalchemy import text
    from taskiq.receiver import Receiver
    from taskiq_fastapi import populate_dependency_context

    from miramedia.config import MiraMediaConfig
    from miramedia.database import get_engine, init_engine
    from miramedia.main import app
    from miramedia.scheduler import (
        background_broker,
        build_scheduler_loop,
        interactive_broker,
    )

    config = MiraMediaConfig()
    init_engine(config.database)

    conn = await get_engine().connect()
    if not await conn.scalar(text("SELECT pg_try_advisory_lock(4871260042)")):
        log.error("Another process holds the scheduler lock; exiting")
        await conn.close()
        return

    for b in (interactive_broker, background_broker):
        await b.startup()
        populate_dependency_context(b, app)
    loop = build_scheduler_loop()
    for source in loop.scheduler.sources:
        await source.startup()

    interactive_finish = asyncio.Event()
    background_finish = asyncio.Event()
    interactive_receiver = Receiver(
        interactive_broker, run_startup=False, max_async_tasks=8
    )
    background_receiver = Receiver(
        background_broker, run_startup=False, max_async_tasks=2
    )
    tasks = [
        asyncio.create_task(interactive_receiver.listen(interactive_finish)),
        asyncio.create_task(background_receiver.listen(background_finish)),
        asyncio.create_task(loop.run(skip_first_run=True)),
    ]
    log.info("Scheduler entrypoint running")
    await asyncio.gather(*tasks)


def main() -> None:
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        log.info("Scheduler entrypoint stopped")


if __name__ == "__main__":
    main()
