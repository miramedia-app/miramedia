"""Unit tests for startup teardown lifecycle."""

from __future__ import annotations

import asyncio

import miramedia.startup as startup


def test_shutdown_startup_cancels_outstanding_startup_tasks() -> None:
    async def run() -> None:
        startup.reset_startup_shutdown_state_for_tests()
        started = asyncio.Event()

        async def long_running() -> None:
            started.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(long_running())
        startup._startup_tasks.add(task)
        task.add_done_callback(startup._startup_tasks.discard)
        await started.wait()

        await startup.shutdown_startup(
            startup.SchedulerContext(),
            native_client=None,
            event_bridge_started=False,
        )

        assert task.cancelled()

    asyncio.run(run())
