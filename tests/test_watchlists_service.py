"""Service-level tests for private watchlists."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from miramedia.exceptions import ConflictError, NotFoundError, UnprocessableEntityError
from miramedia.watchlists.schemas import (
    WatchlistCreate,
    WatchlistItemCreate,
    WatchlistReorder,
    WatchlistUpdate,
)
from miramedia.watchlists.service import WatchlistService
from tests.fakes.repositories import FakeWatchlistRepository

pytestmark = pytest.mark.anyio


def _service(
    *,
    repository: FakeWatchlistRepository | None = None,
    movie_repository: AsyncMock | None = None,
    show_repository: AsyncMock | None = None,
) -> tuple[WatchlistService, FakeWatchlistRepository, AsyncMock, AsyncMock]:
    repo = repository or FakeWatchlistRepository()
    movie_repo = movie_repository or AsyncMock()
    show_repo = show_repository or AsyncMock()
    return WatchlistService(repo, movie_repo, show_repo), repo, movie_repo, show_repo


async def test_create_trims_name_and_description() -> None:
    user_id = uuid.uuid4()
    service, repo, _movie_repo, _show_repo = _service()
    detail = await service.create_watchlist(
        user_id=user_id,
        data=WatchlistCreate(name="  Favorites  ", description="  Notes  "),
    )
    assert detail.name == "Favorites"
    assert detail.description == "Notes"
    stored = repo.watchlists[detail.id]
    assert stored.name == "Favorites"
    assert stored.description == "Notes"


async def test_create_rejects_empty_name_after_trim() -> None:
    user_id = uuid.uuid4()
    service, _repo, _movie_repo, _show_repo = _service()
    with pytest.raises(UnprocessableEntityError, match="1 and 80 characters"):
        await service.create_watchlist(
            user_id=user_id,
            data=WatchlistCreate(name="   "),
        )


async def test_create_rejects_name_over_80_chars() -> None:
    user_id = uuid.uuid4()
    service, _repo, _movie_repo, _show_repo = _service()
    with pytest.raises(UnprocessableEntityError, match="1 and 80 characters"):
        await service.create_watchlist(
            user_id=user_id,
            data=WatchlistCreate(name="x" * 81),
        )


async def test_create_rejects_duplicate_name_case_insensitively() -> None:
    user_id = uuid.uuid4()
    service, repo, _movie_repo, _show_repo = _service()
    await service.create_watchlist(
        user_id=user_id,
        data=WatchlistCreate(name="Favorites"),
    )
    with pytest.raises(ConflictError, match="already exists"):
        await service.create_watchlist(
            user_id=user_id,
            data=WatchlistCreate(name="favorites"),
        )
    assert len(repo.watchlists) == 1


async def test_list_summaries_includes_cover_from_first_item() -> None:
    user_id = uuid.uuid4()
    movie_a = uuid.uuid4()
    movie_b = uuid.uuid4()
    service, _repo, movie_repo, _show_repo = _service()
    movie_repo.get_movie_by_id.return_value = object()
    created = await service.create_watchlist(
        user_id=user_id,
        data=WatchlistCreate(name="Covered"),
    )
    empty = await service.create_watchlist(
        user_id=user_id,
        data=WatchlistCreate(name="Empty"),
    )
    await service.add_item(
        user_id=user_id,
        watchlist_id=created.id,
        data=WatchlistItemCreate(media_kind="movie", media_id=movie_a),
    )
    await service.add_item(
        user_id=user_id,
        watchlist_id=created.id,
        data=WatchlistItemCreate(media_kind="movie", media_id=movie_b),
    )
    summaries = await service.list_watchlists(user_id=user_id)
    by_id = {row.id: row for row in summaries}
    assert by_id[created.id].cover_poster_media_id == movie_a
    assert by_id[created.id].item_count == 2
    assert by_id[empty.id].cover_poster_media_id is None
    assert by_id[empty.id].item_count == 0

    owner_id = uuid.uuid4()
    other_id = uuid.uuid4()
    service, _repo, _movie_repo, _show_repo = _service()
    created = await service.create_watchlist(
        user_id=owner_id,
        data=WatchlistCreate(name="Mine"),
    )
    assert (
        await service.get_watchlist(user_id=other_id, watchlist_id=created.id) is None
    )


async def test_add_item_returns_existing_on_duplicate() -> None:
    user_id = uuid.uuid4()
    movie_id = uuid.uuid4()
    service, repo, movie_repo, _show_repo = _service()
    movie_repo.get_movie_by_id.return_value = object()
    created = await service.create_watchlist(
        user_id=user_id,
        data=WatchlistCreate(name="List"),
    )
    first, created_flag = await service.add_item(
        user_id=user_id,
        watchlist_id=created.id,
        data=WatchlistItemCreate(media_kind="movie", media_id=movie_id),
    )
    second, duplicate_flag = await service.add_item(
        user_id=user_id,
        watchlist_id=created.id,
        data=WatchlistItemCreate(media_kind="movie", media_id=movie_id),
    )
    assert created_flag is True
    assert duplicate_flag is False
    assert first.id == second.id
    assert len(repo.items_for(created.id)) == 1


async def test_add_item_unknown_movie_raises_404() -> None:

    user_id = uuid.uuid4()
    service, _repo, movie_repo, _show_repo = _service()
    movie_repo.get_movie_by_id.side_effect = NotFoundError("missing")
    created = await service.create_watchlist(
        user_id=user_id,
        data=WatchlistCreate(name="List"),
    )
    with pytest.raises(NotFoundError) as exc:
        await service.add_item(
            user_id=user_id,
            watchlist_id=created.id,
            data=WatchlistItemCreate(media_kind="movie", media_id=uuid.uuid4()),
        )
    assert str(exc.value) == "Movie not found"


async def test_reorder_requires_exact_permutation() -> None:
    user_id = uuid.uuid4()
    movie_a = uuid.uuid4()
    movie_b = uuid.uuid4()
    service, _repo, movie_repo, _show_repo = _service()
    movie_repo.get_movie_by_id.return_value = object()
    created = await service.create_watchlist(
        user_id=user_id,
        data=WatchlistCreate(name="List"),
    )
    item_a, _ = await service.add_item(
        user_id=user_id,
        watchlist_id=created.id,
        data=WatchlistItemCreate(media_kind="movie", media_id=movie_a),
    )
    item_b, _ = await service.add_item(
        user_id=user_id,
        watchlist_id=created.id,
        data=WatchlistItemCreate(media_kind="movie", media_id=movie_b),
    )
    before = await service.get_watchlist(user_id=user_id, watchlist_id=created.id)
    assert before is not None
    assert [item.id for item in before.items] == [item_a.id, item_b.id]

    with pytest.raises(UnprocessableEntityError, match="exact permutation"):
        await service.reorder_items(
            user_id=user_id,
            watchlist_id=created.id,
            data=WatchlistReorder(item_ids=[item_a.id]),
        )

    after = await service.get_watchlist(user_id=user_id, watchlist_id=created.id)
    assert after is not None
    assert [item.id for item in after.items] == [item_a.id, item_b.id]


async def test_reorder_applies_new_order() -> None:
    user_id = uuid.uuid4()
    movie_a = uuid.uuid4()
    movie_b = uuid.uuid4()
    service, _repo, movie_repo, _show_repo = _service()
    movie_repo.get_movie_by_id.return_value = object()
    created = await service.create_watchlist(
        user_id=user_id,
        data=WatchlistCreate(name="List"),
    )
    item_a, _ = await service.add_item(
        user_id=user_id,
        watchlist_id=created.id,
        data=WatchlistItemCreate(media_kind="movie", media_id=movie_a),
    )
    item_b, _ = await service.add_item(
        user_id=user_id,
        watchlist_id=created.id,
        data=WatchlistItemCreate(media_kind="movie", media_id=movie_b),
    )
    detail = await service.reorder_items(
        user_id=user_id,
        watchlist_id=created.id,
        data=WatchlistReorder(item_ids=[item_b.id, item_a.id]),
    )
    assert [item.id for item in detail.items] == [item_b.id, item_a.id]
    assert [item.position for item in detail.items] == [0, 1]


async def test_delete_watchlist_is_owner_scoped() -> None:
    owner_id = uuid.uuid4()
    other_id = uuid.uuid4()
    service, _repo, _movie_repo, _show_repo = _service()
    created = await service.create_watchlist(
        user_id=owner_id,
        data=WatchlistCreate(name="Mine"),
    )
    assert (
        await service.delete_watchlist(user_id=other_id, watchlist_id=created.id)
        is False
    )
    assert (
        await service.delete_watchlist(user_id=owner_id, watchlist_id=created.id)
        is True
    )
    assert (
        await service.get_watchlist(user_id=owner_id, watchlist_id=created.id) is None
    )


async def test_create_conflict_from_commit_race_maps_to_conflict_error() -> None:
    user_id = uuid.uuid4()
    repository = AsyncMock()
    repository.name_taken = AsyncMock(return_value=False)
    repository.create = AsyncMock(
        side_effect=ConflictError("A watchlist with this name already exists")
    )
    service, _repo, _movie_repo, _show_repo = _service(repository=repository)
    with pytest.raises(ConflictError, match="already exists"):
        await service.create_watchlist(
            user_id=user_id,
            data=WatchlistCreate(name="Dup"),
        )


async def test_update_conflict_from_commit_race_maps_to_conflict_error() -> None:
    user_id = uuid.uuid4()
    watchlist_id = uuid.uuid4()
    repository = AsyncMock()
    repository.name_taken = AsyncMock(return_value=False)
    repository.update = AsyncMock(
        side_effect=ConflictError("A watchlist with this name already exists")
    )
    service, _repo, _movie_repo, _show_repo = _service(repository=repository)
    with pytest.raises(ConflictError, match="already exists"):
        await service.update_watchlist(
            user_id=user_id,
            watchlist_id=watchlist_id,
            data=WatchlistUpdate(name="Dup"),
        )


async def test_update_watchlist_rejects_duplicate_rename() -> None:
    user_id = uuid.uuid4()
    service, _repo, _movie_repo, _show_repo = _service()
    first = await service.create_watchlist(
        user_id=user_id,
        data=WatchlistCreate(name="Alpha"),
    )
    second = await service.create_watchlist(
        user_id=user_id,
        data=WatchlistCreate(name="Beta"),
    )
    with pytest.raises(ConflictError, match="already exists"):
        await service.update_watchlist(
            user_id=user_id,
            watchlist_id=second.id,
            data=WatchlistUpdate(name="alpha"),
        )
    unchanged = await service.get_watchlist(user_id=user_id, watchlist_id=first.id)
    assert unchanged is not None
    assert unchanged.name == "Alpha"
