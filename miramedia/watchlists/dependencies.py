from typing import Annotated

from fastapi import Depends, HTTPException, status

from miramedia.config import MiraMediaConfig
from miramedia.database import DbSessionDependency
from miramedia.movies.dependencies import movie_repository_dep
from miramedia.shows.dependencies import show_repository_dep
from miramedia.watchlists.repository import WatchlistRepository
from miramedia.watchlists.service import WatchlistService


def require_watchlists_enabled() -> None:
    """Gate runtime endpoints on the ``watchlists.enabled`` config flag.

    The router is mounted unconditionally so its schemas always appear in
    the generated OpenAPI spec; this dependency enforces that the feature
    is actually active before any request hits a handler.
    """
    if not MiraMediaConfig().watchlists.enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Watchlists feature is disabled",
        )


def require_custom_lists_enabled() -> None:
    """Gate custom-list CRUD on its dedicated config flag."""
    require_watchlists_enabled()
    if not MiraMediaConfig().watchlists.custom_lists_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Custom lists feature is disabled",
        )


def require_upcoming_enabled() -> None:
    """Gate the Upcoming endpoint on its dedicated config flag."""
    require_watchlists_enabled()
    if not MiraMediaConfig().watchlists.upcoming_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Upcoming feature is disabled",
        )


def require_watch_next_enabled() -> None:
    """Gate Watch Next (playback watch-next) on its dedicated config flag."""
    if not MiraMediaConfig().watchlists.watch_next_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Watch Next feature is disabled",
        )


def get_watchlist_repository(db_session: DbSessionDependency) -> WatchlistRepository:
    return WatchlistRepository(db_session)


watchlist_repository_dep = Annotated[
    WatchlistRepository, Depends(get_watchlist_repository)
]


def get_watchlist_service(
    watchlist_repository: watchlist_repository_dep,
    movie_repository: movie_repository_dep,
    show_repository: show_repository_dep,
) -> WatchlistService:
    return WatchlistService(
        repository=watchlist_repository,
        movie_repository=movie_repository,
        show_repository=show_repository,
    )


watchlist_service_dep = Annotated[WatchlistService, Depends(get_watchlist_service)]
