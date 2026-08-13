"""Route-level tests for /api/v1/watchlists endpoints."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from miramedia.exceptions import ConflictError, UnprocessableEntityError
from miramedia.watchlists.schemas import (
    WatchlistDetail,
    WatchlistItemView,
    WatchlistSummary,
)

PREFIX = "/api/v1/watchlists"


def _summary(watchlist_id: uuid.UUID | None = None) -> WatchlistSummary:
    now = datetime.now(UTC)
    return WatchlistSummary(
        id=watchlist_id or uuid.uuid4(),
        name="Favorites",
        description=None,
        item_count=0,
        cover_poster_media_id=None,
        created_at=now,
        updated_at=now,
    )


def _detail(watchlist_id: uuid.UUID | None = None) -> WatchlistDetail:
    now = datetime.now(UTC)
    wid = watchlist_id or uuid.uuid4()
    return WatchlistDetail(
        id=wid,
        name="Favorites",
        description=None,
        items=[],
        created_at=now,
        updated_at=now,
    )


def _item_view() -> WatchlistItemView:
    movie_id = uuid.uuid4()
    return WatchlistItemView(
        id=uuid.uuid4(),
        position=0,
        media_kind="movie",
        media_id=movie_id,
        title="Movie",
        poster_media_id=movie_id,
        watched=False,
    )


@pytest.fixture
def watchlists_client(
    override_dependency: Callable[[Callable, object], None],
) -> Callable[..., tuple[TestClient, uuid.UUID, MagicMock]]:
    from miramedia.auth.users import current_active_user
    from miramedia.database import get_session
    from miramedia.main import app
    from miramedia.watchlists.dependencies import get_watchlist_service

    def make(
        *,
        user_id: uuid.UUID | None = None,
        service: MagicMock | None = None,
    ) -> tuple[TestClient, uuid.UUID, MagicMock]:
        user_id = user_id or uuid.uuid4()
        if service is None:
            service = MagicMock()
            service.list_watchlists = AsyncMock(return_value=[])
            service.create_watchlist = AsyncMock(return_value=_detail())
            service.get_watchlist = AsyncMock(return_value=None)
            service.update_watchlist = AsyncMock(return_value=None)
            service.delete_watchlist = AsyncMock(return_value=False)
            service.add_item = AsyncMock(return_value=(_item_view(), True))
            service.reorder_items = AsyncMock(return_value=_detail())
            service.remove_item = AsyncMock(return_value=False)

        async def _stub_session() -> None:
            yield None

        async def _active_user() -> MagicMock:
            user = MagicMock()
            user.id = user_id
            return user

        def _watchlist_service() -> MagicMock:
            return service

        override_dependency(get_session, _stub_session)
        override_dependency(current_active_user, _active_user)
        override_dependency(get_watchlist_service, _watchlist_service)
        return TestClient(app, raise_server_exceptions=False), user_id, service

    return make


def test_list_watchlists_passes_owner(watchlists_client) -> None:
    client, user_id, service = watchlists_client()
    response = client.get(PREFIX)
    assert response.status_code == 200
    service.list_watchlists.assert_awaited_once_with(user_id=user_id)


def test_create_watchlist_returns_201(watchlists_client) -> None:
    client, user_id, service = watchlists_client()
    response = client.post(PREFIX, json={"name": "Favorites"})
    assert response.status_code == 201
    service.create_watchlist.assert_awaited_once()
    assert service.create_watchlist.await_args.kwargs["user_id"] == user_id


def test_create_watchlist_duplicate_name_returns_409(watchlists_client) -> None:
    client, _user_id, service = watchlists_client()
    service.create_watchlist.side_effect = ConflictError("Name already exists")
    response = client.post(PREFIX, json={"name": "Favorites"})
    assert response.status_code == 409


def test_get_watchlist_missing_returns_404(watchlists_client) -> None:
    watchlist_id = uuid.uuid4()
    client, user_id, service = watchlists_client()
    response = client.get(f"{PREFIX}/{watchlist_id}")
    assert response.status_code == 404
    service.get_watchlist.assert_awaited_once_with(
        user_id=user_id,
        watchlist_id=watchlist_id,
    )


def test_add_item_created_returns_201(watchlists_client) -> None:
    watchlist_id = uuid.uuid4()
    client, user_id, service = watchlists_client()
    movie_id = uuid.uuid4()
    response = client.post(
        f"{PREFIX}/{watchlist_id}/items",
        json={"media_kind": "movie", "media_id": str(movie_id)},
    )
    assert response.status_code == 201
    service.add_item.assert_awaited_once()
    assert service.add_item.await_args.kwargs["user_id"] == user_id


def test_add_item_duplicate_returns_200(watchlists_client) -> None:
    watchlist_id = uuid.uuid4()
    client, _user_id, service = watchlists_client()
    service.add_item.return_value = (_item_view(), False)
    movie_id = uuid.uuid4()
    response = client.post(
        f"{PREFIX}/{watchlist_id}/items",
        json={"media_kind": "movie", "media_id": str(movie_id)},
    )
    assert response.status_code == 200


def test_reorder_invalid_permutation_returns_422(watchlists_client) -> None:
    watchlist_id = uuid.uuid4()
    client, _user_id, service = watchlists_client()
    service.reorder_items.side_effect = UnprocessableEntityError(
        "item_ids must be an exact permutation"
    )
    response = client.put(
        f"{PREFIX}/{watchlist_id}/items/order",
        json={"item_ids": [str(uuid.uuid4())]},
    )
    assert response.status_code == 422


def test_delete_watchlist_returns_204(watchlists_client) -> None:
    watchlist_id = uuid.uuid4()
    client, user_id, service = watchlists_client()
    service.delete_watchlist.return_value = True
    response = client.delete(f"{PREFIX}/{watchlist_id}")
    assert response.status_code == 204
    service.delete_watchlist.assert_awaited_once_with(
        user_id=user_id,
        watchlist_id=watchlist_id,
    )


def test_delete_watchlist_missing_returns_404(watchlists_client) -> None:
    watchlist_id = uuid.uuid4()
    client, _user_id, _service = watchlists_client()
    response = client.delete(f"{PREFIX}/{watchlist_id}")
    assert response.status_code == 404


def test_create_watchlist_oversized_name_returns_422(watchlists_client) -> None:
    client, _user_id, service = watchlists_client()
    response = client.post(PREFIX, json={"name": "x" * 256})
    assert response.status_code == 422
    service.create_watchlist.assert_not_called()


def test_create_watchlist_oversized_description_returns_422(
    watchlists_client,
) -> None:
    client, _user_id, service = watchlists_client()
    response = client.post(
        PREFIX,
        json={"name": "ok", "description": "x" * 2001},
    )
    assert response.status_code == 422
    service.create_watchlist.assert_not_called()


def test_update_watchlist_empty_name_returns_422(watchlists_client) -> None:
    watchlist_id = uuid.uuid4()
    client, _user_id, service = watchlists_client()
    response = client.patch(f"{PREFIX}/{watchlist_id}", json={"name": ""})
    assert response.status_code == 422
    service.update_watchlist.assert_not_called()


def test_reorder_oversized_item_ids_returns_422(watchlists_client) -> None:
    watchlist_id = uuid.uuid4()
    client, _user_id, service = watchlists_client()
    response = client.put(
        f"{PREFIX}/{watchlist_id}/items/order",
        json={"item_ids": [str(uuid.uuid4()) for _ in range(10_001)]},
    )
    assert response.status_code == 422
    service.reorder_items.assert_not_called()


def test_create_watchlist_max_name_length_accepted_by_schema(
    watchlists_client,
) -> None:
    client, _user_id, service = watchlists_client()
    response = client.post(PREFIX, json={"name": "x" * 255})
    assert response.status_code == 201
    service.create_watchlist.assert_awaited_once()


def test_response_does_not_expose_owner_id(watchlists_client) -> None:
    client, _user_id, service = watchlists_client()
    service.list_watchlists.return_value = [_summary()]
    response = client.get(PREFIX)
    assert response.status_code == 200
    payload = response.json()[0]
    assert "user_id" not in payload
    assert "owner_id" not in payload
