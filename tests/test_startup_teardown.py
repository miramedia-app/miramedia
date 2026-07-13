"""Unit tests for startup teardown lifecycle."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import miramedia.settings.reload as settings_reload
import miramedia.startup as startup


def test_shutdown_startup_cancels_outstanding_startup_tasks() -> None:
    async def run() -> None:
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
        )

        assert task.cancelled()

    asyncio.run(run())


def test_shutdown_startup_is_idempotent_within_one_context() -> None:
    stop_calls = 0
    original_stop = settings_reload.stop_settings_revision_subscriber

    async def counting_stop() -> None:
        nonlocal stop_calls
        stop_calls += 1
        await original_stop()

    async def run() -> None:
        ctx = startup.SchedulerContext()
        with patch.object(
            settings_reload,
            "stop_settings_revision_subscriber",
            counting_stop,
        ):
            await startup.shutdown_startup(ctx, None)
            await startup.shutdown_startup(ctx, None)
        assert stop_calls == 1

    asyncio.run(run())


def test_two_sequential_contexts_each_shutdown_subscriber() -> None:
    stop_calls = 0
    original_stop = settings_reload.stop_settings_revision_subscriber

    async def counting_stop() -> None:
        nonlocal stop_calls
        stop_calls += 1
        await original_stop()

    async def run() -> None:
        with patch.object(
            settings_reload,
            "stop_settings_revision_subscriber",
            counting_stop,
        ):
            await startup.shutdown_startup(startup.SchedulerContext(), None)
            await startup.shutdown_startup(startup.SchedulerContext(), None)
        assert stop_calls == 2

    asyncio.run(run())


def test_concurrent_shutdown_for_same_context_stops_subscriber_once() -> None:
    stop_calls = 0
    entered = asyncio.Event()
    release = asyncio.Event()
    original_stop = settings_reload.stop_settings_revision_subscriber

    async def gated_stop() -> None:
        nonlocal stop_calls
        stop_calls += 1
        entered.set()
        await release.wait()
        await original_stop()

    async def run() -> None:
        ctx = startup.SchedulerContext()
        with patch.object(
            settings_reload,
            "stop_settings_revision_subscriber",
            gated_stop,
        ):
            first = asyncio.create_task(startup.shutdown_startup(ctx, None))
            await entered.wait()
            second = asyncio.create_task(startup.shutdown_startup(ctx, None))
            release.set()
            await asyncio.gather(first, second)
        assert stop_calls == 1

    asyncio.run(run())
