"""In-memory fakes for DB-free orchestration characterization tests."""

from tests.fakes.db import FakeDb, RecordingSession
from tests.fakes.repositories import (
    FakeMovieRepository,
    FakeRequestRepository,
    FakeSettingsRepository,
    FakeShowRepository,
    FakeTorrentRepository,
)
from tests.fakes.services import (
    build_movie_service,
    build_show_service,
    build_torrent_service,
    run_async,
)

__all__ = [
    "FakeDb",
    "FakeMovieRepository",
    "FakeRequestRepository",
    "FakeSettingsRepository",
    "FakeShowRepository",
    "FakeTorrentRepository",
    "RecordingSession",
    "build_movie_service",
    "build_show_service",
    "build_torrent_service",
    "run_async",
]
