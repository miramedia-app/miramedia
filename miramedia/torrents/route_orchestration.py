"""Torrent route application orchestration — manual download and manual map."""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

from miramedia.exceptions import NotFoundError
from miramedia.file_status import ImportOutcome
from miramedia.indexers.schemas import IndexerQueryResult
from miramedia.movies.schemas import MovieId
from miramedia.shows.schemas import EpisodeId, ShowId
from miramedia.torrents.paths import resolve_within
from miramedia.torrents.schemas import (
    ManualMapRequest,
    ManualMapResult,
    ManualMapTargetType,
    MediaType,
    Quality,
    Torrent,
)

if TYPE_CHECKING:
    from miramedia.indexers.service import IndexerService
    from miramedia.movies.repository import MovieRepository
    from miramedia.movies.service import MovieService
    from miramedia.shows.repository import ShowRepository
    from miramedia.shows.service import ShowService
    from miramedia.torrents.schemas import ManualDownloadRequest
    from miramedia.torrents.service import TorrentService

log = logging.getLogger(__name__)

_OPERATOR_SAFE_BULK_VALUE_ERROR_DETAILS = frozenset(
    {
        "torrent not linked to any media",
        "episode_id required for target_type=episode",
        "movie_id required for target_type=movie",
    }
)


def resolve_variant_quality(body) -> tuple[str, Quality | None]:  # noqa: ANN001
    """Extract ``(variant, quality_override)`` from a download/map body."""
    variant = getattr(body, "variant", "") or ""
    quality_override = getattr(body, "quality_override", None)
    return variant, quality_override


def safe_bulk_item_error_message(exc: Exception, *, fallback: str) -> str:
    """Map per-item bulk failures to fixed categories; logs keep the real cause."""
    if isinstance(exc, ValueError):
        message = str(exc)
        if message in _OPERATOR_SAFE_BULK_VALUE_ERROR_DETAILS:
            return message
        return fallback
    if isinstance(exc, NotFoundError):
        return "not found"
    from miramedia.imports.files import DiskSpaceError

    if isinstance(exc, DiskSpaceError):
        return "insufficient disk space"
    return fallback


async def apply_library_override(
    *,
    media_type: MediaType,
    media_id: uuid.UUID,
    library: str,
    show_service: ShowService,
    movie_service: MovieService,
) -> None:
    """Reassign the show/movie library before linking so files land under it."""
    from miramedia.config import MiraMediaConfig

    cfg = MiraMediaConfig().misc
    if media_type == MediaType.show:
        valid = {"Default", *(lib.name for lib in cfg.show_libraries)}
        if library not in valid:
            msg = f"Unknown show library '{library}'"
            raise ValueError(msg)
        show = await show_service.get_show_by_id(ShowId(media_id))
        if show.library != library:
            await show_service.set_show_library(show=show, library=library)
    else:
        valid = {"Default", *(lib.name for lib in cfg.movie_libraries)}
        if library not in valid:
            msg = f"Unknown movie library '{library}'"
            raise ValueError(msg)
        movie = await movie_service.get_movie_by_id(MovieId(media_id))
        if movie.library != library:
            await movie_service.set_movie_library(movie=movie, library=library)


async def execute_manual_download(
    *,
    body: ManualDownloadRequest,
    torrent_service: TorrentService,
    indexer_service: IndexerService,
    show_service: ShowService,
    movie_service: MovieService,
    show_repository: ShowRepository,
    movie_repository: MovieRepository,
) -> tuple[Torrent, str, Quality | None]:
    """Consume a manual-parse token, persist the result, and download/link."""
    payload = await torrent_service.pop_manual_parse_token(body.download_token)
    if payload is None:
        msg = "Download token not found or already used. Please re-parse."
        raise NotFoundError(msg)

    synthetic_result = IndexerQueryResult.model_validate(payload)

    if body.library is not None:
        await apply_library_override(
            media_type=body.media_type,
            media_id=body.media_id,
            library=body.library,
            show_service=show_service,
            movie_service=movie_service,
        )

    await indexer_service.save_result(synthetic_result)

    variant, quality_override = resolve_variant_quality(body)
    torrent = await torrent_service.download_and_link(
        indexer_result=synthetic_result,
        media_type=body.media_type,
        media_id=body.media_id,
        variant=variant,
        quality_override=quality_override,
        show_repository=show_repository,
        movie_repository=movie_repository,
    )
    return torrent, variant, quality_override


async def map_torrent_files(
    *,
    torrent: Torrent,
    body: ManualMapRequest,
    show_service: ShowService,
    movie_service: MovieService,
    show_repository: ShowRepository,
    movie_repository: MovieRepository,
) -> ManualMapResult:
    """Apply a user-supplied mapping of source files to target media."""
    from miramedia.torrents.paths import get_torrent_filepath

    root = get_torrent_filepath(torrent)
    result = ManualMapResult(mapped=0, skipped=0, failed=0, errors=[])

    episode_ids = list(
        dict.fromkeys(
            item.episode_id
            for item in body.items
            if item.target_type == ManualMapTargetType.episode
            and item.episode_id is not None
        )
    )
    movie_ids = list(
        dict.fromkeys(
            item.movie_id
            for item in body.items
            if item.target_type == ManualMapTargetType.movie
            and item.movie_id is not None
        )
    )
    episode_lookup = (
        await show_repository.get_episodes_with_seasons(
            [EpisodeId(episode_id) for episode_id in episode_ids]
        )
        if episode_ids
        else {}
    )
    show_ids = [
        season.show_id
        for season, _ in episode_lookup.values()
        if season.show_id is not None
    ]
    show_ids = list(dict.fromkeys(show_ids))
    shows_by_id = await show_repository.get_shows_by_ids(show_ids) if show_ids else {}
    movies_by_id = (
        await movie_repository.get_movies_by_ids(
            [MovieId(movie_id) for movie_id in movie_ids]
        )
        if movie_ids
        else {}
    )

    for item in body.items:
        if item.target_type == ManualMapTargetType.skip:
            result.skipped += 1
            continue

        source = resolve_within(root, item.relative_path)
        if source is None:
            result.failed += 1
            result.errors.append(f"path escapes torrent root: {item.relative_path}")
            continue
        if not source.exists() or not source.is_file():
            result.failed += 1
            result.errors.append(f"missing source: {item.relative_path}")
            continue

        item_variant, _ = resolve_variant_quality(item)
        outcome: ImportOutcome = ImportOutcome.failed_io
        error: str | None = None
        try:
            if item.target_type == ManualMapTargetType.episode:
                if item.episode_id is None:
                    msg = "episode_id required for target_type=episode"
                    raise ValueError(msg)  # noqa: TRY301
                pair = episode_lookup.get(item.episode_id)
                if pair is None:
                    msg = f"Episode with id {item.episode_id} not found."
                    raise NotFoundError(msg)  # noqa: TRY301
                season, episode = pair
                show = shows_by_id.get(season.show_id)
                if show is None:
                    msg = f"Show with id {season.show_id} not found."
                    raise NotFoundError(msg)  # noqa: TRY301
                outcome, error = await show_service.import_episode_from_file(
                    show=show,
                    season=season,
                    episode=episode,
                    source_file=source,
                    torrent_id=torrent.id,
                    source_info_hash=torrent.hash,
                    variant=item_variant,
                )
            elif item.target_type == ManualMapTargetType.movie:
                if item.movie_id is None:
                    msg = "movie_id required for target_type=movie"
                    raise ValueError(msg)  # noqa: TRY301
                movie = movies_by_id.get(item.movie_id)
                if movie is None:
                    msg = f"Movie with id {item.movie_id} not found."
                    raise NotFoundError(msg)  # noqa: TRY301
                outcome, error = await movie_service.import_movie_from_file(
                    movie=movie,
                    source_file=source,
                    torrent_id=torrent.id,
                    source_info_hash=torrent.hash,
                    variant=item_variant,
                )
            else:
                msg = f"unknown target_type {item.target_type}"
                raise ValueError(msg)  # noqa: TRY301
        except Exception as exc:
            result.failed += 1
            result.errors.append(
                f"{item.relative_path}: "
                f"{safe_bulk_item_error_message(exc, fallback='import failed')}"
            )
            log.exception("Manual-map import failed for %s", item.relative_path)
            continue

        if outcome == ImportOutcome.imported:
            result.mapped += 1
        else:
            result.failed += 1
            if error:
                result.errors.append(f"{item.relative_path}: {error}")

    return result
