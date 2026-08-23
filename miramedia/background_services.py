"""Background service composition for scheduler tasks and long-running coroutines.

Each ``bg_<svc>_service()`` opens a short-lived ``SessionLocalBackground``
session, constructs the relevant service graph against it, and closes the
session (returning the connection to the background pool) on exit.

See ``miramedia.database.background_session`` for the raw session primitive.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from miramedia.database import background_session

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from miramedia.imports.service import ImportsService
    from miramedia.movies.service import MovieService
    from miramedia.requests.repository import RequestRepository
    from miramedia.requests.service import RequestService
    from miramedia.shows.service import ShowService
    from miramedia.subtitles.service import SubtitleService
    from miramedia.torrents.service import TorrentService


@asynccontextmanager
async def bg_show_service() -> AsyncGenerator[ShowService]:
    """Construct a ``ShowService`` backed by a short-lived background session."""
    from miramedia.indexers.repository import IndexerRepository
    from miramedia.indexers.service import IndexerService
    from miramedia.notifications.repository import NotificationRepository
    from miramedia.notifications.service import NotificationService
    from miramedia.shows.repository import ShowRepository
    from miramedia.shows.service import ShowService
    from miramedia.torrents.repository import TorrentRepository
    from miramedia.torrents.service import TorrentService

    async with background_session() as db:
        svc = ShowService(
            show_repository=ShowRepository(db),
            torrent_service=TorrentService(torrent_repository=TorrentRepository(db)),
            indexer_service=IndexerService(IndexerRepository(db)),
            notification_service=NotificationService(NotificationRepository(db)),
        )
        yield svc


@asynccontextmanager
async def bg_movie_service() -> AsyncGenerator[MovieService]:
    """Construct a ``MovieService`` backed by a short-lived background session."""
    from miramedia.indexers.repository import IndexerRepository
    from miramedia.indexers.service import IndexerService
    from miramedia.movies.repository import MovieRepository
    from miramedia.movies.service import MovieService
    from miramedia.notifications.repository import NotificationRepository
    from miramedia.notifications.service import NotificationService
    from miramedia.torrents.repository import TorrentRepository
    from miramedia.torrents.service import TorrentService

    async with background_session() as db:
        svc = MovieService(
            movie_repository=MovieRepository(db),
            torrent_service=TorrentService(torrent_repository=TorrentRepository(db)),
            indexer_service=IndexerService(IndexerRepository(db)),
            notification_service=NotificationService(NotificationRepository(db)),
        )
        yield svc


@asynccontextmanager
async def bg_torrent_service() -> AsyncGenerator[TorrentService]:
    """Construct a ``TorrentService`` backed by a short-lived background session."""
    from miramedia.torrents.repository import TorrentRepository
    from miramedia.torrents.service import TorrentService

    async with background_session() as db:
        yield TorrentService(torrent_repository=TorrentRepository(db))


@asynccontextmanager
async def bg_imports_service() -> AsyncGenerator[tuple[AsyncSession, ImportsService]]:
    """Construct an ``ImportsService`` graph for import-queue background work.

    Reproduces the historical nesting: one ``SessionLocalBackground`` for the
    repository plus each ``bg_*`` factory opening its own background session.
    """
    from miramedia.database import SessionLocalBackground
    from miramedia.imports.repository import ImportsRepository
    from miramedia.imports.service import ImportsService

    assert SessionLocalBackground is not None  # noqa: S101

    async with SessionLocalBackground() as db:
        async with bg_torrent_service() as torrent_service:
            async with bg_show_service() as show_service:
                async with bg_movie_service() as movie_service:
                    service = ImportsService(
                        repository=ImportsRepository(db),
                        torrent_service=torrent_service,
                        show_service=show_service,
                        movie_service=movie_service,
                    )
                    yield db, service


@asynccontextmanager
async def bg_request_service() -> AsyncGenerator[
    tuple[RequestService, RequestRepository]
]:
    """Construct a (RequestService, RequestRepository) pair backed by a
    short-lived background session.

    Returns a tuple because the fulfill_approved_requests_task uses both —
    the repository directly for Seerr reconcile, the service for the rest.
    """
    import logging

    from miramedia.requests.backends.composite import CompositeRequestProvider
    from miramedia.requests.backends.native import NativeRequestProvider
    from miramedia.requests.dependencies import build_seerr_client
    from miramedia.requests.repository import RequestRepository
    from miramedia.requests.service import RequestService

    log = logging.getLogger(__name__)

    async with background_session() as db:
        repo = RequestRepository(db)
        native = NativeRequestProvider(repo)
        client = build_seerr_client()
        try:
            provider = CompositeRequestProvider(native, repo, client)
            yield RequestService(provider), repo
        finally:
            if client is not None:
                try:
                    await client.aclose()
                except Exception:
                    log.exception("Failed to close Seerr client in bg_request_service")


@asynccontextmanager
async def bg_subtitle_service() -> AsyncGenerator[SubtitleService]:
    """Construct a ``SubtitleService`` backed by a short-lived background session.

    NOTE: the ``ShowService`` / ``MovieService`` it carries also share the same
    short-lived session. Callers must NOT hold the yielded service across slow
    external I/O — open a fresh ``bg_subtitle_service()`` for each unit of work.
    """
    from miramedia.indexers.repository import IndexerRepository
    from miramedia.indexers.service import IndexerService
    from miramedia.movies.repository import MovieRepository
    from miramedia.movies.service import MovieService
    from miramedia.notifications.repository import NotificationRepository
    from miramedia.notifications.service import NotificationService
    from miramedia.shows.repository import ShowRepository
    from miramedia.shows.service import ShowService
    from miramedia.subtitles.repository import SubtitleRepository
    from miramedia.subtitles.service import SubtitleService
    from miramedia.torrents.repository import TorrentRepository
    from miramedia.torrents.service import TorrentService

    async with background_session() as db:
        notif = NotificationService(NotificationRepository(db))
        torrent = TorrentService(torrent_repository=TorrentRepository(db))
        indexer = IndexerService(IndexerRepository(db))
        show_svc = ShowService(
            show_repository=ShowRepository(db),
            torrent_service=torrent,
            indexer_service=indexer,
            notification_service=notif,
        )
        movie_svc = MovieService(
            movie_repository=MovieRepository(db),
            torrent_service=torrent,
            indexer_service=indexer,
            notification_service=notif,
        )
        yield SubtitleService(
            subtitle_repository=SubtitleRepository(db),
            show_service=show_svc,
            movie_service=movie_svc,
        )
