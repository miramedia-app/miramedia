"""Direct tests for shared metadata refresh impls in media_service."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Literal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from miramedia.media_service import (
    MetadataRefreshHooks,
    _try_update_media_metadata_id_impl,
    _update_all_media_metadata_impl,
)
from miramedia.movies.schemas import Movie, MovieId
from miramedia.shows.schemas import Show, ShowId
from tests.fakes import run_async
from tests.fakes.repositories import make_movie, make_show

MediaKind = Literal["show", "movie"]


class _FakeMetadataRepo:
    def __init__(self) -> None:
        self.db = MagicMock()
        self.mark_failure_calls: list[tuple[ShowId | MovieId, datetime]] = []
        self.stamp_check_calls: list[ShowId | MovieId] = []

    async def mark_metadata_failure(
        self, media_id: ShowId | MovieId, backoff_until: datetime
    ) -> None:
        self.mark_failure_calls.append((media_id, backoff_until))

    async def stamp_metadata_check(self, media_id: ShowId | MovieId) -> None:
        self.stamp_check_calls.append(media_id)


class _FakeBgSession:
    def __init__(self, repo: _FakeMetadataRepo, media_noun: MediaKind) -> None:
        if media_noun == "show":
            self.show_repository = repo
        else:
            self.movie_repository = repo


def _make_media(kind: MediaKind, **updates: object) -> Show | Movie:
    name = str(updates.get("name", "Test Media"))
    year = int(updates["year"]) if "year" in updates else 2020
    if kind == "show":
        return make_show(name=name, year=year).model_copy(update=updates)
    return make_movie(name=name, year=year).model_copy(update=updates)


def _media_id(media: Show | Movie) -> ShowId | MovieId:
    return media.id


def _build_hooks(
    kind: MediaKind,
    repo: _FakeMetadataRepo,
    media: Show | Movie | None,
    *,
    update_metadata: AsyncMock | None = None,
    mark_failure: AsyncMock | None = None,
) -> MetadataRefreshHooks:
    session = _FakeBgSession(repo, kind)

    @asynccontextmanager
    async def bg_service():
        yield session

    async def get_media(
        _svc: object, media_id: ShowId | MovieId
    ) -> Show | Movie | None:
        if media is not None and media.id == media_id:
            return media
        return None

    update = update_metadata or AsyncMock(return_value=media)
    mark = mark_failure or AsyncMock()

    return MetadataRefreshHooks(
        bg_service=bg_service,
        media_noun=kind,
        get_media=get_media,
        update_metadata=update,
        mark_failure=mark,
        fetch_native_metadata=lambda _provider, _imdb_id, _language: None,
    )


def _provider(name: str = "tmdb") -> MagicMock:
    provider = MagicMock()
    provider.name = name
    return provider


@pytest.mark.parametrize("kind", ["show", "movie"])
def test_missing_provider_marks_metadata_failure_with_backoff(
    kind: MediaKind,
) -> None:
    media = _make_media(kind, metadata_provider="disabled")
    repo = _FakeMetadataRepo()
    hooks = _build_hooks(kind, repo, media)

    with patch(
        "miramedia.metadata.dependencies.resolve_metadata_provider",
        return_value=None,
    ):
        run_async(_try_update_media_metadata_id_impl(_media_id(media), hooks=hooks))

    assert len(repo.mark_failure_calls) == 1
    media_id, backoff_until = repo.mark_failure_calls[0]
    assert media_id == media.id
    assert backoff_until > datetime.now(UTC)
    assert not repo.stamp_check_calls


@pytest.mark.parametrize("kind", ["show", "movie"])
def test_native_provider_without_imdb_id_stamps_failure(kind: MediaKind) -> None:
    media = _make_media(
        kind,
        metadata_provider="tmdb",
        external_id="tvdb-123",
        imdb_id=None,
    )
    repo = _FakeMetadataRepo()
    hooks = _build_hooks(kind, repo, media)
    native = _provider("native")

    with patch(
        "miramedia.metadata.dependencies.resolve_metadata_provider",
        return_value=native,
    ):
        run_async(_try_update_media_metadata_id_impl(_media_id(media), hooks=hooks))

    assert len(repo.mark_failure_calls) == 1
    assert repo.mark_failure_calls[0][0] == media.id
    hooks.update_metadata.assert_not_awaited()
    assert not repo.stamp_check_calls


@pytest.mark.parametrize("kind", ["show", "movie"])
def test_update_metadata_none_stamps_failure(kind: MediaKind) -> None:
    media = _make_media(kind, metadata_provider="tmdb")
    repo = _FakeMetadataRepo()
    update = AsyncMock(return_value=None)
    hooks = _build_hooks(kind, repo, media, update_metadata=update)

    with patch(
        "miramedia.metadata.dependencies.resolve_metadata_provider",
        return_value=_provider("tmdb"),
    ):
        run_async(_try_update_media_metadata_id_impl(_media_id(media), hooks=hooks))

    assert len(repo.mark_failure_calls) == 1
    assert repo.mark_failure_calls[0][0] == media.id
    assert not repo.stamp_check_calls


@pytest.mark.parametrize("kind", ["show", "movie"])
def test_successful_update_stamps_check_without_failure_mark(kind: MediaKind) -> None:
    media = _make_media(kind, metadata_provider="tmdb")
    repo = _FakeMetadataRepo()
    updated = media.model_copy(update={"overview": "fresh"})
    update = AsyncMock(return_value=updated)
    hooks = _build_hooks(kind, repo, media, update_metadata=update)

    with patch(
        "miramedia.metadata.dependencies.resolve_metadata_provider",
        return_value=_provider("tmdb"),
    ):
        run_async(_try_update_media_metadata_id_impl(_media_id(media), hooks=hooks))

    assert repo.stamp_check_calls == [media.id]
    assert not repo.mark_failure_calls


@pytest.mark.parametrize("kind", ["show", "movie"])
def test_update_all_terminates_when_batch_returns_seen_ids(kind: MediaKind) -> None:
    media_a = _make_media(kind, name="Alpha")
    media_b = _make_media(kind, name="Beta")
    ids = [_media_id(media_a), _media_id(media_b)]
    batches = [list(ids), list(ids)]
    try_calls: list[ShowId | MovieId] = []

    async def get_ids_due(_svc: object, _cutoff: datetime, _limit: int):
        return batches.pop(0) if batches else []

    async def try_update_one(media_id: ShowId | MovieId) -> None:
        try_calls.append(media_id)

    repo = _FakeMetadataRepo()
    hooks = _build_hooks(kind, repo, None)

    run_async(
        _update_all_media_metadata_impl(
            hooks=hooks,
            get_ids_due_for_metadata=get_ids_due,
            try_update_one=try_update_one,
        )
    )

    assert try_calls == ids


@pytest.mark.parametrize("kind", ["show", "movie"])
def test_update_all_swallows_per_item_exception_and_logs(
    kind: MediaKind, caplog: pytest.LogCaptureFixture
) -> None:
    media_ok = _make_media(kind, name="OK")
    media_bad = _make_media(kind, name="Bad")
    bad_id = _media_id(media_bad)
    ok_id = _media_id(media_ok)
    mark_failure = AsyncMock()
    repo = _FakeMetadataRepo()

    async def get_media(
        _svc: object, media_id: ShowId | MovieId
    ) -> Show | Movie | None:
        if media_id == bad_id:
            return media_bad
        if media_id == ok_id:
            return media_ok
        return None

    async def update_metadata(
        _svc: object,
        media: Show | Movie,
        _provider: object,
        _fresh: object,
    ) -> Show | Movie | None:
        if media.id == bad_id:
            msg = "metadata refresh blew up"
            raise RuntimeError(msg)
        return media

    @asynccontextmanager
    async def bg_service():
        yield _FakeBgSession(repo, kind)

    hooks = MetadataRefreshHooks(
        bg_service=bg_service,
        media_noun=kind,
        get_media=get_media,
        update_metadata=update_metadata,
        mark_failure=mark_failure,
        fetch_native_metadata=lambda _provider, _imdb_id, _language: None,
    )

    async def get_ids_due(_svc: object, _cutoff: datetime, _limit: int):
        return [bad_id, ok_id]

    async def try_update_one(media_id: ShowId | MovieId) -> None:
        await _try_update_media_metadata_id_impl(media_id, hooks=hooks)

    with (
        patch(
            "miramedia.metadata.dependencies.resolve_metadata_provider",
            return_value=_provider("tmdb"),
        ),
        caplog.at_level("ERROR"),
    ):
        run_async(
            _update_all_media_metadata_impl(
                hooks=hooks,
                get_ids_due_for_metadata=get_ids_due,
                try_update_one=try_update_one,
            )
        )

    mark_failure.assert_awaited_once_with(bad_id, "unexpected failure")
    assert repo.stamp_check_calls == [ok_id]
    assert any(
        "Failed to update metadata" in record.message for record in caplog.records
    )
