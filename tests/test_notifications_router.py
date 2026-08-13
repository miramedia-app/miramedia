"""Route-level tests for /api/v1/notifications read-state endpoints."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from miramedia.exceptions import NotFoundError
from miramedia.notifications.schemas import NotificationId

PREFIX = "/api/v1/notifications"


@pytest.fixture
def notifications_client(
    override_dependency: Callable[[Callable, object], None],
) -> Callable[..., tuple[TestClient, NotificationId, MagicMock]]:
    from miramedia.auth.users import current_active_user
    from miramedia.database import get_session
    from miramedia.main import app
    from miramedia.notifications.dependencies import get_notification_service

    def make(
        *,
        user_id: uuid.UUID | None = None,
        service: MagicMock | None = None,
    ) -> tuple[TestClient, NotificationId, MagicMock]:
        notification_id = NotificationId(uuid.uuid4())
        user_id = user_id or uuid.uuid4()
        if service is None:
            service = MagicMock()
            service.mark_notification_as_read = AsyncMock(return_value=None)
            service.mark_notification_as_unread = AsyncMock(return_value=None)

        async def _stub_session() -> None:
            yield None

        async def _active_user() -> MagicMock:
            user = MagicMock()
            user.id = user_id
            return user

        def _notification_service() -> MagicMock:
            return service

        override_dependency(get_session, _stub_session)
        override_dependency(current_active_user, _active_user)
        override_dependency(get_notification_service, _notification_service)
        return TestClient(app, raise_server_exceptions=False), notification_id, service

    return make


def test_mark_notification_as_read_returns_204(notifications_client) -> None:
    client, notification_id, service = notifications_client()
    response = client.patch(f"{PREFIX}/{notification_id}/read")
    assert response.status_code == 204
    service.mark_notification_as_read.assert_awaited_once_with(nid=notification_id)


def test_mark_notification_as_read_missing_returns_404(notifications_client) -> None:
    client, notification_id, service = notifications_client()
    service.mark_notification_as_read.side_effect = NotFoundError(
        f"Notification with id {notification_id} not found."
    )
    response = client.patch(f"{PREFIX}/{notification_id}/read")
    assert response.status_code == 404
    assert response.json()["detail"] == (
        f"Notification with id {notification_id} not found."
    )


def test_mark_notification_as_unread_returns_204(notifications_client) -> None:
    client, notification_id, service = notifications_client()
    response = client.patch(f"{PREFIX}/{notification_id}/unread")
    assert response.status_code == 204
    service.mark_notification_as_unread.assert_awaited_once_with(nid=notification_id)


def test_mark_notification_as_unread_missing_returns_404(notifications_client) -> None:
    client, notification_id, service = notifications_client()
    service.mark_notification_as_unread.side_effect = NotFoundError(
        f"Notification with id {notification_id} not found."
    )
    response = client.patch(f"{PREFIX}/{notification_id}/unread")
    assert response.status_code == 404
    assert response.json()["detail"] == (
        f"Notification with id {notification_id} not found."
    )
