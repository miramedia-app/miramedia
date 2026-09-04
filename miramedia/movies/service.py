import asyncio
import shutil
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import cast, overload
from uuid import UUID

from miramedia.config import LibraryItem, MiraMediaConfig
from miramedia.disk_scan import invalidate_disk_scan_cache, scan_rows_for_files
from miramedia.exceptions import (
    NotFoundError,
    RenameError,
)
from miramedia.file_status import ImportOutcome
from miramedia.imports.files import (
    DiskSpaceError,
    ImportConflictError,
    delete_files_matching_stems,
    files_matching_stem,
    find_renamed_duplicate,
    get_files_for_import,
    link_subtitles,
    link_video_into_slot,
    rename_media_slot,
)
from miramedia.indexers.schemas import (
    IndexerQueryResult,
    IndexerQueryResultId,
)
from miramedia.indexers.service import IndexerService
from miramedia.indexers.utils import evaluate_indexer_query_results
from miramedia.media_paths import (
    PathCanonicalResolutionError,
    paths_same_canonical,
)
from miramedia.media_service import (
    BgMediaSessionProtocol,
    MediaFileRowProtocol,
    MediaService,
)
from miramedia.media_status import MediaStatus
from miramedia.metadata.backends.generic import AbstractMetadataProvider
from miramedia.metadata.schemas import MetaDataProviderSearchResult
from miramedia.movies import log
from miramedia.movies.repository import (
    MovieMatchCandidate,
    MovieRepository,
)
from miramedia.movies.schemas import (
    Movie,
    MovieFile,
    MovieId,
    PublicMovie,
    PublicMovieFile,
)
from miramedia.naming import (
    default_movie_folder_name,
    movie_file_stem,
    movie_file_stem_candidates,
    movie_folder_name,
    old_movie_folder_name,
)
from miramedia.notifications.service import NotificationService
from miramedia.torrents.integrity import (
    resolve_movie_file_path_in_memory,
)
from miramedia.torrents.mediainfo import analyze_async
from miramedia.torrents.parsing import (
    SubtitleInfo,
    is_video_file,
    normalize_codec,
    normalize_source,
    parse_release,
    parse_subtitle_filename,
)
from miramedia.torrents.paths import get_torrent_filepath
from miramedia.torrents.quality_naming import NameParts
from miramedia.torrents.schemas import (
    MediaType,
    Quality,
    RichTorrent,
    Torrent,
    TorrentId,
    TorrentMediaContext,
)
from miramedia.torrents.service import TorrentService


def _as_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


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
            "Library %s not found in config, using default library",
            media.library,
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
        from miramedia.background_services import bg_movie_service

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
                "Failed to download poster for movie: %s",
                saved_movie.name,
                exc_info=True,
            )

        return saved_movie

    async def delete_movie(
        self,
        movie: Movie,
        delete_files_on_disk: bool = False,
    ) -> None:
        """
        Delete a movie from the database, optionally deleting files from disk.

        :param movie: The movie to delete.
        :param delete_files_on_disk: Whether to delete the movie's files from disk.
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
                    log.info("Deleted movie directory: %s", movie_dir)
                except OSError:
                    log.exception("Deleting movie directory: %s", movie_dir)

        # Delete the movie (cascades movie_file rows). Then reap every torrent
        # that is now orphaned. ``cleanup_torrent_if_orphaned`` is idempotent
        # and only removes a torrent that has no remaining media link, so it's
        # safe to run unconditionally — an unlinked torrent row is never
        # something the user wants left behind.
        await self.movie_repository.delete_movie(movie_id=movie.id)
        for tid in torrent_ids:
            await self.torrent_service.cleanup_torrent_if_orphaned(tid)

    async def delete_movie_file(
        self,
        movie: Movie,
        file_id: UUID,
        delete_from_disk: bool = True,
        block_source: bool = False,
    ) -> None:
        """Delete a specific movie file record (by id) and optionally its file.

        Idempotent: a missing row is a no-op so a repeat DELETE after a
        successful delete (or a stale UI retry) is not a 404.
        """
        row = await self.movie_repository.get_movie_file_by_id(file_id)
        if row is None:
            return
        # Capture the linked torrent before we drop the file row so we can
        # reap a now-orphaned, still-downloading torrent afterwards.
        torrent_id = row.torrent_id
        if block_source and row.source_info_hash:
            await self.torrent_service.torrent_repository.add_blocked_hash(
                row.source_info_hash, reason="user_blocked"
            )
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

        video_extensions = frozenset({".mkv", ".mp4", ".avi", ".mov"})

        disk_names = await asyncio.to_thread(
            scan_rows_for_files,
            movie_root,
            public_movie_files,
            key=lambda mf: str(mf.id),
            stems=lambda mf: movie_file_stem_candidates(
                movie, mf.quality, NameParts.from_row(mf)
            ),
            video_exts=video_extensions,
        )

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
            torrents = await self.indexer_service.search(
                query=search_query_override, is_tv=False
            )
            quality_allowed, codec_allowed = self._get_effective_preferences(movie)
            return evaluate_indexer_query_results(
                is_tv=False,
                query_results=torrents,
                media=movie,
                quality_allowed=quality_allowed,
                codec_allowed=codec_allowed,
                query_override=search_query_override,
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

    async def get_movie_match_candidates(self) -> list[MovieMatchCandidate]:
        """Return slim (id, name, year) rows for fuzzy title matching."""
        return await self.movie_repository.get_movie_match_candidates()

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
        """Paginated list view using SQL LIMIT/OFFSET and denormalized counters."""
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

    async def get_on_disk_movie_file_qualities(self, movie: Movie) -> list[Quality]:
        """Return qualities for movie files with a matching video on disk."""
        movie_files = await self.movie_repository.get_movie_files_by_movie_id(
            movie_id=movie.id
        )
        if not movie_files:
            return []
        movie_root = self.get_movie_root_path(movie=movie)

        def _scan() -> list[Quality]:
            if not movie_root.exists():
                return []
            video_extensions = {".mkv", ".mp4", ".avi", ".mov"}
            qualities: list[Quality] = []
            for movie_file in movie_files:
                stems = movie_file_stem_candidates(
                    movie, movie_file.quality, NameParts.from_row(movie_file)
                )
                for stem in stems:
                    for path in files_matching_stem(movie_root, stem):
                        if path.suffix.lower() in video_extensions:
                            qualities.append(movie_file.quality)
                            break
                    else:
                        continue
                    break
            return qualities

        return await asyncio.to_thread(_scan)

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

        Enrichment is batched via ``TorrentService._rich_torrents_for_ids``.

        :param movie: The movie.
        :return: A list of RichTorrent objects.
        """
        raw_torrents = await self.movie_repository.get_torrents_by_movie_id(
            movie_id=movie.id
        )
        return await self.torrent_service._rich_torrents_for_ids(
            raw_torrents, live_status=True
        )

    async def _try_download_first_valid(
        self,
        results: list[IndexerQueryResult],
        movie: Movie,
    ) -> IndexerQueryResult | None:
        """Iterate ranked results, downloading the first one not rejected
        by deny-list/no-video preflight. Returns the picked result or
        ``None`` if every candidate was rejected."""
        from miramedia.exceptions import NoVideoFilesError, UnsafeTorrentTitleError

        for candidate in results:
            log.info(
                "Auto-download: downloading %s (%s): %s",
                movie.name,
                movie.year,
                candidate.title,
            )
            try:
                await self.download_torrent(
                    public_indexer_result_id=candidate.id, movie=movie
                )
            except (NoVideoFilesError, UnsafeTorrentTitleError) as e:
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
        from miramedia.database import release_session_before_external_io
        from miramedia.torrents.integrity import (
            IntegrityPathLayout,
            batch_resolve_movie_paths_async,
        )

        await release_session_before_external_io(self.movie_repository.db)
        layout = IntegrityPathLayout.from_config()
        return await batch_resolve_movie_paths_async(rows, movies, layout)

    async def import_movie_from_file(
        self,
        *,
        movie: Movie,
        source_file: Path,
        subtitle_files: list[Path] = (),
        torrent_id: TorrentId | None,
        source_info_hash: str | None = None,
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
            await self._trigger_bazarr_notify_for_movie(dup_row.id, movie.id)
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

        if subtitle_files and not source_in_place:

            def _movie_subtitle_match(sub: Path) -> SubtitleInfo | None:
                sub_info = parse_subtitle_filename(sub.name)
                if sub_info is None or sub_info.language is None:
                    return None
                return sub_info

            def _movie_subtitle_target(sub_info: SubtitleInfo, n: int) -> Path:
                flag_part = (
                    ".forced" if sub_info.forced else (".sdh" if sub_info.sdh else "")
                )
                ordinal = "" if n == 1 else f".{n}"
                return (
                    root
                    / f"{stem}.{sub_info.language}{flag_part}{ordinal}.{sub_info.container}"
                )

            await release_session_before_external_io(self.movie_repository.db)
            await asyncio.to_thread(
                partial(
                    link_subtitles,
                    subtitle_files,
                    match=_movie_subtitle_match,
                    target_for=_movie_subtitle_target,
                )
            )

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
            imported_file_id = existing_file_id
        else:
            added = await self.movie_repository.add_movie_file(
                MovieFile(
                    movie_id=movie.id,
                    quality=chosen_quality,
                    codec=codec,
                    hdr=hdr,
                    source=detected_source,
                    variant=variant,
                    extra=extra,
                    torrent_id=torrent_id,
                    source_info_hash=source_info_hash,
                    import_status=ImportOutcome.imported,
                    imported_at=now,
                    last_attempt_at=now,
                    attempt_count=1,
                )
            )
            imported_file_id = added.id

        await self._trigger_subtitle_search_for_movie(movie.id)
        await self._trigger_bazarr_notify_for_movie(imported_file_id, movie.id)
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
                    "Downloaded subtitles %s for movie %s after import",
                    downloaded,
                    movie_id,
                )
        except Exception:
            log.exception("Subtitle search failed for movie %s after import", movie_id)

    async def _trigger_bazarr_notify_for_movie(
        self, movie_file_id: UUID, movie_id: MovieId
    ) -> None:
        """Best-effort Bazarr webhook after a just-imported movie file.

        No-op unless Bazarr is enabled. Never raises into the import flow.
        """
        try:
            from miramedia.subtitles.repository import SubtitleRepository
            from miramedia.subtitles.service import SubtitleService

            subtitle_service = SubtitleService(
                subtitle_repository=SubtitleRepository(self.movie_repository.db),
                movie_service=self,
            )
            await subtitle_service.notify_bazarr_movie_imported(
                self.movie_repository.db, movie_file_id, movie_id
            )
        except Exception:
            log.exception(
                "Bazarr notify failed for movie file %s after import",
                movie_file_id,
            )

    async def import_movie_from_torrent(self, movie: Movie, torrent: Torrent) -> None:
        """Public import entry point. Runs the import then notifies the imports
        queue so the dashboard reflects the new file statuses (incl. the Done
        tab for a fully-imported torrent). The sync fires on every path via
        ``finally``.
        """
        try:
            await self._run_import_movie_from_torrent(movie=movie, torrent=torrent)
        finally:
            # Targeted torrent + history sync: cleanup_after_import may delete the
            # live torrent while the durable Done row is upserted separately.
            from miramedia.imports.queue_hooks import (
                schedule_import_completion_queue_sync,
            )

            schedule_import_completion_queue_sync(torrent.id)

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
            "Found %s movie files associated with torrent %s",
            len(movie_files),
            torrent.title,
        )

        if not video_files:
            log.error(
                "No video files found in source for movie %s; marking failed_io.",
                movie.name,
            )
            if self.notification_service:
                await self.notification_service.send_notification_to_all_providers(
                    title="Source Files Missing",
                    message=(
                        f"No video files on disk for movie {movie.name}. "
                        "Re-download or remove the torrent via Imports."
                    ),
                )
            await self.movie_repository.update_movie_file_import_status_bulk(
                file_ids=[mf.id for mf in movie_files],
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
                "No video file resolved for movie %s; marking ambiguous.",
                movie.name,
            )
            await self.movie_repository.update_movie_file_import_status_bulk(
                file_ids=[mf.id for mf in movie_files],
                status=ImportOutcome.ambiguous,
                error="No video files; resolve manually.",
            )
            return

        log.debug(
            "Importing these %s video files and %s subtitle files",
            len(primary_videos),
            len(subtitle_files),
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
                await self.movie_repository.update_movie_file_import_status_bulk(
                    file_ids=[mf.id for mf in movie_files],
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
                source_info_hash=torrent.hash,
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
                        "Cleaned up torrent %s after successful import",
                        torrent.title,
                    )
                except Exception:
                    log.exception(
                        "Failed to clean up torrent %s after import",
                        torrent.title,
                    )
        else:
            log.error(
                "Failed to import files for torrent %s. Check logs for details.",
                torrent.title,
            )

            if self.notification_service:
                await self.notification_service.send_notification_to_all_providers(
                    title="Import Failed",
                    message=f"Failed to import files for movie {movie.name}. Please check logs.",
                )

        log.info("Finished importing files for torrent %s", torrent.title)

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
        try:
            is_canonical = paths_same_canonical(source_directory, canonical_dir)
        except PathCanonicalResolutionError as exc:
            log.exception("Failed to resolve canonical path for %s", source_directory)
            raise RenameError from exc
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

        log.debug("Found movie: %s for metadata update.", db_movie.name)

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
                "Could not fetch fresh metadata for movie: %s (%s)",
                db_movie.name,
                db_movie.year,
            )
            return None
        log.debug("Fetched fresh metadata for movie: %s", fresh_movie_data.name)

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
                    "Failed to download poster for movie: %s",
                    updated_movie.name,
                    exc_info=True,
                )
        log.info(
            "Updated metadata for movie: %s (%s)",
            updated_movie.name,
            updated_movie.year,
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
    from miramedia.background_services import bg_movie_service
    from miramedia.media_service import (
        AutoDownloadIdHooks,
        _auto_download_for_movie_impl,
        _try_auto_download_media_id_impl,
    )

    await _try_auto_download_media_id_impl(
        movie_id,
        hooks=AutoDownloadIdHooks(
            bg_service=bg_movie_service,
            media_noun="movie",
            lock_key_prefix="auto_dl_movie",
            get_media=lambda svc, mid: svc.movie_repository.get_movie_by_id(
                movie_id=mid
            ),
            run_for_media=_auto_download_for_movie_impl,
        ),
    )


async def _auto_download_missing_movies_impl() -> None:
    from miramedia.background_services import bg_movie_service
    from miramedia.media_service import _auto_download_missing_media_impl

    await _auto_download_missing_media_impl(
        bg_service=bg_movie_service,
        get_candidate_flags=lambda svc: (
            svc.movie_repository.get_movie_auto_download_candidate_flags()
        ),
        try_auto_download_id=lambda mid, _n: _try_auto_download_movie_id_impl(mid),
        media_noun="movie",
        max_downloads_per_item=5,
    )


async def _mark_movie_metadata_failure(movie_id: MovieId, reason: str) -> None:
    from miramedia.background_services import bg_movie_service
    from miramedia.media_service import _mark_media_metadata_failure

    await _mark_media_metadata_failure(
        movie_id,
        reason,
        bg_service=bg_movie_service,
        repository_attr="movie_repository",
        media_noun="movie",
    )


async def _try_update_movie_metadata_id_impl(movie_id: MovieId) -> None:
    from miramedia.background_services import bg_movie_service
    from miramedia.media_service import (
        MetadataRefreshHooks,
        _try_update_media_metadata_id_impl,
    )

    hooks = MetadataRefreshHooks(
        bg_service=bg_movie_service,
        media_noun="movie",
        get_media=lambda svc, mid: svc.movie_repository.get_movie_by_id(movie_id=mid),
        update_metadata=lambda svc, movie, provider, fresh: svc.update_movie_metadata(
            db_movie=movie,
            metadata_provider=provider,
            fresh_movie_data=fresh,
        ),
        mark_failure=_mark_movie_metadata_failure,
        fetch_native_metadata=lambda provider, imdb_id, language: (
            provider.get_movie_metadata(movie_id=imdb_id, language=language)
        ),
    )
    await _try_update_media_metadata_id_impl(movie_id, hooks=hooks)


async def _update_all_movies_metadata_impl() -> None:
    from miramedia.background_services import bg_movie_service
    from miramedia.media_service import (
        MetadataRefreshHooks,
        _update_all_media_metadata_impl,
    )

    hooks = MetadataRefreshHooks(
        bg_service=bg_movie_service,
        media_noun="movie",
        get_media=lambda svc, mid: svc.movie_repository.get_movie_by_id(movie_id=mid),
        update_metadata=lambda svc, movie, provider, fresh: svc.update_movie_metadata(
            db_movie=movie,
            metadata_provider=provider,
            fresh_movie_data=fresh,
        ),
        mark_failure=_mark_movie_metadata_failure,
        fetch_native_metadata=lambda provider, imdb_id, language: (
            provider.get_movie_metadata(movie_id=imdb_id, language=language)
        ),
    )
    await _update_all_media_metadata_impl(
        hooks=hooks,
        get_ids_due_for_metadata=lambda svc, cutoff, limit: (
            svc.movie_repository.get_movie_ids_due_for_metadata(
                older_than=cutoff, limit=limit
            )
        ),
        try_update_one=_try_update_movie_metadata_id_impl,
    )
