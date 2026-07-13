"""Tests for native-indexer last_success_at recording from worker threads."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from miramedia.indexers.backends.native import NativeIndexer, invalidate_native_indexer


def _minimal_indexer() -> NativeIndexer:
    return NativeIndexer(db_sites=[])


class TestNativeSuccessRecording:
    def setup_method(self) -> None:
        invalidate_native_indexer()

    def teardown_method(self) -> None:
        invalidate_native_indexer()

    def test_native_record_successes_schedules_from_worker_thread(self) -> None:
        async def _run() -> None:
            indexer = _minimal_indexer()
            indexer._loop = asyncio.get_running_loop()
            recorded = asyncio.Event()

            async def _mark_recorded(_site_id: object) -> None:
                recorded.set()

            mock_repo = MagicMock()
            mock_repo.record_site_success = AsyncMock(side_effect=_mark_recorded)

            session_cm = MagicMock()
            session_cm.__aenter__ = AsyncMock(return_value=MagicMock())
            session_cm.__aexit__ = AsyncMock(return_value=None)

            with (
                patch(
                    "miramedia.database.SessionLocalBackground",
                    return_value=session_cm,
                ),
                patch(
                    "miramedia.indexers.repository.IndexerRepository",
                    return_value=mock_repo,
                ),
            ):
                await asyncio.to_thread(indexer._record_successes_threadsafe, [1, 1, 2])
                await asyncio.wait_for(recorded.wait(), timeout=1.0)

            mock_repo.record_site_success.assert_awaited()
            assert {
                c.args[0] for c in mock_repo.record_site_success.await_args_list
            } == {
                1,
                2,
            }

        asyncio.run(_run())

    def test_native_record_successes_no_loop_noop(self) -> None:
        indexer = _minimal_indexer()
        indexer._loop = None
        indexer._record_successes_threadsafe([1])
