from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from miramedia.auth.users import current_active_user, current_superuser
from miramedia.movies.schemas import MovieId
from miramedia.shows.schemas import EpisodeId, ShowId
from miramedia.subtitles.dependencies import subtitle_service_dep
from miramedia.subtitles.schemas import (
    ShowSubtitleStatus,
    SubtitleFile,
    SubtitleSearchResponse,
    SubtitleStatus,
)

log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/subtitles",
    tags=["subtitles"],
    dependencies=[Depends(current_active_user)],
)


# --- Shows (aggregate across all episodes) -----------------------------------


@router.get("/shows/{show_id}/status")
async def get_show_subtitle_status(
    show_id: ShowId,
    subtitle_service: subtitle_service_dep,
    season_number: Annotated[int | None, Query()] = None,
    episode_number: Annotated[int | None, Query()] = None,
) -> ShowSubtitleStatus:
    """Subtitle status for all episodes of a show (optional season/episode filter)."""
    return await subtitle_service.get_show_subtitle_status(
        show_id=show_id,
        season_number=season_number,
        episode_number=episode_number,
    )


@router.get("/shows/{show_id}/files")
async def get_show_subtitle_files(
    show_id: ShowId,
    subtitle_service: subtitle_service_dep,
) -> dict[str, list[SubtitleFile]]:
    """Subtitle files for all episodes of a show, keyed by episode id.

    Batches what would otherwise be one request per downloaded episode.
    """
    return await subtitle_service.get_show_subtitle_files(show_id)


# --- Episodes ----------------------------------------------------------------


@router.get("/episodes/{episode_id}/status")
async def get_episode_subtitle_status(
    episode_id: EpisodeId,
    subtitle_service: subtitle_service_dep,
) -> SubtitleStatus:
    """Get subtitle status for an episode."""
    return await subtitle_service.get_episode_subtitle_status(episode_id)


@router.get("/episodes/{episode_id}/files")
async def get_episode_subtitle_files(
    episode_id: EpisodeId,
    subtitle_service: subtitle_service_dep,
) -> list[SubtitleFile]:
    """List all subtitle files for an episode."""
    return await subtitle_service.get_episode_subtitle_files(episode_id)


@router.delete(
    "/episodes/{episode_id}/files",
    dependencies=[Depends(current_superuser)],
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_episode_subtitle_file(
    episode_id: EpisodeId,
    file_name: Annotated[str, Query()],
    subtitle_service: subtitle_service_dep,
) -> None:
    """Delete a specific subtitle file for an episode."""
    await subtitle_service.delete_episode_subtitle_file(
        episode_id=episode_id, file_name=file_name
    )


@router.post(
    "/episodes/{episode_id}/search",
    dependencies=[Depends(current_superuser)],
)
async def search_episode_subtitles(
    episode_id: EpisodeId,
    subtitle_service: subtitle_service_dep,
) -> SubtitleSearchResponse:
    """Trigger subtitle search for an episode."""
    downloaded = await subtitle_service.search_episode_subtitles(episode_id)
    return SubtitleSearchResponse(downloaded=downloaded, count=len(downloaded))


# --- Movies ------------------------------------------------------------------


@router.get("/movies/{movie_id}/status")
async def get_movie_subtitle_status(
    movie_id: MovieId,
    subtitle_service: subtitle_service_dep,
) -> SubtitleStatus:
    """Get subtitle status for a movie."""
    return await subtitle_service.get_movie_subtitle_status(movie_id)


@router.get("/movies/{movie_id}/files")
async def get_movie_subtitle_files(
    movie_id: MovieId,
    subtitle_service: subtitle_service_dep,
) -> list[SubtitleFile]:
    """List all subtitle files for a movie."""
    return await subtitle_service.get_movie_subtitle_files(movie_id)


@router.delete(
    "/movies/{movie_id}/files",
    dependencies=[Depends(current_superuser)],
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_movie_subtitle_file(
    movie_id: MovieId,
    file_name: Annotated[str, Query()],
    subtitle_service: subtitle_service_dep,
) -> None:
    """Delete a specific subtitle file for a movie."""
    await subtitle_service.delete_movie_subtitle_file(
        movie_id=movie_id, file_name=file_name
    )


@router.post(
    "/movies/{movie_id}/search",
    dependencies=[Depends(current_superuser)],
)
async def search_movie_subtitles(
    movie_id: MovieId,
    subtitle_service: subtitle_service_dep,
) -> dict:
    """Trigger subtitle search for a movie."""
    downloaded = await subtitle_service.search_movie_subtitles(movie_id)
    return {"downloaded": downloaded, "count": len(downloaded)}


# --- Maintenance -------------------------------------------------------------


@router.post(
    "/scan",
    dependencies=[Depends(current_superuser)],
)
async def scan_all_subtitles(
    subtitle_service: subtitle_service_dep,
) -> dict:
    """Trigger a full scan for all missing subtitles."""
    await subtitle_service.scan_all_missing_subtitles()
    return {"status": "scan complete"}
