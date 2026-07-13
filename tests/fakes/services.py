"""Service builders and async test helpers."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

from miramedia.movies.service import MovieService
from miramedia.shows.service import ShowService
from miramedia.torrents.service import TorrentService
from tests.fakes.repositories import (
    FakeMovieRepository,
    FakeShowRepository,
    FakeTorrentRepository,
)


def run_async[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


def build_show_service(
    show_repo: FakeShowRepository | None = None,
    torrent_repo: FakeTorrentRepository | None = None,
) -> tuple[ShowService, FakeShowRepository, FakeTorrentRepository]:
    show_repo = show_repo or FakeShowRepository()
    torrent_repo = torrent_repo or FakeTorrentRepository()
    torrent_svc = TorrentService(torrent_repo)
    svc = ShowService(show_repo, torrent_svc, None, None)
    return svc, show_repo, torrent_repo


def build_movie_service(
    movie_repo: FakeMovieRepository | None = None,
    torrent_repo: FakeTorrentRepository | None = None,
) -> tuple[MovieService, FakeMovieRepository, FakeTorrentRepository]:
    movie_repo = movie_repo or FakeMovieRepository()
    torrent_repo = torrent_repo or FakeTorrentRepository()
    torrent_svc = TorrentService(torrent_repo)
    svc = MovieService(movie_repo, torrent_svc, None, None)
    return svc, movie_repo, torrent_repo


def build_torrent_service(
    torrent_repo: FakeTorrentRepository | None = None,
) -> tuple[TorrentService, FakeTorrentRepository]:
    torrent_repo = torrent_repo or FakeTorrentRepository()
    return TorrentService(torrent_repo), torrent_repo
