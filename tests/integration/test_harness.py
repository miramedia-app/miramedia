"""Harness regression tests for session tracking and truncation."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from tests.integration.conftest import _TrackedSessions, _truncate_application_tables

pytestmark = pytest.mark.integration


def test_tracked_sessions_close_all_releases_open_transaction(
    tracked_sessions: _TrackedSessions,
    run_async,
    integration_engine,
) -> None:
    session = tracked_sessions.open()

    async def _touch() -> None:
        await session.execute(text("SELECT 1"))
        assert session.in_transaction()

    run_async(_touch())
    run_async(tracked_sessions.close_all())
    assert not session.in_transaction()

    async def _truncate() -> None:
        await _truncate_application_tables(integration_engine)

    run_async(_truncate())


def test_make_session_fixture_closes_leaked_session(make_session, run_async) -> None:
    session = make_session()

    async def _touch() -> None:
        await session.execute(text("SELECT 1"))

    run_async(_touch())
    # No manual close — ``make_session`` teardown must rollback/close before truncate.
