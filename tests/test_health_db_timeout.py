"""Health checks must fail fast on a stalled Postgres and stay quiet."""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import MagicMock, patch

import pytest

from miramedia.core.router import (
    HEALTH_DB_TIMEOUT_SECONDS,
    _detailed_health,
    _run_health_db_query,
)


class _HangingConnect:
    async def __aenter__(self):
        await asyncio.sleep(30)
        msg = "connect should have been timed out"
        raise AssertionError(msg)

    async def __aexit__(self, *_args):
        return False


class _HangingEngine:
    def connect(self):
        return _HangingConnect()


def test_health_db_query_times_out_hanging_connect() -> None:
    with pytest.raises(TimeoutError):
        asyncio.run(_run_health_db_query(_HangingEngine(), "SELECT 1", seconds=0.05))


def test_detailed_health_db_connect_timeout_is_quiet(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)
    engine = MagicMock()
    engine.connect.side_effect = lambda: _HangingConnect()
    pool = MagicMock()
    pool.size.return_value = 1
    pool.checkedout.return_value = 0
    pool.overflow.return_value = 0
    request_engine = MagicMock(pool=pool)

    with (
        patch("miramedia.core.router.HEALTH_DB_TIMEOUT_SECONDS", 0.05),
        patch("miramedia.database.healthcheck_engine", engine),
        patch("miramedia.database.get_engine", return_value=request_engine),
        patch("miramedia.database.background_engine", None),
        patch("miramedia.database.export_pool_gauges"),
        patch("miramedia.core.router._get_expected_alembic_head", return_value="abc"),
        patch("miramedia.metadata.cache.get_all_cache_stats", return_value={}),
    ):
        payload = asyncio.run(_detailed_health())

    assert payload["db"]["ok"] is False
    assert payload["db"]["error"] == "timeout"
    assert payload["alembic"]["ok"] is False
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings
    assert all(r.exc_info is None for r in warnings)


def test_health_db_timeout_constant_is_under_docker_probe() -> None:
    assert HEALTH_DB_TIMEOUT_SECONDS <= 3
