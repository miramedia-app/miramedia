"""Plan 082 integrity mismatch pagination against real PostgreSQL."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from miramedia.movies.repository import MovieRepository
from miramedia.movies.service import MovieService
from miramedia.shows.repository import ShowRepository
from miramedia.shows.service import ShowService
from miramedia.torrents.integrity import (
    INTEGRITY_MISMATCH_DEFAULT_LIMIT,
    INTEGRITY_MISMATCH_MAX_LIMIT,
)
from miramedia.torrents.repository import TorrentRepository
from miramedia.torrents.service import TorrentService
from tests.integration.builders import insert_movie_mismatch, insert_show_mismatch

pytestmark = pytest.mark.integration

_SHOW_A = uuid.UUID("00000000-0000-4000-8000-000000000001")
_SHOW_B = uuid.UUID("00000000-0000-4000-8000-000000000002")
_MOVIE_A = uuid.UUID("00000000-0000-4000-8000-000000000003")
_MOVIE_B = uuid.UUID("00000000-0000-4000-8000-000000000004")


def _torrent_stack(db) -> tuple[TorrentService, ShowService, MovieService]:
    torrent_repo = TorrentRepository(db)
    show_repo = ShowRepository(db)
    movie_repo = MovieRepository(db)
    torrent_svc = TorrentService(torrent_repository=torrent_repo)
    show_svc = ShowService(
        show_repository=show_repo,
        torrent_service=torrent_svc,
        indexer_service=AsyncMock(),
        notification_service=AsyncMock(),
    )
    movie_svc = MovieService(
        movie_repository=movie_repo,
        torrent_service=torrent_svc,
        indexer_service=AsyncMock(),
        notification_service=AsyncMock(),
    )
    return torrent_svc, show_svc, movie_svc


async def _seed_mismatch_grid(db) -> None:
    await insert_show_mismatch(db, file_id=_SHOW_A, show_name="Alpha Show")
    await insert_show_mismatch(db, file_id=_SHOW_B, show_name="Bravo Show")
    await insert_movie_mismatch(db, file_id=_MOVIE_A, movie_name="Alpha Movie")
    await insert_movie_mismatch(db, file_id=_MOVIE_B, movie_name="Bravo Movie")


def test_paginate_empty_out_of_range_page(db, run_async) -> None:
    async def _run_test() -> None:
        await _seed_mismatch_grid(db)
        repo = TorrentRepository(db)
        page = await repo.paginate_sha1_mismatch_keys(offset=99, limit=10)
        assert page.total == 4
        assert page.keys == []

    run_async(_run_test())


def test_paginate_populated_page_preserves_order_and_total(db, run_async) -> None:
    async def _run_test() -> None:
        await _seed_mismatch_grid(db)
        repo = TorrentRepository(db)
        page = await repo.paginate_sha1_mismatch_keys(offset=0, limit=3)
        assert page.total == 4
        assert [key.file_id for key in page.keys] == [_SHOW_A, _SHOW_B, _MOVIE_A]
        assert [key.media_type for key in page.keys] == ["show", "show", "movie"]

    run_async(_run_test())


def test_list_integrity_mismatches_service_caps_limit_and_sets_next_offset(
    db, run_async, monkeypatch
) -> None:
    async def _run_test() -> None:
        await _seed_mismatch_grid(db)
        torrent_svc, show_svc, movie_svc = _torrent_stack(db)
        monkeypatch.setattr(
            "miramedia.torrents.service.batch_resolve_episode_paths_async",
            AsyncMock(return_value={}),
        )
        monkeypatch.setattr(
            "miramedia.torrents.service.batch_resolve_movie_paths_async",
            AsyncMock(return_value={}),
        )

        page = await torrent_svc.list_integrity_mismatches(
            offset=0,
            limit=INTEGRITY_MISMATCH_MAX_LIMIT + 50,
            show_service=show_svc,
            movie_service=movie_svc,
        )
        assert page.total == 4
        assert page.limit == INTEGRITY_MISMATCH_MAX_LIMIT
        assert len(page.items) == 4
        assert page.items[0].media_type == "show"
        assert page.items[-1].media_type == "movie"

        tail = await torrent_svc.list_integrity_mismatches(
            offset=3,
            limit=INTEGRITY_MISMATCH_DEFAULT_LIMIT,
            show_service=show_svc,
            movie_service=movie_svc,
        )
        assert tail.total == 4
        assert len(tail.items) == 1
        assert tail.items[0].file_id == _MOVIE_B
        assert tail.next_offset is None

    run_async(_run_test())
