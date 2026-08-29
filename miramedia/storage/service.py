"""Read-only storage-health orchestration (SQL + bounded library/path overlay)."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from miramedia.config import BasicConfig, MiraMediaConfig
from miramedia.database import release_sessions_before_external_io
from miramedia.exceptions import NotFoundError
from miramedia.movies.repository import MovieRepository
from miramedia.movies.schemas import MovieFile as MovieFileSchema
from miramedia.movies.schemas import MovieId
from miramedia.shows.repository import ShowRepository
from miramedia.shows.schemas import EpisodeFile as EpisodeFileSchema
from miramedia.storage.repository import StorageHealthRepository
from miramedia.storage.schemas import (
    FRESHNESS_NOTE,
    PaginatedStorageHealthFiles,
    StorageHealthCounts,
    StorageHealthFile,
    StorageHealthLibraryProbe,
    StorageHealthSummary,
    StorageMediaType,
    StorageVolume,
)
from miramedia.storage.states import (
    ListFilterState,
    apply_path_overlay,
    classify_sql_state,
)
from miramedia.storage.volumes import probe_storage_volumes
from miramedia.torrents.integrity import (
    IntegrityPathLayout,
    batch_resolve_episode_paths_async,
    batch_resolve_movie_paths_async,
)
from miramedia.torrents.schemas import Quality

log = logging.getLogger(__name__)

_SLOW_PATH_SECONDS = 2.0


def probe_library_root(
    name: str,
    kind: StorageMediaType,
    path: Path | str,
) -> StorageHealthLibraryProbe:
    """Bounded root liveness: exists / is_dir / readable. Never walks media."""
    raw = str(path).strip()
    if not raw:
        return StorageHealthLibraryProbe(
            name=name, kind=kind, path=raw, ok=False, error="unset_name"
        )
    root = Path(raw)
    try:
        if not root.exists():
            return StorageHealthLibraryProbe(
                name=name, kind=kind, path=raw, ok=False, error="missing"
            )
        if not root.is_dir():
            return StorageHealthLibraryProbe(
                name=name, kind=kind, path=raw, ok=False, error="not_a_directory"
            )
        if not os.access(root, os.R_OK):
            return StorageHealthLibraryProbe(
                name=name, kind=kind, path=raw, ok=False, error="permission"
            )
    except OSError:
        return StorageHealthLibraryProbe(
            name=name, kind=kind, path=raw, ok=False, error="permission"
        )
    return StorageHealthLibraryProbe(
        name=name, kind=kind, path=raw, ok=True, error=None
    )


def configured_library_roots(misc: BasicConfig) -> list[StorageHealthLibraryProbe]:
    """O(library-roots) probe list: defaults + named libraries."""
    targets: list[tuple[str, StorageMediaType, Path]] = [
        ("Default", "show", Path(misc.show_directory)),
        ("Default", "movie", Path(misc.movie_directory)),
    ]
    targets.extend(
        (lib.name, "show", Path(lib.path))
        for lib in misc.show_libraries
        if lib.name != "Default"
    )
    targets.extend(
        (lib.name, "movie", Path(lib.path))
        for lib in misc.movie_libraries
        if lib.name != "Default"
    )
    return [probe_library_root(name, kind, path) for name, kind, path in targets]


def configured_library_names(misc: BasicConfig) -> set[str]:
    names = {"Default"}
    names.update(lib.name for lib in misc.show_libraries)
    names.update(lib.name for lib in misc.movie_libraries)
    return names


def probe_storage_summary(
    misc: BasicConfig,
) -> tuple[list[StorageHealthLibraryProbe], list[StorageVolume]]:
    """Synchronous bounded probe bundle for one configured-root set."""
    return configured_library_roots(misc), probe_storage_volumes(misc)


def _library_ok(
    probes: list[StorageHealthLibraryProbe],
    *,
    kind: StorageMediaType,
    library: str,
) -> bool | None:
    name = (library or "").strip() or "Default"
    for probe in probes:
        if probe.kind == kind and probe.name == name:
            return probe.ok
    return None


class StorageHealthService:
    def __init__(
        self,
        db: AsyncSession,
        *,
        show_repository: ShowRepository,
        movie_repository: MovieRepository,
        repository: StorageHealthRepository | None = None,
        config: MiraMediaConfig | None = None,
    ) -> None:
        self.db = db
        self.show_repository = show_repository
        self.movie_repository = movie_repository
        self.repository = repository or StorageHealthRepository(db)
        self._config = config

    def _misc(self) -> BasicConfig:
        cfg = self._config or MiraMediaConfig()
        return cfg.misc

    async def _release_sessions(self) -> None:
        await release_sessions_before_external_io(
            self.db,
            self.repository.db,
            self.show_repository.db,
            self.movie_repository.db,
        )

    async def get_summary(self) -> StorageHealthSummary:
        started = time.monotonic()
        buckets = await self.repository.count_buckets()
        title_libraries = await self.repository.list_title_library_names()
        misc = self._misc()
        configured = configured_library_names(misc)
        unconfigured = [name for name in title_libraries if name not in configured]
        await self._release_sessions()
        libraries, volumes = await asyncio.to_thread(probe_storage_summary, misc)
        elapsed = time.monotonic() - started
        if elapsed > _SLOW_PATH_SECONDS:
            log.warning("storage health summary slow path duration_s=%.3f", elapsed)
        return StorageHealthSummary(
            generated_at=datetime.now(UTC),
            integrity_check_enabled=misc.integrity_check_enabled,
            integrity_check_interval_hours=misc.integrity_check_interval_hours,
            freshness_note=FRESHNESS_NOTE,
            counts=StorageHealthCounts(
                imported=buckets["imported"],
                healthy=buckets["healthy"],
                unknown=buckets["unknown"],
                corrupt=buckets["corrupt"],
                orphaned=buckets["orphaned"],
                pending=buckets["pending"],
                missing=None,
            ),
            libraries=libraries,
            unconfigured_library_names=unconfigured,
            volumes=volumes,
        )

    async def list_files(
        self,
        *,
        offset: int,
        limit: int,
        state: ListFilterState | None = None,
        media_type: StorageMediaType | None = None,
        q: str | None = None,
    ) -> PaginatedStorageHealthFiles:
        started = time.monotonic()
        page = await self.repository.paginate_keys(
            offset=offset,
            limit=limit,
            state=state,
            media_type=media_type,
            q=q,
        )
        items = await self._hydrate_page(page.keys)
        elapsed = time.monotonic() - started
        if elapsed > _SLOW_PATH_SECONDS:
            log.warning(
                "storage health list slow path duration_s=%.3f limit=%d",
                elapsed,
                limit,
            )
        page_span = len(page.keys)
        next_offset = (
            offset + page_span
            if page_span > 0 and offset + page_span < page.total
            else None
        )
        return PaginatedStorageHealthFiles(
            items=items,
            total=page.total,
            offset=offset,
            limit=limit,
            next_offset=next_offset,
        )

    async def get_file(
        self,
        *,
        media_type: StorageMediaType,
        file_id: UUID,
    ) -> StorageHealthFile:
        key_media: Literal["show", "movie"] = media_type
        from miramedia.torrents.integrity import Sha1MismatchPageKey

        items = await self._hydrate_page(
            [Sha1MismatchPageKey(media_type=key_media, file_id=file_id)],
            require_existing=True,
        )
        if not items:
            msg = f"Storage health file {media_type}/{file_id} was not found"
            raise NotFoundError(msg)
        return items[0]

    async def _hydrate_page(
        self,
        keys: list,
        *,
        require_existing: bool = False,
    ) -> list[StorageHealthFile]:
        show_ids = [key.file_id for key in keys if key.media_type == "show"]
        movie_ids = [key.file_id for key in keys if key.media_type == "movie"]
        show_rows_map = await self.repository.get_episode_files_by_ids(show_ids)
        movie_rows_map = await self.repository.get_movie_files_by_ids(movie_ids)
        if require_existing and keys:
            key = keys[0]
            if key.media_type == "show" and key.file_id not in show_rows_map:
                return []
            if key.media_type == "movie" and key.file_id not in movie_rows_map:
                return []
        show_rows = [show_rows_map[fid] for fid in show_ids if fid in show_rows_map]
        movie_rows = [movie_rows_map[fid] for fid in movie_ids if fid in movie_rows_map]
        episode_context = await self.show_repository.batch_episodes_with_context(
            [row.episode_id for row in show_rows]
        )
        shows = await self.show_repository.get_shows_by_ids(
            list({ctx.show_id for ctx in episode_context.values()})
        )
        movies = await self.movie_repository.get_movies_by_ids(
            [row.movie_id for row in movie_rows]
        )
        movie_names = await self.movie_repository.get_movie_names_by_ids(
            [row.movie_id for row in movie_rows]
        )
        await self._release_sessions()
        probes = configured_library_roots(self._misc())
        layout = IntegrityPathLayout.from_config(self._config)
        show_paths = await batch_resolve_episode_paths_async(
            show_rows, episode_context, shows, layout
        )
        movie_paths = await batch_resolve_movie_paths_async(movie_rows, movies, layout)
        out: list[StorageHealthFile] = []
        for key in keys:
            if key.media_type == "show":
                row = show_rows_map.get(key.file_id)
                if row is None:
                    continue
                out.append(
                    self._show_item(
                        row,
                        episode_context=episode_context,
                        shows=shows,
                        path=show_paths.get(row.id),
                        probes=probes,
                    )
                )
            else:
                row = movie_rows_map.get(key.file_id)
                if row is None:
                    continue
                out.append(
                    self._movie_item(
                        row,
                        movies=movies,
                        movie_names=movie_names,
                        path=movie_paths.get(row.id),
                        probes=probes,
                    )
                )
        return out

    def _show_item(
        self,
        row: EpisodeFileSchema,
        *,
        episode_context: dict,
        shows: dict,
        path: Path | None,
        probes: list[StorageHealthLibraryProbe],
    ) -> StorageHealthFile:
        media_title = ""
        episode_label: str | None = None
        media_id = row.episode_id
        library = "Default"
        try:
            ctx = episode_context[row.episode_id]
            media_title = ctx.show_name
            episode_label = f"S{ctx.season_number:02d}E{ctx.episode_number:02d}"
            media_id = ctx.show_id
            show = shows.get(ctx.show_id)
            if show is not None:
                library = show.library or "Default"
        except Exception:
            log.exception(
                "Failed to resolve show title for storage-health episode_file %s",
                row.id,
            )
        sql_state = classify_sql_state(
            import_status=row.import_status,
            import_error=row.import_error,
            sha1=row.sha1,
            torrent_id=row.torrent_id,
        )
        resolved = str(path) if path is not None else None
        state = apply_path_overlay(
            sql_state,
            library_ok=_library_ok(probes, kind="show", library=library),
            path=resolved,
        )
        return StorageHealthFile(
            file_id=row.id,
            media_type="show",
            media_id=media_id,
            media_title=media_title,
            episode=episode_label,
            library=library,
            quality=Quality(row.quality),
            variant_tag=row.variant or "",
            import_status=row.import_status,
            import_error=row.import_error,
            sha1=row.sha1,
            imported_at=row.imported_at,
            last_attempt_at=row.last_attempt_at,
            torrent_id=row.torrent_id,
            state=state,
            path=resolved,
        )

    def _movie_item(
        self,
        row: MovieFileSchema,
        *,
        movies: dict,
        movie_names: dict[MovieId, str],
        path: Path | None,
        probes: list[StorageHealthLibraryProbe],
    ) -> StorageHealthFile:
        media_title = ""
        library = "Default"
        try:
            media_title = movie_names[row.movie_id]
        except Exception:
            log.exception(
                "Failed to resolve movie title for storage-health movie_file %s",
                row.id,
            )
        movie = movies.get(row.movie_id)
        if movie is not None:
            library = movie.library or "Default"
        sql_state = classify_sql_state(
            import_status=row.import_status,
            import_error=row.import_error,
            sha1=row.sha1,
            torrent_id=row.torrent_id,
        )
        resolved = str(path) if path is not None else None
        state = apply_path_overlay(
            sql_state,
            library_ok=_library_ok(probes, kind="movie", library=library),
            path=resolved,
        )
        return StorageHealthFile(
            file_id=row.id,
            media_type="movie",
            media_id=row.movie_id,
            media_title=media_title,
            episode=None,
            library=library,
            quality=Quality(row.quality),
            variant_tag=row.variant or "",
            import_status=row.import_status,
            import_error=row.import_error,
            sha1=row.sha1,
            imported_at=row.imported_at,
            last_attempt_at=row.last_attempt_at,
            torrent_id=row.torrent_id,
            state=state,
            path=resolved,
        )
