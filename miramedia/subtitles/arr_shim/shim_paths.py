"""On-disk path helpers shared by the Sonarr/Radarr Bazarr shim."""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import Iterable, Sequence
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from miramedia.config import MiraMediaConfig
from miramedia.file_status import ImportOutcome
from miramedia.media_inventory import MediaFileInventory
from miramedia.movies.models import Movie
from miramedia.movies.schemas import Movie as MovieSchema
from miramedia.movies.schemas import MovieFile as MovieFileSchema
from miramedia.movies.schemas import MovieId
from miramedia.shows.models import Episode, EpisodeFile, Show
from miramedia.shows.schemas import EpisodeFile as EpisodeFileSchema
from miramedia.shows.schemas import EpisodeId, EpisodeIntegrityContext, ShowId
from miramedia.shows.schemas import Show as ShowSchema
from miramedia.torrents.integrity import (
    IntegrityPathLayout,
    batch_resolve_episode_paths_sync,
    batch_resolve_movie_paths_sync,
)


def show_library_roots(config: MiraMediaConfig | None = None) -> list[Path]:
    """Distinct configured show library roots (default + named libraries)."""
    cfg = config or MiraMediaConfig()
    misc = cfg.misc
    seen: set[str] = set()
    roots: list[Path] = []
    for candidate in (
        misc.show_directory,
        *(Path(lib.path) for lib in misc.show_libraries),
    ):
        key = str(candidate.resolve()) if candidate.exists() else str(candidate)
        if key in seen:
            continue
        seen.add(key)
        roots.append(candidate)
    return roots


def movie_library_roots(config: MiraMediaConfig | None = None) -> list[Path]:
    """Distinct configured movie library roots (default + named libraries)."""
    cfg = config or MiraMediaConfig()
    misc = cfg.misc
    seen: set[str] = set()
    roots: list[Path] = []
    for candidate in (
        misc.movie_directory,
        *(Path(lib.path) for lib in misc.movie_libraries),
    ):
        key = str(candidate.resolve()) if candidate.exists() else str(candidate)
        if key in seen:
            continue
        seen.add(key)
        roots.append(candidate)
    return roots


def rootfolder_payloads(roots: Sequence[Path]) -> list[dict[str, object]]:
    """Sonarr-shaped rootfolder rows for Bazarr path accessibility checks."""
    payloads: list[dict[str, object]] = []
    for index, root in enumerate(roots, start=1):
        accessible = False
        free_space = 0
        try:
            accessible = root.is_dir()
        except OSError:
            accessible = False
        if accessible:
            path = str(root.resolve())
            try:
                free_space = shutil.disk_usage(root).free
            except OSError:
                free_space = 0
        else:
            try:
                path = str(root.resolve()) if root.exists() else str(root)
            except OSError:
                path = str(root)
        payloads.append(
            {
                "id": index,
                "path": path,
                "accessible": accessible,
                "freeSpace": free_space,
            }
        )
    return payloads


def show_schema_from_scalar_columns(show: Show) -> ShowSchema:
    """Build a pydantic ``Show`` without loading ``Show.seasons`` from the ORM."""
    from miramedia.media_state import ProgressStatus

    progress_status = show.list_progress_status
    if progress_status is None:
        progress_status = ProgressStatus.none
    elif not isinstance(progress_status, ProgressStatus):
        progress_status = ProgressStatus(progress_status)

    return ShowSchema(
        id=ShowId(show.id),
        name=show.name,
        overview=show.overview,
        year=show.year,
        ended=show.ended,
        external_id=show.external_id,
        metadata_provider=show.metadata_provider,
        continuous_download=show.continuous_download,
        skipped=show.skipped,
        library=show.library or "Default",
        original_language=show.original_language,
        imdb_id=show.imdb_id,
        vote_average=show.vote_average,
        content_rating=show.content_rating,
        genres=show.genres,
        cast=show.cast,
        preferred_quality=show.preferred_quality,
        preferred_codec=show.preferred_codec,
        subtitle_languages=show.subtitle_languages,
        last_metadata_check=show.last_metadata_check,
        metadata_failure_backoff_until=show.metadata_failure_backoff_until,
        auto_download_backoff_until=show.auto_download_backoff_until,
        wanted_episode_count=show.wanted_episode_count or 0,
        downloaded_episode_count=show.downloaded_episode_count or 0,
        list_progress_status=progress_status,
        seasons=[],
    )


def show_root_path(layout: IntegrityPathLayout, show: Show) -> Path:
    """Absolute show directory using the same layout rules as imports/streams.

    ``IntegrityPathLayout`` works on the pydantic schemas, not the ORM rows, so
    every entry point converts first (it raises ``TypeError`` on ORM objects).
    """
    return layout.show_root(ShowSchema.model_validate(show))


def show_root_path_from_scalar_columns(layout: IntegrityPathLayout, show: Show) -> Path:
    """Like :func:`show_root_path` but never touches ``Show.seasons`` on the ORM."""
    return layout.show_root(show_schema_from_scalar_columns(show))


def batch_show_root_paths(
    layout: IntegrityPathLayout, shows: Iterable[Show]
) -> dict[UUID, Path]:
    return {show.id: show_root_path(layout, show) for show in shows}


def movie_root_path(layout: IntegrityPathLayout, movie: Movie) -> Path:
    """Absolute movie directory using the same layout rules as imports/streams."""
    return layout.movie_root(movie)


def batch_movie_root_paths(
    layout: IntegrityPathLayout, movies: Iterable[Movie]
) -> dict[UUID, Path]:
    return {movie.id: layout.movie_root(movie) for movie in movies}


def _imported_movie_file_rows(movie: Movie) -> list[MovieFileSchema]:
    return [
        MovieFileSchema.model_validate(movie_file)
        for movie_file in movie.movie_files
        if movie_file.import_status == ImportOutcome.imported
    ]


def batch_movie_file_paths_for_movie(
    layout: IntegrityPathLayout,
    movie: Movie,
) -> dict[UUID, Path | None]:
    """Resolve on-disk video paths for every imported file on one movie."""
    rows = _imported_movie_file_rows(movie)
    if not rows:
        return {}
    movies = {MovieId(movie.id): MovieSchema.model_validate(movie)}
    return batch_resolve_movie_paths_sync(rows, movies, layout)


def batch_movie_file_paths_for_movies(
    layout: IntegrityPathLayout,
    movies: Sequence[Movie],
) -> dict[UUID, Path | None]:
    rows: list[MovieFileSchema] = []
    movie_map: dict[MovieId, MovieSchema] = {}
    for movie in movies:
        movie_map[MovieId(movie.id)] = MovieSchema.model_validate(movie)
        rows.extend(_imported_movie_file_rows(movie))
    if not rows:
        return {}
    return batch_resolve_movie_paths_sync(rows, movie_map, layout)


def _episode_context_for_show(show: Show) -> dict[EpisodeId, EpisodeIntegrityContext]:
    context: dict[EpisodeId, EpisodeIntegrityContext] = {}
    for season in show.seasons:
        for episode in season.episodes:
            context[EpisodeId(episode.id)] = EpisodeIntegrityContext(
                episode_number=episode.number,
                season_number=season.number,
                show_id=ShowId(show.id),
                show_name=show.name,
            )
    return context


def _imported_episode_file_rows(show: Show) -> list[EpisodeFileSchema]:
    rows: list[EpisodeFileSchema] = []
    for season in show.seasons:
        for episode in season.episodes:
            rows.extend(
                EpisodeFileSchema.model_validate(episode_file)
                for episode_file in episode.episode_files
                if episode_file.import_status == ImportOutcome.imported
            )
    return rows


def batch_episode_file_paths_for_episode(
    layout: IntegrityPathLayout,
    show: Show,
    episode: Episode,
    episode_files: Sequence[EpisodeFile],
) -> dict[UUID, Path | None]:
    """Resolve on-disk video paths for imported files on one episode only."""
    rows = [
        EpisodeFileSchema.model_validate(episode_file)
        for episode_file in episode_files
        if episode_file.import_status == ImportOutcome.imported
    ]
    if not rows:
        return {}
    season_number = episode.season.number
    context = {
        EpisodeId(episode.id): EpisodeIntegrityContext(
            episode_number=episode.number,
            season_number=season_number,
            show_id=ShowId(show.id),
            show_name=show.name,
        )
    }
    shows = {ShowId(show.id): show_schema_from_scalar_columns(show)}
    return batch_resolve_episode_paths_sync(rows, context, shows, layout)


def batch_episode_file_paths_for_show(
    layout: IntegrityPathLayout,
    show: Show,
) -> dict[UUID, Path | None]:
    """Resolve on-disk video paths for every imported file on one show."""
    rows = _imported_episode_file_rows(show)
    if not rows:
        return {}
    context = _episode_context_for_show(show)
    shows = {ShowId(show.id): ShowSchema.model_validate(show)}
    return batch_resolve_episode_paths_sync(rows, context, shows, layout)


def batch_episode_file_paths_for_shows(
    layout: IntegrityPathLayout,
    shows: Sequence[Show],
) -> dict[UUID, Path | None]:
    paths: dict[UUID, Path | None] = {}
    for show in shows:
        paths.update(batch_episode_file_paths_for_show(layout, show))
    return paths


async def batch_video_sizes(
    db: AsyncSession,
    *,
    file_ids: Sequence[UUID],
    paths: dict[UUID, Path | None],
) -> dict[UUID, int]:
    """Return byte sizes from inventory when possible, else ``stat()`` on disk."""
    if not file_ids:
        return {}

    stmt = select(MediaFileInventory.file_id, MediaFileInventory.size_bytes).where(
        MediaFileInventory.file_id.in_(file_ids),
        MediaFileInventory.kind == "video",
        MediaFileInventory.language == "",
    )
    rows = (await db.execute(stmt)).all()
    sizes: dict[UUID, int] = {
        file_id: int(size_bytes) for file_id, size_bytes in rows if size_bytes > 0
    }

    missing = [file_id for file_id in file_ids if file_id not in sizes]
    if not missing:
        return sizes

    def _stat_missing() -> dict[UUID, int]:
        resolved: dict[UUID, int] = {}
        for file_id in missing:
            path = paths.get(file_id)
            if path is None:
                continue
            try:
                resolved[file_id] = path.stat().st_size
            except OSError:
                continue
        return resolved

    sizes.update(await asyncio.to_thread(_stat_missing))
    return sizes
