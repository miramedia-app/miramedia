import asyncio
import os
import pprint
import shutil
import threading
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import overload
from uuid import UUID

from cachetools import TTLCache


def _disk_scan_concurrency() -> int:
    try:
        return max(1, int(os.getenv("MIRAMEDIA_DISK_SCAN_CONCURRENCY", "8")))
    except (TypeError, ValueError):
        return 8


# Cap concurrent directory scans on the list endpoints. Unbounded, a large
# library fans out hundreds of stat-heavy threads at once, saturating the anyio
# threadpool (so every other to_thread queues) and the NAS disk — the "idle CPU
# but slow" symptom. Mirrors the torrents-list RPC semaphore.
_DISK_SCAN_CONCURRENCY = _disk_scan_concurrency()


def _disk_scan_cache_ttl() -> float:
    try:
        return max(0.0, float(os.getenv("MIRAMEDIA_DISK_SCAN_CACHE_TTL", "30")))
    except (TypeError, ValueError):
        return 30.0


# Short-TTL cache for season-dir scans. The dashboard re-fetches the shows list
# on navigation/poll; without this every load re-scans the whole library off
# the NAS disk. Disk contents change only on import/delete, so a few seconds of
# staleness on the download badge is fine. Keyed by dir path; thread-safe (the
# scan runs in to_thread worker threads). TTL=0 disables. Bumped on import via
# invalidate_disk_scan_cache().
_DISK_SCAN_CACHE_TTL = _disk_scan_cache_ttl()
_scan_cache: TTLCache = TTLCache(maxsize=8192, ttl=_DISK_SCAN_CACHE_TTL or 1)
_scan_cache_lock = threading.Lock()


def invalidate_disk_scan_cache() -> None:
    """Drop all cached season scans (call after an import/delete mutates disk)."""
    with _scan_cache_lock:
        _scan_cache.clear()


def _as_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


# These imports intentionally follow the module-level scan-cache setup above.
from miramedia.config import MiraMediaConfig  # noqa: E402
from miramedia.exceptions import (  # noqa: E402
    BadRequestError,
    NotFoundError,
    RenameError,
)
from miramedia.file_status import ImportOutcome  # noqa: E402
from miramedia.imports.files import (  # noqa: E402
    DiskSpaceError,
    ImportConflictError,
    ensure_free_space,
    files_matching_stem,
    find_renamed_duplicate,
    get_files_for_import,
    import_file,
    rename_media_slot,
)
from miramedia.indexers.schemas import (  # noqa: E402
    IndexerQueryResult,
    IndexerQueryResultId,
)
from miramedia.indexers.service import IndexerService  # noqa: E402
from miramedia.indexers.utils import evaluate_indexer_query_results  # noqa: E402
from miramedia.media_status import MediaStatus  # noqa: E402
from miramedia.metadata.backends.generic import AbstractMetadataProvider  # noqa: E402
from miramedia.metadata.schemas import MetaDataProviderSearchResult  # noqa: E402
from miramedia.naming import (  # noqa: E402
    default_season_folder_name,
    default_show_folder_name,
    episode_file_stem,
    episode_file_stem_candidates,
    old_show_folder_name,
    season_folder_name,
    show_folder_name,
)
from miramedia.notifications.service import NotificationService  # noqa: E402
from miramedia.shows import log  # noqa: E402
from miramedia.shows.models import Show as ShowOrm  # noqa: E402
from miramedia.shows.repository import ShowRepository  # noqa: E402
from miramedia.shows.schemas import (  # noqa: E402
    Episode,
    EpisodeFile,
    EpisodeId,
    PublicEpisodeFile,
    PublicSeason,
    PublicShow,
    Season,
    SeasonId,
    SeasonNumber,
    Show,
    ShowId,
)
from miramedia.torrents.mediainfo import analyze_async  # noqa: E402
from miramedia.torrents.parsing import (  # noqa: E402
    is_video_file,
    match_episode_file,
    match_special_file,
    match_subtitle_file,
    normalize_codec,
    normalize_source,
    parse_release,
)
from miramedia.torrents.quality_naming import NameParts  # noqa: E402
from miramedia.torrents.schemas import (  # noqa: E402
    Quality,
    RichTorrent,
    Torrent,
    TorrentId,
    TorrentMediaContext,
    TorrentStatus,
)
from miramedia.torrents.service import TorrentService  # noqa: E402
from miramedia.torrents.utils import get_torrent_filepath  # noqa: E402


class ShowService:
    def __init__(
        self,
        show_repository: ShowRepository,
        torrent_service: TorrentService,
        indexer_service: IndexerService,
        notification_service: NotificationService,
    ) -> None:
        self.show_repository = show_repository
        self.torrent_service = torrent_service
        self.indexer_service = indexer_service
        self.notification_service = notification_service

    async def add_show(
        self,
        external_id: str,
        metadata_provider: AbstractMetadataProvider,
        language: str | None = None,
    ) -> Show:
        """Persist a show row + poster.

        Does NOT trigger auto-download — callers that want it must run
        ``_try_auto_download_show_id_impl(saved.id)`` AFTER the surrounding
        ``bg_show_service`` session has closed. Doing it inline would pin
        the add session through the slow indexer fan-out (cloudflare bypass
        + parallel HTTP across sites), risking
        ``InterfaceError: connection is closed`` from
        ``idle_in_transaction_session_timeout`` plus a
        ``PendingRollbackError`` on the bg-session commit that follows.
        """
        show_with_metadata = await asyncio.to_thread(
            partial(
                metadata_provider.get_show_metadata,
                show_id=external_id,
                language=language,
            )
        )
        # Prevent duplicates across providers by checking imdb_id
        if show_with_metadata.imdb_id:
            existing = await self.show_repository.show_exists_by_imdb_id(
                show_with_metadata.imdb_id
            )
            if existing:
                return existing
        # Specials (Season 0) are persisted as skipped on add unless specials
        # auto-download is enabled — so the skipped flag is the single source of
        # truth (a user can later mark an individual special wanted and it
        # sticks). See _show_to_public for the display side.
        if not MiraMediaConfig().misc.download_specials:
            for season in show_with_metadata.seasons:
                if season.number == 0:
                    season.skipped = True
                    for episode in season.episodes:
                        episode.skipped = True
        saved_show = await self.show_repository.save_show(show=show_with_metadata)
        log.info(
            "Added show %s (%s) [id=%s, provider=%s]",
            saved_show.name,
            saved_show.year,
            saved_show.id,
            metadata_provider.name,
        )
        try:
            await asyncio.to_thread(
                partial(metadata_provider.download_show_poster_image, show=saved_show)
            )
        except Exception:
            log.warning(
                f"Failed to download poster for show: {saved_show.name}", exc_info=True
            )

        return saved_show

    async def get_total_downloaded_episoded_count(self) -> int:
        """
        Get total number of downloaded episodes.
        """

        return await self.show_repository.get_total_downloaded_episodes_count()

    async def set_show_library(self, show: Show, library: str) -> None:
        await self.show_repository.set_show_library(show_id=show.id, library=library)

    async def delete_show(
        self,
        show: Show,
        delete_files_on_disk: bool = False,
        delete_torrents: bool = False,  # noqa: ARG002 — kept for API back-compat; torrents reaped unconditionally
    ) -> None:
        """
        Delete a show from the database, optionally deleting files and torrents.

        :param show: The show to delete.
        :param delete_files_on_disk: Whether to delete the show's files from disk.
        :param delete_torrents: Whether to delete associated torrents from the torrent client.
        """
        log.debug(f"Deleting ID: {show.id} - Name: {show.name}")

        # Snapshot linked torrents before the cascade removes episode_file rows
        # (FK ON DELETE CASCADE), so the now-orphaned torrents can be reaped and
        # don't survive as "Unlinked" ghosts on the torrents page.
        torrents = await self.show_repository.get_torrents_by_show_id(show_id=show.id)
        torrent_ids = [t.id for t in torrents]

        if delete_files_on_disk:
            show_dir = self.get_root_show_directory(show=show)
            log.debug(f"Attempt to delete show directory: {show_dir}")
            if show_dir.exists() and show_dir.is_dir():
                await asyncio.to_thread(shutil.rmtree, show_dir)
                log.info(f"Deleted show directory: {show_dir}")

        # Delete the show (cascades season/episode/episode_file rows), then reap
        # orphaned torrents. ``cleanup_torrent_if_orphaned`` only removes a
        # torrent with no remaining media link, so running it unconditionally is
        # safe; ``delete_torrents`` is kept for API back-compat.
        await self.show_repository.delete_show(show_id=show.id)
        for tid in torrent_ids:
            await self.torrent_service.cleanup_torrent_if_orphaned(tid)

    async def delete_episode_file(
        self,
        file_id: UUID,
        delete_from_disk: bool = True,
    ) -> None:
        """
        Delete an episode file record (by surrogate id) and optionally its
        files from disk.
        """
        row = await self.show_repository.get_episode_file_by_id(file_id=file_id)
        if row is None:
            msg = f"Episode file {file_id} not found."
            raise NotFoundError(msg)

        # Capture the linked torrent before we drop the file row so we can
        # reap a now-orphaned, still-downloading torrent afterwards.
        torrent_id = row.torrent_id
        episode = await self.show_repository.get_episode(episode_id=row.episode_id)
        season = await self.show_repository.get_season_by_episode(
            episode_id=row.episode_id
        )
        if delete_from_disk:
            show = await self.show_repository.get_show_by_id(show_id=season.show_id)
            season_dir = self.get_root_season_directory(
                show=show, season_number=season.number
            )
            stems = episode_file_stem_candidates(
                show,
                season_number=season.number,
                episode_number=episode.number,
                quality=row.quality,
                parts=NameParts.from_row(row),
            )

            def _delete_files() -> None:
                for stem in stems:
                    for f in files_matching_stem(season_dir, stem):
                        try:
                            f.unlink(missing_ok=True)
                            log.info(f"Deleted file: {f}")
                        except OSError:
                            log.warning(f"Failed to delete file: {f}", exc_info=True)

            # iterdir + unlink are blocking syscalls; running them inline in
            # an ``async def`` freezes the event loop and stalls every other
            # request until the delete finishes.
            await asyncio.to_thread(_delete_files)
        await self.show_repository.delete_episode_file(file_id=file_id)
        if torrent_id is not None:
            await self.torrent_service.cleanup_torrent_if_orphaned(torrent_id)

        from miramedia.media_state import refresh_media_state

        await refresh_media_state(self.show_repository.db, show_id=season.show_id)

    async def delete_season_files(
        self,
        season: Season,
        delete_from_disk: bool = True,
    ) -> None:
        """
        Delete all episode files for a season and mark all episodes as skipped.
        """
        if delete_from_disk:
            show = await self.show_repository.get_show_by_id(show_id=season.show_id)
            season_dir = self.get_root_season_directory(
                show=show, season_number=season.number
            )
            if season_dir.exists() and season_dir.is_dir():
                await asyncio.to_thread(shutil.rmtree, season_dir)
                log.info(f"Deleted season directory: {season_dir}")
        for episode in season.episodes:
            await self.show_repository.update_episode_skipped(
                episode_id=episode.id, skipped=True
            )

        from miramedia.media_state import refresh_media_state

        await refresh_media_state(self.show_repository.db, show_id=season.show_id)

    async def get_public_episode_files_by_season_id(
        self, season: Season
    ) -> list[PublicEpisodeFile]:
        """
        Get all public episode files for a given season.

        :param season: The season object.
        :return: A list of public episode files.
        """
        from miramedia.file_status import FileStatus

        episode_files = await self.show_repository.get_episode_files_by_season_id(
            season_id=season.id
        )
        public_episode_files = [
            PublicEpisodeFile.model_validate(x) for x in episode_files
        ]

        show = await self.show_repository.get_show_by_season_id(season_id=season.id)
        season_dir = self.get_root_season_directory(
            show=show, season_number=season.number
        )
        episode_map = {ep.id: ep for ep in season.episodes}

        # Batch resolve torrent imported-state. Read-only: no client RPC, no
        # per-file DB writes — the torrents poll on the page already covers
        # live status.
        torrent_ids = [
            ef.torrent_id for ef in public_episode_files if ef.torrent_id is not None
        ]
        imported_by_torrent = (
            await self.torrent_service.bulk_check_torrents_imported(torrent_ids)
            if torrent_ids
            else {}
        )

        video_exts = {".mkv", ".mp4", ".avi", ".mov"}

        # One off-loop scan of the season dir, bucketed by stem prefix. Keyed
        # by the file row's surrogate id.
        def _scan_disk() -> dict[UUID, str]:
            out: dict[UUID, str] = {}
            if not season_dir.exists() or not season_dir.is_dir():
                return out
            try:
                entries = list(season_dir.iterdir())
            except OSError:
                return out
            for ef in public_episode_files:
                ep = episode_map.get(ef.episode_id)
                if ep is None:
                    continue
                stems = episode_file_stem_candidates(
                    show,
                    season_number=season.number,
                    episode_number=ep.number,
                    quality=ef.quality,
                    parts=NameParts.from_row(ef),
                )
                for stem in stems:
                    prefix = stem + "."
                    for p in entries:
                        if (
                            p.is_file()
                            and p.name.startswith(prefix)
                            and p.suffix.lower() in video_exts
                        ):
                            out[ef.id] = p.name
                            break
                    if ef.id in out:
                        break
            return out

        disk_names = await asyncio.to_thread(_scan_disk)

        result = []
        for episode_file in public_episode_files:
            tid = episode_file.torrent_id
            considered_imported = (
                episode_file.import_status == ImportOutcome.imported
                or tid is None
                or imported_by_torrent.get(tid, False)
            )
            if considered_imported:
                episode_file.downloaded = True
                episode_file.status = MediaStatus.downloaded

            file_name = disk_names.get(episode_file.id)
            file_on_disk = file_name is not None
            if file_on_disk:
                episode_file.file_name = file_name

            if file_on_disk:
                episode_file.file_status = FileStatus.imported
            elif episode_file.import_status == ImportOutcome.imported:
                episode_file.file_status = FileStatus.removed
            elif tid is None:
                episode_file.file_status = FileStatus.orphaned
            elif episode_file.downloaded:
                episode_file.file_status = FileStatus.removed
            else:
                episode_file.file_status = FileStatus.queued

            result.append(episode_file)
        return result

    @overload
    async def check_if_show_exists(
        self, *, external_id: str, metadata_provider: str
    ) -> bool:
        """
        Check if a show exists in the database.

        :param external_id: The provider's ID of the show.
        :param metadata_provider: The metadata provider.
        :return: True if the show exists, False otherwise.
        """

    @overload
    async def check_if_show_exists(self, *, show_id: ShowId) -> bool:
        """
        Check if a show exists in the database.

        :param show_id: The ID of the show.
        :return: True if the show exists, False otherwise.
        """

    async def check_if_show_exists(
        self, *, external_id=None, metadata_provider=None, show_id=None
    ) -> bool:
        if not (external_id is None or metadata_provider is None):
            try:
                await self.show_repository.get_show_by_external_id(
                    external_id=external_id, metadata_provider=metadata_provider
                )
            except NotFoundError:
                return False
        elif show_id is not None:
            try:
                await self.show_repository.get_show_by_id(show_id=show_id)
            except NotFoundError:
                return False
        else:
            msg = "Use one of the provided overloads for this function!"
            raise ValueError(msg)

        return True

    async def get_all_available_torrents_for_a_season(
        self,
        season_number: int,
        show_id: ShowId,
        search_query_override: str | None = None,
    ) -> list[IndexerQueryResult]:
        """
        Get all available torrents for a given season.

        :param season_number: The number of the season.
        :param show_id: The ID of the show.
        :param search_query_override: Optional override for the search query.
        :return: A list of indexer query results.
        """

        from miramedia.database import release_session_before_external_io

        if search_query_override:
            await release_session_before_external_io(self.show_repository.db)
            return await self.indexer_service.search(
                query=search_query_override, is_tv=True
            )

        show = await self.show_repository.get_show_by_id(show_id=show_id)

        # Release the session BEFORE the slow indexer fan-out so the
        # asyncpg connection doesn't sit idle-in-TX through cloudflare
        # bypass + parallel HTTP. Session re-checks out a fresh conn on
        # the next statement (the per-result save_result writes inside
        # search_season).
        await release_session_before_external_io(self.show_repository.db)

        torrents = await self.indexer_service.search_season(
            show=show, season_number=season_number
        )

        results = [torrent for torrent in torrents if season_number in torrent.season]

        quality_allowed, codec_allowed = self._get_effective_preferences(show)
        return evaluate_indexer_query_results(
            is_tv=True,
            query_results=results,
            media=show,
            quality_allowed=quality_allowed,
            codec_allowed=codec_allowed,
        )

    async def get_all_available_torrents_for_an_episode(
        self,
        season_number: int,
        episode_number: int,
        show_id: ShowId,
        search_query_override: str | None = None,
    ) -> list[IndexerQueryResult]:
        """
        Get all available torrents for a given episode.

        :param season_number: The number of the season.
        :param episode_number: The number of the episode.
        :param show_id: The ID of the show.
        :param search_query_override: Optional override for the search query.
        :return: A list of indexer query results.
        """
        from miramedia.database import release_session_before_external_io

        if search_query_override:
            await release_session_before_external_io(self.show_repository.db)
            return await self.indexer_service.search(
                query=search_query_override, is_tv=True
            )

        show = await self.show_repository.get_show_by_id(show_id=show_id)

        # Specials (Season 0) are rarely named "S00E00" by release groups — they
        # carry the special's own title instead. Search by that title and match
        # results on title overlap rather than the S/E parse.
        if season_number == 0:
            return await self._search_special_episode(
                show=show, episode_number=episode_number
            )

        # Release session before slow indexer fan-out. See companion
        # comment in get_all_available_torrents_for_a_season.
        await release_session_before_external_io(self.show_repository.db)

        torrents = await self.indexer_service.search_episode(
            show=show, season_number=season_number, episode_number=episode_number
        )

        # Include torrents that contain this specific episode, or season packs
        # for the right season (they contain the episode too)
        results = [
            torrent
            for torrent in torrents
            if (episode_number in torrent.episode and season_number in torrent.season)
            or (not torrent.episode and season_number in torrent.season)
        ]

        quality_allowed, codec_allowed = self._get_effective_preferences(show)
        return evaluate_indexer_query_results(
            is_tv=True,
            query_results=results,
            media=show,
            quality_allowed=quality_allowed,
            codec_allowed=codec_allowed,
        )

    async def _search_special_episode(
        self, show: Show, episode_number: int
    ) -> list[IndexerQueryResult]:
        """Search indexers for a Season 0 special by its episode title.

        Release groups name specials after the special's title (not ``S00E00``),
        so we query ``"{show} {special title}"`` and keep results whose title
        overlaps the special title. Falls back to ``"{show} special"`` when the
        episode has no usable title.
        """
        from miramedia.database import release_session_before_external_io
        from miramedia.imports.matching import _normalize_title_for_matching
        from miramedia.indexers.utils import sanitize_search_query

        title = ""
        for season in show.seasons:
            if season.number != 0:
                continue
            for episode in season.episodes:
                if episode.number == episode_number:
                    title = (episode.title or "").strip()
                    break

        title_words = set(_normalize_title_for_matching(title).lower().split())
        # Drop the show's own words from the match set so e.g. "The Bear" in the
        # special title doesn't let an unrelated S01 pack pass the overlap test.
        show_words = set(_normalize_title_for_matching(show.name).lower().split())
        match_words = title_words - show_words

        query = f"{show.name} {title}".strip() if title else f"{show.name} special"
        query = sanitize_search_query(query)

        await release_session_before_external_io(self.show_repository.db)
        torrents = await self.indexer_service.search(query=query, is_tv=True)

        if match_words:
            results = []
            for t in torrents:
                t_words = set(_normalize_title_for_matching(t.title).lower().split())
                overlap = match_words & t_words
                if len(overlap) / len(match_words) >= 0.6:
                    results.append(t)
        else:
            # No distinguishing title words — accept results that look special.
            results = [t for t in torrents if "special" in t.title.lower()]

        quality_allowed, codec_allowed = self._get_effective_preferences(show)
        return evaluate_indexer_query_results(
            is_tv=True,
            query_results=results,
            media=show,
            quality_allowed=quality_allowed,
            codec_allowed=codec_allowed,
        )

    async def get_all_shows(self) -> list[Show]:
        """
        Get all shows.

        :return: A list of all shows.
        """
        return await self.show_repository.get_shows()

    async def get_all_public_shows(self) -> list[PublicShow]:
        """Return list-view PublicShow objects with computed download/status."""
        shows = await self.show_repository.get_shows()
        return await self._shows_to_public_with_disk_scan(shows)

    async def get_paginated_public_shows(
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
        airing: list[str] | None = None,
        excluded_airing: list[str] | None = None,
        statuses: list[str] | None = None,
        excluded_statuses: list[str] | None = None,
    ) -> tuple[list[PublicShow], int]:
        """Paginated counterpart pushing LIMIT/OFFSET into SQL.

        Only the rows on the requested page incur a disk-scan, so the cost
        is proportional to page size instead of library size.
        """
        shows, total = await self.show_repository.get_shows_paginated(
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
            airing=airing,
            excluded_airing=excluded_airing,
            statuses=statuses,
            excluded_statuses=excluded_statuses,
        )
        return self._shows_to_public_list(shows), total

    async def count_public_shows(
        self,
        *,
        query: str | None = None,
        libraries: list[str] | None = None,
        excluded_libraries: list[str] | None = None,
        genres: list[str] | None = None,
        excluded_genres: list[str] | None = None,
        decades: list[int] | None = None,
        excluded_decades: list[int] | None = None,
        airing: list[str] | None = None,
        excluded_airing: list[str] | None = None,
        statuses: list[str] | None = None,
        excluded_statuses: list[str] | None = None,
    ) -> int:
        return await self.show_repository.count_shows_filtered(
            query=query,
            libraries=libraries,
            excluded_libraries=excluded_libraries,
            genres=genres,
            excluded_genres=excluded_genres,
            decades=decades,
            excluded_decades=excluded_decades,
            airing=airing,
            excluded_airing=excluded_airing,
            statuses=statuses,
            excluded_statuses=excluded_statuses,
        )

    def _shows_to_public_list(self, shows: list[ShowOrm]) -> list[PublicShow]:
        """Fast list transform using denormalized counters (no disk scan)."""
        from sqlalchemy import inspect as sa_inspect

        out: list[PublicShow] = []
        for show in shows:
            data = {
                c.key: getattr(show, c.key)
                for c in sa_inspect(show).mapper.column_attrs
            }
            data["wanted_episode_count"] = int(show.wanted_episode_count)
            data["downloaded_episode_count"] = int(show.downloaded_episode_count)
            data["seasons"] = []
            if show.skipped:
                data["status"] = MediaStatus.skipped
            elif show.list_progress_status == "complete":
                data["status"] = MediaStatus.downloaded
            else:
                data["status"] = MediaStatus.wanted
            out.append(PublicShow.model_validate(data))
        return out

    async def _shows_to_public_with_disk_scan(
        self, shows: list[Show]
    ) -> list[PublicShow]:
        if not shows:
            return []

        # Build the full {(show_id, season_id): season_dir} map first, then
        # scan every directory in parallel — one to_thread fan-out for the
        # whole library instead of one per show.
        season_dirs: dict[tuple[ShowId, SeasonId], Path] = {}
        for show in shows:
            for season in show.seasons:
                season_dirs[(show.id, season.id)] = self.get_root_season_directory(
                    show, season.number
                )

        if season_dirs:
            sem = asyncio.Semaphore(_DISK_SCAN_CONCURRENCY)

            async def _scan(d: Path) -> set[str]:
                async with sem:
                    return await asyncio.to_thread(self._scan_season_video_files, d)

            scanned = await asyncio.gather(*(_scan(d) for d in season_dirs.values()))
            disk_by_key: dict[tuple[ShowId, SeasonId], set[str]] = dict(
                zip(season_dirs.keys(), scanned, strict=True)
            )
        else:
            disk_by_key = {}

        return [
            self._show_to_public(
                show,
                disk_by_season={
                    season_id: disk_by_key.get((show.id, season_id), set())
                    for season_id in (s.id for s in show.seasons)
                },
                include_seasons=False,
            )
            for show in shows
        ]

    async def annotate_search_results(
        self, results: list[MetaDataProviderSearchResult]
    ) -> list[MetaDataProviderSearchResult]:
        """Public re-annotation entrypoint for cached search results.

        ``added``/``id`` reflect current library membership, so a cached
        provider result list (e.g. the ``/recommended`` response cache) MUST be
        re-annotated per request — otherwise a title imported after the cache
        was populated keeps showing "Add". Annotation is one bulk DB query (+
        optional native bridge), cheap next to the cached provider HTTP fan-out.
        """
        return await self._annotate_added_status(results)

    async def _annotate_added_status(
        self, results: list[MetaDataProviderSearchResult]
    ) -> list[MetaDataProviderSearchResult]:
        """Mark which search results are already tracked in the DB.

        Uses one bulk query instead of one round-trip per result.
        """
        # Three-way match (mirrors the scan's _resolve_existing): a result is
        # "added" when the library has a row whose imdb_id OR external_id equals
        # the result's IMDb id, OR whose (external_id, metadata_provider) equals
        # the result's provider key. The external_id==imdb_id arm is what catches
        # native/scan-imported rows, which store the IMDb id in external_id and
        # leave the imdb_id column NULL — an imdb_id-only lookup wrongly showed
        # those as "Add" instead of "View".
        imdb_ids = [str(r.imdb_id) for r in results if r.imdb_id]
        provider_keys = [(r.external_id, r.metadata_provider) for r in results]
        rows = await self.show_repository.shows_existing_by_identifiers(
            imdb_ids, provider_keys
        )
        by_imdb = {imdb: sid for imdb, _ext, _prov, sid in rows if imdb is not None}
        by_external = {ext: sid for _imdb, ext, _prov, sid in rows}
        by_provider = {(ext, prov): sid for _imdb, ext, prov, sid in rows}
        # The provider search is @cached and returns its list by reference.
        # Mutating those objects in place would (a) leak one request's
        # annotation into another's view and (b) persist a stale added=True /
        # dangling id after the show is deleted, until the TTL expires. Copy and
        # set both fields unconditionally on the copies.
        annotated: list[MetaDataProviderSearchResult] = []
        for result in results:
            show_id = None
            if result.imdb_id:
                imdb = str(result.imdb_id)
                show_id = by_imdb.get(imdb) or by_external.get(imdb)
            if show_id is None:
                show_id = by_provider.get(
                    (result.external_id, result.metadata_provider)
                )
            copy = result.model_copy()
            copy.added = show_id is not None
            copy.id = show_id
            annotated.append(copy)

        await self._bridge_native_added_status(annotated)
        return annotated

    async def _bridge_native_added_status(
        self, annotated: list[MetaDataProviderSearchResult]
    ) -> None:
        """Second-pass match for IMDb-keyed (native/scan) library rows.

        TMDB/TVDB search results carry no ``imdb_id``, so the three-way match
        above can't link them to native-provider library rows (keyed on the
        IMDb id, provider ``native``). Resolve the IMDb id for the still-unmatched
        results via their provider (one cached call each) and match against the
        native index. Skipped entirely when the library has no native rows.
        """
        from miramedia.metadata.dependencies import get_metadata_provider

        unresolved = [c for c in annotated if not c.added and not c.imdb_id]
        if not unresolved:
            return
        native_index = await self.show_repository.native_imdb_index()
        if not native_index:
            return

        async def _resolve(copy: MetaDataProviderSearchResult) -> None:
            try:
                provider = get_metadata_provider(copy.metadata_provider)  # type: ignore[arg-type]
            except Exception:
                return
            imdb = await asyncio.to_thread(provider.get_show_imdb_id, copy.external_id)
            show_id = native_index.get(imdb) if imdb else None
            if show_id is not None:
                copy.added = True
                copy.id = show_id

        await asyncio.gather(*(_resolve(c) for c in unresolved))

    async def discover_shows(
        self, query: str | None = None, skip: int = 0
    ) -> list[MetaDataProviderSearchResult]:
        """Search (or, with ``query=None``, fetch trending) shows across the
        enabled providers in precedence order — TMDB → TVDB → Cinemeta →
        TVMaze — returning the first provider's non-empty results.

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
                    partial(provider.search_show, query, skip=skip)
                )
            except MetadataProviderUnavailableError:
                continue
            saw_reachable = True
            if results:
                return await self._annotate_added_status(results)
        if providers and not saw_reachable:
            raise MetadataProviderUnavailableError
        return []

    @staticmethod
    def _scan_season_video_files(season_dir: Path) -> set[str]:
        """Sync helper: list lowercase video filenames in a season directory.

        Uses ``os.scandir`` so the whole listing is one syscall batch and the
        is-file check reads the cached dirent type — no per-entry ``stat()``,
        which is the expensive part on a NAS spinning disk.
        """
        if _DISK_SCAN_CACHE_TTL > 0:
            key = str(season_dir)
            with _scan_cache_lock:
                hit = _scan_cache.get(key)
            if hit is not None:
                return hit

        video_extensions = {".mkv", ".mp4", ".avi", ".mov"}
        names: set[str] = set()
        try:
            with os.scandir(season_dir) as it:
                for entry in it:
                    name = entry.name
                    dot = name.rfind(".")
                    if dot == -1:
                        continue
                    if name[dot:].lower() not in video_extensions:
                        continue
                    try:
                        if entry.is_file():
                            names.add(name.lower())
                    except OSError:
                        continue
        except FileNotFoundError:
            # Cache the empty result too — a missing season dir is stable, no
            # point re-scandir'ing it every list load.
            if _DISK_SCAN_CACHE_TTL > 0:
                with _scan_cache_lock:
                    _scan_cache[str(season_dir)] = set()
            return set()
        except OSError as e:
            # Transient (permissions/IO) — don't cache, let the next call retry.
            log.error(f"Disk scan failed for {season_dir}: {e}")
            return set()
        if _DISK_SCAN_CACHE_TTL > 0:
            with _scan_cache_lock:
                _scan_cache[str(season_dir)] = names
        return names

    def _show_to_public(
        self,
        show: Show,
        *,
        disk_by_season: dict[SeasonId, set[str]],
        include_seasons: bool = True,
    ) -> PublicShow:
        """Pure transform: Show + pre-scanned disk maps → PublicShow.

        Extracted from ``get_public_show_by_id`` so the batched list path
        (``get_all_public_shows``) can scan every season directory once and
        share the result, avoiding one ``to_thread`` fan-out per show.
        """
        # PERF TODO: ``PublicShow.model_validate`` runs full pydantic
        # validation per row (and recursively per PublicSeason/PublicEpisode).
        # ``Model.model_construct`` would skip validation for a meaningful
        # speedup on list paths, but PublicShow/PublicSeason/PublicEpisode
        # rely on nested validation + status enums get assigned after the
        # initial validate() call, so swapping in model_construct is fragile
        # (any missed field becomes a 500 at serialise time). Revisit if
        # this transform shows up in flamegraphs.
        public_show = PublicShow.model_validate(show)
        public_show.skipped = show.skipped
        public_seasons: list[PublicSeason] = []

        # Specials (Season 0) are persisted as skipped at add time when specials
        # auto-download is off (see ``add_show``), so the skipped flags below are
        # the single source of truth — a user can mark an individual special
        # wanted and it sticks.
        for season in show.seasons:
            public_season = PublicSeason.model_validate(season)
            season_files = disk_by_season.get(season.id, set())

            for public_episode, episode in zip(
                public_season.episodes, season.episodes, strict=True
            ):
                public_episode.downloaded = self._episode_downloaded_from_cache(
                    episode=episode,
                    season_number=season.number,
                    season_files=season_files,
                )
                if public_episode.skipped:
                    public_episode.status = MediaStatus.skipped
                elif public_episode.downloaded:
                    public_episode.status = MediaStatus.downloaded
                else:
                    public_episode.status = MediaStatus.wanted

            # Season status: season.skipped is sticky (never auto-changes to downloaded)
            has_episodes = bool(public_season.episodes)
            any_downloaded = any(ep.downloaded for ep in public_season.episodes)
            all_skipped = has_episodes and all(
                ep.skipped for ep in public_season.episodes
            )
            all_accounted_for = has_episodes and all(
                ep.downloaded or ep.skipped for ep in public_season.episodes
            )
            public_season.downloaded = (
                has_episodes and any_downloaded and all_accounted_for
            )
            if season.skipped:
                public_season.status = MediaStatus.skipped
            elif not has_episodes:
                public_season.status = MediaStatus.wanted
            elif all_skipped:
                # No episodes wanted — effective skip even if season flag is False
                public_season.status = MediaStatus.skipped
            elif all_accounted_for and any_downloaded:
                public_season.status = MediaStatus.downloaded
            else:
                public_season.status = MediaStatus.wanted
            public_seasons.append(public_season)

        public_show.seasons = public_seasons

        # Compute show-level status from seasons — show.skipped overrides
        if show.skipped:
            public_show.status = MediaStatus.skipped
        elif not public_seasons:
            public_show.status = MediaStatus.wanted
        elif all(s.status == MediaStatus.skipped for s in public_seasons):
            public_show.status = MediaStatus.skipped
        elif all(
            s.status in (MediaStatus.downloaded, MediaStatus.skipped)
            for s in public_seasons
        ) and any(s.status == MediaStatus.downloaded for s in public_seasons):
            public_show.status = MediaStatus.downloaded
        else:
            public_show.status = MediaStatus.wanted

        # Aggregate counts over wanted (non-skipped) episodes — matches the
        # frontend's per-show progress badge. Computed from the built tree so it
        # stays consistent with the per-episode ``downloaded`` flags above.
        wanted = 0
        downloaded = 0
        for s in public_seasons:
            for ep in s.episodes:
                if ep.skipped:
                    continue
                wanted += 1
                if ep.downloaded:
                    downloaded += 1
        public_show.wanted_episode_count = wanted
        public_show.downloaded_episode_count = downloaded

        if not include_seasons:
            # List path: drop the heavy season/episode tree from the response —
            # the grid only needs the counts above. Detail keeps the full tree.
            public_show.seasons = []

        return public_show

    async def get_public_show_by_id(self, show: Show) -> PublicShow:
        """
        Get a public show from a Show object.

        :param show: The show object.
        :return: A public show.
        """
        # Scan every season directory in parallel, off the event loop, so the
        # per-episode download check is a pure set lookup instead of a
        # blocking iterdir() per episode.
        season_dirs = {
            season.id: self.get_root_season_directory(show, season.number)
            for season in show.seasons
        }
        if season_dirs:
            scanned = await asyncio.gather(
                *(
                    asyncio.to_thread(self._scan_season_video_files, d)
                    for d in season_dirs.values()
                )
            )
            disk_by_season: dict[SeasonId, set[str]] = dict(
                zip(season_dirs.keys(), scanned, strict=True)
            )
        else:
            disk_by_season = {}

        return self._show_to_public(show, disk_by_season=disk_by_season)

    async def get_show_by_id(self, show_id: ShowId) -> Show:
        """
        Get a show by its ID.

        :param show_id: The ID of the show.
        :return: The show.
        """
        return await self.show_repository.get_show_by_id(show_id=show_id)

    async def is_season_downloaded(self, season: Season, show: Show) -> bool:
        """
        Check if a season is downloaded.

        :param season: The season object.
        :param show: The show object.
        :return: True if the season is downloaded, False otherwise.
        """
        episodes = season.episodes

        if not episodes:
            return False

        for episode in episodes:
            if not await self.is_episode_downloaded(
                episode=episode, season=season, show=show
            ):
                return False
        return True

    @staticmethod
    def _episode_downloaded_from_cache(
        episode: Episode,
        season_number: SeasonNumber,
        season_files: set[str],
    ) -> bool:
        """Sync helper: decide download status from precomputed inputs.

        ``episode.episode_files`` is eager-loaded by the Show repository, so
        no DB roundtrip happens here. ``season_files`` is a single per-season
        disk scan shared across every episode in that season.
        """
        if not episode.episode_files:
            return False
        if not season_files:
            return False
        episode_token = f"S{season_number:02d}E{episode.number:02d}".lower()
        return any(episode_token in name for name in season_files)

    async def is_episode_downloaded(
        self, episode: Episode, season: Season, show: Show
    ) -> bool:
        """
        Check if an episode is downloaded and imported (file exists on disk).

        An episode is considered downloaded if:
        - There is at least one EpisodeFile in the database AND
        - A matching episode file exists in the season directory on disk.

        :param episode: The episode object.
        :param season: The season object.
        :param show: The show object.
        :return: True if the episode is downloaded and imported, False otherwise.
        """
        if not episode.episode_files:
            return False
        season_dir = self.get_root_season_directory(show, season.number)
        season_files = await asyncio.to_thread(
            self._scan_season_video_files, season_dir
        )
        return self._episode_downloaded_from_cache(
            episode=episode,
            season_number=season.number,
            season_files=season_files,
        )

    async def set_season_skipped(self, season_id: SeasonId, skipped: bool) -> None:
        """
        Set season skipped flag and cascade to its episodes.

        Skipping a season skips every non-downloaded episode; making a season
        wanted clears the skipped flag on every non-downloaded episode.
        Downloaded episodes keep their existing flag because their file on
        disk represents an explicit user choice.
        """
        season = await self.show_repository.get_season(season_id=season_id)
        show = await self.show_repository.get_show_by_id(show_id=season.show_id)
        await self.show_repository.update_season_skipped(
            season_id=season_id, skipped=skipped
        )
        for episode in season.episodes:
            if await self.is_episode_downloaded(
                episode=episode, season=season, show=show
            ):
                continue
            if bool(episode.skipped) == skipped:
                continue
            await self.show_repository.update_episode_skipped(
                episode_id=episode.id, skipped=skipped
            )

    async def set_episode_skipped(self, episode_id: EpisodeId, skipped: bool) -> None:
        """
        Set episode skipped flag and propagate intent upward.

        Marking an episode wanted while its season is flagged skipped flips the
        season to wanted without touching sibling episodes — sibling state is
        preserved so users can still maintain per-episode skips.
        """
        await self.show_repository.update_episode_skipped(
            episode_id=episode_id, skipped=skipped
        )
        if skipped:
            return
        season = await self.show_repository.get_season_by_episode(episode_id=episode_id)
        if season.skipped:
            await self.show_repository.update_season_skipped(
                season_id=season.id, skipped=False
            )

    async def get_show_by_external_id(
        self, external_id: str, metadata_provider: str
    ) -> Show | None:
        """
        Get a show by its metadata provider ID.
        """
        return await self.show_repository.get_show_by_external_id(
            external_id=external_id, metadata_provider=metadata_provider
        )

    async def get_season(self, season_id: SeasonId) -> Season:
        """
        Get a season by its ID.

        :param season_id: The ID of the season.
        :return: The season.
        """
        return await self.show_repository.get_season(season_id=season_id)

    async def get_episode(self, episode_id: EpisodeId) -> Episode:
        """
        Get an episode by its ID.

        :param episode_id: The ID of the episode.
        :return: The episode.
        """
        return await self.show_repository.get_episode(episode_id=episode_id)

    async def get_season_by_episode(self, episode_id: EpisodeId) -> Season:
        """
        Get a season by the episode ID.

        :param episode_id: The ID of the episode.
        :return: The season.
        """
        return await self.show_repository.get_season_by_episode(episode_id=episode_id)

    async def get_torrents_for_show(self, show: Show) -> list[RichTorrent]:
        """
        Get torrents for a given show.

        Per-torrent metadata queries (status, seasons, episodes, files,
        import progress) run serially: they share the request-scoped
        AsyncSession, which is not safe for concurrent use (asyncpg raises
        "another operation is in progress").

        :param show: The show.
        :return: A list of RichTorrent objects.
        """
        raw_torrents = await self.show_repository.get_torrents_by_show_id(
            show_id=show.id
        )

        async def _enrich(t: Torrent) -> RichTorrent:
            try:
                t = await self.torrent_service.get_torrent_status(t)
            except RuntimeError:
                pass
            # Serial — same shared AsyncSession across these four calls.
            # Parallel gather would race the single asyncpg connection.
            seasons = await self.show_repository.get_seasons_by_torrent_id(
                torrent_id=t.id,
            )
            episodes = await self.show_repository.get_episodes_by_torrent_id(
                torrent_id=t.id,
            )
            episode_files = await self.torrent_service.get_episode_files_of_torrent(
                torrent=t,
            )
            import_progress = await self.torrent_service.compute_import_progress(t)
            variant = episode_files[0].variant if episode_files else ""
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
                    media_type="show",
                    media_id=show.id,
                    media_name=show.name,
                    media_year=show.year,
                    metadata_provider=show.metadata_provider,
                    seasons=sorted(seasons) if seasons else None,
                    episodes=sorted(episodes)
                    if len(seasons) == 1 and episodes
                    else None,
                ),
            )

        return [await _enrich(t) for t in raw_torrents]

    async def _auto_download_first_valid(
        self,
        results: list,
        show: Show,
        label: str,
        episode_target: tuple[int, int] | None = None,
    ) -> IndexerQueryResult | None:
        """Iterate results, downloading the first one not preflight-rejected.

        Returns the picked indexer result or ``None`` when every candidate
        was deny-listed or contained no video files.

        *episode_target* pins linking to an explicit ``(season, episode)`` for
        releases whose title can't be parsed (Season 0 specials).
        """
        from miramedia.exceptions import NoVideoFilesError

        for candidate in results:
            log.info(
                "Auto-download: downloading %s %s: %s",
                show.name,
                label,
                candidate.title,
            )
            try:
                await self.download_torrent(
                    public_indexer_result_id=candidate.id,
                    show_id=show.id,
                    episode_target=episode_target,
                )
            except NoVideoFilesError as e:
                log.info("Auto-download: skipping %s — %s", candidate.title, e)
                continue
            return candidate
        log.info(
            "Auto-download: no usable candidates for %s %s after deny-list/no-video filtering",
            show.name,
            label,
        )
        return None

    async def download_torrent(
        self,
        public_indexer_result_id: IndexerQueryResultId,
        show_id: ShowId,
        override_variant: str = "",
        episode_target: tuple[int, int] | None = None,
    ) -> Torrent:
        """
        Download a torrent for a given indexer result and show.
        Delegates to TorrentService.download_and_link().

        *episode_target* pins the link to an explicit ``(season, episode)`` when
        the release title can't be parsed — used for Season 0 specials.
        """
        from miramedia.torrents.schemas import MediaType

        indexer_result = await self.indexer_service.get_result(
            result_id=public_indexer_result_id
        )
        return await self.torrent_service.download_and_link(
            indexer_result=indexer_result,
            media_type=MediaType.show,
            media_id=show_id,
            variant=override_variant,
            show_repository=self.show_repository,
            episode_target=episode_target,
        )

    def _show_library_parent(self, show: Show) -> Path:
        misc_config = MiraMediaConfig().misc
        if show.library and show.library != "Default":
            for library in misc_config.show_libraries:
                if library.name == show.library:
                    return Path(library.path)
            log.warning(
                f"Library '{show.library}' for show '{show.name}' not found in configured show_libraries, falling back to default show directory."
            )
        return misc_config.show_directory

    def get_root_show_directory(self, show: Show, *, write: bool = False) -> Path:
        show_directory_name = show_folder_name(show)
        parent = self._show_library_parent(show)
        new_path = parent / show_directory_name
        if write or new_path.exists():
            return new_path

        default_directory_name = default_show_folder_name(show)
        old_directory_name = old_show_folder_name(show)
        for fallback_name in (default_directory_name, old_directory_name):
            if fallback_name == show_directory_name:
                continue
            old_path = parent / fallback_name
            if old_path.exists():
                return old_path
        return new_path

    def get_root_season_directory(
        self, show: Show, season_number: int, *, write: bool = False
    ) -> Path:
        root = self.get_root_show_directory(show, write=write)
        current = root / Path(season_folder_name(season_number))
        if write:
            return current
        fallback = root / Path(default_season_folder_name(season_number))
        if not current.exists() and fallback != current and fallback.exists():
            return fallback
        return current

    async def move_show_library(
        self,
        show: Show,
        target_library: str,
        *,
        delete_source: bool = True,
    ) -> dict:
        """Re-home a show's directory under a different configured library.

        Hardlinks every file from the existing show directory to the new
        location (cross-FS falls back to copy via ``import_file``), then
        rewrites ``show.library`` so future writes land under the new root.
        Source files are removed when ``delete_source=True``.
        """
        misc_config = MiraMediaConfig().misc
        valid = {"Default", *(lib.name for lib in misc_config.show_libraries)}
        if target_library not in valid:
            msg = f"Unknown show library '{target_library}'"
            raise ValueError(msg)

        if show.library == target_library:
            return {"moved": 0, "skipped": True, "reason": "already in target library"}

        old_root = self.get_root_show_directory(show)
        # Compute the new root with the target library; restore the original
        # in-memory value so a partial failure doesn't leave the Pydantic
        # object out of sync with the DB row.
        original_library = show.library
        show.library = target_library
        try:
            new_root = self.get_root_show_directory(show)
        finally:
            show.library = original_library

        if not old_root.exists():
            await self.show_repository.set_show_library(
                show_id=show.id, library=target_library
            )
            return {"moved": 0, "skipped": True, "reason": "source directory missing"}

        new_root.mkdir(parents=True, exist_ok=True)

        def _link_tree(src_root: Path, dst_root: Path) -> tuple[int, list[str]]:
            local_moved = 0
            local_errors: list[str] = []
            for source in src_root.rglob("*"):
                if not source.is_file():
                    continue
                rel = source.relative_to(src_root)
                target = dst_root / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    ensure_free_space(target.parent, source.stat().st_size)
                    import_file(target_file=target, source_file=source)
                    local_moved += 1
                except (DiskSpaceError, ImportConflictError, OSError) as exc:
                    local_errors.append(f"{rel}: {exc}")
            return local_moved, local_errors

        # Releasing before the copy: _link_tree hardlinks/copies the whole show
        # tree (minutes, multi-GB on the NAS). Without this the asyncpg conn
        # sits idle-in-transaction for the duration and can exhaust the small
        # request pool — the same failure class fixed across the import paths.
        from miramedia.database import release_session_before_external_io

        await release_session_before_external_io(self.show_repository.db)
        moved, errors = await asyncio.to_thread(_link_tree, old_root, new_root)

        if errors:
            log.warning("move_show_library partial: %d errors", len(errors))
            return {
                "moved": moved,
                "errors": errors,
                "old_root": str(old_root),
                "new_root": str(new_root),
                "library_changed": False,
            }

        # Persist the new library *only* after a clean link pass so a partial
        # failure doesn't leave DB pointing at a directory missing files.
        await self.show_repository.set_show_library(
            show_id=show.id, library=target_library
        )

        if delete_source:
            try:
                await asyncio.to_thread(shutil.rmtree, old_root)
            except OSError as exc:
                errors.append(f"remove source: {exc}")

        return {
            "moved": moved,
            "errors": errors,
            "old_root": str(old_root),
            "new_root": str(new_root),
            "library_changed": True,
        }

    async def resolve_episode_file_path(self, episode_file) -> Path | None:  # noqa: ANN001
        """Find the on-disk video file for an EpisodeFile row, or ``None``.

        Used by the integrity audit task. Globs ``{stem}.*`` in the season
        directory and returns the first video extension found.
        """
        try:
            episode = await self.show_repository.get_episode(
                episode_id=episode_file.episode_id
            )
            season = await self.show_repository.get_season_by_episode(
                episode_id=episode_file.episode_id
            )
            show = await self.show_repository.get_show_by_id(show_id=season.show_id)
        except NotFoundError:
            return None
        season_dir = self.get_root_season_directory(
            show=show, season_number=season.number
        )
        if not season_dir.exists():
            return None
        stems = episode_file_stem_candidates(
            show,
            season_number=season.number,
            episode_number=episode.number,
            quality=episode_file.quality,
            parts=NameParts.from_row(episode_file),
        )
        for stem in stems:
            for candidate in files_matching_stem(season_dir, stem):
                if candidate.suffix.lower() in {
                    ".mkv",
                    ".mp4",
                    ".avi",
                    ".mov",
                    ".m4v",
                    ".webm",
                    ".ts",
                    ".wmv",
                }:
                    return candidate
        return None

    async def reconcile_orphaned_failed_imports(self) -> int:
        """Heal "ghost" failed episode files whose library file is on disk.

        A torrent's post-import cleanup can detach an episode file (FK ``ON
        DELETE SET NULL``) right after an overlapping import stamped it
        ``failed_io`` — but the winning attempt had already published the real
        file. Such rows can never be retried (no torrent) and are invisible on
        the torrent-centric imports page, yet keep the dashboard "failed" badge
        lit. If the on-disk library file exists, the import genuinely
        succeeded: flip the row to ``imported`` so the count clears truthfully.
        """
        orphans = await self.show_repository.get_orphaned_failed_episode_files()
        healed = 0
        for ef in orphans:
            if await self.resolve_episode_file_path(ef) is None:
                continue
            await self.show_repository.update_episode_file_import_status(
                file_id=ef.id, status=ImportOutcome.imported, error=None
            )
            healed += 1
        if healed:
            log.info("Reconciled %d ghost failed episode import(s) to imported", healed)
        return healed

    async def import_episode_from_file(
        self,
        *,
        show: Show,
        season: Season,
        episode: Episode,
        source_file: Path,
        subtitle_files: list[Path] = (),
        torrent_id: TorrentId | None,
        variant: str = "",
        quality: Quality | None = None,
        existing_file_id: UUID | None = None,
    ) -> tuple[ImportOutcome, str | None]:
        """Link ``source_file`` to ``episode`` and persist the EpisodeFile row.

        Detection happens here, at import time, because the file is present:
        ``codec``/``hdr``/``source`` (and ``quality`` when not supplied) are
        read from ``source_file`` via mediainfo + filename parse. ``variant``
        is the free text the user entered; ``extra`` is an auto collision
        discriminator that keeps same-quality filenames unique on disk.

        ``subtitle_files``: optional pool of candidate subtitle files; each
        is re-matched via ``match_subtitle_file`` and linked next to the
        target video. Caller may pass the whole season pool — non-matches
        are skipped.

        ``quality``: caller-supplied (torrent flow uses search-time quality).
        ``None`` triggers detection from the source file.

        ``existing_file_id``: the pre-created link row (downloaded-torrent
        path) to finalize; ``None`` for scan/new imports, which insert a fresh
        row.
        """
        from datetime import datetime

        try:
            season_dir = self.get_root_season_directory(
                show=show, season_number=season.number, write=True
            )
            season_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            return ImportOutcome.failed_io, f"mkdir season dir: {exc}"

        from miramedia.database import release_session_before_external_io

        # mediainfo scan can take seconds on multi-GB files. Release session
        # so the asyncpg conn doesn't sit idle-in-TX through it. analyze_async
        # runs the scan in a worker thread (capped by MIRAMEDIA_MEDIAINFO_-
        # CONCURRENCY), so it never blocks the event loop / UI.
        await release_session_before_external_io(self.show_repository.db)
        info = await analyze_async(source_file, fallback_title=source_file.name)
        release = parse_release(source_file.name)

        chosen_quality = quality if quality is not None else info.quality
        codec = normalize_codec(info.video_codec or release.video_codec)
        hdr = bool(info.hdr)
        source = normalize_source(release.source)

        existing_files = await self.show_repository.get_episode_files_by_season_id(
            season_id=season.id
        )
        episode_rows = [
            f
            for f in existing_files
            if f.episode_id == episode.id and f.id != existing_file_id
        ]

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
            for f in episode_rows:
                for cand_stem in episode_file_stem_candidates(
                    show,
                    season_number=season.number,
                    episode_number=episode.number,
                    quality=f.quality,
                    parts=NameParts.from_row(f),
                ):
                    matches = [
                        p
                        for p in files_matching_stem(season_dir, cand_stem)
                        if is_video_file(p)
                    ]
                    if matches:
                        slot_paths[f.id] = matches[0]
                        slot_stems[f.id] = cand_stem
                        break
            dup_id = find_renamed_duplicate(source_file, slot_paths)
            if dup_id is not None:
                dup_row = next(f for f in episode_rows if f.id == dup_id)
                dup_stem = slot_stems[dup_id]

        # Collision discriminator: build the set of (quality, codec, variant,
        # extra) tuples already used by OTHER files for this episode, then bump
        # extra "2","3"… until ours is unique. The dup row we are about to
        # overwrite is excluded so it can reclaim its own tuple.
        taken = {
            (f.quality, f.codec, f.variant, f.extra)
            for f in episode_rows
            if dup_row is None or f.id != dup_row.id
        }
        extra = ""
        n = 2
        while (chosen_quality, codec, variant, extra) in taken:
            extra = str(n)
            n += 1

        parts = NameParts(
            codec=codec, hdr=hdr, source=source, variant=variant, extra=extra
        )
        stem = episode_file_stem(
            show,
            season_number=season.number,
            episode_number=episode.number,
            quality=chosen_quality,
            parts=parts,
        )
        target_video = (season_dir / stem).with_suffix(source_file.suffix)

        # The source already lives in the destination dir when scan drops files
        # straight into the canonical show folder. Hardlinking a new canonical
        # name onto that same inode leaves the original name behind as a visible
        # duplicate, so we rename the source in place instead of copying.
        source_in_place = source_file.parent.resolve() == target_video.parent.resolve()

        if dup_row is not None and not source_in_place:
            # Same content already on disk under ``dup_stem``: rename the whole
            # slot (video + sibling subtitles) to the new canonical name and
            # finalize the existing row. No copy, no duplicate.
            old_stem = dup_stem

            def _rename_slot() -> None:
                rename_media_slot(season_dir, old_stem, stem)

            await release_session_before_external_io(self.show_repository.db)
            await asyncio.to_thread(_rename_slot)
            await self.show_repository.finalize_episode_file_import(
                file_id=dup_row.id,
                quality=chosen_quality,
                codec=codec,
                hdr=hdr,
                source=source,
                variant=variant,
                extra=extra,
                status=ImportOutcome.imported,
            )
            await self._trigger_subtitle_search_for_episode(episode.id)
            from miramedia.media_state import refresh_media_state

            await refresh_media_state(self.show_repository.db, show_id=show.id)
            invalidate_disk_scan_cache()
            log.info("Renamed existing episode slot %r -> %r (dedup)", old_stem, stem)
            return ImportOutcome.imported, None

        def _link_video() -> None:
            if source_in_place:
                # Rename the in-library source (and its sibling subtitles) to the
                # canonical name; no second inode, no leftover original.
                rename_media_slot(season_dir, source_file.stem, stem)
                return
            ensure_free_space(target_video.parent, source_file.stat().st_size)
            import_file(target_file=target_video, source_file=source_file)

        # Hardlink is fast on same FS; cross-FS falls back to copy which
        # can take minutes for multi-GB files. Release session in both
        # cases — the cost is one extra checkout on the next write.
        await release_session_before_external_io(self.show_repository.db)
        try:
            await asyncio.to_thread(_link_video)
        except (DiskSpaceError, ImportConflictError) as exc:
            return ImportOutcome.failed_io, str(exc)

        def _link_subs() -> None:
            used: set[Path] = set()
            for sub in subtitle_files:
                sub_info = match_subtitle_file(
                    sub.name, season=season.number, episode=episode.number
                )
                if not sub_info:
                    continue
                lang_part = f".{sub_info.language}" if sub_info.language else ""
                flag_part = (
                    ".forced" if sub_info.forced else (".sdh" if sub_info.sdh else "")
                )
                target_sub = target_video.with_suffix(
                    f"{lang_part}{flag_part}.{sub_info.container}"
                )
                # Disambiguate collisions: two subs with the same lang+flag would
                # resolve to the same path and silently clobber each other.
                # Dedupe only within this batch (not against disk) so re-imports
                # stay idempotent given stable source ordering.
                if target_sub in used:
                    n = 2
                    while True:
                        candidate = target_video.with_suffix(
                            f"{lang_part}{flag_part}.{n}.{sub_info.container}"
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
            await release_session_before_external_io(self.show_repository.db)
            await asyncio.to_thread(_link_subs)

        now = datetime.now(UTC)
        if existing_file_id is not None:
            await self.show_repository.finalize_episode_file_import(
                file_id=existing_file_id,
                quality=chosen_quality,
                codec=codec,
                hdr=hdr,
                source=source,
                variant=variant,
                extra=extra,
                status=ImportOutcome.imported,
            )
        else:
            await self.show_repository.add_episode_file(
                episode_file=EpisodeFile(
                    episode_id=episode.id,
                    quality=chosen_quality,
                    codec=codec,
                    hdr=hdr,
                    source=source,
                    variant=variant,
                    extra=extra,
                    torrent_id=torrent_id,
                    import_status=ImportOutcome.imported,
                    imported_at=now,
                    last_attempt_at=now,
                    attempt_count=1,
                )
            )

        await self._trigger_subtitle_search_for_episode(episode.id)
        from miramedia.media_state import refresh_media_state

        await refresh_media_state(self.show_repository.db, show_id=show.id)
        invalidate_disk_scan_cache()
        return ImportOutcome.imported, None

    async def _trigger_subtitle_search_for_episode(self, episode_id: EpisodeId) -> None:
        """Best-effort native subtitle search for a just-imported episode.

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
                subtitle_repository=SubtitleRepository(self.show_repository.db),
                show_service=self,
            )
            downloaded = await subtitle_service.search_episode_subtitles(episode_id)
            if downloaded:
                log.info(
                    f"Downloaded subtitles {downloaded} for episode {episode_id} after import"
                )
        except Exception:
            log.exception(
                f"Subtitle search failed for episode {episode_id} after import"
            )

    async def import_show_from_torrent(self, show: Show, torrent: Torrent) -> None:
        """Public import entry point. Runs the import then notifies the imports
        queue so the dashboard reflects the new file statuses (incl. the Done
        tab for a fully-imported torrent). The sync fires on every path —
        success, partial, failed_io, early-return — via ``finally``.
        """
        try:
            await self._run_import_show_from_torrent(show=show, torrent=torrent)
        finally:
            # Full (debounced) rebuild rather than a targeted torrent sync:
            # cleanup_after_import may have deleted the torrent, in which case
            # the imported files surface as a torrent-independent Done entry.
            from miramedia.imports.queue_hooks import schedule_import_queue_rebuild

            schedule_import_queue_rebuild()

    async def _run_import_show_from_torrent(self, show: Show, torrent: Torrent) -> None:
        """Map torrent files onto the pre-created EpisodeFile rows for this
        torrent. Per-row: match a video via guessit, then delegate to
        ``import_episode_from_file``.
        """
        video_files, subtitle_files, _all_files = await asyncio.to_thread(
            get_files_for_import, get_torrent_filepath(torrent=torrent)
        )
        # Warm the guessit cache off-loop so the per-episode match loop below
        # (match_episode_file → parse_release) hits cache instead of blocking
        # the event loop with synchronous guessit calls. See the matching note
        # in import_show_from_directory.
        await asyncio.to_thread(lambda: [parse_release(f.name) for f in video_files])

        outcomes: list[ImportOutcome] = []

        log.debug(
            f"Importing these {len(video_files)} files:\n" + pprint.pformat(video_files)
        )

        episode_files = await self.torrent_service.get_episode_files_of_torrent(
            torrent=torrent
        )
        if not episode_files:
            log.warning(
                f"No episode files associated with torrent {torrent.title}, skipping import."
            )
            return

        log.info(
            f"Found {len(episode_files)} episode files associated with torrent {torrent.title}"
        )

        if not video_files:
            log.error(
                f"No video files found in source for show {show.name}; marking failed_io."
            )
            if self.notification_service:
                await self.notification_service.send_notification_to_all_providers(
                    title="Source Files Missing",
                    message=(
                        f"No video files on disk for show {show.name}. "
                        "Re-download or remove the torrent via Imports."
                    ),
                )
            for ef in episode_files:
                await self.show_repository.update_episode_file_import_status(
                    file_id=ef.id,
                    status=ImportOutcome.failed_io,
                    error="Source files missing on disk.",
                )
            return

        imported_episodes_by_season: dict[int, list[int]] = {}

        for episode_file in episode_files:
            season = await self.get_season_by_episode(
                episode_id=episode_file.episode_id
            )
            episode = await self.get_episode(episode_file.episode_id)

            anime_absolute = getattr(episode, "absolute_number", None)
            matched_video = next(
                (
                    f
                    for f in video_files
                    if match_episode_file(
                        f.name,
                        season=season.number,
                        episode=episode.number,
                        anime_absolute=anime_absolute,
                    )
                ),
                None,
            )

            # Season 0 specials rarely carry S00E00 markers — fall back to
            # title-overlap matching (mirrors the download-time search).
            if matched_video is None and season.number == 0:
                matched_video = next(
                    (
                        f
                        for f in video_files
                        if match_special_file(
                            f.name,
                            episode_title=episode.title,
                            show_name=show.name,
                            accept_lone_file=len(video_files) == 1,
                        )
                    ),
                    None,
                )

            if matched_video is None:
                await self.show_repository.update_episode_file_import_status(
                    file_id=episode_file.id,
                    status=ImportOutcome.failed_no_match,
                    error="No matching video file",
                )
                if self.notification_service:
                    await self.notification_service.send_notification_to_all_providers(
                        title="Missing Episode File",
                        message=(
                            f"No video file found for S{season.number:02d}E{episode.number:02d} "
                            f"for show {show.name}. Manual intervention may be required."
                        ),
                    )
                log.warning(
                    f"File for S{season.number}E{episode.number} not found when trying to import episode for show {show.name}."
                )
                outcomes.append(ImportOutcome.failed_no_match)
                continue

            outcome, error = await self.import_episode_from_file(
                show=show,
                season=season,
                episode=episode,
                source_file=matched_video,
                subtitle_files=subtitle_files,
                torrent_id=torrent.id,
                variant=episode_file.variant,
                # quality omitted on purpose: detect it from the actual file
                # (mediainfo) so torrent imports name files identically to scan
                # imports. The indexer's claimed quality can be wrong.
                existing_file_id=episode_file.id,
            )

            if outcome != ImportOutcome.imported:
                await self.show_repository.update_episode_file_import_status(
                    file_id=episode_file.id,
                    status=outcome,
                    error=error,
                )
            outcomes.append(outcome)

            if outcome == ImportOutcome.imported:
                imported_episodes_by_season.setdefault(season.number, []).append(
                    episode.number
                )
                log.info(
                    f"Episode {episode.number} from Season {season.number} successfully imported from torrent {torrent.title}"
                )
            else:
                log.warning(
                    f"Episode {episode.number} from Season {season.number} failed ({outcome}) from torrent {torrent.title}: {error}"
                )

        success_messages: list[str] = []
        for season_number, episodes in imported_episodes_by_season.items():
            episode_list = ",".join(str(e) for e in sorted(episodes))
            success_messages.append(
                f"Episode(s): {episode_list} from Season {season_number}"
            )

        episodes_summary = "; ".join(success_messages)
        all_imported = bool(outcomes) and all(
            o == ImportOutcome.imported for o in outcomes
        )

        # Snapshot the outcome into the durable history log BEFORE any
        # cleanup_after_import removes the live torrent row.
        await self.torrent_service.record_import_history(torrent)

        if all_imported:
            if self.notification_service:
                await self.notification_service.send_notification_to_all_providers(
                    title="TV Show imported successfully",
                    message=(
                        f"Successfully imported {episodes_summary} "
                        f"of {show.name} ({show.year}) "
                        f"from torrent {torrent.title}."
                    ),
                )

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
            if self.notification_service:
                await self.notification_service.send_notification_to_all_providers(
                    title="Failed to import TV Show",
                    message=f"Importing {show.name} ({show.year}) from torrent {torrent.title} completed with errors. Please check the logs for details.",
                )

        log.info(
            f"Finished importing files for torrent {torrent.title} {'without' if all_imported else 'with'} errors"
        )

    async def refresh_metadata_with_fallback(self, show: Show) -> None:
        """Refresh a show's metadata using the best enabled provider.

        Order: original provider, then any enabled provider via IMDb ID, then
        exact-match search by name+year. All sync provider calls run in a
        thread so the event loop is not blocked. Session is released before
        each external HTTP fetch so the asyncpg connection doesn't sit
        idle-in-TX through provider latency.
        """
        from miramedia.database import release_session_before_external_io
        from miramedia.metadata.dependencies import (
            get_all_enabled_providers,
            get_metadata_provider,
        )

        try:
            provider = get_metadata_provider(show.metadata_provider)
        except Exception:
            provider = None

        if provider is not None:
            await self.update_show_metadata(db_show=show, metadata_provider=provider)
            return

        if show.imdb_id:
            for p in get_all_enabled_providers():
                await release_session_before_external_io(self.show_repository.db)
                fresh = await asyncio.to_thread(
                    p.get_show_metadata_by_imdb, show.imdb_id
                )
                if fresh:
                    await self.update_show_metadata(
                        db_show=show, metadata_provider=p, fresh_show_data=fresh
                    )
                    return

        target_name = show.name.lower().strip()
        for p in get_all_enabled_providers():
            try:
                await release_session_before_external_io(self.show_repository.db)
                results = await asyncio.to_thread(p.search_show, show.name)
            except Exception:  # noqa: S112 — best-effort provider fan-out, non-fatal; try next provider
                continue
            for result in results:
                if result.name.lower().strip() != target_name:
                    continue
                if show.year and result.year and result.year != show.year:
                    continue
                await release_session_before_external_io(self.show_repository.db)
                fresh = await asyncio.to_thread(p.get_show_metadata, result.external_id)
                if fresh:
                    await self.update_show_metadata(
                        db_show=show, metadata_provider=p, fresh_show_data=fresh
                    )
                    return

        msg = (
            f"Cannot refresh metadata: {show.metadata_provider} provider is not "
            "enabled and could not find a matching show on any enabled provider."
        )
        raise BadRequestError(msg)

    async def update_show_metadata(
        self,
        db_show: Show,
        metadata_provider: AbstractMetadataProvider,
        fresh_show_data: Show | None = None,
    ) -> Show | None:
        """
        Updates the metadata of a show.
        This includes adding new seasons and episodes if available from the metadata provider.
        It also updates existing show, season, and episode attributes if they have changed.

        :param metadata_provider: The metadata provider object to fetch fresh data from.
        :param db_show: The Show to update
        :param fresh_show_data: Pre-fetched metadata. If None, fetches from provider using db_show.external_id.
        :return: The updated Show object, or None if the show is not found or an error occurs.
        """
        from miramedia.database import release_session_before_external_io

        log.debug(f"Found show: {db_show.name} for metadata update.")

        if fresh_show_data is None:
            # Release session before the slow metadata HTTP fetch so the
            # asyncpg connection doesn't sit idle-in-TX while the provider
            # responds. Session re-checks out on the next statement (the
            # update_show_attributes write below).
            await release_session_before_external_io(self.show_repository.db)
            # Use stored original_language preference for metadata fetching
            fresh_show_data = await asyncio.to_thread(
                partial(
                    metadata_provider.get_show_metadata,
                    show_id=db_show.external_id,
                    language=db_show.original_language,
                )
            )
        if not fresh_show_data:
            log.warning(
                f"Could not fetch fresh metadata for show {db_show.name} ({db_show.metadata_provider}: {db_show.external_id}) from {db_show.metadata_provider}."
            )
            return None
        log.debug(f"Fetched fresh metadata for show: {fresh_show_data.name}")

        # Only update external_id if the provider matches — a fallback provider
        # returns IDs in a different format that would break directory matching.
        new_external_id = (
            fresh_show_data.external_id
            if metadata_provider.name == db_show.metadata_provider
            else None
        )

        await self.show_repository.update_show_attributes(
            show_id=db_show.id,
            name=fresh_show_data.name,
            overview=fresh_show_data.overview,
            year=fresh_show_data.year,
            ended=fresh_show_data.ended,
            external_id=new_external_id,
            imdb_id=fresh_show_data.imdb_id,
            continuous_download=db_show.continuous_download
            if fresh_show_data.ended is False
            else False,
            vote_average=fresh_show_data.vote_average,
            content_rating=fresh_show_data.content_rating,
            genres=fresh_show_data.genres,
            cast=fresh_show_data.cast,
        )

        # Track whether new seasons/episodes were added (poster may have changed)
        poster_refresh_needed = False

        # Process seasons and episodes. Match on season/episode NUMBER (the DB
        # natural key — UNIQUE(show_id, number) / UNIQUE(season_id, number)),
        # not provider external_id. This keeps refresh idempotent even when the
        # active provider changes (e.g. native flips Cinemeta↔TVMaze), whose id
        # formats differ — matching by external_id would create duplicate
        # seasons/episodes on the next refresh.
        existing_seasons_by_number = {s.number: s for s in db_show.seasons}

        for fresh_season_data in fresh_show_data.seasons:
            if fresh_season_data.number in existing_seasons_by_number:
                # Season already exists (matched by number). Seasons carry no
                # mutable attributes of their own anymore — just reconcile the
                # episodes below.
                existing_season = existing_seasons_by_number[fresh_season_data.number]

                # Process episodes for this season
                existing_episodes_by_number = {
                    ep.number: ep for ep in existing_season.episodes
                }
                for fresh_episode_data in fresh_season_data.episodes:
                    if fresh_episode_data.number in existing_episodes_by_number:
                        # Update existing episode
                        existing_episode = existing_episodes_by_number[
                            fresh_episode_data.number
                        ]

                        await self.show_repository.update_episode_attributes(
                            episode_id=existing_episode.id,
                            title=fresh_episode_data.title,
                            overview=fresh_episode_data.overview,
                            air_date=fresh_episode_data.air_date,
                        )
                    else:
                        # Add new episode — inherit skipped from season
                        log.debug(
                            f"Adding new episode {fresh_episode_data.number} to season {existing_season.number}"
                        )
                        episode_schema = Episode(
                            id=EpisodeId(fresh_episode_data.id),
                            number=fresh_episode_data.number,
                            title=fresh_episode_data.title,
                            overview=fresh_episode_data.overview,
                        )
                        await self.show_repository.add_episode_to_season(
                            season_id=existing_season.id,
                            episode_data=episode_schema,
                            skipped=existing_season.skipped,
                        )
            else:
                # Add new season (and its episodes)
                # Mark as skipped if continuous_download is effectively off
                poster_refresh_needed = True
                log.debug(
                    f"Adding new season {fresh_season_data.number} to show {db_show.name}"
                )
                global_cd = MiraMediaConfig().misc.continuous_download
                effective_cd = (
                    db_show.continuous_download
                    if db_show.continuous_download is not None
                    else global_cd
                )
                specials_enabled = MiraMediaConfig().misc.download_specials
                if fresh_season_data.number == 0 and not specials_enabled:
                    skip_new = True  # Specials default to skipped unless enabled
                else:
                    skip_new = not effective_cd

                episodes_for_schema = [
                    Episode(
                        id=EpisodeId(ep_data.id),
                        number=ep_data.number,
                        title=ep_data.title,
                        overview=ep_data.overview,
                    )
                    for ep_data in fresh_season_data.episodes
                ]

                season_schema = Season(
                    id=SeasonId(fresh_season_data.id),
                    number=fresh_season_data.number,
                    episodes=episodes_for_schema,
                )
                await self.show_repository.add_season_to_show(
                    show_id=db_show.id, season_data=season_schema, skipped=skip_new
                )

        updated_show = await self.show_repository.get_show_by_id(show_id=db_show.id)

        from miramedia.metadata.utils import poster_exists

        _poster_exists = poster_exists(metadata_provider.storage_path, updated_show.id)
        if poster_refresh_needed or not _poster_exists:
            log.debug(
                f"Poster download for {updated_show.name}: "
                f"new_season={poster_refresh_needed}, missing={not _poster_exists}"
            )
            try:
                await asyncio.to_thread(
                    partial(
                        metadata_provider.download_show_poster_image, show=updated_show
                    )
                )
            except Exception:
                log.warning(
                    f"Failed to download poster for show: {updated_show.name}",
                    exc_info=True,
                )
        log.info(f"Updated metadata for show: {updated_show.name}")
        return updated_show

    async def set_show_continuous_download(
        self, show: Show, continuous_download: bool | None
    ) -> Show:
        """
        Set the continuous download flag for a show.

        :param show: The show object.
        :param continuous_download: True/False to override, None to use global default.
        :return: The updated Show object.
        """
        show, _ = await self.show_repository.update_show_attributes(
            show_id=show.id, continuous_download=continuous_download
        )
        return show

    async def set_show_preferred_quality(
        self, show: Show, preferred_quality: list[str] | None
    ) -> Show:
        show, _ = await self.show_repository.update_show_attributes(
            show_id=show.id, preferred_quality=preferred_quality
        )
        return show

    async def set_show_preferred_codec(
        self, show: Show, preferred_codec: list[str] | None
    ) -> Show:
        show, _ = await self.show_repository.update_show_attributes(
            show_id=show.id, preferred_codec=preferred_codec
        )
        return show

    async def set_show_subtitle_languages(
        self, show: Show, subtitle_languages: list[str] | None
    ) -> Show:
        show, _ = await self.show_repository.update_show_attributes(
            show_id=show.id, subtitle_languages=subtitle_languages
        )
        return show

    def _get_effective_preferences(
        self, show: Show
    ) -> tuple[list[str] | None, list[str] | None]:
        """Return per-show quality/codec preferences.

        Tri-state list semantics:
          - ``None``: no per-show override — keep every enabled-matching
            result and apply the option's ``score_modifier`` (the implicit
            global default derived from the quality/codec rules).
          - ``[]``: explicit "Any" — keep every enabled-matching result but
            neutralize quality/codec score contribution.
          - non-empty list: whitelist those option names; ``score_modifier``
            still applies.
        """
        q = list(show.preferred_quality) if show.preferred_quality is not None else None
        c = list(show.preferred_codec) if show.preferred_codec is not None else None
        return q, c

    async def import_show_from_directory(
        self, show: Show, source_directory: Path
    ) -> bool:
        """Dot-rename the source directory up-front so it stops shadowing the
        canonical show dir, then hardlink matching episodes into the canonical
        show dir. Returns ``True`` if at least one episode imported.

        Skip the dot-rename when ``source_directory`` *is* the canonical show
        dir — renaming it to ``.Foo`` would orphan the whole library. This is
        the path scan takes when files are dropped into an already-tracked
        show folder; hardlinking the canonical-stem target inside the same dir
        is a safe no-op (``import_file`` is inode-checked).
        """
        canonical_dir = self.get_root_show_directory(show=show, write=False)
        is_canonical = source_directory.absolute() == canonical_dir.absolute()  # noqa: ASYNC240 — cheap path resolution, intentional
        if not is_canonical and not source_directory.name.startswith("."):
            dot_path = source_directory.parent / ("." + source_directory.name)
            try:
                await asyncio.to_thread(source_directory.rename, dot_path)
            except Exception as e:
                log.exception(
                    f"Failed to mark source {source_directory} as imported (rename to {dot_path})"
                )
                raise RenameError from e
            source_directory = dot_path

        video_files, subtitle_files, _all_files = await asyncio.to_thread(
            partial(get_files_for_import, directory=source_directory)
        )

        # Warm the guessit cache for every video in ONE worker thread before the
        # episodexfile match loop. guessit is synchronous CPU work; called
        # inline on the event loop (match_episode_file → parse_release) across a
        # large show it blocks the loop for seconds and freezes the UI. Parsing
        # off-loop here means every subsequent parse_release on the loop is a
        # cheap cache hit. (lru_cache is thread-safe; the cache is process-wide.)
        await asyncio.to_thread(lambda: [parse_release(f.name) for f in video_files])

        any_imported = False
        total_matched = 0
        total_skipped_already_imported = 0
        for season in show.seasons:
            if getattr(season, "skipped", False):
                log.info(
                    "Scan import: season %s of show %s is skipped; not importing any episodes",
                    season.number,
                    show.name,
                )
                continue
            season_files = await self.show_repository.get_episode_files_by_season_id(
                season_id=season.id
            )

            # Inode set of already-linked files in this season. Source videos
            # whose inode matches one of these are already imported under a
            # different stem — skip them so we don't insert a duplicate row +
            # double-link. ``import_episode_from_file`` derives the collision
            # ``extra`` from the file's detected components, so the scan loop
            # no longer pre-computes any variant.
            existing_inodes: set[int] = set()
            for ef in season_files:
                try:
                    path = await self.resolve_episode_file_path(ef)
                except Exception:
                    path = None
                if path is None:
                    continue
                try:
                    existing_inodes.add(path.stat().st_ino)
                except OSError:
                    continue

            for episode in season.episodes:
                if getattr(episode, "skipped", False):
                    log.info(
                        "Scan import: episode S%02dE%02d of show %s is skipped; not importing",
                        season.number,
                        episode.number,
                        show.name,
                    )
                    continue
                anime_absolute = getattr(episode, "absolute_number", None)
                matched_videos = [
                    f
                    for f in video_files
                    if match_episode_file(
                        f.name,
                        season=season.number,
                        episode=episode.number,
                        anime_absolute=anime_absolute,
                    )
                ]
                # Season 0 specials: title-overlap fallback when no S00E00 marker.
                # Only accept a lone file when this season has a single special,
                # so a generic filename can't be claimed by the wrong special.
                if not matched_videos and season.number == 0:
                    lone = len(video_files) == 1 and len(season.episodes) == 1
                    matched_videos = [
                        f
                        for f in video_files
                        if match_special_file(
                            f.name,
                            episode_title=episode.title,
                            show_name=show.name,
                            accept_lone_file=lone,
                        )
                    ]
                if not matched_videos:
                    continue
                await asyncio.to_thread(
                    partial(
                        matched_videos.sort,
                        key=lambda p: p.stat().st_size,
                        reverse=True,
                    )
                )

                for video in matched_videos:
                    total_matched += 1
                    try:
                        if video.stat().st_ino in existing_inodes:
                            log.debug(
                                "Skipping %s: inode already imported for %s S%02dE%02d",
                                video,
                                show.name,
                                season.number,
                                episode.number,
                            )
                            total_skipped_already_imported += 1
                            continue
                    except OSError:
                        pass

                    outcome, _ = await self.import_episode_from_file(
                        show=show,
                        season=season,
                        episode=episode,
                        source_file=video,
                        subtitle_files=subtitle_files,
                        torrent_id=None,
                        variant="",
                    )
                    if outcome == ImportOutcome.imported:
                        any_imported = True

        # Treat "every matched video already linked" as success — nothing
        # new to do. Caller (scan task) records the dir as imported so the
        # next scan's _still_resolved snapshot drops it from the imports UI.
        return any_imported or (
            total_matched > 0 and total_skipped_already_imported == total_matched
        )

    async def import_all_torrents(self) -> None:
        """Iterate ready torrents and import each in a FRESH bg session.

        Session lifetime: ``self`` only spans the brief candidate-list read.
        Every per-torrent import (libtorrent RPC + mediainfo + hardlink + DB
        writeback + subtitle HTTP) runs against its own short-lived
        ``bg_show_service`` session so a slow torrent never pins the outer
        scheduler session for the whole batch.
        """
        from miramedia.database import bg_show_service

        # Heal detached "ghost" failures first, in their own short-lived
        # session, so the disk globbing never pins the outer scheduler session.
        async with bg_show_service() as svc:
            await svc.reconcile_orphaned_failed_imports()

        torrents = await self.torrent_service.get_all_torrents()
        finished = [t for t in torrents if t.status == TorrentStatus.finished]
        ready_ids: list[tuple[TorrentId, str]] = []
        if finished:
            # Batch the imported-check (2 queries total) instead of 2 per
            # torrent — this sweep runs every minute and most finished torrents
            # are already imported, so the old N-query pattern was constant
            # background DB load on the NAS. Only the non-imported minority pays
            # the per-torrent is_due_for_retry.
            imported_map = await self.torrent_service.bulk_check_torrents_imported(
                [t.id for t in finished]
            )
            for t in finished:
                if imported_map.get(t.id, False):
                    continue
                if not await self.torrent_service.is_due_for_retry(t):
                    continue
                ready_ids.append((t.id, t.title))

        imported_count = 0
        for torrent_id, torrent_title in ready_ids:
            show = None
            try:
                async with bg_show_service() as fresh_show_service:
                    # Re-fetch the torrent on the fresh session so any
                    # writeback inside ``import_show_from_torrent`` operates
                    # on a row attached to THIS session (not the stale one).
                    t = await fresh_show_service.torrent_service.torrent_repository.get_torrent_by_id(
                        torrent_id=torrent_id
                    )
                    if t is None:
                        continue
                    show = await fresh_show_service.torrent_service.get_show_of_torrent(
                        torrent=t
                    )
                    if show is None:
                        continue
                    await fresh_show_service.import_show_from_torrent(
                        torrent=t, show=show
                    )
                imported_count += 1
            except Exception as e:
                # Broad on purpose — see MovieService.import_all_torrents. A
                # non-RuntimeError raise here used to escape the sweep silently
                # while leaving files at attempt_count==0, so is_due_for_retry
                # stayed True forever (a tight retry loop with no diagnostics).
                show_name = show.name if show is not None else "<unknown>"
                log.error(
                    f"Error importing torrent {torrent_title} for show {show_name}: {e}",
                    exc_info=True,
                )
                await self._mark_torrent_import_failed(
                    torrent_id, "Import raised; see logs."
                )
        if imported_count:
            invalidate_disk_scan_cache()
            log.info(f"Imported {imported_count} show torrent(s)")

    async def _mark_torrent_import_failed(
        self, torrent_id: TorrentId, error: str
    ) -> None:
        """Stamp every episode file of ``torrent_id`` failed_io in a fresh
        session so backoff engages after a raising import (see
        MovieService._mark_torrent_import_failed)."""
        from miramedia.database import bg_show_service

        try:
            async with bg_show_service() as svc:
                files = await svc.torrent_service.torrent_repository.get_episode_files_of_torrent(
                    torrent_id=torrent_id
                )
                for f in files:
                    if f.import_status == ImportOutcome.imported:
                        continue
                    await svc.show_repository.update_episode_file_import_status(
                        file_id=f.id, status=ImportOutcome.failed_io, error=error
                    )
        except Exception:
            log.exception("Failed to mark torrent %s import failed", torrent_id)

    async def update_all_shows_metadata(self) -> None:
        """Thin wrapper around :func:`_update_all_shows_metadata_impl`.

        Per-iteration session lifetime lives in the module-level helper so
        the same code path is safe whether called from an existing service
        instance or from a scheduler task that opens its own fresh
        ``bg_show_service``.
        """
        await _update_all_shows_metadata_impl()

    async def auto_download_missing_episodes(self) -> None:
        """Thin wrapper around :func:`_auto_download_missing_episodes_impl`.

        Per-iteration session lifetime lives in the module-level helper so
        the same code path is safe whether called from an existing service
        instance or from a scheduler task that opens its own fresh
        ``bg_show_service``.
        """
        await _auto_download_missing_episodes_impl()


async def _auto_download_for_show_impl(show: Show, max_downloads: int) -> None:
    """Self-contained auto-download for a single show.

    Manages its own ``bg_show_service`` sessions: a short ``snap`` session
    for pre-loop snapshot reads (active torrents, missing episodes), then
    per-iteration ``iter_svc`` for each season-pack / episode indexer fan-
    out, and per-write ``backoff_svc`` for backoff updates. The caller is
    NOT required to hold any session — that's the point: holding a session
    through the slow indexer gather would leave the connection ``idle in
    transaction`` long enough for Postgres ``idle_in_transaction_session_
    timeout`` to reap it and surface as ``InterfaceError: connection is
    closed`` (plus ``PendingRollbackError`` on the held session's commit).
    """
    from datetime import UTC, datetime, timedelta

    from miramedia.database import bg_show_service

    # Defense in depth: respect both the skip flag and the per-show
    # continuous_download flag even when a caller forgets to pre-filter.
    if show.skipped:
        log.debug(f"Auto-download: skipping {show.name} (show marked as skipped)")
        return
    # Explicit False wins over the global default; only None falls back.
    global_cd = MiraMediaConfig().misc.continuous_download
    if show.continuous_download is False:
        log.debug(f"Auto-download: skipping {show.name} (continuous_download disabled)")
        return
    if show.continuous_download is None and not global_cd:
        log.debug(
            f"Auto-download: skipping {show.name} (no per-show override, global disabled)"
        )
        return

    # Honor per-show backoff set after a sweep where every candidate was
    # deny-listed. Avoids burning the indexer fan-out + CF bypass on
    # releases we already know are bad.
    now_utc = datetime.now(UTC)
    if (
        show.auto_download_backoff_until is not None
        and show.auto_download_backoff_until > now_utc
    ):
        log.debug(
            "Auto-download: skipping %s (backoff until %s)",
            show.name,
            show.auto_download_backoff_until.isoformat(),
        )
        return

    # Compare air dates against the server's *local* calendar date (matches the
    # original date.today() semantics). air_date is a tz-naive calendar date, so
    # use local — not UTC — to avoid skipping an episode that has aired locally.
    today = now_utc.astimezone().date()
    missing_by_season: dict[int, list[int]] = {}

    # Pre-loop snapshot. Short session — closed BEFORE the slow indexer
    # loop, so its connection cannot sit idle-in-TX during the gather.
    async with bg_show_service() as snap:
        active_torrents = await snap.show_repository.get_torrents_by_show_id(
            show_id=show.id
        )
        active_count = 0
        for t in active_torrents:
            if not await snap.torrent_service.is_torrent_imported(t):
                active_count += 1
        if active_count > 0:
            log.debug(
                f"Auto-download: show {show.name} has {active_count} active downloads, skipping"
            )
            return

        # Gather missing episodes grouped by season. Skip not-yet-aired
        # episodes — chasing a release that doesn't exist yet just hits
        # indexers for nothing. Manual search still works on demand for
        # users who want to grab early-leaked rips.
        for season in show.seasons:
            if season.skipped:
                continue  # Respect per-season skip (specials default skipped)
            for episode in season.episodes:
                if episode.skipped:
                    continue  # Respect per-episode skip
                if episode.air_date is not None and episode.air_date > today:
                    continue  # Future episode — don't auto-search
                if not await snap.is_episode_downloaded(
                    episode=episode, season=season, show=show
                ):
                    # Check if there's already an EpisodeFile record (download pending)
                    existing_files = (
                        await snap.show_repository.get_episode_files_by_episode_id(
                            episode_id=episode.id
                        )
                    )
                    if existing_files:
                        continue
                    missing_by_season.setdefault(season.number, []).append(
                        episode.number
                    )

    if not missing_by_season:
        log.debug(f"Auto-download: no missing episodes for {show.name}")
        return

    log.info(
        f"Auto-download: {show.name} has missing episodes in {len(missing_by_season)} season(s)"
    )

    downloads_started = 0
    # Track across all season+episode searches whether every result was
    # deny-listed. Drives the per-show auto-download backoff at the end.
    had_blocked_only = False
    had_unblocked = False

    for season_number in sorted(missing_by_season.keys()):
        missing_episodes = sorted(missing_by_season[season_number])
        if downloads_started >= max_downloads:
            break

        season = next((s for s in show.seasons if s.number == season_number), None)
        if season is None:
            continue

        total_episodes = len(season.episodes)
        missing_count = len(missing_episodes)

        # If more than half the season is missing, try a season pack first.
        # Specials (Season 0) have no "season pack" concept — search per-episode.
        if season_number != 0 and missing_count > total_episodes / 2:
            try:
                async with bg_show_service() as iter_svc:
                    results = await iter_svc.get_all_available_torrents_for_a_season(
                        season_number=season_number, show_id=show.id
                    )
                    # Filter to season packs (no specific episode)
                    season_packs = [r for r in results if not r.episode]
                    raw_count = len(season_packs)
                    season_packs = await iter_svc.torrent_service.filter_deny_listed(
                        season_packs
                    )
                    if season_packs:
                        had_unblocked = True
                    elif raw_count:
                        had_blocked_only = True
                    if season_packs:
                        picked = await iter_svc._auto_download_first_valid(
                            results=season_packs,
                            show=show,
                            label=f"season pack S{season_number:02d}",
                        )
                        if picked is not None:
                            downloads_started += 1
                            if iter_svc.notification_service:
                                await iter_svc.notification_service.send_notification_to_all_providers(
                                    title="Auto-download started",
                                    message=(
                                        f"Downloading season pack S{season_number:02d} "
                                        f"of {show.name} ({show.year}): {picked.title}"
                                    ),
                                )
                            continue
            except Exception:
                log.exception(
                    f"Auto-download: failed to find season pack for {show.name} S{season_number:02d}"
                )

        # Download individual episodes
        for ep_number in missing_episodes:
            if downloads_started >= max_downloads:
                break
            try:
                async with bg_show_service() as iter_svc:
                    results = await iter_svc.get_all_available_torrents_for_an_episode(
                        season_number=season_number,
                        episode_number=ep_number,
                        show_id=show.id,
                    )
                    raw_count = len(results)
                    results = await iter_svc.torrent_service.filter_deny_listed(results)
                    if results:
                        had_unblocked = True
                    elif raw_count:
                        had_blocked_only = True
                    if not results:
                        log.debug(
                            f"Auto-download: no results for {show.name} "
                            f"S{season_number:02d}E{ep_number:02d}"
                        )
                        continue

                    picked = await iter_svc._auto_download_first_valid(
                        results=results,
                        show=show,
                        label=f"S{season_number:02d}E{ep_number:02d}",
                        # Specials are matched by title, not S00E00 — pin the
                        # link target so the EpisodeFile is created regardless.
                        episode_target=(
                            (season_number, ep_number) if season_number == 0 else None
                        ),
                    )
                    if picked is None:
                        continue
                    downloads_started += 1

                    if iter_svc.notification_service:
                        await iter_svc.notification_service.send_notification_to_all_providers(
                            title="Auto-download started",
                            message=(
                                f"Downloading {show.name} ({show.year}) "
                                f"S{season_number:02d}E{ep_number:02d}: {picked.title}"
                            ),
                        )
            except Exception:
                log.exception(
                    f"Auto-download: failed to download {show.name} "
                    f"S{season_number:02d}E{ep_number:02d}"
                )

    # Per-show backoff decision after all season+episode sweeps:
    # * any download started → clear backoff so future sweeps fire normally.
    # * no downloads, every search this run was 100% deny-listed → back off.
    # * mixed (some unblocked candidates existed but none were picked, or
    #   no candidates at all) → leave existing backoff state alone.
    try:
        if downloads_started > 0:
            if show.auto_download_backoff_until is not None:
                async with bg_show_service() as backoff_svc:
                    await backoff_svc.show_repository.set_auto_download_backoff(
                        show.id, None
                    )
        elif had_blocked_only and not had_unblocked:
            backoff_hours = max(
                MiraMediaConfig().misc.auto_download_interval_hours * 2,
                12,
            )
            until = now_utc + timedelta(hours=backoff_hours)
            async with bg_show_service() as backoff_svc:
                await backoff_svc.show_repository.set_auto_download_backoff(
                    show.id, until
                )
            log.info(
                "Auto-download: %s — all candidate(s) deny-listed across sweeps, backing off until %s",
                show.name,
                until.isoformat(),
            )
    except Exception:
        log.exception(f"Auto-download: failed to update backoff state for {show.name}")


async def _try_auto_download_show_id_impl(
    show_id: ShowId, max_downloads: int = 5
) -> None:
    """Run one continuous-download iteration for a single show.

    Fetches the show in a short bg session, closes it, then delegates
    to the self-contained :func:`_auto_download_for_show_impl`. NO
    session is held during the slow indexer fan-out, so the connection
    cannot sit idle-in-TX through cloudflare bypass + parallel HTTP.

    Swallows iteration errors so callers (loops) don't abort mid-sweep.
    """
    from miramedia.database import bg_show_service

    try:
        async with bg_show_service() as svc:
            # Re-fetch in this short session so we observe any state
            # changes (skipped, continuous_download toggled off, ...)
            # since the snapshot.
            fresh = await svc.show_repository.get_show_by_id(show_id=show_id)
        if fresh is None:
            return
        await _auto_download_for_show_impl(fresh, max_downloads)
    except Exception:
        log.exception("Auto-download: error processing show id=%s", show_id)


async def _auto_download_missing_episodes_impl() -> None:
    """Per-iteration ``bg_show_service`` worker for continuous-download shows.

    Session lifetime: the outer snapshot session is closed BEFORE the loop.
    Every per-show iteration opens its own short-lived ``bg_show_service``
    so a slow indexer fan-out (cloudflare bypass spinup, 60s timeout, etc.)
    can never pin the shared session past
    ``idle_in_transaction_session_timeout`` and poison subsequent iterations
    with ``InterfaceError: connection is closed``.

    Mirrors the round-3 ``import_all_torrents`` fix in this module.
    """
    from miramedia.database import bg_show_service

    global_default = MiraMediaConfig().misc.continuous_download

    # Phase 1: cheap snapshot of candidate IDs (single short bg session).
    async with bg_show_service() as svc:
        all_shows = await svc.show_repository.get_shows()
    candidates = [
        show
        for show in all_shows
        # Skipped shows are excluded regardless of the continuous_download
        # flag — the "Skip this show (prevent automatic downloads)" toggle
        # is meant to override everything else.
        if not show.skipped
        and (
            show.continuous_download is True
            or (show.continuous_download is None and global_default)
        )
    ]
    log.info(
        f"Auto-download: checking {len(candidates)} shows with continuous_download enabled"
    )

    max_downloads_per_show = 5

    # Phase 2: per-item processing with a FRESH bg session per iteration.
    for show in candidates:
        await _try_auto_download_show_id_impl(show.id, max_downloads_per_show)


def _metadata_failure_backoff_until() -> datetime:
    from datetime import timedelta

    hours = max(1, int(MiraMediaConfig().metadata.failure_backoff_hours))
    return datetime.now(UTC) + timedelta(hours=hours)


async def _mark_show_metadata_failure(show_id: ShowId, reason: str) -> None:
    """Stamp a failed metadata attempt so the scheduler backs off."""
    from miramedia.database import bg_show_service

    backoff_until = _metadata_failure_backoff_until()
    try:
        async with bg_show_service() as svc:
            await svc.show_repository.mark_metadata_failure(show_id, backoff_until)
        log.info(
            "Metadata refresh for show id=%s backed off until %s (%s)",
            show_id,
            backoff_until.isoformat(),
            reason,
        )
    except Exception:
        log.exception("Failed to mark show metadata failure for id=%s", show_id)


async def _try_update_show_metadata_id_impl(show_id: ShowId) -> None:
    """Refresh metadata for a single show in a fresh bg session.

    Opens its own ``bg_show_service``, so a slow provider HTTP call cannot
    pin a shared session past ``idle_in_transaction_session_timeout``.
    Swallows errors so a single bad show doesn't abort the bulk loop.
    """
    from miramedia.database import bg_show_service, release_session_before_external_io
    from miramedia.metadata.dependencies import resolve_metadata_provider

    try:
        async with bg_show_service() as svc:
            show = await svc.show_repository.get_show_by_id(show_id=show_id)
            if show is None:
                return
            metadata_provider = resolve_metadata_provider(show.metadata_provider)
            if not metadata_provider:
                log.warning(
                    f"No available metadata provider for show {show.name}, skipping update."
                )
                await svc.show_repository.mark_metadata_failure(
                    show.id, _metadata_failure_backoff_until()
                )
                return

            # Native-provider fallback for non-IMDb IDs — pre-fetch using imdb_id.
            fresh_data = None
            if (
                metadata_provider.name != show.metadata_provider
                and metadata_provider.name == "native"
                and not show.external_id.startswith("tt")
            ):
                if not show.imdb_id:
                    log.warning(
                        f"Cannot update {show.name}: native provider requires IMDb ID "
                        f"but show only has {show.metadata_provider} ID {show.external_id}"
                    )
                    await svc.show_repository.mark_metadata_failure(
                        show.id, _metadata_failure_backoff_until()
                    )
                    return
                log.info(
                    f"Using IMDb ID {show.imdb_id} instead of {show.metadata_provider} ID "
                    f"{show.external_id} for native provider lookup of {show.name}"
                )
                await release_session_before_external_io(svc.show_repository.db)
                fresh_data = await asyncio.to_thread(
                    partial(
                        metadata_provider.get_show_metadata,
                        show_id=show.imdb_id,
                        language=show.original_language,
                    )
                )

            updated_show = await svc.update_show_metadata(
                db_show=show,
                metadata_provider=metadata_provider,
                fresh_show_data=fresh_data,
            )
            if updated_show:
                await svc.show_repository.stamp_metadata_check(show.id)
                log.debug("Updated show metadata", extra={"show": updated_show.name})
            else:
                log.warning(f"Failed to update metadata for show id={show_id}")
                await svc.show_repository.mark_metadata_failure(
                    show.id, _metadata_failure_backoff_until()
                )
    except Exception as exc:
        from miramedia.metadata.utils import is_provider_unreachable

        if is_provider_unreachable(exc):
            log.warning(
                "Metadata provider unreachable for show id=%s: %s", show_id, exc
            )
            await _mark_show_metadata_failure(show_id, "provider unreachable")
        else:
            log.exception("Failed to update metadata for show id=%s", show_id)
            await _mark_show_metadata_failure(show_id, "unexpected failure")


async def _update_all_shows_metadata_impl() -> None:
    """Per-iteration ``bg_show_service`` worker for metadata refresh.

    Phase 1: snapshot candidate show IDs in one short session, then close.
    Phase 2: each show iteration opens its own fresh session via
    :func:`_try_update_show_metadata_id_impl`, so a slow provider HTTP
    timeout cannot pin a shared session past
    ``idle_in_transaction_session_timeout``.
    """
    from datetime import UTC, datetime, timedelta

    from miramedia.database import bg_show_service

    check_interval = timedelta(hours=MiraMediaConfig().metadata.check_interval_hours)
    now = datetime.now(UTC)

    cutoff = now - check_interval
    batch_size = 200
    total_checked = 0
    seen_ids: set[ShowId] = set()
    log.info("Updating metadata for shows due since %s", cutoff.isoformat())
    while True:
        async with bg_show_service() as svc:
            show_ids = await svc.show_repository.get_show_ids_due_for_metadata(
                older_than=cutoff, limit=batch_size
            )
        show_ids = [show_id for show_id in show_ids if show_id not in seen_ids]
        if not show_ids:
            break
        total_checked += len(show_ids)
        for show_id in show_ids:
            seen_ids.add(show_id)
            await _try_update_show_metadata_id_impl(show_id)
    if total_checked:
        log.info("Metadata refresh checked %d shows", total_checked)
