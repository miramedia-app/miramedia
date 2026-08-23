"""Unit tests for watchlist auto-remove helpers."""

from __future__ import annotations

import uuid
from collections.abc import Generator
from unittest.mock import AsyncMock, patch

import pytest

from miramedia.config import MiraMediaConfig
from miramedia.watchlists.auto_remove import (
    auto_remove_watched_from_lists,
    auto_remove_watched_media_ids,
)
from tests.fakes.repositories import FakeWatchlistRepository

pytestmark = pytest.mark.anyio


async def _fake_with_item(
    *,
    user_id: uuid.UUID,
    media_kind: str = "movie",
    media_id: uuid.UUID | None = None,
) -> tuple[FakeWatchlistRepository, uuid.UUID]:
    fake = FakeWatchlistRepository()
    row = await fake.create(user_id=user_id, name="List", description=None)
    resolved_media_id = media_id or uuid.uuid4()
    await fake.add_item(
        user_id=user_id,
        watchlist_id=row.id,
        media_kind=media_kind,
        media_id=resolved_media_id,
    )
    return fake, resolved_media_id


def _patch_fake_repo(
    monkeypatch: pytest.MonkeyPatch, fake: FakeWatchlistRepository
) -> None:
    class StubRepo:
        def __init__(self, db) -> None:
            self._db = db
            self.delete_items_for_media = fake.delete_items_for_media
            self.delete_items_for_media_ids = fake.delete_items_for_media_ids

    monkeypatch.setattr(
        "miramedia.watchlists.auto_remove.WatchlistRepository", StubRepo
    )


@pytest.fixture(autouse=True)
def _restore_watchlists_config() -> Generator[None]:
    cfg = MiraMediaConfig().watchlists
    native = cfg.native
    original = (
        cfg.auto_remove_watched,
        cfg.max_lists_per_user,
        cfg.max_items_per_list,
        native.enabled,
        native.custom_lists,
        native.upcoming,
        native.upcoming_default_past_days,
        native.upcoming_default_future_days,
    )
    yield
    (
        cfg.auto_remove_watched,
        cfg.max_lists_per_user,
        cfg.max_items_per_list,
        native.enabled,
        native.custom_lists,
        native.upcoming,
        native.upcoming_default_past_days,
        native.upcoming_default_future_days,
    ) = original


async def test_auto_remove_watched_media_ids_empty_list_returns_zero() -> None:
    with patch("miramedia.watchlists.auto_remove.WatchlistRepository") as repo_cls:
        removed = await auto_remove_watched_media_ids(
            AsyncMock(),
            user_id=uuid.uuid4(),
            media_kind="episode",
            media_ids=[],
        )
        assert removed == 0
        repo_cls.assert_not_called()


async def test_auto_remove_watched_media_ids_batches_when_flags_on() -> None:
    cfg = MiraMediaConfig().watchlists
    cfg.auto_remove_watched = True
    cfg.native.enabled = True
    cfg.native.custom_lists = True

    user_id = uuid.uuid4()
    media_ids = [uuid.uuid4(), uuid.uuid4(), uuid.uuid4()]
    delete_mock = AsyncMock(return_value=3)
    init_calls: list[object] = []

    class StubRepo:
        def __init__(self, db) -> None:
            init_calls.append(db)
            self.delete_items_for_media_ids = delete_mock

    with patch("miramedia.watchlists.auto_remove.WatchlistRepository", StubRepo):
        removed = await auto_remove_watched_media_ids(
            AsyncMock(),
            user_id=user_id,
            media_kind="episode",
            media_ids=media_ids,
        )

    assert removed == 3
    assert len(init_calls) == 1
    delete_mock.assert_awaited_once_with(
        user_id=user_id,
        media_kind="episode",
        media_ids=media_ids,
    )


async def test_auto_remove_watched_media_ids_skips_when_auto_remove_disabled() -> None:
    cfg = MiraMediaConfig().watchlists
    cfg.auto_remove_watched = False
    cfg.native.enabled = True
    cfg.native.custom_lists = True

    with patch("miramedia.watchlists.auto_remove.WatchlistRepository") as repo_cls:
        removed = await auto_remove_watched_media_ids(
            AsyncMock(),
            user_id=uuid.uuid4(),
            media_kind="episode",
            media_ids=[uuid.uuid4()],
        )
        assert removed == 0
        repo_cls.assert_not_called()


async def test_auto_remove_watched_media_ids_skips_when_custom_lists_disabled() -> None:
    cfg = MiraMediaConfig().watchlists
    cfg.auto_remove_watched = True
    cfg.native.enabled = True
    cfg.native.custom_lists = False

    with patch("miramedia.watchlists.auto_remove.WatchlistRepository") as repo_cls:
        removed = await auto_remove_watched_media_ids(
            AsyncMock(),
            user_id=uuid.uuid4(),
            media_kind="episode",
            media_ids=[uuid.uuid4()],
        )
        assert removed == 0
        repo_cls.assert_not_called()


async def test_auto_remove_watched_from_lists_removes_when_flags_on(
    monkeypatch,
) -> None:
    cfg = MiraMediaConfig().watchlists
    cfg.auto_remove_watched = True
    cfg.native.enabled = True
    cfg.native.custom_lists = True

    user_id = uuid.uuid4()
    fake, media_id = await _fake_with_item(user_id=user_id)
    _patch_fake_repo(monkeypatch, fake)

    removed = await auto_remove_watched_from_lists(
        AsyncMock(),
        user_id=user_id,
        media_kind="movie",
        media_id=media_id,
    )

    assert removed == 1
    assert fake.items == {}


async def test_auto_remove_watched_from_lists_skips_when_auto_remove_disabled(
    monkeypatch,
) -> None:
    cfg = MiraMediaConfig().watchlists
    cfg.auto_remove_watched = False
    cfg.native.enabled = True
    cfg.native.custom_lists = True

    user_id = uuid.uuid4()
    fake, media_id = await _fake_with_item(user_id=user_id)
    _patch_fake_repo(monkeypatch, fake)

    removed = await auto_remove_watched_from_lists(
        AsyncMock(),
        user_id=user_id,
        media_kind="movie",
        media_id=media_id,
    )

    assert removed == 0
    assert len(fake.items) == 1


async def test_auto_remove_watched_from_lists_skips_when_custom_lists_disabled(
    monkeypatch,
) -> None:
    cfg = MiraMediaConfig().watchlists
    cfg.auto_remove_watched = True
    cfg.native.enabled = True
    cfg.native.custom_lists = False

    user_id = uuid.uuid4()
    fake, media_id = await _fake_with_item(user_id=user_id)
    _patch_fake_repo(monkeypatch, fake)

    removed = await auto_remove_watched_from_lists(
        AsyncMock(),
        user_id=user_id,
        media_kind="movie",
        media_id=media_id,
    )

    assert removed == 0
    assert len(fake.items) == 1


async def test_auto_remove_watched_from_lists_skips_when_native_disabled(
    monkeypatch,
) -> None:
    cfg = MiraMediaConfig().watchlists
    cfg.auto_remove_watched = True
    cfg.native.enabled = False
    cfg.native.custom_lists = True

    user_id = uuid.uuid4()
    fake, media_id = await _fake_with_item(user_id=user_id)
    _patch_fake_repo(monkeypatch, fake)

    removed = await auto_remove_watched_from_lists(
        AsyncMock(),
        user_id=user_id,
        media_kind="movie",
        media_id=media_id,
    )

    assert removed == 0
    assert len(fake.items) == 1


async def test_auto_remove_watched_media_ids_removes_when_flags_on(monkeypatch) -> None:
    cfg = MiraMediaConfig().watchlists
    cfg.auto_remove_watched = True
    cfg.native.enabled = True
    cfg.native.custom_lists = True

    user_id = uuid.uuid4()
    fake, media_id = await _fake_with_item(user_id=user_id, media_kind="episode")
    _patch_fake_repo(monkeypatch, fake)

    removed = await auto_remove_watched_media_ids(
        AsyncMock(),
        user_id=user_id,
        media_kind="episode",
        media_ids=[media_id],
    )

    assert removed == 1
    assert fake.items == {}


async def test_auto_remove_watched_media_ids_skips_when_native_disabled(
    monkeypatch,
) -> None:
    cfg = MiraMediaConfig().watchlists
    cfg.auto_remove_watched = True
    cfg.native.enabled = False
    cfg.native.custom_lists = True

    user_id = uuid.uuid4()
    fake, media_id = await _fake_with_item(user_id=user_id, media_kind="episode")
    _patch_fake_repo(monkeypatch, fake)

    removed = await auto_remove_watched_media_ids(
        AsyncMock(),
        user_id=user_id,
        media_kind="episode",
        media_ids=[media_id],
    )

    assert removed == 0
    assert len(fake.items) == 1
