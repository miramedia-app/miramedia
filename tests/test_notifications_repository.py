"""Repository tests for notification read-state updates."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from unittest.mock import AsyncMock

import pytest

from miramedia.exceptions import NotFoundError
from miramedia.notifications.repository import NotificationRepository
from miramedia.notifications.schemas import NotificationId


@dataclass
class _RowcountResult:
    rowcount: int


@pytest.mark.parametrize(
    "method_name",
    ["mark_notification_as_read", "mark_notification_as_unread"],
)
def test_mark_notification_existing_row_succeeds(method_name: str) -> None:
    nid = NotificationId(uuid.uuid4())

    async def _run() -> None:
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_RowcountResult(1))
        repo = NotificationRepository(db)
        await getattr(repo, method_name)(nid)
        db.execute.assert_awaited_once()

    asyncio.run(_run())


@pytest.mark.parametrize(
    "method_name",
    ["mark_notification_as_read", "mark_notification_as_unread"],
)
def test_mark_notification_missing_row_raises_not_found(method_name: str) -> None:
    nid = NotificationId(uuid.uuid4())

    async def _run() -> None:
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_RowcountResult(0))
        repo = NotificationRepository(db)
        with pytest.raises(NotFoundError, match=str(nid)):
            await getattr(repo, method_name)(nid)

    asyncio.run(_run())
