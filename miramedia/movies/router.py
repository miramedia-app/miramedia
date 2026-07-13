import asyncio
import time
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
from miramedia.exceptions import NotFoundError
from miramedia.metadata.dependencies import metadata_provider_dep
from miramedia.metadata.schemas import MetaDataProviderSearchResult
from miramedia.movies.dependencies import (
    movie_dep,
    movie_repository_dep,
    movie_service_dep,
)
from miramedia.movies.schemas import (
    Movie,
    PublicMovie,
    PublicMovieFile,
)
from miramedia.subtitles.dependencies import subtitle_service_dep
from miramedia.subtitles.schemas import SubtitleFile

router = APIRouter(
    prefix="/movies",
    tags=["movies"],
    dependencies=[Depends(current_active_user)],
)
_FACETS_CACHE: TTLCache = TTLCache(maxsize=1, ttl=300)


class PreferredQualityBody(BaseModel):
    preferred_quality: list[str] | None = None


class PreferredCodecBody(BaseModel):
    preferred_codec: list[str] | None = None


class MediaFacetOptions(BaseModel):
    libraries: list[str] = Field(default_factory=list)
    genres: list[str] = Field(default_factory=list)
    decades: list[int] = Field(default_factory=list)


class MovieDetailBundle(BaseModel):
    movie: PublicMovie
    files: list[PublicMovieFile] = Field(default_factory=list)
    subtitles: list[SubtitleFile] = Field(default_factory=list)


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
async def search_for_movie(
    query: Annotated[str, Query()],
    movie_service: movie_service_dep,
) -> list[MetaDataProviderSearchResult]:
    """Search for a movie across enabled providers in precedence order
    (TMDB → TVDB → Cinemeta), returning the first non-empty hit."""
    return await movie_service.discover_movies(query=query)


@router.get("/lookup/{imdb_id}")
def lookup_movie_by_imdb(
    imdb_id: str, metadata_provider: metadata_provider_dep
) -> dict:
    """Look up enriched metadata (overview, rating, year) for an IMDb id, pre-add."""
    if hasattr(metadata_provider, "enrich_movie_result"):
        result = metadata_provider.enrich_movie_result(imdb_id)
        if result:
            return result
    return {}


_RECOMMENDED_CACHE: dict[tuple[str, int], tuple[float, list]] = {}
_RECOMMENDED_CACHE_LOCK = asyncio.Lock()
_RECOMMENDED_TTL = 3600.0  # 1h — trending shifts daily, not per page refresh.


@router.get("/recommended")
async def get_popular_movies(
    response: Response,
    movie_service: movie_service_dep,
    skip: Annotated[int, Query(ge=0)] = 0,
) -> list[MetaDataProviderSearchResult]:
    """Recommended/popular movies across enabled providers in precedence order
    (TMDB → TVDB → Cinemeta). Provider search cached 1h server-side; the
    response is not browser-cached (no-store) because the added/id library
    flags are per-request state and would otherwise show a stale "Add"."""
    key = ("movies", skip)
    now = time.monotonic()
    async with _RECOMMENDED_CACHE_LOCK:
        cached = _RECOMMENDED_CACHE.get(key)
    if cached and now - cached[0] < _RECOMMENDED_TTL:
        response.headers["Cache-Control"] = "private, no-store"
        # Only the provider search is cached (expensive HTTP fan-out); the
        # added/id flags are per-library state, so re-annotate every hit or a
        # title imported after the cache was filled keeps showing "Add".
        return await movie_service.annotate_search_results(cached[1])
    results = await movie_service.discover_movies(skip=skip)
    async with _RECOMMENDED_CACHE_LOCK:
        _RECOMMENDED_CACHE[key] = (now, results)
    response.headers["Cache-Control"] = "private, no-store"
    return results


# -----------------------------------------------------------------------------
# MOVIES
# -----------------------------------------------------------------------------


@router.get("")
async def get_all_movies(
    movie_service: movie_service_dep,
    response: Response,
    limit: Annotated[int | None, Query(gt=0, le=500)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    q: Annotated[str | None, Query()] = None,
    sort: Annotated[str | None, Query()] = None,
    library: Annotated[list[str] | None, Query()] = None,
    exclude_library: Annotated[list[str] | None, Query()] = None,
    genre: Annotated[list[str] | None, Query()] = None,
    exclude_genre: Annotated[list[str] | None, Query()] = None,
    decade: Annotated[list[int] | None, Query()] = None,
    exclude_decade: Annotated[list[int] | None, Query()] = None,
    status_filter: Annotated[list[str] | None, Query(alias="status")] = None,
    exclude_status: Annotated[list[str] | None, Query()] = None,
) -> list[PublicMovie]:
    """
    Get all movies in the library with computed download/status fields.

    When ``limit`` is supplied, pagination is pushed into SQL. The whole-
    library form remains for backwards-compatible callers.
    """
    if limit is None:
        movies = await movie_service.get_all_public_movies()
        return movies[offset:] if offset else movies
    page, total = await movie_service.get_paginated_public_movies(
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
        statuses=status_filter,
        excluded_statuses=exclude_status,
    )
    response.headers["X-Total-Count"] = str(total)
    return page


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_can_add_media)],
    responses={
        status.HTTP_201_CREATED: {
            "model": Movie,
            "description": "Successfully created movie",
        }
    },
)
async def add_a_movie(
    movie_service: movie_service_dep,
    metadata_provider: metadata_provider_dep,
    movie_id: Annotated[str, Query()],
    language: Annotated[str | None, Query()] = None,
) -> Movie | dict:
    """Add a movie to the library.

    If the movie is already tracked, returns it synchronously. Otherwise
    enqueues a background task that fetches metadata + persists the movie;
    the endpoint returns immediately so the UI stays interactive.
    """
    try:
        existing = await movie_service.get_movie_by_external_id(
            external_id=movie_id, metadata_provider=metadata_provider.name
        )
    except NotFoundError:
        existing = None
    if existing:
        return existing

    from miramedia.scheduler import add_movie_task

    await add_movie_task.kiq(
        external_id=movie_id,
        metadata_provider_name=metadata_provider.name,
        language=language,
    )
    return {"status": "queued", "external_id": movie_id}


@router.get("/libraries")
def get_available_libraries() -> list[LibraryItem]:
    """
    Get available Movie libraries from configuration.
    """
    return MiraMediaConfig().misc.movie_libraries


@router.get("/facets")
async def get_movie_facets(movie_repository: movie_repository_dep) -> MediaFacetOptions:
    cached = _FACETS_CACHE.get("all")
    if cached is None:
        cached = await movie_repository.get_movie_facets()
        _FACETS_CACHE["all"] = cached
    return MediaFacetOptions(**cached)


@router.get("/{movie_id}/detail-bundle")
async def get_movie_detail_bundle(
    movie: movie_dep,
    movie_service: movie_service_dep,
    subtitle_service: subtitle_service_dep,
) -> MovieDetailBundle:
    return MovieDetailBundle(
        movie=await movie_service.get_public_movie_by_id(movie=movie),
        files=await movie_service.get_public_movie_files(movie=movie),
        subtitles=await subtitle_service.get_movie_subtitle_files(movie.id),
    )


# -----------------------------------------------------------------------------
# MOVIES - SINGLE RESOURCE
# -----------------------------------------------------------------------------


@router.get("/{movie_id}")
async def get_movie_by_id(
    movie_service: movie_service_dep, movie: movie_dep
) -> PublicMovie:
    """
    Get details for a specific movie.
    """
    return await movie_service.get_public_movie_by_id(movie=movie)


@router.delete(
    "/{movie_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(current_superuser)],
)
async def delete_a_movie(
    movie_service: movie_service_dep,
    movie: movie_dep,
    delete_files_on_disk: Annotated[bool, Query()] = False,
    delete_torrents: Annotated[bool, Query()] = False,
) -> None:
    """
    Delete a movie from the library.
    """
    await movie_service.delete_movie(
        movie=movie,
        delete_files_on_disk=delete_files_on_disk,
        delete_torrents=delete_torrents,
    )


@router.post(
    "/{movie_id}/library",
    dependencies=[Depends(current_superuser)],
    status_code=status.HTTP_204_NO_CONTENT,
)
async def set_library(
    movie: movie_dep,
    movie_service: movie_service_dep,
    library: Annotated[str, Query()],
) -> None:
    """
    Set the library path for a Movie.
    """
    await movie_service.set_movie_library(movie=movie, library=library)
    return


@router.post(
    "/{movie_id}/move-library",
    dependencies=[Depends(current_superuser)],
    status_code=status.HTTP_200_OK,
)
async def move_movie_library(
    movie: movie_dep,
    movie_service: movie_service_dep,
    target_library: Annotated[str, Query()],
    delete_source: Annotated[bool, Query()] = True,
) -> dict:
    """Phase 8.1 — physically move a movie's directory under a new library."""
    try:
        return await movie_service.move_movie_library(
            movie=movie, target_library=target_library, delete_source=delete_source
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.post(
    "/{movie_id}/preferred-quality",
    dependencies=[Depends(current_superuser)],
)
async def set_preferred_quality(
    movie: movie_dep,
    movie_service: movie_service_dep,
    body: PreferredQualityBody,
) -> PublicMovie:
    """Set the preferred qualities for a movie.

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
    await movie_service.set_movie_preferred_quality(
        movie=movie, preferred_quality=body.preferred_quality
    )
    return await movie_service.get_public_movie_by_id(movie=movie)


@router.post(
    "/{movie_id}/preferred-codec",
    dependencies=[Depends(current_superuser)],
)
async def set_preferred_codec(
    movie: movie_dep,
    movie_service: movie_service_dep,
    body: PreferredCodecBody,
) -> PublicMovie:
    """Set the preferred codecs for a movie. Same semantics as preferred-quality."""
    _validate_option_names(
        body.preferred_codec,
        {opt.name for opt in MiraMediaConfig().indexers.codec_options if opt.enabled},
        "codec",
    )
    await movie_service.set_movie_preferred_codec(
        movie=movie, preferred_codec=body.preferred_codec
    )
    return await movie_service.get_public_movie_by_id(movie=movie)


@router.post(
    "/{movie_id}/subtitle-languages",
    dependencies=[Depends(current_superuser)],
)
async def set_subtitle_languages(
    movie: movie_dep,
    movie_service: movie_service_dep,
    subtitle_languages: Annotated[list[str] | None, Query()] = None,
) -> PublicMovie:
    """
    Set the subtitle languages for a movie. Pass None to use the global default.
    """
    await movie_service.set_movie_subtitle_languages(
        movie=movie, subtitle_languages=subtitle_languages
    )
    return await movie_service.get_public_movie_by_id(movie=movie)


@router.post(
    "/{movie_id}/continuous-download",
    dependencies=[Depends(current_superuser)],
)
async def set_continuous_download(
    movie: movie_dep,
    movie_service: movie_service_dep,
    continuous_download: Annotated[bool | None, Query()] = None,
) -> PublicMovie:
    """
    Set continuous download for a movie. True/False to override, None to use global default.
    """
    await movie_service.set_movie_continuous_download(
        movie=movie, continuous_download=continuous_download
    )
    return await movie_service.get_public_movie_by_id(movie=movie)


@router.post(
    "/{movie_id}/skip",
    dependencies=[Depends(current_superuser)],
    status_code=status.HTTP_204_NO_CONTENT,
)
async def set_movie_skipped(
    movie: movie_dep,
    movie_service: movie_service_dep,
    skipped: Annotated[bool, Query()] = True,
) -> None:
    """
    Set the skipped status for a movie.
    """
    await movie_service.movie_repository.update_movie_skipped(
        movie_id=movie.id, skipped=skipped
    )


@router.post(
    "/{movie_id}/metadata",
    dependencies=[Depends(current_superuser)],
)
async def update_movie_metadata(
    movie: movie_dep,
    movie_service: movie_service_dep,
) -> PublicMovie:
    """
    Update a movie's metadata. Uses the best available enabled provider,
    cross-referencing via IMDb ID if the original provider is unavailable.
    """
    await movie_service.refresh_metadata_with_fallback(movie)
    return await movie_service.get_public_movie_by_id(movie=movie)


@router.get("/{movie_id}/files")
async def get_movie_files_by_movie_id(
    movie_service: movie_service_dep, movie: movie_dep
) -> list[PublicMovieFile]:
    """
    Get files associated with a specific movie.
    """
    return await movie_service.get_public_movie_files(movie=movie)


@router.delete(
    "/{movie_id}/files/{file_id}",
    dependencies=[Depends(current_superuser)],
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_movie_file(
    movie: movie_dep,
    movie_service: movie_service_dep,
    file_id: UUID,
    delete_from_disk: Annotated[bool, Query()] = True,
) -> None:
    """Delete a specific file for a movie, addressed by its surrogate id."""
    await movie_service.delete_movie_file(
        movie=movie,
        file_id=file_id,
        delete_from_disk=delete_from_disk,
    )
