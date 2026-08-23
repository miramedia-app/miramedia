from typing import Annotated
from uuid import UUID

from cachetools import TTLCache
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field

from miramedia.auth.users import (
    current_active_user,
    current_superuser,
    require_can_add_media,
)
from miramedia.config import LibraryItem, MiraMediaConfig
from miramedia.database import release_session_before_external_io
from miramedia.exceptions import NotFoundError
from miramedia.metadata.dependencies import metadata_provider_dep
from miramedia.metadata.schemas import MetaDataProviderSearchResult
from miramedia.recommended_discovery_cache import _RECOMMENDED_SHOWS_CACHE
from miramedia.shows.dependencies import (
    season_dep,
    show_dep,
    show_repository_dep,
    show_service_dep,
)
from miramedia.shows.schemas import (
    EpisodeId,
    PublicEpisodeFile,
    PublicShow,
    Season,
    SeasonId,
    Show,
)
from miramedia.subtitles.dependencies import subtitle_service_dep
from miramedia.subtitles.schemas import SubtitleFile
from miramedia.torrents.schemas import RichTorrent

router = APIRouter(
    prefix="/shows",
    tags=["shows"],
    dependencies=[Depends(current_active_user)],
)
_FACETS_CACHE: TTLCache = TTLCache(maxsize=1, ttl=300)

# Episodes and seasons are addressed by their own global ids, not nested under
# a show id — so they get top-level routers instead of squatting `/shows/...`.
episodes_router = APIRouter(
    prefix="/episodes",
    tags=["episodes"],
    dependencies=[Depends(current_active_user)],
)
seasons_router = APIRouter(
    prefix="/seasons",
    tags=["seasons"],
    dependencies=[Depends(current_active_user)],
)


class PreferredQualityBody(BaseModel):
    preferred_quality: list[str] | None = None


class PreferredCodecBody(BaseModel):
    preferred_codec: list[str] | None = None


class MediaFacetOptions(BaseModel):
    libraries: list[str] = Field(default_factory=list)
    genres: list[str] = Field(default_factory=list)
    decades: list[int] = Field(default_factory=list)


class ShowDetailBundle(BaseModel):
    show: PublicShow
    torrents: list[RichTorrent] = Field(default_factory=list)
    subtitles_by_episode: dict[str, list[SubtitleFile]] = Field(default_factory=dict)


def _validate_option_names(
    values: list[str] | None, enabled: set[str], kind: str
) -> None:
    if not values:
        return
    invalid = [v for v in values if v not in enabled]
    if invalid:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"{kind.capitalize()} {invalid!r} not enabled. Enabled: {sorted(enabled)}",
        )


# -----------------------------------------------------------------------------
# METADATA & SEARCH
# -----------------------------------------------------------------------------


@router.get("/search")
async def search_metadata_providers_for_a_show(
    show_service: show_service_dep,
    query: Annotated[str, Query()],
) -> list[MetaDataProviderSearchResult]:
    """Search for a show across enabled providers in precedence order
    (TMDB → TVDB → Cinemeta → TVMaze), returning the first non-empty hit."""
    return await show_service.discover_shows(query=query)


@router.get("/recommended")
async def get_recommended_shows(
    response: Response,
    show_service: show_service_dep,
    skip: Annotated[int, Query(ge=0)] = 0,
) -> list[MetaDataProviderSearchResult]:
    """Recommended/popular shows across enabled providers in precedence order
    (TMDB → TVDB → Cinemeta → TVMaze).

    Provider search cached ~1h server-side; the response is not browser-cached
    (no-store) because the added/id library flags are per-request state and a
    browser cache would otherwise show a stale "Add" after an import.
    """
    response.headers["Cache-Control"] = "private, no-store"
    await release_session_before_external_io(show_service.show_repository.db)
    return await _RECOMMENDED_SHOWS_CACHE.get(
        skip,
        lambda: show_service.discover_shows(skip=skip),
        show_service.annotate_search_results,
    )


# -----------------------------------------------------------------------------
# SHOWS
# -----------------------------------------------------------------------------


@router.get("")
async def get_all_shows(
    show_service: show_service_dep,
    response: Response,
    limit: Annotated[int, Query(gt=0, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    q: Annotated[str | None, Query()] = None,
    sort: Annotated[str | None, Query()] = None,
    library: Annotated[list[str] | None, Query()] = None,
    exclude_library: Annotated[list[str] | None, Query()] = None,
    genre: Annotated[list[str] | None, Query()] = None,
    exclude_genre: Annotated[list[str] | None, Query()] = None,
    decade: Annotated[list[int] | None, Query()] = None,
    exclude_decade: Annotated[list[int] | None, Query()] = None,
    airing: Annotated[list[str] | None, Query()] = None,
    exclude_airing: Annotated[list[str] | None, Query()] = None,
    status_filter: Annotated[list[str] | None, Query(alias="status")] = None,
    exclude_status: Annotated[list[str] | None, Query()] = None,
) -> list[PublicShow]:
    """List shows with bounded SQL pagination and computed download/status fields.

    Pagination is always pushed into SQL so only the requested page is
    eager-loaded. ``limit`` defaults to 100 (max 500); ``X-Total-Count``
    reports the filtered total.
    """
    page, total = await show_service.get_paginated_public_shows(
        offset=offset,
        limit=limit,
        query=q,
        sort=sort,
        libraries=library,
        excluded_libraries=exclude_library,
        genres=genre,
        excluded_genres=exclude_genre,
        decades=decade,
        excluded_decades=exclude_decade,
        airing=airing,
        excluded_airing=exclude_airing,
        statuses=status_filter,
        excluded_statuses=exclude_status,
    )
    response.headers["X-Total-Count"] = str(total)
    return page


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_can_add_media)],
)
async def add_a_show(
    show_service: show_service_dep,
    metadata_provider: metadata_provider_dep,
    show_id: Annotated[str, Query()],
    language: Annotated[str | None, Query()] = None,
) -> Show | dict:
    """Add a show to the library.

    If the show is already tracked, returns it synchronously so the UI can
    deep-link to its detail page. Otherwise enqueues a background task that
    fetches metadata and persists the show; the endpoint returns immediately
    so the UI stays interactive. The new entry appears in the shows list
    once the task completes.
    """
    try:
        existing = await show_service.get_show_by_external_id(
            show_id, metadata_provider=metadata_provider.name
        )
    except NotFoundError:
        existing = None
    if existing:
        return existing

    from miramedia.scheduler import add_show_task

    await add_show_task.kiq(
        external_id=show_id,
        metadata_provider_name=metadata_provider.name,
        language=language,
    )
    return {"status": "queued", "external_id": show_id}


@router.get("/libraries")
def get_available_libraries() -> list[LibraryItem]:
    """
    Get available show libraries from configuration.
    """
    return MiraMediaConfig().misc.show_libraries


@router.get("/facets")
async def get_show_facets(show_repository: show_repository_dep) -> MediaFacetOptions:
    cached = _FACETS_CACHE.get("all")
    if cached is None:
        cached = await show_repository.get_show_facets()
        _FACETS_CACHE["all"] = cached
    return MediaFacetOptions(**cached)


@router.get("/{show_id}/detail-bundle")
async def get_show_detail_bundle(
    show: show_dep,
    show_service: show_service_dep,
    subtitle_service: subtitle_service_dep,
) -> ShowDetailBundle:
    return ShowDetailBundle(
        show=await show_service.get_public_show_by_id(show=show),
        torrents=await show_service.get_torrents_for_show(show=show),
        subtitles_by_episode=await subtitle_service.get_show_subtitle_files(show.id),
    )


# -----------------------------------------------------------------------------
# SHOWS - INDIVIDUAL
# -----------------------------------------------------------------------------


@router.get("/{show_id}")
async def get_a_show(show: show_dep, show_service: show_service_dep) -> PublicShow:
    """
    Get details for a specific show.
    """
    return await show_service.get_public_show_by_id(show=show)


@router.delete(
    "/{show_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(current_superuser)],
)
async def delete_a_show(
    show_service: show_service_dep,
    show: show_dep,
    delete_files_on_disk: Annotated[bool, Query()] = False,
) -> None:
    """
    Delete a show from the library.
    """
    await show_service.delete_show(
        show=show,
        delete_files_on_disk=delete_files_on_disk,
    )


@router.post(
    "/{show_id}/metadata",
    dependencies=[Depends(current_superuser)],
)
async def update_shows_metadata(
    show: show_dep, show_service: show_service_dep
) -> PublicShow:
    """
    Update a show's metadata. Uses the best available enabled provider,
    cross-referencing via IMDb ID if the original provider is unavailable.
    """
    await show_service.refresh_metadata_with_fallback(show)
    return await show_service.get_public_show_by_id(show=show)


@router.post(
    "/{show_id}/continuous-download",
    dependencies=[Depends(current_superuser)],
)
async def set_continuous_download(
    show: show_dep,
    show_service: show_service_dep,
    continuous_download: Annotated[bool | None, Query()] = None,
) -> PublicShow:
    """
    Set continuous download for a show. True/False to override, None to use global default.
    """
    await show_service.set_show_continuous_download(
        show=show, continuous_download=continuous_download
    )
    return await show_service.get_public_show_by_id(show=show)


@router.post(
    "/{show_id}/skip",
    dependencies=[Depends(current_superuser)],
    status_code=status.HTTP_204_NO_CONTENT,
)
async def set_show_skipped(
    show: show_dep,
    show_service: show_service_dep,
    skipped: Annotated[bool, Query()] = True,
) -> None:
    """
    Set the skipped status for a show.
    """
    await show_service.show_repository.update_show_skipped(
        show_id=show.id, skipped=skipped
    )


@seasons_router.post(
    "/{season_id}/skip",
    dependencies=[Depends(current_superuser)],
    status_code=status.HTTP_204_NO_CONTENT,
)
async def set_season_skipped(
    season_id: SeasonId,
    show_service: show_service_dep,
    skipped: Annotated[bool, Query()] = True,
) -> None:
    """
    Set the skipped status for a season.

    Cascades to episodes: non-downloaded episodes adopt the new flag while
    downloaded episodes keep their existing state.
    """
    await show_service.set_season_skipped(season_id=season_id, skipped=skipped)


@episodes_router.post(
    "/{episode_id}/skip",
    dependencies=[Depends(current_superuser)],
    status_code=status.HTTP_204_NO_CONTENT,
)
async def set_episode_skipped(
    episode_id: EpisodeId,
    show_service: show_service_dep,
    skipped: Annotated[bool, Query()] = True,
) -> None:
    """
    Set the skipped status for an individual episode.

    Marking an episode wanted while its season is skipped also flips the
    season to wanted; sibling episodes retain their state.
    """
    await show_service.set_episode_skipped(episode_id=episode_id, skipped=skipped)


@episodes_router.delete(
    "/files/{file_id}",
    dependencies=[Depends(current_superuser)],
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_episode_file(
    file_id: UUID,
    show_service: show_service_dep,
    delete_from_disk: Annotated[bool, Query()] = True,
) -> None:
    """Delete a specific episode file (by surrogate id) and optionally remove
    it from disk."""
    await show_service.delete_episode_file(
        file_id=file_id,
        delete_from_disk=delete_from_disk,
    )


@seasons_router.delete(
    "/{season_id}",
    dependencies=[Depends(current_superuser)],
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_season(
    season: season_dep,
    show_service: show_service_dep,
    delete_from_disk: Annotated[bool, Query()] = True,
) -> None:
    """
    Delete all files for a season and mark all episodes as skipped.
    """
    await show_service.delete_season_files(
        season=season, delete_from_disk=delete_from_disk
    )


@router.post(
    "/{show_id}/library",
    dependencies=[Depends(current_superuser)],
    status_code=status.HTTP_204_NO_CONTENT,
)
async def set_library(
    show: show_dep,
    show_service: show_service_dep,
    library: Annotated[str, Query()],
) -> None:
    """
    Set the library path for a Show.
    """
    await show_service.set_show_library(show=show, library=library)
    return


@router.post(
    "/{show_id}/move-library",
    dependencies=[Depends(current_superuser)],
    status_code=status.HTTP_200_OK,
)
async def move_show_library(
    show: show_dep,
    show_service: show_service_dep,
    target_library: Annotated[str, Query()],
    delete_source: Annotated[bool, Query()] = True,
) -> dict:
    """Phase 8.1 — physically move a show's directory under a new library.

    Hardlinks every file to the new root, rewrites ``show.library``, and
    optionally removes the old directory once the link pass succeeds.
    """
    try:
        return await show_service.move_show_library(
            show=show, target_library=target_library, delete_source=delete_source
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.post(
    "/{show_id}/preferred-quality",
    dependencies=[Depends(current_superuser)],
)
async def set_preferred_quality(
    show: show_dep,
    show_service: show_service_dep,
    body: PreferredQualityBody,
) -> PublicShow:
    """Set the preferred qualities for a show.

    ``preferred_quality`` semantics:
      - ``None``: inherit the global default.
      - ``[]``: explicit "Any" — keep all enabled-quality results without
        adding any quality score.
      - non-empty list of enabled option names: whitelist those options.
    """
    _validate_option_names(
        body.preferred_quality,
        {opt.name for opt in MiraMediaConfig().indexers.quality_options if opt.enabled},
        "quality",
    )
    await show_service.set_show_preferred_quality(
        show=show, preferred_quality=body.preferred_quality
    )
    return await show_service.get_public_show_by_id(show=show)


@router.post(
    "/{show_id}/preferred-codec",
    dependencies=[Depends(current_superuser)],
)
async def set_preferred_codec(
    show: show_dep,
    show_service: show_service_dep,
    body: PreferredCodecBody,
) -> PublicShow:
    """Set the preferred codecs for a show. Same semantics as preferred-quality."""
    _validate_option_names(
        body.preferred_codec,
        {opt.name for opt in MiraMediaConfig().indexers.codec_options if opt.enabled},
        "codec",
    )
    await show_service.set_show_preferred_codec(
        show=show, preferred_codec=body.preferred_codec
    )
    return await show_service.get_public_show_by_id(show=show)


@router.post(
    "/{show_id}/subtitle-languages",
    dependencies=[Depends(current_superuser)],
)
async def set_subtitle_languages(
    show: show_dep,
    show_service: show_service_dep,
    subtitle_languages: Annotated[list[str] | None, Query()] = None,
) -> PublicShow:
    """
    Set the subtitle languages for a show. Pass None to use the global default.
    """
    await show_service.set_show_subtitle_languages(
        show=show, subtitle_languages=subtitle_languages
    )
    return await show_service.get_public_show_by_id(show=show)


@router.get("/{show_id}/torrents")
async def get_a_shows_torrents(
    show: show_dep, show_service: show_service_dep
) -> list[RichTorrent]:
    """
    Get torrents associated with a specific show.
    """
    return await show_service.get_torrents_for_show(show=show)


# -----------------------------------------------------------------------------
# SEASONS
# -----------------------------------------------------------------------------


@seasons_router.get("/{season_id}")
def get_season(season: season_dep) -> Season:
    """
    Get details for a specific season.
    """
    return season


@seasons_router.get("/{season_id}/files")
async def get_episode_files(
    season: season_dep, show_service: show_service_dep
) -> list[PublicEpisodeFile]:
    """
    Get episode files associated with a specific season.
    """
    return await show_service.get_public_episode_files_by_season_id(season=season)


# -----------------------------------------------------------------------------
# STATISTICS
# -----------------------------------------------------------------------------


@episodes_router.get(
    "/count",
    status_code=status.HTTP_200_OK,
    description="Total number of episodes downloaded",
)
async def get_total_count_of_downloaded_episodes(
    show_service: show_service_dep,
) -> int:
    """
    Get the total count of downloaded episodes across all shows.
    """
    return await show_service.get_total_downloaded_episoded_count()
