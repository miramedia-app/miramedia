import asyncio
import os
import shutil
import threading
from collections import defaultdict
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import cast, overload
from uuid import UUID

from cachetools import TTLCache


def _disk_scan_concurrency() -> int:
    try:
        return max(1, int(os.getenv("MIRAMEDIA_DISK_SCAN_CONCURRENCY", "8")))
    except (TypeError, ValueError):
        return 8


# Cap concurrent movie-root scans on the list endpoint — see the shows-service
# equivalent. Unbounded fan-out saturates the threadpool + NAS disk.
_DISK_SCAN_CONCURRENCY = _disk_scan_concurrency()


def _disk_scan_cache_ttl() -> float:
    try:
        return max(0.0, float(os.getenv("MIRAMEDIA_DISK_SCAN_CACHE_TTL", "30")))
    except (TypeError, ValueError):
        return 30.0


# Short-TTL cache for movie-root "is downloaded" scans — the movies list
# re-scans every load/poll otherwise. Keyed by (movie_root, frozenset of stems);
# value is the bool. Thread-safe (runs in to_thread). TTL=0 disables. Cleared on
# import via invalidate_disk_scan_cache().
_DISK_SCAN_CACHE_TTL = _disk_scan_cache_ttl()
_scan_cache: TTLCache = TTLCache(maxsize=8192, ttl=_DISK_SCAN_CACHE_TTL or 1)
_scan_cache_lock = threading.Lock()


def invalidate_disk_scan_cache() -> None:
    """Drop all cached movie scans (call after an import mutates disk)."""
    with _scan_cache_lock:
        _scan_cache.clear()


def _as_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


# These imports intentionally follow the module-level scan-cache setup above.
from miramedia.config import LibraryItem, MiraMediaConfig  # noqa: E402
from miramedia.exceptions import (  # noqa: E402
    NotFoundError,
    RenameError,
)
from miramedia.file_status import ImportOutcome  # noqa: E402
from miramedia.imports.files import (  # noqa: E402
    DiskSpaceError,
    ImportConflictError,
    delete_files_matching_stems,
    files_matching_stem,
    find_renamed_duplicate,
    get_files_for_import,
    import_file,
    link_video_into_slot,
    rename_media_slot,
)
from miramedia.indexers.schemas import (  # noqa: E402
    IndexerQueryResult,
    IndexerQueryResultId,
)
from miramedia.indexers.service import IndexerService  # noqa: E402
from miramedia.indexers.utils import evaluate_indexer_query_results  # noqa: E402
from miramedia.media_service import (  # noqa: E402
    BgMediaSessionProtocol,
    MediaFileRowProtocol,
    MediaService,
)
from miramedia.media_status import MediaStatus  # noqa: E402
from miramedia.metadata.backends.generic import AbstractMetadataProvider  # noqa: E402
from miramedia.metadata.schemas import MetaDataProviderSearchResult  # noqa: E402
from miramedia.movies import log  # noqa: E402
from miramedia.movies.repository import MovieRepository  # noqa: E402
from miramedia.movies.schemas import (  # noqa: E402
    Movie,
    MovieFile,
    MovieId,
    PublicMovie,
    PublicMovieFile,
)
from miramedia.naming import (  # noqa: E402
    default_movie_folder_name,
    movie_file_stem,
    movie_file_stem_candidates,
    movie_folder_name,
    old_movie_folder_name,
)
from miramedia.notifications.service import NotificationService  # noqa: E402
from miramedia.torrents.integrity import (  # noqa: E402
    list_video_files_in_directory,
    resolve_movie_file_path_in_memory,
)
from miramedia.torrents.mediainfo import analyze_async  # noqa: E402
from miramedia.torrents.parsing import (  # noqa: E402
    is_video_file,
    normalize_codec,
    normalize_source,
    parse_release,
    parse_subtitle_filename,
)
from miramedia.torrents.quality_naming import NameParts  # noqa: E402
from miramedia.torrents.schemas import (  # noqa: E402
    MediaType,
    Quality,
    RichTorrent,
    Torrent,
    TorrentId,
    TorrentMediaContext,
)
from miramedia.torrents.service import TorrentService  # noqa: E402
from miramedia.torrents.utils import get_torrent_filepath  # noqa: E402


class MovieService(MediaService[Movie, MovieId]):
    def __init__(
        self,
        movie_repository: MovieRepository,
        torrent_service: TorrentService,
        indexer_service: IndexerService,
        notification_service: NotificationService,
    ) -> None:
        self.movie_repository = movie_repository
        self.torrent_service = torrent_service
        self.indexer_service = indexer_service
        self.notification_service = notification_service

    @property
    def media_repository(self) -> MovieRepository:
        return self.movie_repository

    async def _update_media_attributes(
        self, media_id: MovieId, **kwargs: object
    ) -> tuple[Movie, object]:
        return await self.movie_repository.update_movie_attributes(
            movie_id=media_id, **kwargs
        )

    def _media_id(self, media: Movie) -> MovieId:
        return media.id

    def _configured_libraries(self) -> list[LibraryItem]:
        return MiraMediaConfig().misc.movie_libraries

    def _default_media_directory(self) -> Path:
        return MiraMediaConfig().misc.movie_directory

    def _media_library_name(self, media: Movie) -> str | None:
        return media.library

    def _warn_library_not_found(self, media: Movie) -> None:
        log.warning(
            f"Library {media.library} not found in config, using default library"
        )

    def _primary_folder_name(self, media: Movie) -> str:
        return movie_folder_name(media)

    def _fallback_folder_names(self, media: Movie) -> tuple[str, str]:
        return default_movie_folder_name(media), old_movie_folder_name(media)

    async def _native_imdb_index(self) -> dict[str, MovieId]:
        return await self.movie_repository.native_imdb_index()

    def _provider_imdb_lookup(
        self, provider: AbstractMetadataProvider, external_id: str
    ) -> str | None:
        return provider.get_movie_imdb_id(external_id)

    async def _existing_by_identifiers(
        self, imdb_ids: list[str], provider_keys: list[tuple[str, str]]
    ) -> list[tuple[str | None, str, str, MovieId]]:
        return await self.movie_repository.movies_existing_by_identifiers(
            imdb_ids, provider_keys
        )

    def _valid_library_names(self) -> set[str]:
        misc_config = MiraMediaConfig().misc
        return {"Default", *(lib.name for lib in misc_config.movie_libraries)}

    def _unknown_library_message(self, target_library: str) -> str:
        return f"Unknown movie library '{target_library}'"

    async def _set_media_library(self, media_id: MovieId, library: str) -> None:
        await self.movie_repository.set_movie_library(
            movie_id=media_id, library=library
        )

    def _move_library_log_label(self) -> str:
        return "move_movie_library"

    async def _get_orphaned_failed_files(self) -> list[MediaFileRowProtocol]:
        files = await self.movie_repository.get_orphaned_failed_movie_files()
        return cast(list[MediaFileRowProtocol], files)

    async def _resolve_media_file_path(
        self, file_row: MediaFileRowProtocol
    ) -> Path | None:
        return await self.resolve_movie_file_path(cast(MovieFile, file_row))

    async def _update_media_file_import_status(
        self, file_id: UUID, status: ImportOutcome, error: str | None
    ) -> None:
        await self.movie_repository.update_movie_file_import_status(
            file_id=file_id, status=status, error=error
        )

    def _reconcile_orphan_log_noun(self) -> str:
        return "movie"

    def _bg_service(self) -> AbstractAsyncContextManager["MovieService"]:
        from miramedia.database import bg_movie_service

        return bg_movie_service()

    async def _iter_torrent_import_files(
        self, svc: BgMediaSessionProtocol, torrent_id: TorrentId
    ) -> list[MediaFileRowProtocol]:
        movie_svc = cast(MovieService, svc)
        files = await movie_svc.torrent_service.torrent_repository.get_movie_files_of_torrent(
            torrent_id=torrent_id
        )
        return cast(list[MediaFileRowProtocol], files)

    async def _stamp_file_import_failed(
        self, svc: BgMediaSessionProtocol, file_id: UUID, error: str
    ) -> None:
        movie_svc = cast(MovieService, svc)
        await movie_svc.movie_repository.update_movie_file_import_status(
            file_id=file_id, status=ImportOutcome.failed_io, error=error
        )

    async def _get_media_of_torrent(
        self, svc: BgMediaSessionProtocol, torrent: Torrent
    ) -> Movie | None:
        movie_svc = cast(MovieService, svc)
        return await movie_svc.torrent_service.get_movie_of_torrent(torrent=torrent)

    async def _import_media_from_torrent(
        self, svc: BgMediaSessionProtocol, torrent: Torrent, media: Movie
    ) -> None:
        movie_svc = cast(MovieService, svc)
        await movie_svc.import_movie_from_torrent(torrent=torrent, movie=media)

    def _import_all_success_log(self, count: int) -> None:
        log.info("Imported %d movie torrent(s)", count)

    def _log_import_all_failure(
        self, torrent_title: str, media: Movie | None, exc: BaseException
    ) -> None:
        del media, exc
        log.exception("Failed to import torrent %s", torrent_title)

    def _invalidate_disk_scan_cache(self) -> None:
        invalidate_disk_scan_cache()

    async def _refresh_update_metadata(
        self,
        media: Movie,
        metadata_provider: AbstractMetadataProvider,
        *,
        fresh_data: Movie | None = None,
    ) -> None:
        await self.update_movie_metadata(
            db_movie=media,
            metadata_provider=metadata_provider,
            fresh_movie_data=fresh_data,
        )

    def _metadata_by_imdb(
        self, provider: AbstractMetadataProvider, imdb_id: str
    ) -> Movie | None:
        return provider.get_movie_metadata_by_imdb(imdb_id)

    def _search_provider(
        self, provider: AbstractMetadataProvider, query: str
    ) -> list[MetaDataProviderSearchResult]:
        return provider.search_movie(query)

    def _fetch_metadata(
        self, provider: AbstractMetadataProvider, external_id: str
    ) -> Movie | None:
        return provider.get_movie_metadata(external_id)

    def _refresh_not_found_message(self, media: Movie) -> str:
        return (
            f"Cannot refresh metadata: {media.metadata_provider} provider is not "
            "enabled and could not find a matching movie on any enabled provider."
        )

    def _torrent_media_type(self) -> MediaType:
        return MediaType.movie

    def _torrent_repository_kwargs(self) -> dict[str, object]:
        return {"movie_repository": self.movie_repository}

    async def add_movie(
        self,
        external_id: str,
        metadata_provider: AbstractMetadataProvider,
        language: str | None = None,
    ) -> Movie:
        """Persist a movie row + poster.

        Does NOT trigger auto-download — callers that want it must run
        ``_try_auto_download_movie_id_impl(saved.id)`` AFTER the surrounding
        ``bg_movie_service`` session has closed. Doing it inline would pin
        the add session through the slow indexer fan-out (cloudflare bypass
        + parallel HTTP across sites), risking
        ``InterfaceError: connection is closed`` from
        ``idle_in_transaction_session_timeout`` plus a
        ``PendingRollbackError`` on the bg-session commit that follows.
        """
        movie_with_metadata = await asyncio.to_thread(
            partial(
                metadata_provider.get_movie_metadata,
                movie_id=external_id,
                language=language,
            )
        )
        if not movie_with_metadata:
            raise NotFoundError

        # Prevent duplicates across providers by checking imdb_id
        if movie_with_metadata.imdb_id:
            existing = await self.movie_repository.movie_exists_by_imdb_id(
                movie_with_metadata.imdb_id
            )
            if existing:
                return existing

        saved_movie = await self.movie_repository.save_movie(movie=movie_with_metadata)
        log.info(
            "Added movie %s (%s) [id=%s, provider=%s]",
            saved_movie.name,
            saved_movie.year,
            saved_movie.id,
            metadata_provider.name,
        )
        from miramedia.database import release_session_before_external_io

        await release_session_before_external_io(self.movie_repository.db)
        try:
            await asyncio.to_thread(
                partial(
                    metadata_provider.download_movie_poster_image, movie=saved_movie
                )
            )
        except Exception:
            log.warning(
                f"Failed to download poster for movie: {saved_movie.name}",
                exc_info=True,
            )

        return saved_movie

    async def delete_movie(
        self,
        movie: Movie,
        delete_files_on_disk: bool = False,
        delete_torrents: bool = False,  # noqa: ARG002 — kept for API back-compat; torrents reaped unconditionally
    ) -> None:
        """
        Delete a movie from the database, optionally deleting files and torrents.

        :param movie: The movie to delete.
        :param delete_files_on_disk: Whether to delete the movie's files from disk.
        :param delete_torrents: Whether to delete associated torrents from the torrent client.
        """
        # Snapshot the torrents linked to this movie BEFORE deletion. The
        # movie_file rows cascade-delete with the movie (FK ON DELETE CASCADE),
        # so afterwards we can no longer discover which torrents it owned —
        # and any we don't reap survive as "Unlinked" ghosts on the torrents
        # page (still downloading, since cancel never ran).
        movie_torrents = await self.movie_repository.get_torrents_by_movie_id(
            movie_id=movie.id
        )
        torrent_ids = [t.id for t in movie_torrents]

        if delete_files_on_disk:
            movie_dir = self.get_movie_root_path(movie=movie)
            if movie_dir.exists() and movie_dir.is_dir():
                try:
                    await asyncio.to_thread(shutil.rmtree, movie_dir)
                    log.info(f"Deleted movie directory: {movie_dir}")
                except OSError:
                    log.exception(f"Deleting movie directory: {movie_dir}")

        # Delete the movie (cascades movie_file rows). Then reap every torrent
        # that is now orphaned. ``cleanup_torrent_if_orphaned`` is idempotent
        # and only removes a torrent that has no remaining media link, so it's
        # safe to run unconditionally — an unlinked torrent row is never
        # something the user wants left behind. ``delete_torrents`` is kept for
        # API back-compat but reaping no longer depends on it.
        await self.movie_repository.delete_movie(movie_id=movie.id)
        for tid in torrent_ids:
            await self.torrent_service.cleanup_torrent_if_orphaned(tid)

    async def delete_movie_file(
        self,
        movie: Movie,
        file_id: UUID,
        delete_from_disk: bool = True,
    ) -> None:
        """Delete a specific movie file record (by id) and optionally its file."""
        row = await self.movie_repository.get_movie_file_by_id(file_id)
        if row is None:
            msg = f"Movie file {file_id} not found."
            raise NotFoundError(msg)
        # Capture the linked torrent before we drop the file row so we can
        # reap a now-orphaned, still-downloading torrent afterwards.
        torrent_id = row.torrent_id
        if delete_from_disk:
            movie_root = self.get_movie_root_path(movie=movie)
            stems = movie_file_stem_candidates(
                movie, row.quality, NameParts.from_row(row)
            )

            # iterdir + unlink are blocking syscalls; running them inline in
            # an ``async def`` freezes the event loop and stalls every other
            # request until the delete finishes.
            await asyncio.to_thread(delete_files_matching_stems, movie_root, stems)
        await self.movie_repository.delete_movie_file(file_id)
        if torrent_id is not None:
            await self.torrent_service.cleanup_torrent_if_orphaned(torrent_id)

        from miramedia.media_state import refresh_media_state

        await refresh_media_state(self.movie_repository.db, movie_id=movie.id)

    async def get_public_movie_files(self, movie: Movie) -> list[PublicMovieFile]:
        """
        Get all public movie files for a given movie.

        :param movie: The movie object.
        :return: A list of public movie files.
        """
        from miramedia.file_status import FileStatus

        movie_files = await self.movie_repository.get_movie_files_by_movie_id(
            movie_id=movie.id
        )
        public_movie_files = [PublicMovieFile.model_validate(x) for x in movie_files]
        movie_root = self.get_movie_root_path(movie=movie)
        video_extensions = {".mkv", ".mp4", ".avi", ".mov"}

        # Batch resolve torrent imported-state. Read-only: no client RPC, no
        # per-file DB writes — the torrents poll on the page already covers
        # live status.
        torrent_ids = [
            mf.torrent_id for mf in public_movie_files if mf.torrent_id is not None
        ]
        imported_by_torrent = (
            await self.torrent_service.bulk_check_torrents_imported(torrent_ids)
            if torrent_ids
            else {}
        )

        def _scan_disk() -> dict[str, str]:
            out: dict[str, str] = {}
            if not movie_root.exists() or not movie_root.is_dir():
                return out
            try:
                entries = list(movie_root.iterdir())
            except OSError:
                return out
            for mf in public_movie_files:
                stems = movie_file_stem_candidates(
                    movie, mf.quality, NameParts.from_row(mf)
                )
                key = str(mf.id)
                for stem in stems:
                    prefix = stem + "."
                    for p in entries:
                        if (
                            p.is_file()
                            and p.name.startswith(prefix)
                            and p.suffix.lower() in video_extensions
                        ):
                            out[key] = p.name
                            break
                    if key in out:
                        break
            return out

        disk_names = await asyncio.to_thread(_scan_disk)

        result = []
        for movie_file in public_movie_files:
            tid = movie_file.torrent_id
            movie_file.imported = (
                movie_file.import_status == ImportOutcome.imported
                or tid is None
                or imported_by_torrent.get(tid, False)
            )
            movie_file.status = (
                MediaStatus.downloaded if movie_file.imported else MediaStatus.wanted
            )

            key = str(movie_file.id)
            file_name = disk_names.get(key)
            file_on_disk = file_name is not None
            if file_on_disk:
                movie_file.file_name = file_name

            if file_on_disk:
                movie_file.file_status = FileStatus.imported
            elif movie_file.import_status == ImportOutcome.imported:
                movie_file.file_status = FileStatus.removed
            elif tid is None:
                movie_file.file_status = FileStatus.orphaned
            elif movie_file.imported:
                movie_file.file_status = FileStatus.removed
            else:
                movie_file.file_status = FileStatus.queued

            result.append(movie_file)
        return result

    @overload
    async def check_if_movie_exists(
        self, *, external_id: str, metadata_provider: str
    ) -> bool:
        """
        Check if a movie exists in the database.

        :param external_id: The provider's ID of the movie.
        :param metadata_provider: The metadata provider.
        :return: True if the movie exists, False otherwise.
        """

    @overload
    async def check_if_movie_exists(self, *, movie_id: MovieId) -> bool:
        """
        Check if a movie exists in the database.

        :param movie_id: The ID of the movie.
        :return: True if the movie exists, False otherwise.
        """

    async def check_if_movie_exists(
        self,
        *,
        external_id=None,
        metadata_provider=None,
        movie_id=None,
    ) -> bool:
        """
        Check if a movie exists in the database.
        """

        if not (external_id is None or metadata_provider is None):
            try:
                await self.movie_repository.get_movie_by_external_id(
                    external_id=external_id, metadata_provider=metadata_provider
                )
            except NotFoundError:
                return False
        elif movie_id is not None:
            try:
                await self.movie_repository.get_movie_by_id(movie_id=movie_id)
            except NotFoundError:
                return False
        else:
            msg = "Use one of the provided overloads for this function!"
            raise ValueError(msg)

        return True

    async def get_all_available_torrents_for_movie(
        self, movie: Movie, search_query_override: str | None = None
    ) -> list[IndexerQueryResult]:
        """
        Get all available torrents for a given movie.

        :param movie: The movie object.
        :param search_query_override: Optional override for the search query.
        :return: A list of indexer query results.
        """
        from miramedia.database import release_session_before_external_io

        # Release the session BEFORE the slow indexer fan-out so the
        # asyncpg connection doesn't sit idle-in-TX through cloudflare
        # bypass + parallel HTTP. Session re-checks out a fresh conn on
        # the next statement (the per-result save_result writes inside
        # search_movie).
        await release_session_before_external_io(self.movie_repository.db)

        if search_query_override:
            return await self.indexer_service.search(
                query=search_query_override, is_tv=False
            )

        torrents = await self.indexer_service.search_movie(movie=movie)

        quality_allowed, codec_allowed = self._get_effective_preferences(movie)
        return evaluate_indexer_query_results(
            is_tv=False,
            query_results=torrents,
            media=movie,
            quality_allowed=quality_allowed,
            codec_allowed=codec_allowed,
        )

    async def get_all_movies(self) -> list[Movie]:
        """
        Get all movies.

        :return: A list of all movies.
        """
        return await self.movie_repository.get_movies()

    async def get_all_movie_ids(self) -> list[MovieId]:
        """Return all movie primary keys without loading full rows."""
        return await self.movie_repository.get_movie_ids()

    async def get_all_public_movies(self) -> list[PublicMovie]:
        """Return list-view PublicMovie objects with computed download/status."""
        movies = await self.movie_repository.get_movies()
        return await self._movies_to_public_with_disk_scan(movies)

    async def get_paginated_public_movies(
        self,
        *,
        offset: int = 0,
        limit: int = 50,
        query: str | None = None,
        sort: str | None = None,
        libraries: list[str] | None = None,
        excluded_libraries: list[str] | None = None,
        genres: list[str] | None = None,
        excluded_genres: list[str] | None = None,
        decades: list[int] | None = None,
        excluded_decades: list[int] | None = None,
        statuses: list[str] | None = None,
        excluded_statuses: list[str] | None = None,
    ) -> tuple[list[PublicMovie], int]:
        """SQL-paginated counterpart of :meth:`get_all_public_movies`."""
        movies, total = await self.movie_repository.get_movies_paginated(
            offset=offset,
            limit=limit,
            query=query,
            sort=sort,
            libraries=libraries,
            excluded_libraries=excluded_libraries,
            genres=genres,
            excluded_genres=excluded_genres,
            decades=decades,
            excluded_decades=excluded_decades,
            statuses=statuses,
            excluded_statuses=excluded_statuses,
        )
        return self._movies_to_public_list(movies), total

    async def count_public_movies(
        self,
        *,
        query: str | None = None,
        libraries: list[str] | None = None,
        excluded_libraries: list[str] | None = None,
        genres: list[str] | None = None,
        excluded_genres: list[str] | None = None,
        decades: list[int] | None = None,
        excluded_decades: list[int] | None = None,
        statuses: list[str] | None = None,
        excluded_statuses: list[str] | None = None,
    ) -> int:
        return await self.movie_repository.count_movies_filtered(
            query=query,
            libraries=libraries,
            excluded_libraries=excluded_libraries,
            genres=genres,
            excluded_genres=excluded_genres,
            decades=decades,
            excluded_decades=excluded_decades,
            statuses=statuses,
            excluded_statuses=excluded_statuses,
        )

    def _movies_to_public_list(self, movies: list[Movie]) -> list[PublicMovie]:
        """Fast list transform using denormalized ``movie.downloaded``."""
        return [
            self._movie_to_public(
                movie=m,
                downloaded=bool(m.downloaded),
                torrent_entries=[],
                progress_rows={},
            )
            for m in movies
        ]

    async def _movies_to_public_with_disk_scan(
        self, movies: list[Movie]
    ) -> list[PublicMovie]:
        if not movies:
            return []

        movie_ids = [m.id for m in movies]
        files_by_movie = await self.movie_repository.get_movie_files_for_movies(
            movie_ids
        )
        # The list grid only needs the "downloaded" badge (from the disk scan
        # below), never per-movie torrents — so skip the get_torrents_for_movies
        # + import-aggregate queries and ship an empty torrents list. The detail
        # endpoint (get_public_movie_by_id) loads torrents with live status.

        # Disk presence: one fan-out covering every movie root, capped so a
        # large library doesn't flood the threadpool + NAS disk at once.
        movie_roots = {m.id: self.get_movie_root_path(movie=m) for m in movies}
        sem = asyncio.Semaphore(_DISK_SCAN_CONCURRENCY)

        async def _scan(m: Movie) -> bool:
            async with sem:
                return await asyncio.to_thread(
                    self._scan_movie_downloaded,
                    movie_roots[m.id],
                    m,
                    files_by_movie.get(m.id, []),
                )

        downloaded_flags = await asyncio.gather(*(_scan(m) for m in movies))
        downloaded_by_id: dict[MovieId, bool] = dict(
            zip((m.id for m in movies), downloaded_flags, strict=True)
        )

        return [
            self._movie_to_public(
                movie=m,
                downloaded=downloaded_by_id.get(m.id, False),
                torrent_entries=[],
                progress_rows={},
            )
            for m in movies
        ]

    @staticmethod
    def _scan_movie_downloaded(
        movie_root: Path, movie: Movie, movie_files: list[MovieFile]
    ) -> bool:
        """Sync helper: does any expected variant file exist on disk?

        Mirrors the inner ``_check`` in ``is_movie_downloaded`` but accepts
        pre-fetched ``movie_files`` so the bulk list path doesn't re-query.
        """
        if not movie_files:
            return False
        # Expected stems determine the answer along with disk contents, so key
        # the cache on both — a new variant request changes the stems and must
        # not read a stale bool.
        stems: list[str] = []
        for movie_file in movie_files:
            stems.extend(
                movie_file_stem_candidates(
                    movie, movie_file.quality, NameParts.from_row(movie_file)
                )
            )
        cache_key = (str(movie_root), frozenset(stems))
        if _DISK_SCAN_CACHE_TTL > 0:
            with _scan_cache_lock:
                hit = _scan_cache.get(cache_key)
            if hit is not None:
                return hit

        video_extensions = {".mkv", ".mp4", ".avi", ".mov"}
        # Scan the movie root ONCE (os.scandir = one syscall batch, cached
        # dirent type) and match stems against the in-memory name list. The old
        # code called files_matching_stem per stem, doing a fresh iterdir+stat
        # pass over the same dir for every variant — N disk scans per movie.
        video_names: list[str] = []
        try:
            with os.scandir(movie_root) as it:
                for entry in it:
                    name = entry.name
                    dot = name.rfind(".")
                    if dot == -1 or name[dot:].lower() not in video_extensions:
                        continue
                    try:
                        if entry.is_file():
                            video_names.append(name)
                    except OSError:
                        continue
        except (FileNotFoundError, OSError):
            return False

        result = False
        for stem in stems:
            prefix = stem + "."
            if any(n.startswith(prefix) for n in video_names):
                result = True
                break
        if _DISK_SCAN_CACHE_TTL > 0:
            with _scan_cache_lock:
                _scan_cache[cache_key] = result
        return result

    def _movie_to_public(
        self,
        movie: Movie,
        *,
        downloaded: bool,
        torrent_entries: list[tuple[Torrent, str]],
        progress_rows: dict,
    ) -> PublicMovie:
        """Pure transform: Movie + pre-fetched context → PublicMovie.

        Used by the bulk list path; ``get_public_movie_by_id`` keeps its
        own enrichment flow because the detail view needs live torrent
        status from the download client.
        """
        # PERF TODO: same as ``ShowService._show_to_public`` — switching
        # ``PublicMovie.model_validate`` to ``model_construct`` would skip
        # per-row validation, but the transform mutates ``status`` /
        # ``downloaded`` / ``torrents`` after the validate call and the
        # nested RichTorrent serialisation still has to go through the
        # normal model_validate path on every torrent. Net win is small
        # and the failure mode (silent missing field) is nasty — defer.
        public_movie = PublicMovie.model_validate(movie)
        public_movie.downloaded = downloaded
        if movie.skipped:
            public_movie.status = MediaStatus.skipped
        elif downloaded:
            public_movie.status = MediaStatus.downloaded
        else:
            public_movie.status = MediaStatus.wanted
        public_movie.skipped = movie.skipped
        public_movie.torrents = [
            RichTorrent(
                id=t.id,
                status=t.status,
                progress=t.progress,
                num_peers=t.num_peers,
                num_seeds=t.num_seeds,
                download_speed=t.download_speed,
                title=t.title,
                quality=t.quality,
                hash=t.hash,
                usenet=t.usenet,
                variant=variant,
                import_progress=TorrentService._build_progress_from_rows(
                    progress_rows.get(t.id, [])
                ),
                media=TorrentMediaContext(
                    media_type="movie",
                    media_id=movie.id,
                    media_name=movie.name,
                    media_year=movie.year,
                    metadata_provider=movie.metadata_provider,
                ),
            )
            for (t, variant) in torrent_entries
        ]
        return public_movie

    async def discover_movies(
        self, query: str | None = None, skip: int = 0
    ) -> list[MetaDataProviderSearchResult]:
        """Search (or, with ``query=None``, fetch trending) movies across the
        enabled providers in precedence order — TMDB → TVDB → Cinemeta —
        returning the first provider's non-empty results.

        Falls through to the next provider when one is unreachable or returns
        nothing. Raises :class:`MetadataProviderUnavailableError` only when
        every enabled provider was unreachable, so the UI can show a retry
        affordance rather than a misleading empty grid.
        """
        from miramedia.exceptions import MetadataProviderUnavailableError
        from miramedia.metadata.dependencies import get_discovery_providers

        providers = get_discovery_providers()
        saw_reachable = False
        for provider in providers:
            try:
                results = await asyncio.to_thread(
                    partial(provider.search_movie, query, skip=skip)
                )
            except MetadataProviderUnavailableError:
                continue
            saw_reachable = True
            if results:
                return await self._annotate_added_status(results)
        if providers and not saw_reachable:
            raise MetadataProviderUnavailableError
        return []

    async def get_public_movie_by_id(self, movie: Movie) -> PublicMovie:
        """
        Get a public movie from a Movie object.

        Torrent enrichment + download check run serially because they share
        the request-scoped AsyncSession.

        :param movie: The movie object.
        :return: A public movie.
        """
        # Serial — both calls share the request-scoped AsyncSession.
        torrents = await self.get_torrents_for_movie(movie=movie)
        downloaded = await self.is_movie_downloaded(movie=movie)
        public_movie = PublicMovie.model_validate(movie)
        public_movie.downloaded = downloaded
        if movie.skipped:
            public_movie.status = MediaStatus.skipped
        elif public_movie.downloaded:
            public_movie.status = MediaStatus.downloaded
        else:
            public_movie.status = MediaStatus.wanted
        public_movie.skipped = movie.skipped
        public_movie.torrents = torrents
        return public_movie

    async def get_movie_by_id(self, movie_id: MovieId) -> Movie:
        """
        Get a movie by its ID.

        :param movie_id: The ID of the movie.
        :return: The movie.
        """
        return await self.movie_repository.get_movie_by_id(movie_id=movie_id)

    async def is_movie_downloaded(self, movie: Movie) -> bool:
        """
        Check if a movie has at least one file present on disk.

        Requires actual filesystem presence — DB import_status alone is not
        enough, since a user may have deleted the file outside the app.
        Disk scan runs off the event loop.
        """
        movie_files = await self.movie_repository.get_movie_files_by_movie_id(
            movie_id=movie.id
        )
        if not movie_files:
            return False
        movie_root = self.get_movie_root_path(movie=movie)

        def _check() -> bool:
            if not movie_root.exists():
                return False
            video_extensions = {".mkv", ".mp4", ".avi", ".mov"}
            for movie_file in movie_files:
                stems = movie_file_stem_candidates(
                    movie, movie_file.quality, NameParts.from_row(movie_file)
                )
                for stem in stems:
                    for f in files_matching_stem(movie_root, stem):
                        if f.suffix.lower() in video_extensions:
                            return True
            return False

        return await asyncio.to_thread(_check)

    async def get_movie_by_external_id(
        self, external_id: str, metadata_provider: str
    ) -> Movie | None:
        """
        Get a movie by its metadata provider ID.
        """
        return await self.movie_repository.get_movie_by_external_id(
            external_id=external_id, metadata_provider=metadata_provider
        )

    async def set_movie_library(self, movie: Movie, library: str) -> None:
        await self.movie_repository.set_movie_library(
            movie_id=movie.id, library=library
        )

    async def get_torrents_for_movie(self, movie: Movie) -> list[RichTorrent]:
        """
        Get torrents for a given movie.

        Per-torrent queries run serially: shared AsyncSession is unsafe for
        concurrent use.

        :param movie: The movie.
        :return: A list of RichTorrent objects.
        """
        raw_torrents = await self.movie_repository.get_torrents_by_movie_id(
            movie_id=movie.id
        )

        async def _enrich(t: Torrent) -> RichTorrent:
            try:
                t = await self.torrent_service.get_torrent_status(t)
            except RuntimeError:
                pass
            # Serial — shared AsyncSession.
            movie_files = await self.torrent_service.get_movie_files_of_torrent(
                torrent=t,
            )
            import_progress = await self.torrent_service.compute_import_progress(t)
            variant = movie_files[0].variant if movie_files else ""
            return RichTorrent(
                id=t.id,
                status=t.status,
                progress=t.progress,
                num_peers=t.num_peers,
                num_seeds=t.num_seeds,
                download_speed=t.download_speed,
                title=t.title,
                quality=t.quality,
                hash=t.hash,
                usenet=t.usenet,
                variant=variant,
                import_progress=import_progress,
                media=TorrentMediaContext(
                    media_type="movie",
                    media_id=movie.id,
                    media_name=movie.name,
                    media_year=movie.year,
                    metadata_provider=movie.metadata_provider,
                ),
            )

        return [await _enrich(t) for t in raw_torrents]

    async def _try_download_first_valid(
        self,
        results: list[IndexerQueryResult],
        movie: Movie,
    ) -> IndexerQueryResult | None:
        """Iterate ranked results, downloading the first one not rejected
        by deny-list/no-video preflight. Returns the picked result or
        ``None`` if every candidate was rejected."""
        from miramedia.exceptions import NoVideoFilesError

        for candidate in results:
            log.info(
                f"Auto-download: downloading {movie.name} ({movie.year}): {candidate.title}"
            )
            try:
                await self.download_torrent(
                    public_indexer_result_id=candidate.id, movie=movie
                )
            except NoVideoFilesError as e:
                log.info("Auto-download: skipping %s — %s", candidate.title, e)
                continue
            return candidate
        return None

    async def download_torrent(
        self,
        public_indexer_result_id: IndexerQueryResultId,
        movie: Movie,
        override_variant: str = "",
    ) -> Torrent:
        """
        Download a torrent for a given indexer result and movie.
        Delegates to TorrentService.download_and_link().
        """
        return await self._download_and_link_torrent(
            public_indexer_result_id, movie.id, override_variant
        )

    def _movie_library_parent(self, movie: Movie) -> Path:
        return self._library_parent(movie)

    def get_movie_root_path(self, movie: Movie, *, write: bool = False) -> Path:
        return self.get_root_media_directory(movie, write=write)

    async def move_movie_library(
        self,
        movie: Movie,
        target_library: str,
        *,
        delete_source: bool = True,
    ) -> dict:
        """Re-home a movie's directory under a different configured library.

        Mirrors ``ShowService.move_show_library``.
        """
        return await self.move_media_library(
            movie, target_library, delete_source=delete_source
        )

    def _pick_movie_videos(
        self, video_files: list[Path], *, movie: Movie | None = None
    ) -> list[Path]:
        """Return every video that's worth importing.

        Drops files below a 100 MB floor (samples, bonus features) unless
        every candidate is tiny, in which case keep them all. Sorts by size
        descending so the largest variant is imported first.
        """
        size_floor = 100 * 1024 * 1024
        sized = [f for f in video_files if f.stat().st_size >= size_floor]
        if not sized:
            sized = list(video_files)
        sized.sort(key=lambda p: p.stat().st_size, reverse=True)
        if movie is not None and len(sized) > 1:
            log.info(
                "Movie %s import has %d candidate videos: %s",
                movie.name,
                len(sized),
                [p.name for p in sized],
            )
        return sized

    async def resolve_movie_file_path(self, movie_file) -> Path | None:  # noqa: ANN001
        """Find the on-disk video file for a MovieFile row, or ``None``."""
        try:
            movie = await self.movie_repository.get_movie_by_id(
                movie_id=movie_file.movie_id
            )
        except NotFoundError:
            return None
        movie_root = self.get_movie_root_path(movie=movie)
        return resolve_movie_file_path_in_memory(
            movie=movie,
            movie_file=movie_file,
            movie_root=movie_root,
        )

    async def batch_resolve_movie_file_paths(
        self,
        rows: list[MovieFile],
        movies: dict[MovieId, Movie],
    ) -> dict[UUID, Path | None]:
        """Resolve on-disk paths for a batch with one directory scan per movie."""
        grouped: dict[MovieId, list[MovieFile]] = defaultdict(list)
        for row in rows:
            grouped[row.movie_id].append(row)

        paths: dict[UUID, Path | None] = {row.id: None for row in rows}
        for movie_id, items in grouped.items():
            movie = movies.get(movie_id)
            if movie is None:
                continue
            movie_root = self.get_movie_root_path(movie=movie)
            video_files = await asyncio.to_thread(
                list_video_files_in_directory, movie_root
            )
            for row in items:
                paths[row.id] = resolve_movie_file_path_in_memory(
                    movie=movie,
                    movie_file=row,
                    movie_root=movie_root,
                    video_files=video_files,
                )
        return paths

    async def import_movie_from_file(
        self,
        *,
        movie: Movie,
        source_file: Path,
        subtitle_files: list[Path] = (),
        torrent_id: TorrentId | None,
        variant: str = "",
        quality: Quality | None = None,
        existing_file_id: UUID | None = None,
    ) -> tuple[ImportOutcome, str | None]:
        """Link ``source_file`` to ``movie`` and persist the MovieFile row.

        Detects the file's naming properties (``codec``/``hdr``/``source`` and,
        when ``quality`` is ``None``, the resolution bucket) from the actual
        file at import time, derives an ``extra`` collision discriminator so the
        rendered stem stays unique, hardlinks to that stem, then persists.

        ``variant``: free text the user entered.
        ``existing_file_id``: the pre-created (link-time) row to finalize. When
        ``None`` a brand-new row is inserted (scan / fresh import path).

        ``subtitle_files``: optional pool; each parsed via
        ``parse_subtitle_filename`` and linked next to the target video.
        """
        try:
            root = self.get_movie_root_path(movie=movie, write=True)
            root.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            return ImportOutcome.failed_io, f"mkdir movie root: {exc}"

        from miramedia.database import release_session_before_external_io

        # Detect file properties at import time (the file exists now).
        # mediainfo scan can take seconds on multi-GB files. Release session
        # so the asyncpg conn doesn't sit idle-in-TX through it.
        await release_session_before_external_io(self.movie_repository.db)
        info = await analyze_async(source_file, fallback_title=source_file.name)
        release = parse_release(source_file.name)
        codec = normalize_codec(info.video_codec or release.video_codec)
        hdr = bool(info.hdr)
        detected_source = normalize_source(release.source)
        chosen_quality = quality if quality is not None else info.quality

        existing_files = await self.movie_repository.get_movie_files_by_movie_id(
            movie_id=movie.id
        )
        movie_rows = [f for f in existing_files if f.id != existing_file_id]

        # Rename-in-place dedup: if this exact content is already in the library
        # under an older name (e.g. a pre-codec-tag "...1080p.mkv" when importing
        # "...1080p [h265].mkv"), reuse that slot — rename it to the new
        # canonical name and update its row — instead of writing a second
        # physical copy. Scan/manual path only (existing_file_id is None); the
        # torrent flow has a pre-created row whose identity must be preserved.
        dup_row = None
        dup_stem = ""
        if existing_file_id is None:
            slot_paths: dict[UUID, Path] = {}
            slot_stems: dict[UUID, str] = {}
            for f in movie_rows:
                for cand_stem in movie_file_stem_candidates(
                    movie, f.quality, NameParts.from_row(f)
                ):
                    matches = [
                        p
                        for p in files_matching_stem(root, cand_stem)
                        if is_video_file(p)
                    ]
                    if matches:
                        slot_paths[f.id] = matches[0]
                        slot_stems[f.id] = cand_stem
                        break
            dup_id = find_renamed_duplicate(source_file, slot_paths)
            if dup_id is not None:
                dup_row = next(f for f in movie_rows if f.id == dup_id)
                dup_stem = slot_stems[dup_id]

        # Compute the collision discriminator over OTHER files for this movie
        # keyed by (quality, codec, variant, extra). Start "" and bump
        # "2","3"… until the rendered tuple is unique. The dup row we are about
        # to overwrite is excluded so it can reclaim its own tuple.
        taken = {
            (f.quality, f.codec, f.variant, f.extra)
            for f in movie_rows
            if dup_row is None or f.id != dup_row.id
        }
        extra = ""
        counter = 2
        while (chosen_quality, codec, variant, extra) in taken:
            extra = str(counter)
            counter += 1

        parts = NameParts(
            codec=codec, hdr=hdr, source=detected_source, variant=variant, extra=extra
        )
        stem = movie_file_stem(movie, chosen_quality, parts)
        target_video = (root / stem).with_suffix(source_file.suffix)

        # The source already lives in the destination dir when scan drops files
        # straight into the canonical movie folder. Hardlinking a new canonical
        # name onto that same inode leaves the original name behind as a visible
        # duplicate, so we rename the source in place instead of copying.
        source_in_place = source_file.parent.resolve() == target_video.parent.resolve()

        if dup_row is not None and not source_in_place:
            # Same content already on disk under ``dup_stem``: rename the whole
            # slot (video + sibling subtitles) to the new canonical name and
            # finalize the existing row. No copy, no duplicate.
            old_stem = dup_stem

            await release_session_before_external_io(self.movie_repository.db)
            await asyncio.to_thread(rename_media_slot, root, old_stem, stem)
            await self.movie_repository.finalize_movie_file_import(
                file_id=dup_row.id,
                quality=chosen_quality,
                codec=codec,
                hdr=hdr,
                source=detected_source,
                variant=variant,
                extra=extra,
                status=ImportOutcome.imported,
            )
            await self._trigger_subtitle_search_for_movie(movie.id)
            from miramedia.media_state import refresh_media_state

            await refresh_media_state(self.movie_repository.db, movie_id=movie.id)
            invalidate_disk_scan_cache()
            log.info("Renamed existing movie slot %r -> %r (dedup)", old_stem, stem)
            return ImportOutcome.imported, None

        # Hardlink is fast on same FS; cross-FS falls back to copy which
        # can take minutes for multi-GB files. Release session in both
        # cases — the cost is one extra checkout on the next write.
        await release_session_before_external_io(self.movie_repository.db)
        try:
            await asyncio.to_thread(
                link_video_into_slot,
                root,
                source_file,
                stem,
                target_video,
                source_in_place=source_in_place,
            )
        except (DiskSpaceError, ImportConflictError) as exc:
            return ImportOutcome.failed_io, str(exc)

        def _link_subs() -> None:
            used: set[Path] = set()
            for sub in subtitle_files:
                sub_info = parse_subtitle_filename(sub.name)
                if sub_info is None or sub_info.language is None:
                    continue
                flag_part = (
                    ".forced" if sub_info.forced else (".sdh" if sub_info.sdh else "")
                )
                target_sub = (
                    root / f"{stem}.{sub_info.language}{flag_part}.{sub_info.container}"
                )
                # Disambiguate same-lang+flag collisions (in-batch only, to keep
                # re-imports idempotent) — otherwise the second sub silently
                # clobbers the first.
                if target_sub in used:
                    n = 2
                    while True:
                        candidate = (
                            root
                            / f"{stem}.{sub_info.language}{flag_part}.{n}.{sub_info.container}"
                        )
                        if candidate not in used:
                            target_sub = candidate
                            break
                        n += 1
                used.add(target_sub)
                try:
                    import_file(target_file=target_sub, source_file=sub)
                except DiskSpaceError:
                    log.exception("Disk space error importing subtitle %s", sub)

        # In-place rename already moved the source's sibling subtitles; the
        # pooled paths are stale, so skip the link pass.
        if subtitle_files and not source_in_place:
            await release_session_before_external_io(self.movie_repository.db)
            await asyncio.to_thread(_link_subs)

        now = datetime.now(UTC)
        if existing_file_id is not None:
            await self.movie_repository.finalize_movie_file_import(
                file_id=existing_file_id,
                quality=chosen_quality,
                codec=codec,
                hdr=hdr,
                source=detected_source,
                variant=variant,
                extra=extra,
                status=ImportOutcome.imported,
            )
        else:
            await self.movie_repository.add_movie_file(
                MovieFile(
                    movie_id=movie.id,
                    quality=chosen_quality,
                    codec=codec,
                    hdr=hdr,
                    source=detected_source,
                    variant=variant,
                    extra=extra,
                    torrent_id=torrent_id,
                    import_status=ImportOutcome.imported,
                    imported_at=now,
                    last_attempt_at=now,
                    attempt_count=1,
                )
            )

        await self._trigger_subtitle_search_for_movie(movie.id)
        from miramedia.media_state import refresh_media_state

        await refresh_media_state(self.movie_repository.db, movie_id=movie.id)
        invalidate_disk_scan_cache()
        return ImportOutcome.imported, None

    async def _trigger_subtitle_search_for_movie(self, movie_id: MovieId) -> None:
        """Best-effort native subtitle search for a just-imported movie.

        No-op unless a native subtitle provider is enabled. Never raises into
        the import flow.
        """
        config = MiraMediaConfig()
        if not (config.subtitles.enabled and config.subtitles.native.enabled):
            return
        try:
            from miramedia.subtitles.repository import SubtitleRepository
            from miramedia.subtitles.service import SubtitleService

            subtitle_service = SubtitleService(
                subtitle_repository=SubtitleRepository(self.movie_repository.db),
                movie_service=self,
            )
            downloaded = await subtitle_service.search_movie_subtitles(movie_id)
            if downloaded:
                log.info(
                    f"Downloaded subtitles {downloaded} for movie {movie_id} after import"
                )
        except Exception:
            log.exception(f"Subtitle search failed for movie {movie_id} after import")

    async def import_movie_from_torrent(self, movie: Movie, torrent: Torrent) -> None:
        """Public import entry point. Runs the import then notifies the imports
        queue so the dashboard reflects the new file statuses (incl. the Done
        tab for a fully-imported torrent). The sync fires on every path via
        ``finally``.
        """
        try:
            await self._run_import_movie_from_torrent(movie=movie, torrent=torrent)
        finally:
            # Full (debounced) rebuild rather than a targeted torrent sync:
            # cleanup_after_import may have deleted the torrent, in which case
            # the imported files surface as a torrent-independent Done entry.
            from miramedia.imports.queue_hooks import schedule_import_queue_rebuild

            schedule_import_queue_rebuild()

    async def _run_import_movie_from_torrent(
        self, movie: Movie, torrent: Torrent
    ) -> None:
        """
        Organizes files from a torrent into the movie directory structure.
        :param torrent: The Torrent object
        :param movie: The Movie object
        """

        video_files, subtitle_files, _all_files = get_files_for_import(
            get_torrent_filepath(torrent=torrent)
        )

        movie_files: list[
            MovieFile
        ] = await self.torrent_service.get_movie_files_of_torrent(torrent=torrent)
        log.info(
            f"Found {len(movie_files)} movie files associated with torrent {torrent.title}"
        )

        if not video_files:
            log.error(
                f"No video files found in source for movie {movie.name}; marking failed_io."
            )
            if self.notification_service:
                await self.notification_service.send_notification_to_all_providers(
                    title="Source Files Missing",
                    message=(
                        f"No video files on disk for movie {movie.name}. "
                        "Re-download or remove the torrent via Imports."
                    ),
                )
            for mf in movie_files:
                await self.movie_repository.update_movie_file_import_status(
                    file_id=mf.id,
                    status=ImportOutcome.failed_io,
                    error="Source files missing on disk.",
                )
            return

        primary_videos = await asyncio.to_thread(
            partial(self._pick_movie_videos, video_files, movie=movie)
        )
        if not primary_videos:
            if self.notification_service:
                await self.notification_service.send_notification_to_all_providers(
                    title="Manual Import Required",
                    message=(
                        f"No suitable video file found for movie {movie.name}. "
                        "Resolve via Imports page."
                    ),
                )
            log.error(
                f"No video file resolved for movie {movie.name}; marking ambiguous."
            )
            for mf in movie_files:
                await self.movie_repository.update_movie_file_import_status(
                    file_id=mf.id,
                    status=ImportOutcome.ambiguous,
                    error="No video files; resolve manually.",
                )
            return

        log.debug(
            f"Importing these {len(primary_videos)} video files and {len(subtitle_files)} subtitle files"
        )

        # Ambiguity guard (restored): the torrent path imports only the largest
        # video. If a second candidate is ~as large (≥80%), the torrent likely
        # holds two distinct videos (double feature / alt cut) and silently
        # dropping the runner-up is wrong — flag ambiguous for manual review
        # instead. A much-smaller runner-up is just an extra that slipped the
        # 100 MB floor, so the single-video happy path is unaffected.
        if len(primary_videos) > 1:
            top_sizes = await asyncio.to_thread(
                lambda: [p.stat().st_size for p in primary_videos[:2]]
            )
            if top_sizes[0] > 0 and top_sizes[1] >= 0.8 * top_sizes[0]:
                log.warning(
                    "Movie %s torrent has %d comparably-sized videos; "
                    "marking ambiguous",
                    movie.name,
                    len(primary_videos),
                )
                if self.notification_service:
                    await self.notification_service.send_notification_to_all_providers(
                        title="Manual Import Required",
                        message=(
                            f"Multiple comparable video files for movie "
                            f"{movie.name}. Resolve via Imports page."
                        ),
                    )
                for mf in movie_files:
                    await self.movie_repository.update_movie_file_import_status(
                        file_id=mf.id,
                        status=ImportOutcome.ambiguous,
                        error="Multiple comparable video files; resolve manually.",
                    )
                return

        primary = primary_videos[0]
        outcomes: list[ImportOutcome] = []
        for movie_file in movie_files:
            outcome, error = await self.import_movie_from_file(
                movie=movie,
                source_file=primary,
                subtitle_files=subtitle_files,
                torrent_id=torrent.id,
                variant=movie_file.variant,
                # quality omitted on purpose: detect it from the actual file
                # (mediainfo) so torrent imports name files identically to scan
                # imports. The indexer's claimed quality can be wrong.
                existing_file_id=movie_file.id,
            )
            if outcome != ImportOutcome.imported:
                await self.movie_repository.update_movie_file_import_status(
                    file_id=movie_file.id,
                    status=outcome,
                    error=error,
                )
            outcomes.append(outcome)

        all_imported = bool(outcomes) and all(
            o == ImportOutcome.imported for o in outcomes
        )

        # Snapshot the outcome into the durable history log BEFORE any
        # cleanup_after_import removes the live torrent row.
        await self.torrent_service.record_import_history(torrent)

        if all_imported:
            if self.notification_service:
                await self.notification_service.send_notification_to_all_providers(
                    title="Movie Downloaded",
                    message=f"Movie {movie.name} has been successfully downloaded and imported.",
                )

            # Clean up the torrent from the download client and disk
            if MiraMediaConfig().misc.cleanup_after_import:
                try:
                    await self.torrent_service.cancel_download(
                        torrent, delete_files=True
                    )
                    await self.torrent_service.delete_torrent(torrent_id=torrent.id)
                    log.info(
                        f"Cleaned up torrent {torrent.title} after successful import"
                    )
                except Exception:
                    log.exception(
                        f"Failed to clean up torrent {torrent.title} after import"
                    )
        else:
            log.error(
                f"Failed to import files for torrent {torrent.title}. Check logs for details."
            )

            if self.notification_service:
                await self.notification_service.send_notification_to_all_providers(
                    title="Import Failed",
                    message=f"Failed to import files for movie {movie.name}. Please check logs.",
                )

        log.info(f"Finished importing files for torrent {torrent.title}")

    async def import_movie_from_directory(
        self, movie: Movie, source_directory: Path
    ) -> bool:
        """Dot-rename the source directory up-front so it stops shadowing the
        canonical movie dir, then hardlink each useful video into the canonical
        movie dir. Each scanned video becomes its own MovieFile row;
        ``import_movie_from_file`` detects its naming properties and assigns an
        ``extra`` collision discriminator so same-quality stems stay unique.

        Skip the dot-rename when ``source_directory`` *is* the canonical movie
        dir — renaming it would orphan the library. Hit by scan when files are
        dropped into an already-tracked movie folder.
        """
        canonical_dir = self.get_movie_root_path(movie=movie, write=False)
        is_canonical = source_directory.absolute() == canonical_dir.absolute()  # noqa: ASYNC240 — cheap path resolution, intentional
        if not is_canonical and not source_directory.name.startswith("."):
            dot_path = source_directory.parent / ("." + source_directory.name)
            try:
                await asyncio.to_thread(source_directory.rename, dot_path)
            except Exception:
                log.exception(
                    "Failed to mark source %s as imported (rename to %s)",
                    source_directory,
                    dot_path,
                )
                raise RenameError from None
            source_directory = dot_path

        video_files, subtitle_files, _all_files = await asyncio.to_thread(
            partial(get_files_for_import, directory=source_directory)
        )

        primary_videos = await asyncio.to_thread(
            partial(self._pick_movie_videos, video_files, movie=movie)
        )
        if not primary_videos:
            log.warning("No usable videos in %s for %s", source_directory, movie.name)
            return False

        existing_files = await self.movie_repository.get_movie_files_by_movie_id(
            movie_id=movie.id
        )

        # Build inode set of files already linked to this movie. If a source
        # video shares an inode with one of these, it's already imported under
        # a different stem — skip it instead of producing a duplicate hardlink
        # under a fresh ``extra`` discriminator. Hit by scan re-running over a
        # fully-imported canonical dir.
        existing_inodes: set[int] = set()
        for mf in existing_files:
            try:
                path = await self.resolve_movie_file_path(mf)
            except Exception:
                path = None
            if path is None:
                continue
            try:
                existing_inodes.add(path.stat().st_ino)
            except OSError:
                continue

        any_imported = False
        skipped_already_imported = 0
        for video in primary_videos:
            try:
                if video.stat().st_ino in existing_inodes:
                    log.debug(
                        "Skipping %s: inode already imported for movie %s",
                        video,
                        movie.name,
                    )
                    skipped_already_imported += 1
                    continue
            except OSError:
                pass

            # Naming components (codec/hdr/source) + quality are detected inside
            # import_movie_from_file; the collision discriminator (``extra``) is
            # assigned there too. Scan supplies no user variant.
            outcome, err = await self.import_movie_from_file(
                movie=movie,
                source_file=video,
                subtitle_files=subtitle_files,
                torrent_id=None,
                variant="",
            )
            if outcome == ImportOutcome.imported:
                any_imported = True
            else:
                log.warning("Failed importing %s: %s", video.name, err)

        # Treat "every video already linked" as success — the dir is fully
        # imported, nothing new to do. Caller (scan task) records the item
        # as imported so the next scan's _still_resolved snapshot drops it.
        return any_imported or (
            skipped_already_imported > 0
            and skipped_already_imported == len(primary_videos)
        )

    async def update_movie_metadata(
        self,
        db_movie: Movie,
        metadata_provider: AbstractMetadataProvider,
        fresh_movie_data: Movie | None = None,
    ) -> Movie | None:
        """
        Updates the metadata of a movie.

        :param metadata_provider: The metadata provider object to fetch fresh data from.
        :param db_movie: The Movie to update
        :param fresh_movie_data: Pre-fetched metadata. If None, fetches from provider using db_movie.external_id.
        :return: The updated Movie object, or None if the movie is not found or an error occurs.
        """
        from miramedia.database import release_session_before_external_io

        log.debug(f"Found movie: {db_movie.name} for metadata update.")

        if fresh_movie_data is None:
            # Release session before the slow metadata HTTP fetch so the
            # asyncpg connection doesn't sit idle-in-TX while the provider
            # responds. Session re-checks out on the next statement (the
            # update_movie_attributes write below).
            await release_session_before_external_io(self.movie_repository.db)
            # Use stored original_language preference for metadata fetching
            fresh_movie_data = await asyncio.to_thread(
                partial(
                    metadata_provider.get_movie_metadata,
                    movie_id=db_movie.external_id,
                    language=db_movie.original_language,
                )
            )
        if not fresh_movie_data:
            log.warning(
                f"Could not fetch fresh metadata for movie: {db_movie.name} ({db_movie.year})"
            )
            return None
        log.debug(f"Fetched fresh metadata for movie: {fresh_movie_data.name}")

        await self.movie_repository.update_movie_attributes(
            movie_id=db_movie.id,
            name=fresh_movie_data.name,
            overview=fresh_movie_data.overview,
            year=fresh_movie_data.year,
            release_date=fresh_movie_data.release_date,
            imdb_id=fresh_movie_data.imdb_id,
            vote_average=fresh_movie_data.vote_average,
            content_rating=fresh_movie_data.content_rating,
            runtime=fresh_movie_data.runtime,
            genres=fresh_movie_data.genres,
            cast=fresh_movie_data.cast,
        )

        updated_movie = await self.movie_repository.get_movie_by_id(
            movie_id=db_movie.id
        )

        from miramedia.metadata.utils import poster_exists

        if not poster_exists(metadata_provider.storage_path, updated_movie.id):
            await release_session_before_external_io(self.movie_repository.db)
            try:
                await asyncio.to_thread(
                    partial(
                        metadata_provider.download_movie_poster_image,
                        movie=updated_movie,
                    )
                )
            except Exception:
                log.warning(
                    f"Failed to download poster for movie: {updated_movie.name}",
                    exc_info=True,
                )
        log.info(
            f"Updated metadata for movie: {updated_movie.name} ({updated_movie.year})"
        )
        return updated_movie

    async def update_all_metadata(self) -> None:
        """Thin wrapper around :func:`_update_all_movies_metadata_impl`.

        Per-iteration session lifetime lives in the module-level helper so
        the same code path is safe whether called from an existing service
        instance or from a scheduler task that opens its own fresh
        ``bg_movie_service``.
        """
        await _update_all_movies_metadata_impl()

    async def set_movie_preferred_quality(
        self, movie: Movie, preferred_quality: list[str] | None
    ) -> Movie:
        return await self._set_preferred_quality(movie, preferred_quality)

    async def set_movie_preferred_codec(
        self, movie: Movie, preferred_codec: list[str] | None
    ) -> Movie:
        return await self._set_preferred_codec(movie, preferred_codec)

    async def set_movie_subtitle_languages(
        self, movie: Movie, subtitle_languages: list[str] | None
    ) -> Movie:
        return await self._set_subtitle_languages(movie, subtitle_languages)

    async def set_movie_continuous_download(
        self, movie: Movie, continuous_download: bool | None
    ) -> Movie:
        return await self._set_continuous_download(movie, continuous_download)

    async def auto_download_missing_movies(self) -> None:
        """Thin wrapper around :func:`_auto_download_missing_movies_impl`.

        Per-iteration session lifetime lives in the module-level helper so
        the same code path is safe whether called from an existing service
        instance or from a scheduler task that opens its own fresh
        ``bg_movie_service``.
        """
        await _auto_download_missing_movies_impl()


async def _try_auto_download_movie_id_impl(movie_id: MovieId) -> None:
    """Run one continuous-download iteration for a single movie.

    Opens its own short-lived ``bg_movie_service`` and runs the same
    snapshot-and-act flow as the periodic loop. Called by the periodic
    sweep AND by ``add_movie_task`` so the long indexer gather never
    runs inside the add-movie task's outer session (which would otherwise
    sit ``idle in transaction`` from ``save_movie`` and get reaped by
    ``idle_in_transaction_session_timeout``).

    Swallows iteration errors so callers (loops) don't abort mid-sweep.
    """
    from datetime import UTC, datetime, timedelta

    from miramedia.database import bg_movie_service
    from miramedia.scheduler import _import_sweep_lock

    lock = _import_sweep_lock(f"auto_dl_movie:{movie_id}")
    if lock.locked():
        log.debug(
            "Auto-download: movie id=%s already in progress; skipping overlapping run",
            movie_id,
        )
        return
    async with lock:
        # Local calendar date (matches the original date.today() semantics).
        # release_date is a tz-naive calendar date, so compare against local — not
        # UTC — to avoid skipping a movie already released locally.
        today = datetime.now(UTC).astimezone().date()
        try:
            async with bg_movie_service() as svc:
                # Re-fetch on the fresh session — state may have changed
                # since the snapshot, and the row must be attached to THIS
                # session for any downstream writeback to succeed.
                fresh = await svc.movie_repository.get_movie_by_id(movie_id=movie_id)
                if fresh is None:
                    return
                if fresh.skipped:
                    return
                # Defense in depth — explicit False wins even if some
                # caller bypassed the outer filter.
                if fresh.continuous_download is False:
                    log.debug(
                        f"Auto-download: skipping {fresh.name} (continuous_download disabled)"
                    )
                    return
                # Skip unreleased movies — chasing a torrent before the
                # release date is wasted bandwidth. Manual search still
                # works for early-leaked rips.
                if fresh.release_date is not None and fresh.release_date > today:
                    log.debug(
                        f"Auto-download: skipping {fresh.name} (release date {fresh.release_date} in the future)"
                    )
                    return

                # Honor per-movie backoff set after a sweep where every
                # candidate was deny-listed (see below). Skips the indexer
                # fan-out + CF bypass entirely until the window passes.
                now_utc = datetime.now(UTC)
                if (
                    fresh.auto_download_backoff_until is not None
                    and fresh.auto_download_backoff_until > now_utc
                ):
                    log.debug(
                        "Auto-download: skipping %s (backoff until %s)",
                        fresh.name,
                        fresh.auto_download_backoff_until.isoformat(),
                    )
                    return

                if await svc.is_movie_downloaded(movie=fresh):
                    return

                # Check for active (non-imported) downloads
                movie_files = await svc.movie_repository.get_movie_files_by_movie_id(
                    movie_id=fresh.id
                )
                active_torrents = [
                    mf for mf in movie_files if mf.torrent_id is not None
                ]
                if active_torrents:
                    log.debug(
                        f"Auto-download: movie {fresh.name} has active downloads, skipping"
                    )
                    return

                raw_results = await svc.get_all_available_torrents_for_movie(
                    movie=fresh
                )
                results = await svc.torrent_service.filter_deny_listed(raw_results)
                if not results:
                    if raw_results:
                        # Every candidate was deny-listed — back off so the
                        # next sweep doesn't re-burn the indexer fan-out
                        # for nothing.
                        backoff_hours = max(
                            MiraMediaConfig().misc.auto_download_interval_hours * 2,
                            12,
                        )
                        until = now_utc + timedelta(hours=backoff_hours)
                        await svc.movie_repository.set_auto_download_backoff(
                            fresh.id, until
                        )
                        log.info(
                            "Auto-download: %s — all %d candidate(s) deny-listed, backing off until %s",
                            fresh.name,
                            len(raw_results),
                            until.isoformat(),
                        )
                    else:
                        log.debug(f"Auto-download: no results for {fresh.name}")
                    return

                # Fresh candidate appeared — clear any prior backoff so
                # subsequent sweeps can act immediately.
                if fresh.auto_download_backoff_until is not None:
                    await svc.movie_repository.set_auto_download_backoff(fresh.id, None)

                picked = await svc._try_download_first_valid(
                    results=results, movie=fresh
                )
                if picked is None:
                    log.info(
                        "Auto-download: no usable candidates for %s after deny-list/no-video filtering",
                        fresh.name,
                    )
                    return

                if svc.notification_service:
                    await svc.notification_service.send_notification_to_all_providers(
                        title="Auto-download started",
                        message=f"Downloading {fresh.name} ({fresh.year}): {picked.title}",
                    )
        except Exception:
            log.exception("Auto-download: error processing movie id=%s", movie_id)
            # No shared session to roll back — the per-iteration session
            # is already torn down by the ``async with`` exit.


async def _auto_download_missing_movies_impl() -> None:
    """Per-iteration ``bg_movie_service`` worker for continuous-download movies.

    Session lifetime: the outer snapshot session is closed BEFORE the loop.
    Every per-movie iteration opens its own short-lived ``bg_movie_service``
    so a slow indexer fan-out (cloudflare bypass spinup, 60s timeout, etc.)
    can never pin the shared session past
    ``idle_in_transaction_session_timeout`` and poison subsequent iterations
    with ``InterfaceError: connection is closed``.

    Mirrors the round-3 ``import_all_torrents`` fix in this module.
    """
    from miramedia.database import bg_movie_service

    global_default = MiraMediaConfig().misc.continuous_download

    # Phase 1: cheap snapshot of candidate IDs (single short bg session).
    async with bg_movie_service() as svc:
        rows = await svc.movie_repository.get_movie_auto_download_candidate_flags()
    candidate_ids = [
        movie_id
        for movie_id, skipped, continuous_download in rows
        # Skipped movies are excluded regardless of continuous_download.
        if not skipped
        and (
            continuous_download is True
            or (continuous_download is None and global_default)
        )
    ]
    log.info(
        f"Auto-download: checking {len(candidate_ids)} movies with continuous_download enabled"
    )

    # Phase 2: per-item processing with a FRESH bg session per iteration.
    for movie_id in candidate_ids:
        await _try_auto_download_movie_id_impl(movie_id)


def _metadata_failure_backoff_until() -> datetime:
    from datetime import timedelta

    hours = max(1, int(MiraMediaConfig().metadata.failure_backoff_hours))
    return datetime.now(UTC) + timedelta(hours=hours)


async def _mark_movie_metadata_failure(movie_id: MovieId, reason: str) -> None:
    """Stamp a failed metadata attempt so the scheduler backs off."""
    from miramedia.database import bg_movie_service

    backoff_until = _metadata_failure_backoff_until()
    try:
        async with bg_movie_service() as svc:
            await svc.movie_repository.mark_metadata_failure(movie_id, backoff_until)
        log.info(
            "Metadata refresh for movie id=%s backed off until %s (%s)",
            movie_id,
            backoff_until.isoformat(),
            reason,
        )
    except Exception:
        log.exception("Failed to mark movie metadata failure for id=%s", movie_id)


async def _try_update_movie_metadata_id_impl(movie_id: MovieId) -> None:
    """Refresh metadata for a single movie in a fresh bg session.

    Opens its own ``bg_movie_service``, so a slow provider HTTP call cannot
    pin a shared session past ``idle_in_transaction_session_timeout``.
    Swallows errors so a single bad movie doesn't abort the bulk loop.
    """
    from miramedia.database import bg_movie_service, release_session_before_external_io
    from miramedia.metadata.dependencies import resolve_metadata_provider

    try:
        async with bg_movie_service() as svc:
            movie = await svc.movie_repository.get_movie_by_id(movie_id=movie_id)
            if movie is None:
                return
            metadata_provider = resolve_metadata_provider(movie.metadata_provider)
            if not metadata_provider:
                log.warning(
                    f"No available metadata provider for movie {movie.name}, skipping update."
                )
                await svc.movie_repository.mark_metadata_failure(
                    movie.id, _metadata_failure_backoff_until()
                )
                return

            # Native-provider fallback for non-IMDb IDs — pre-fetch using imdb_id.
            fresh_data = None
            if (
                metadata_provider.name != movie.metadata_provider
                and metadata_provider.name == "native"
                and not movie.external_id.startswith("tt")
            ):
                if not movie.imdb_id:
                    log.warning(
                        f"Cannot update {movie.name}: native provider requires IMDb ID "
                        f"but movie only has {movie.metadata_provider} ID {movie.external_id}"
                    )
                    await svc.movie_repository.mark_metadata_failure(
                        movie.id, _metadata_failure_backoff_until()
                    )
                    return
                log.info(
                    f"Using IMDb ID {movie.imdb_id} instead of {movie.metadata_provider} ID "
                    f"{movie.external_id} for native provider lookup of {movie.name}"
                )
                await release_session_before_external_io(svc.movie_repository.db)
                fresh_data = await asyncio.to_thread(
                    partial(
                        metadata_provider.get_movie_metadata,
                        movie_id=movie.imdb_id,
                        language=movie.original_language,
                    )
                )

            updated_movie = await svc.update_movie_metadata(
                db_movie=movie,
                metadata_provider=metadata_provider,
                fresh_movie_data=fresh_data,
            )
            if updated_movie:
                await svc.movie_repository.stamp_metadata_check(movie.id)
            else:
                await svc.movie_repository.mark_metadata_failure(
                    movie.id, _metadata_failure_backoff_until()
                )
    except Exception as exc:
        from miramedia.metadata.utils import is_provider_unreachable

        if is_provider_unreachable(exc):
            log.warning(
                "Metadata provider unreachable for movie id=%s: %s", movie_id, exc
            )
            await _mark_movie_metadata_failure(movie_id, "provider unreachable")
        else:
            log.exception("Failed to update metadata for movie id=%s", movie_id)
            await _mark_movie_metadata_failure(movie_id, "unexpected failure")


async def _update_all_movies_metadata_impl() -> None:
    """Per-iteration ``bg_movie_service`` worker for metadata refresh.

    Phase 1: snapshot candidate movie IDs in one short session, then close.
    Phase 2: each movie iteration opens its own fresh session via
    :func:`_try_update_movie_metadata_id_impl`, so a slow provider HTTP
    timeout cannot pin a shared session past
    ``idle_in_transaction_session_timeout``.
    """
    from datetime import UTC, datetime, timedelta

    from miramedia.database import bg_movie_service

    check_interval = timedelta(hours=MiraMediaConfig().metadata.check_interval_hours)
    now = datetime.now(UTC)

    cutoff = now - check_interval
    batch_size = 200
    total_checked = 0
    seen_ids: set[MovieId] = set()
    log.info("Updating metadata for movies due since %s", cutoff.isoformat())
    while True:
        async with bg_movie_service() as svc:
            movie_ids = await svc.movie_repository.get_movie_ids_due_for_metadata(
                older_than=cutoff, limit=batch_size
            )
        movie_ids = [movie_id for movie_id in movie_ids if movie_id not in seen_ids]
        if not movie_ids:
            break
        total_checked += len(movie_ids)
        for movie_id in movie_ids:
            seen_ids.add(movie_id)
            await _try_update_movie_metadata_id_impl(movie_id)
    if total_checked:
        log.info("Metadata refresh checked %d movies", total_checked)
