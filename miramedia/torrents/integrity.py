"""Per-file SHA1 helpers for the Phase 6.5 integrity audit.

The integrity feature is opt-in (``misc.integrity_check_enabled``). When on:

* ``compute_sha1(path)`` is called immediately after a successful import to
  capture the canonical hash on the EpisodeFile/MovieFile row.
* The scheduled ``verify_imported_files_task`` re-hashes each row that has a
  stored ``sha1`` and logs a WARNING for any mismatch (and stamps
  ``import_error`` so the imports dashboard surfaces the problem).

Compare-and-set predicates compare the database state observed *before*
filesystem hashing — never the freshly computed digest.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, NamedTuple
from uuid import UUID

from sqlalchemy import ColumnElement, and_

from miramedia.config import MiraMediaConfig
from miramedia.file_status import ImportOutcome
from miramedia.movies.models import MovieFile
from miramedia.movies.schemas import Movie, MovieId
from miramedia.movies.schemas import MovieFile as MovieFileSchema
from miramedia.naming import (
    default_movie_folder_name,
    default_season_folder_name,
    default_show_folder_name,
    episode_file_stem_candidates,
    movie_file_stem_candidates,
    movie_folder_name,
    old_movie_folder_name,
    old_show_folder_name,
    season_folder_name,
    show_folder_name,
)
from miramedia.shows.models import EpisodeFile
from miramedia.shows.schemas import EpisodeFile as EpisodeFileSchema
from miramedia.shows.schemas import EpisodeId, EpisodeIntegrityContext, Show, ShowId
from miramedia.torrents.quality_naming import NameParts
from miramedia.torrents.schemas import Quality

log = logging.getLogger(__name__)

_MISMATCH_ERROR_PREFIX = "sha1 mismatch%"

# Bounded integrity-mismatch API (Plan 082).
INTEGRITY_MISMATCH_DEFAULT_LIMIT = 50
INTEGRITY_MISMATCH_MAX_LIMIT = 100

# Scheduler chunk size for verify_imported_files_task (Plan 082).
INTEGRITY_AUDIT_CHUNK_SIZE = 100

_VIDEO_SUFFIXES = frozenset(
    {".mkv", ".mp4", ".avi", ".mov", ".m4v", ".webm", ".ts", ".wmv"}
)

IntegrityFileModel = type[EpisodeFile | MovieFile]


class Sha1MismatchPageKey(NamedTuple):
    media_type: Literal["show", "movie"]
    file_id: UUID


class Sha1MismatchPage(NamedTuple):
    keys: list[Sha1MismatchPageKey]
    total: int


def integrity_audit_snapshot_where(
    file_table: IntegrityFileModel,
    file_id: UUID,
    *,
    expected_sha1: str | None,
    expected_import_error: str | None,
) -> ColumnElement[bool]:
    """Row must still match the import fields observed before hashing."""
    if expected_sha1 is None:
        sha_predicate = file_table.sha1.is_(None)
    else:
        sha_predicate = file_table.sha1.is_not_distinct_from(expected_sha1)
    if expected_import_error is None:
        import_error_predicate = file_table.import_error.is_(None)
    else:
        import_error_predicate = file_table.import_error.is_not_distinct_from(
            expected_import_error
        )
    return and_(
        file_table.id == file_id,
        file_table.import_status == ImportOutcome.imported,
        sha_predicate,
        import_error_predicate,
    )


def integrity_mismatch_action_snapshot_where(
    file_table: IntegrityFileModel,
    file_id: UUID,
    *,
    expected_sha1: str | None,
    expected_import_error: str,
) -> ColumnElement[bool]:
    """Row must still match the mismatch fields observed when the action started."""
    if expected_sha1 is None:
        sha_predicate = file_table.sha1.is_(None)
    else:
        sha_predicate = file_table.sha1.is_not_distinct_from(expected_sha1)
    return and_(
        file_table.id == file_id,
        file_table.import_status == ImportOutcome.imported,
        file_table.import_error.like(_MISMATCH_ERROR_PREFIX),
        file_table.import_error.is_not_distinct_from(expected_import_error),
        sha_predicate,
    )


_CHUNK = 1024 * 1024  # 1 MiB


@dataclass(frozen=True)
class IntegrityPathLayout:
    """Config-driven on-disk layout without a database session."""

    _show_libraries: tuple[tuple[str, Path], ...]
    _movie_libraries: tuple[tuple[str, Path], ...]
    _default_show_directory: Path
    _default_movie_directory: Path

    @classmethod
    def from_config(cls, config: MiraMediaConfig | None = None) -> IntegrityPathLayout:
        cfg = config or MiraMediaConfig()
        misc = cfg.misc
        return cls(
            _show_libraries=tuple(
                (lib.name, Path(lib.path)) for lib in misc.show_libraries
            ),
            _movie_libraries=tuple(
                (lib.name, Path(lib.path)) for lib in misc.movie_libraries
            ),
            _default_show_directory=Path(misc.show_directory),
            _default_movie_directory=Path(misc.movie_directory),
        )

    def _library_parent(self, library: str, *, for_show: bool) -> Path:
        libraries = self._show_libraries if for_show else self._movie_libraries
        if library and library != "Default":
            for name, path in libraries:
                if name == library:
                    return path
            log.warning(
                "Library '%s' not found in configured %s libraries, using default",
                library,
                "show" if for_show else "movie",
            )
        return (
            self._default_show_directory if for_show else self._default_movie_directory
        )

    def _media_root(self, media: Show | Movie, *, for_show: bool) -> Path:
        if for_show:
            if not isinstance(media, Show):
                msg = "expected Show"
                raise TypeError(msg)
            dir_name = show_folder_name(media)
            fallback_names = (
                default_show_folder_name(media),
                old_show_folder_name(media),
            )
        else:
            if not isinstance(media, Movie):
                msg = "expected Movie"
                raise TypeError(msg)
            dir_name = movie_folder_name(media)
            fallback_names = (
                default_movie_folder_name(media),
                old_movie_folder_name(media),
            )
        parent = self._library_parent(media.library, for_show=for_show)
        new_path = parent / dir_name
        if new_path.exists():
            return new_path
        for fallback_name in fallback_names:
            if fallback_name == dir_name:
                continue
            old_path = parent / fallback_name
            if old_path.exists():
                return old_path
        return new_path

    def show_root(self, show: Show) -> Path:
        return self._media_root(show, for_show=True)

    def season_directory(self, show: Show, season_number: int) -> Path:
        root = self.show_root(show)
        current = root / Path(season_folder_name(season_number))
        fallback = root / Path(default_season_folder_name(season_number))
        if not current.exists() and fallback != current and fallback.exists():
            return fallback
        return current

    def movie_root(self, movie: Movie) -> Path:
        return self._media_root(movie, for_show=False)


def scan_directory_for_stem_prefixes(
    directory: Path,
    stem_prefixes: frozenset[str],
) -> dict[str, Path]:
    """Return one deterministic video path per requested ``stem + '.'`` prefix.

    Memory is O(len(stem_prefixes)), not O(directory size). When several files
    share a prefix, the lexicographically smallest filename wins.
    """
    if not stem_prefixes or not directory.exists() or not directory.is_dir():
        return {}
    best: dict[str, Path] = {}
    try:
        for entry in directory.iterdir():
            if not entry.is_file():
                continue
            if entry.suffix.lower() not in _VIDEO_SUFFIXES:
                continue
            name = entry.name
            for prefix in stem_prefixes:
                if not name.startswith(prefix):
                    continue
                current = best.get(prefix)
                if current is None or name < current.name:
                    best[prefix] = entry
    except OSError:
        return {}
    return best


def resolve_video_path_from_stems(
    directory: Path,
    stems: Iterable[str],
    *,
    candidates: dict[str, Path] | None = None,
) -> Path | None:
    """Return the first video file matching any stem under ``directory``."""
    prefix_map = (
        candidates
        if candidates is not None
        else scan_directory_for_stem_prefixes(
            directory, frozenset(f"{stem}." for stem in stems)
        )
    )
    if not prefix_map:
        return None
    for stem in stems:
        candidate = prefix_map.get(stem + ".")
        if candidate is not None:
            return candidate
    return None


def resolve_episode_file_path_in_memory(
    *,
    show: Show,
    season_number: int,
    episode_number: int,
    episode_file: EpisodeFileSchema,
    season_dir: Path,
    candidates: dict[str, Path] | None = None,
) -> Path | None:
    """Pure in-memory episode path resolution (same semantics as ShowService)."""
    stems = episode_file_stem_candidates(
        show,
        season_number=season_number,
        episode_number=episode_number,
        quality=Quality(episode_file.quality),
        parts=NameParts.from_row(episode_file),
    )
    prefixes = frozenset(f"{stem}." for stem in stems)
    resolved_candidates = candidates
    if resolved_candidates is None:
        resolved_candidates = scan_directory_for_stem_prefixes(season_dir, prefixes)
    return resolve_video_path_from_stems(
        season_dir, stems, candidates=resolved_candidates
    )


def resolve_movie_file_path_in_memory(
    *,
    movie: Movie,
    movie_file: MovieFileSchema,
    movie_root: Path,
    candidates: dict[str, Path] | None = None,
) -> Path | None:
    """Pure in-memory movie path resolution (same semantics as MovieService)."""
    stems = movie_file_stem_candidates(
        movie, Quality(movie_file.quality), NameParts.from_row(movie_file)
    )
    prefixes = frozenset(f"{stem}." for stem in stems)
    resolved_candidates = candidates
    if resolved_candidates is None:
        resolved_candidates = scan_directory_for_stem_prefixes(movie_root, prefixes)
    return resolve_video_path_from_stems(
        movie_root, stems, candidates=resolved_candidates
    )


def batch_resolve_episode_paths_sync(
    rows: list[EpisodeFileSchema],
    episode_context: dict[EpisodeId, EpisodeIntegrityContext],
    shows: dict[ShowId, Show],
    layout: IntegrityPathLayout,
) -> dict[UUID, Path | None]:
    """Resolve episode paths off-session with one bounded scan per season."""
    grouped: dict[
        tuple[ShowId, int], list[tuple[EpisodeFileSchema, EpisodeIntegrityContext]]
    ] = defaultdict(list)
    for row in rows:
        ctx = episode_context.get(row.episode_id)
        if ctx is None:
            continue
        grouped[(ctx.show_id, ctx.season_number)].append((row, ctx))

    paths: dict[UUID, Path | None] = {row.id: None for row in rows}
    for (show_id, season_number), items in grouped.items():
        show = shows.get(show_id)
        if show is None:
            continue
        season_dir = layout.season_directory(show, season_number)
        prefixes: set[str] = set()
        for row, ctx in items:
            stems = episode_file_stem_candidates(
                show,
                season_number=ctx.season_number,
                episode_number=ctx.episode_number,
                quality=Quality(row.quality),
                parts=NameParts.from_row(row),
            )
            prefixes.update(f"{stem}." for stem in stems)
        candidates = scan_directory_for_stem_prefixes(season_dir, frozenset(prefixes))
        for row, ctx in items:
            paths[row.id] = resolve_episode_file_path_in_memory(
                show=show,
                season_number=ctx.season_number,
                episode_number=ctx.episode_number,
                episode_file=row,
                season_dir=season_dir,
                candidates=candidates,
            )
    return paths


def batch_resolve_movie_paths_sync(
    rows: list[MovieFileSchema],
    movies: dict[MovieId, Movie],
    layout: IntegrityPathLayout,
) -> dict[UUID, Path | None]:
    """Resolve movie paths off-session with one bounded scan per movie root."""
    grouped: dict[MovieId, list[MovieFileSchema]] = defaultdict(list)
    for row in rows:
        grouped[row.movie_id].append(row)

    paths: dict[UUID, Path | None] = {row.id: None for row in rows}
    for movie_id, items in grouped.items():
        movie = movies.get(movie_id)
        if movie is None:
            continue
        movie_root = layout.movie_root(movie)
        prefixes: set[str] = set()
        for row in items:
            stems = movie_file_stem_candidates(
                movie, Quality(row.quality), NameParts.from_row(row)
            )
            prefixes.update(f"{stem}." for stem in stems)
        candidates = scan_directory_for_stem_prefixes(movie_root, frozenset(prefixes))
        for row in items:
            paths[row.id] = resolve_movie_file_path_in_memory(
                movie=movie,
                movie_file=row,
                movie_root=movie_root,
                candidates=candidates,
            )
    return paths


async def batch_resolve_episode_paths_async(
    rows: list[EpisodeFileSchema],
    episode_context: dict[EpisodeId, EpisodeIntegrityContext],
    shows: dict[ShowId, Show],
    layout: IntegrityPathLayout,
) -> dict[UUID, Path | None]:
    return await asyncio.to_thread(
        batch_resolve_episode_paths_sync,
        rows,
        episode_context,
        shows,
        layout,
    )


async def batch_resolve_movie_paths_async(
    rows: list[MovieFileSchema],
    movies: dict[MovieId, Movie],
    layout: IntegrityPathLayout,
) -> dict[UUID, Path | None]:
    return await asyncio.to_thread(
        batch_resolve_movie_paths_sync,
        rows,
        movies,
        layout,
    )


def compute_sha1(path: Path) -> str | None:
    """Return the SHA1 hex digest of ``path``, or ``None`` on I/O error.

    Reads the file in 1 MiB chunks so large media files don't pin memory.
    Returning ``None`` on failure keeps the audit non-fatal — the caller logs
    and moves on rather than aborting the whole sweep.
    """
    try:
        h = hashlib.sha1()  # noqa: S324 — used for change detection, not security
        with path.open("rb") as fh:
            while True:
                chunk = fh.read(_CHUNK)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        log.warning("sha1 compute failed for %s", path, exc_info=True)
        return None
