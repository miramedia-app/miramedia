import asyncio
import logging
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy.exc import IntegrityError

from miramedia.events.bus import Event, get_event_bus
from miramedia.exceptions import (
    ConflictError,
    MediaSkippedError,
    NotFoundError,
    NoVideoFilesError,
)
from miramedia.file_status import ImportOutcome
from miramedia.indexers.schemas import IndexerQueryResult
from miramedia.movies.schemas import Movie, MovieFile
from miramedia.shows.schemas import Episode, EpisodeFile, EpisodeNumber, Show
from miramedia.torrents.integrity import INTEGRITY_MISMATCH_MAX_LIMIT
from miramedia.torrents.manager import DownloadManager
from miramedia.torrents.repository import TorrentRepository
from miramedia.torrents.schemas import (
    ImportFileDetail,
    ImportProgress,
    ImportStatusCounts,
    ImportStatusEntry,
    ImportStatusFilter,
    IntegrityActionResult,
    IntegrityMismatch,
    MediaType,
    PaginatedIntegrityMismatches,
    Quality,
    RichTorrent,
    Torrent,
    TorrentId,
    TorrentMediaContext,
    TorrentStatus,
)

if TYPE_CHECKING:
    from miramedia.movies.repository import MovieRepository
    from miramedia.movies.service import MovieService
    from miramedia.shows.repository import ShowRepository
    from miramedia.shows.service import ShowService
    from miramedia.torrents.schemas import TorrentSourceFile

log = logging.getLogger(__name__)


def _torrent_rpc_concurrency_limit() -> int:
    """Read the live-status fan-out cap from env, default 10.

    Tuned to keep qBittorrent / libtorrent RPC queues responsive under
    big libraries — 500 simultaneous status RPCs can saturate the client.
    """
    raw = os.getenv("MIRAMEDIA_TORRENT_RPC_CONCURRENCY", "10")
    try:
        parsed = int(raw)
    except ValueError:
        log.warning(
            "Invalid MIRAMEDIA_TORRENT_RPC_CONCURRENCY=%r, falling back to 10", raw
        )
        return 10
    return max(1, parsed)


_TORRENT_RPC_CONCURRENCY = _torrent_rpc_concurrency_limit()


class TorrentService:
    def __init__(
        self,
        torrent_repository: TorrentRepository,
        download_manager: DownloadManager | None = None,
    ) -> None:
        self.torrent_repository = torrent_repository
        self.download_manager = download_manager or DownloadManager()

    async def get_episode_files_of_torrent(self, torrent: Torrent) -> list[EpisodeFile]:
        """
        Returns all episode files of a torrent
        :param torrent: the torrent to get the episode files of
        :return: list of episode files
        """
        return await self.torrent_repository.get_episode_files_of_torrent(
            torrent_id=torrent.id
        )

    async def get_show_of_torrent(self, torrent: Torrent) -> Show | None:
        """
        Returns the show of a torrent
        :param torrent: the torrent to get the show of
        :return: the show of the torrent
        """
        return await self.torrent_repository.get_show_of_torrent(torrent_id=torrent.id)

    async def get_movie_of_torrent(self, torrent: Torrent) -> Movie | None:
        """
        Returns the movie of a torrent
        :param torrent: the torrent to get the movie of
        :return: the movie of the torrent
        """
        return await self.torrent_repository.get_movie_of_torrent(torrent_id=torrent.id)

    async def download(self, indexer_result: IndexerQueryResult) -> Torrent:
        from miramedia.database import release_session_before_external_io

        log.info(f"Starting download for torrent: {indexer_result.title}")

        # Preflight: check the deny-list and (when payload is available) the
        # file list before handing the result to the download client. Skipped
        # for usenet. For pure magnets the file dict only arrives once we've
        # joined the swarm — the post-add verifier (kicked off after linking
        # + resume in download_and_link) handles that case.
        if not indexer_result.usenet:
            await self._preflight_reject(indexer_result)

        # Release session before the slow torrent-client RPC (libtorrent
        # magnet metadata fetch or qBit POST + handshake). Session
        # re-checks out on the save_torrent write below.
        await release_session_before_external_io(self.torrent_repository.db)
        torrent = await asyncio.to_thread(
            self.download_manager.download, indexer_result
        )

        # Belt-and-suspenders: the backend just computed the authoritative
        # info-hash for the torrent. If preflight missed it (e.g. magnet xt
        # parsed differently, .torrent fetch failed, deny-list write raced
        # under it), reject again here before the client invests any work.
        if not indexer_result.usenet and torrent.hash:
            if await self.torrent_repository.is_hash_blocked(torrent.hash):
                log.warning(
                    "Post-add gate: removing %s — info-hash %s is on the deny-list",
                    indexer_result.title,
                    torrent.hash,
                )
                try:
                    await asyncio.to_thread(
                        self.download_manager.remove_torrent, torrent, True
                    )
                except Exception:
                    log.exception(
                        "Failed to remove deny-listed torrent %s from client",
                        torrent.title,
                    )
                msg = f"Torrent {indexer_result.title!r} is on the deny-list"
                raise NoVideoFilesError(msg)

        saved = await self.torrent_repository.save_torrent(torrent=torrent)
        # Push to any connected SSE clients so the torrents dashboard can
        # invalidate its list query without waiting for the next poll tick.
        get_event_bus().publish(
            Event(type="torrent.created", data={"id": str(saved.id)})
        )
        return saved

    async def filter_deny_listed(
        self, results: list[IndexerQueryResult]
    ) -> list[IndexerQueryResult]:
        """Drop results whose magnet info-hash is on the deny-list.

        Only magnet URLs are checked — extracting the hash is a cheap local
        parse, no network. ``.torrent`` URL results are left untouched (the
        pre-add inspection in :meth:`download` will reject them if blocked,
        at the cost of one HTTP fetch per attempt).
        """
        if not results:
            return results
        import libtorrent

        hashes: dict[int, str] = {}
        for idx, r in enumerate(results):
            if not r.download_url.startswith("magnet:"):
                continue
            try:
                hashes[idx] = str(
                    libtorrent.parse_magnet_uri(r.download_url).info_hash
                ).lower()
            except Exception:  # noqa: S112 — best-effort, non-fatal
                continue

        if not hashes:
            return results

        blocked: set[str] = set()
        for h in set(hashes.values()):
            if await self.torrent_repository.is_hash_blocked(h):
                blocked.add(h)

        if not blocked:
            log.info(
                "Deny-list filter: checked %d magnet hash(es) against deny-list, none blocked. Hashes: %s",
                len(set(hashes.values())),
                sorted(set(hashes.values())),
            )
            return results

        kept: list[IndexerQueryResult] = []
        dropped = 0
        for idx, r in enumerate(results):
            h = hashes.get(idx)
            if h in blocked:
                log.info("Deny-list filter: dropping %r (hash=%s)", r.title, h)
                dropped += 1
                continue
            kept.append(r)
        if dropped:
            log.info(
                "Deny-list filter: dropped %d/%d magnet result(s)",
                dropped,
                len(results),
            )
        return kept

    async def _preflight_reject(self, indexer_result: IndexerQueryResult) -> str | None:
        """Run the deny-list + file inspection gates pre-add.

        Returns the info-hash if we managed to compute one — the caller uses
        it to decide whether post-add verification is still needed (only the
        magnet branch leaves the file list unknown).
        """
        from miramedia.database import release_session_before_external_io
        from miramedia.torrents.utils import has_meaningful_video, inspect_torrent

        # inspect_torrent may HTTP-fetch a .torrent payload + parse it.
        # Release the session through that fetch.
        await release_session_before_external_io(self.torrent_repository.db)
        inspection = await asyncio.to_thread(inspect_torrent, indexer_result)

        if inspection.info_hash and await self.torrent_repository.is_hash_blocked(
            inspection.info_hash
        ):
            log.warning(
                "Refusing torrent %r — info-hash %s is on the deny-list",
                indexer_result.title,
                inspection.info_hash,
            )
            msg = f"Torrent {indexer_result.title!r} is on the deny-list"
            raise NoVideoFilesError(msg)

        if inspection.files is not None and not has_meaningful_video(inspection.files):
            preview = (
                ", ".join(
                    f"{f.path.name} ({f.size / 1024 / 1024:.1f}MB)"
                    for f in inspection.files[:5]
                )
                or "<empty>"
            )
            log.warning(
                "Refusing torrent %r — no meaningful video in payload (%d files: %s)",
                indexer_result.title,
                len(inspection.files),
                preview,
            )
            if inspection.info_hash:
                await self.torrent_repository.add_blocked_hash(
                    inspection.info_hash,
                    title=indexer_result.title,
                    reason="no_meaningful_video",
                )
                log.info(
                    "Added %s (%s) to deny-list",
                    inspection.info_hash,
                    indexer_result.title,
                )
            msg = f"Torrent {indexer_result.title!r} contains no meaningful video files"
            raise NoVideoFilesError(msg)

        return inspection.info_hash

    def _spawn_post_add_verifier(self, torrent: Torrent) -> None:
        """Fire-and-forget background task; suppresses task warnings."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            log.debug("No running event loop — skipping post-add verifier")
            return
        task = loop.create_task(self._verify_torrent_has_video_files(torrent))
        task.add_done_callback(self._log_verifier_failure)

    @staticmethod
    def _log_verifier_failure(task: "asyncio.Task[None]") -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            log.exception("Post-add metadata verifier crashed", exc_info=exc)

    async def _verify_torrent_has_video_files(
        self,
        torrent: Torrent,
        *,
        timeout_seconds: float = 60.0,
        poll_interval_seconds: float = 2.0,
    ) -> None:
        """Poll the download client until metadata arrives, then enforce video.

        Fail-open on timeout — leaves the torrent running and logs at WARN.
        When the file list does arrive and contains no meaningful video
        (i.e. only sample/decoy clips alongside other payloads), the torrent
        is removed (data deleted), linked episode/movie file rows are
        dropped, and the info-hash is added to the deny-list so the same
        release can't be re-queued.
        """
        from miramedia.torrents.utils import has_meaningful_video

        log.info(
            "Post-add verifier: watching %s (hash=%s) for metadata",
            torrent.title,
            torrent.hash,
        )
        deadline = asyncio.get_event_loop().time() + timeout_seconds
        while True:
            try:
                files = await asyncio.to_thread(
                    self.download_manager.get_torrent_files, torrent
                )
            except Exception:
                log.exception(
                    "Post-add verifier: file lookup failed for %s",
                    torrent.title,
                )
                files = None

            if files is not None:
                preview = (
                    ", ".join(
                        f"{f.path.name} ({f.size / 1024 / 1024:.1f}MB)"
                        for f in files[:5]
                    )
                    or "<empty>"
                )
                if has_meaningful_video(files):
                    log.info(
                        "Post-add verifier: %s OK (%d files: %s)",
                        torrent.title,
                        len(files),
                        preview,
                    )
                    return
                log.warning(
                    "Post-add verifier: removing %s — no meaningful video (%d files: %s)",
                    torrent.title,
                    len(files),
                    preview,
                )
                await self._block_and_remove(torrent, reason="no_meaningful_video")
                return

            if asyncio.get_event_loop().time() >= deadline:
                log.warning(
                    "Post-add verifier: timeout (%ss) waiting on metadata for %s — failing open",
                    int(timeout_seconds),
                    torrent.title,
                )
                return
            await asyncio.sleep(poll_interval_seconds)

    async def _block_and_remove(self, torrent: Torrent, *, reason: str) -> None:
        """Deny-list the hash, drop linked rows, then remove the torrent.

        Runs from a fire-and-forget background task — uses
        :data:`SessionLocalBackground` instead of the request-scoped session
        so it keeps working after the caller's HTTP request finishes.
        """
        from miramedia.database import SessionLocalBackground
        from miramedia.movies.repository import MovieRepository
        from miramedia.shows.repository import ShowRepository

        if SessionLocalBackground is None:
            log.error(
                "SessionLocalBackground unavailable — cannot deny-list %s",
                torrent.hash,
            )
            return

        async with SessionLocalBackground() as db:
            torrent_repo = TorrentRepository(db)
            show_repo = ShowRepository(db)
            movie_repo = MovieRepository(db)
            try:
                await torrent_repo.add_blocked_hash(
                    torrent.hash, title=torrent.title, reason=reason
                )
                log.info(
                    "Added %s (%s) to deny-list (reason=%s)",
                    torrent.hash,
                    torrent.title,
                    reason,
                )
            except Exception:
                log.exception("Failed to add %s to torrent deny-list", torrent.hash)
            try:
                await show_repo.remove_episode_files_by_torrent_id(torrent.id)
            except Exception:
                log.exception(
                    "Failed to remove linked episode_file rows for %s", torrent.id
                )
            try:
                await movie_repo.remove_movie_files_by_torrent_id(torrent.id)
            except Exception:
                log.exception(
                    "Failed to remove linked movie_file rows for %s", torrent.id
                )
            try:
                await asyncio.to_thread(
                    self.download_manager.remove_torrent, torrent, True
                )
            except Exception:
                log.exception(
                    "Failed to remove torrent %s from client during eviction",
                    torrent.title,
                )
            try:
                await torrent_repo.delete_torrent(torrent_id=torrent.id)
            except Exception:
                log.exception(
                    "Failed to delete torrent row %s after deny-list eviction",
                    torrent.id,
                )
            try:
                await db.commit()
            except Exception:
                log.exception(
                    "Failed to commit deny-list eviction for %s", torrent.title
                )
        get_event_bus().publish(
            Event(type="torrent.deleted", data={"id": str(torrent.id)})
        )

    async def get_torrent_status(
        self, torrent: Torrent, persist: bool = True
    ) -> Torrent:
        """Refresh a torrent's live fields from the download client.

        Pass ``persist=False`` from hot loops (e.g. the list endpoint) to
        skip the per-torrent DB write — the scheduler's periodic refresh
        already persists state.
        """
        (
            new_status,
            new_progress,
            num_peers,
            num_seeds,
            dl_speed,
        ) = await asyncio.to_thread(self.download_manager.get_torrent_status, torrent)
        # Preserve user-initiated paused status; still update transient fields
        if torrent.status == TorrentStatus.paused:
            torrent.progress = new_progress
        # Don't overwrite a meaningful DB status with 'unknown' when the
        # download client can no longer find the torrent (e.g. after restart).
        elif (
            new_status != TorrentStatus.unknown
            or torrent.status == TorrentStatus.unknown
        ):
            torrent.status = new_status
            torrent.progress = new_progress
        torrent.num_peers = num_peers
        torrent.num_seeds = num_seeds
        torrent.download_speed = dl_speed
        if persist:
            await self.torrent_repository.save_torrent(torrent=torrent)
            # Only emit on the persist path — the unpersisted hot-path
            # (list endpoint fan-out) would flood the bus on every render
            # without changing anything clients can observe.
            get_event_bus().publish(
                Event(type="torrent.updated", data={"id": str(torrent.id)})
            )
        return torrent

    async def cancel_download(
        self, torrent: Torrent, delete_files: bool = False
    ) -> Torrent:
        """
        cancels download of a torrent

        :param delete_files: Deletes the downloaded files of the torrent too, deactivated by default
        :param torrent: the torrent to cancel
        """
        log.info(f"Cancelling download for torrent: {torrent.title}")
        await asyncio.to_thread(
            self.download_manager.remove_torrent, torrent, delete_files
        )
        return await self.get_torrent_status(torrent=torrent)

    async def pause_download(self, torrent: Torrent) -> Torrent:
        """
        Internal pause — used during linking. Not logged at INFO to avoid noise.
        """
        log.debug(f"Pausing download for torrent: {torrent.title}")
        await asyncio.to_thread(self.download_manager.pause_torrent, torrent)
        return await self.get_torrent_status(torrent=torrent)

    async def resume_download(self, torrent: Torrent) -> Torrent:
        """
        Internal resume — used after linking. Not logged at INFO to avoid noise.
        """
        log.debug(f"Resuming download for torrent: {torrent.title}")
        await asyncio.to_thread(self.download_manager.resume_torrent, torrent)
        return await self.get_torrent_status(torrent=torrent)

    async def user_pause_download(self, torrent: Torrent) -> Torrent:
        """Pause a torrent on behalf of the user (persists paused status)."""
        log.info(f"User pausing download for torrent: {torrent.title}")
        await asyncio.to_thread(self.download_manager.pause_torrent, torrent)
        torrent.status = TorrentStatus.paused
        saved = await self.torrent_repository.save_torrent(torrent=torrent)
        get_event_bus().publish(
            Event(type="torrent.updated", data={"id": str(saved.id)})
        )
        return saved

    async def user_resume_download(self, torrent: Torrent) -> Torrent:
        """Resume a user-paused torrent (clears paused status)."""
        log.info(f"User resuming download for torrent: {torrent.title}")
        await asyncio.to_thread(self.download_manager.resume_torrent, torrent)
        torrent.status = TorrentStatus.downloading
        await self.torrent_repository.save_torrent(torrent=torrent)
        # get_torrent_status will emit a torrent.updated event after its
        # own persist, so we don't double-publish here.
        return await self.get_torrent_status(torrent=torrent)

    async def get_all_torrents(self) -> list[Torrent]:
        all_db_torrents = await self.torrent_repository.get_all_torrents()
        if not all_db_torrents:
            return []

        # Parallel-fan-out live-status fetches, but capped: each call is a
        # libtorrent / qbittorrent RPC and unbounded gather() with a 500-
        # torrent library would queue 500 simultaneous RPCs against the
        # client. The semaphore keeps the fan-out at
        # ``MIRAMEDIA_TORRENT_RPC_CONCURRENCY`` (default 10) in-flight.
        # ``persist=False`` skips the per-list DB write — the scheduler's
        # refresh job owns persistence.
        sem = asyncio.Semaphore(_TORRENT_RPC_CONCURRENCY)

        async def _fetch(t: Torrent) -> Torrent:
            async with sem:
                try:
                    return await self.get_torrent_status(t, persist=False)
                except Exception:
                    return t

        torrents = await asyncio.gather(*(_fetch(t) for t in all_db_torrents))
        return list(torrents)

    async def get_torrent_by_id(self, torrent_id: TorrentId) -> Torrent:
        return await self.get_torrent_status(
            await self.torrent_repository.get_torrent_by_id(torrent_id=torrent_id)
        )

    async def delete_torrent(self, torrent_id: TorrentId) -> None:
        log.info(f"Deleting torrent with ID: {torrent_id}")
        # Per-file: the repository drops only the not-yet-imported ("queued")
        # file rows and leaves imported library media in place (its torrent FK
        # is SET NULL on delete). Partially-imported torrents therefore keep
        # the files that finished and discard the rest.
        await self.torrent_repository.delete_torrent(
            torrent_id=torrent_id, delete_associated_media_files=True
        )
        get_event_bus().publish(
            Event(type="torrent.deleted", data={"id": str(torrent_id)})
        )

    async def cleanup_torrent_if_orphaned(self, torrent_id: TorrentId) -> None:
        """Remove a torrent that just lost its last media link.

        Deleting an episode/movie file from a detail page severs the only
        link to an in-progress (not-yet-imported) torrent. Without this the
        torrent keeps running in the download client and shows as an
        "Unlinked" row on the torrents page. If any linked file remains, or
        the torrent has already been imported, it is left untouched.
        """
        from miramedia.exceptions import NotFoundError

        try:
            torrent = await self.torrent_repository.get_torrent_by_id(
                torrent_id=torrent_id
            )
        except NotFoundError:
            return

        ep_files = await self.torrent_repository.get_episode_files_of_torrent(
            torrent_id
        )
        mv_files = await self.torrent_repository.get_movie_files_of_torrent(torrent_id)
        if ep_files or mv_files:
            return  # still linked to other media — keep it

        try:
            await self.cancel_download(torrent=torrent, delete_files=True)
        except Exception:
            log.warning(
                f"Failed to stop orphaned torrent {torrent.hash} in client",
                exc_info=True,
            )
        await self.delete_torrent(torrent_id=torrent_id)
        log.info(f"Removed orphaned torrent after file delete: {torrent.title}")

    async def get_movie_files_of_torrent(self, torrent: Torrent) -> list[MovieFile]:
        return await self.torrent_repository.get_movie_files_of_torrent(
            torrent_id=torrent.id
        )

    @staticmethod
    def compute_import_progress_from_files(files: list) -> ImportProgress:
        """Aggregate import outcomes from a pre-fetched file list.

        Pure helper so callers that already loaded the per-torrent files
        (e.g. ``_build_import_status_entry``) don't re-query the DB.
        """
        if not files:
            return ImportProgress()
        progress = ImportProgress(total=len(files))
        for f in files:
            status = getattr(f, "import_status", ImportOutcome.pending)
            if status == ImportOutcome.imported:
                progress.imported += 1
            elif status == ImportOutcome.ambiguous:
                progress.ambiguous += 1
            elif status in (ImportOutcome.failed_no_match, ImportOutcome.failed_io):
                progress.failed += 1
                if not progress.last_error:
                    progress.last_error = getattr(f, "import_error", None)
            else:
                progress.pending += 1
            attempt = getattr(f, "last_attempt_at", None)
            if attempt and (
                progress.last_attempt_at is None or attempt > progress.last_attempt_at
            ):
                progress.last_attempt_at = attempt
        return progress

    async def compute_import_progress(self, torrent: Torrent) -> ImportProgress:
        """Aggregate per-file import outcomes into a single progress record."""
        # Serial — same AsyncSession; concurrent use raises asyncpg's
        # "another operation is in progress".
        episode_files = await self.torrent_repository.get_episode_files_of_torrent(
            torrent_id=torrent.id,
        )
        movie_files = await self.torrent_repository.get_movie_files_of_torrent(
            torrent_id=torrent.id,
        )
        return self.compute_import_progress_from_files(
            list(episode_files) + list(movie_files)
        )

    async def is_torrent_imported(self, torrent: Torrent) -> bool:
        """True iff every file linked to ``torrent`` reached the imported state."""
        return (await self.compute_import_progress(torrent)).all_imported

    async def bulk_check_torrents_imported(
        self, torrent_ids: list[TorrentId]
    ) -> dict[TorrentId, bool]:
        """Resolve imported-state for many torrents in 2 DB queries.

        Avoids the N-queries-per-file pattern used by media file list endpoints.
        Read-only — no torrent client RPC, no row writes.
        """
        if not torrent_ids:
            return {}
        unique_ids = list({tid for tid in torrent_ids if tid is not None})
        if not unique_ids:
            return {}
        episode_files = await self.torrent_repository.get_episode_files_for_torrents(
            unique_ids
        )
        movie_files = await self.torrent_repository.get_movie_files_for_torrents(
            unique_ids
        )
        by_torrent: dict[TorrentId, list] = {tid: [] for tid in unique_ids}
        for f in episode_files:
            if f.torrent_id in by_torrent:
                by_torrent[f.torrent_id].append(f)
        for f in movie_files:
            if f.torrent_id in by_torrent:
                by_torrent[f.torrent_id].append(f)
        return {
            tid: self.compute_import_progress_from_files(files).all_imported
            for tid, files in by_torrent.items()
        }

    def _bucket_progress(self, progress: ImportProgress) -> set[ImportStatusFilter]:
        """Categorise an :class:`ImportProgress` snapshot into filter buckets."""
        buckets: set[ImportStatusFilter] = {ImportStatusFilter.all}
        if progress.failed > 0:
            buckets.add(ImportStatusFilter.failed)
        if progress.ambiguous > 0:
            buckets.add(ImportStatusFilter.ambiguous)
        if progress.imported > 0 and progress.imported < progress.total:
            buckets.add(ImportStatusFilter.partial)
        if (
            progress.total > 0
            and progress.imported == 0
            and progress.failed == 0
            and progress.ambiguous == 0
        ):
            buckets.add(ImportStatusFilter.pending)
        if progress.last_attempt_at is not None:
            # Compare in aware UTC. The old code used naive utcnow() vs a
            # local-converted naive attempt, shifting the 24h "recent" window
            # by the host's UTC offset on a non-UTC box.
            cutoff = datetime.now(UTC) - timedelta(hours=24)
            attempt = progress.last_attempt_at
            if attempt.tzinfo is None:
                attempt = attempt.replace(tzinfo=UTC)
            if attempt >= cutoff:
                buckets.add(ImportStatusFilter.recent)
        return buckets

    def _compose_import_status_entry(
        self,
        torrent: Torrent,
        *,
        episode_files: list[EpisodeFile],
        movie_files: list[MovieFile],
        ep_lookup: dict[uuid.UUID, tuple[int, int]],
        show_ctx: dict | None,
        movie_ctx: dict | None,
    ) -> ImportStatusEntry:
        """Build one import-status row from pre-fetched relations."""
        progress = self.compute_import_progress_from_files(
            list(episode_files) + list(movie_files)
        )
        details: list[ImportFileDetail] = []
        media_context: TorrentMediaContext | None = None

        if episode_files and show_ctx is not None:
            seasons = sorted(
                {
                    ep_lookup[ef.episode_id][0]
                    for ef in episode_files
                    if ef.episode_id in ep_lookup
                }
            )
            episodes = sorted(
                {
                    ep_lookup[ef.episode_id][1]
                    for ef in episode_files
                    if ef.episode_id in ep_lookup
                }
            )
            media_context = TorrentMediaContext(
                media_type="show",
                media_id=show_ctx["show_id"],
                media_name=show_ctx["show_name"],
                media_year=show_ctx["show_year"],
                metadata_provider=show_ctx["metadata_provider"],
                seasons=seasons or None,
                episodes=episodes or None,
            )
            for ef in episode_files:
                if ef.episode_id in ep_lookup:
                    sn, en = ep_lookup[ef.episode_id]
                    label = f"S{sn:02d}E{en:02d}"
                else:
                    label = "Episode"
                details.append(
                    ImportFileDetail(
                        media_label=label,
                        variant=ef.variant,
                        quality=ef.quality,
                        import_status=ef.import_status,
                        import_error=ef.import_error,
                        last_attempt_at=ef.last_attempt_at,
                        imported_at=ef.imported_at,
                        attempt_count=ef.attempt_count,
                    )
                )

        if movie_files:
            if movie_ctx is not None:
                media_context = TorrentMediaContext(
                    media_type="movie",
                    media_id=movie_ctx["movie_id"],
                    media_name=movie_ctx["movie_name"],
                    media_year=movie_ctx["movie_year"],
                    metadata_provider=movie_ctx["metadata_provider"],
                )
            movie_name = movie_ctx["movie_name"] if movie_ctx else "Movie"
            details.extend(
                ImportFileDetail(
                    media_label=movie_name,
                    variant=mf.variant,
                    quality=mf.quality,
                    import_status=mf.import_status,
                    import_error=mf.import_error,
                    last_attempt_at=mf.last_attempt_at,
                    imported_at=mf.imported_at,
                    attempt_count=mf.attempt_count,
                )
                for mf in movie_files
            )

        try:
            from miramedia.torrents.utils import get_torrent_filepath

            source_dir = str(get_torrent_filepath(torrent))
        except Exception:
            source_dir = ""

        return ImportStatusEntry(
            torrent_id=torrent.id,
            torrent_title=torrent.title,
            torrent_status=torrent.status,
            source_dir=source_dir,
            media=media_context,
            progress=progress,
            files=details,
        )

    async def _prefetch_import_status_data(
        self, torrents: list[Torrent]
    ) -> tuple[
        dict[TorrentId, list[EpisodeFile]],
        dict[TorrentId, list[MovieFile]],
        dict[TorrentId, dict[uuid.UUID, tuple[int, int]]],
        dict[TorrentId, dict],
        dict[TorrentId, dict],
    ]:
        if not torrents:
            return {}, {}, {}, {}, {}
        torrent_ids = [t.id for t in torrents]
        ep_by_tid: dict[TorrentId, list[EpisodeFile]] = {tid: [] for tid in torrent_ids}
        mv_by_tid: dict[TorrentId, list[MovieFile]] = {tid: [] for tid in torrent_ids}
        for ef in await self.torrent_repository.get_episode_files_for_torrents(
            torrent_ids
        ):
            if ef.torrent_id in ep_by_tid:
                ep_by_tid[ef.torrent_id].append(ef)
        for mf in await self.torrent_repository.get_movie_files_for_torrents(
            torrent_ids
        ):
            if mf.torrent_id in mv_by_tid:
                mv_by_tid[mf.torrent_id].append(mf)
        ep_lookup = await self.torrent_repository.get_episode_label_lookup_for_torrents(
            torrent_ids
        )
        show_ctx = await self.torrent_repository.get_show_contexts_for_torrents(
            torrent_ids
        )
        movie_ctx = await self.torrent_repository.get_movie_contexts_for_torrents(
            torrent_ids
        )
        return ep_by_tid, mv_by_tid, ep_lookup, show_ctx, movie_ctx

    async def _build_import_status_entry(self, torrent: Torrent) -> ImportStatusEntry:
        """Build the per-torrent view used by the imports page."""
        (
            ep_by_tid,
            mv_by_tid,
            ep_lookup,
            show_ctx,
            movie_ctx,
        ) = await self._prefetch_import_status_data([torrent])
        return self._compose_import_status_entry(
            torrent,
            episode_files=ep_by_tid.get(torrent.id, []),
            movie_files=mv_by_tid.get(torrent.id, []),
            ep_lookup=ep_lookup.get(torrent.id, {}),
            show_ctx=show_ctx.get(torrent.id),
            movie_ctx=movie_ctx.get(torrent.id),
        )

    async def record_import_history(self, torrent: Torrent) -> None:
        """Snapshot a torrent's import result into ``torrent_history``.

        Called by the show/movie import flow right before any
        ``cleanup_after_import`` removal, so the durable log captures the
        outcome + per-file detail + media context even when the live torrent
        row is about to be deleted. Best-effort — never raises into import.
        """
        from miramedia.torrents.schemas import TorrentHistoryOutcome

        try:
            entry = await self._build_import_status_entry(torrent)
            p = entry.progress
            if p.total > 0 and p.imported == p.total:
                outcome = TorrentHistoryOutcome.imported
            elif p.failed > 0:
                outcome = TorrentHistoryOutcome.failed
            else:
                outcome = TorrentHistoryOutcome.downloaded
            imported_at = max(
                (f.imported_at for f in entry.files if f.imported_at is not None),
                default=None,
            )
            media = entry.media
            await self.torrent_repository.record_torrent_imported(
                torrent,
                outcome=outcome.value,
                files=[f.model_dump(mode="json") for f in entry.files],
                files_total=p.total,
                files_imported=p.imported,
                import_error=p.last_error,
                imported_at=imported_at,
                media_type=media.media_type if media else None,
                media_id=media.media_id if media else None,
                media_name=media.media_name if media else None,
                media_year=media.media_year if media else None,
            )
        except Exception:
            log.warning(
                "Failed to record import history for torrent %s",
                getattr(torrent, "hash", "?"),
                exc_info=True,
            )

    @staticmethod
    def is_import_ready(torrent: Torrent) -> bool:
        """A torrent belongs on the imports page only once its download finished.

        Episode/movie files are linked to a torrent at download *start*, so the
        mere presence of linked files is not enough — an in-progress download
        would otherwise show on the imports page. The import sweep itself only
        imports ``finished`` torrents; mirror that gate here so the imports
        queue and the actual import work agree on what is ready.
        """
        return torrent.status == TorrentStatus.finished

    async def build_all_import_status_entries(self) -> list[ImportStatusEntry]:
        """All torrent import rows — batched queries, for the unified imports API.

        Only torrents whose download has finished are surfaced; the live client
        status is refreshed for link-bearing torrents because the persisted DB
        ``status`` lags the download client.
        """
        torrents = await self.torrent_repository.get_all_torrents()
        (
            ep_by_tid,
            mv_by_tid,
            ep_lookup,
            show_ctx,
            movie_ctx,
        ) = await self._prefetch_import_status_data(torrents)
        # Candidates = torrents with at least one linked file. Refresh their
        # live download status so the finished-gate below is accurate.
        candidates = [t for t in torrents if ep_by_tid.get(t.id) or mv_by_tid.get(t.id)]
        candidates = await self._fetch_live_torrent_statuses(candidates)
        entries: list[ImportStatusEntry] = []
        for torrent in candidates:
            if not self.is_import_ready(torrent):
                continue
            entries.append(
                self._compose_import_status_entry(
                    torrent,
                    episode_files=ep_by_tid.get(torrent.id, []),
                    movie_files=mv_by_tid.get(torrent.id, []),
                    ep_lookup=ep_lookup.get(torrent.id, {}),
                    show_ctx=show_ctx.get(torrent.id),
                    movie_ctx=movie_ctx.get(torrent.id),
                )
            )
        return entries

    async def list_import_statuses(
        self,
        *,
        bucket: ImportStatusFilter,
        offset: int,
        limit: int,
    ) -> tuple[list[ImportStatusEntry], int]:
        """Return paginated import-status entries for the requested bucket."""
        torrents = await self.torrent_repository.get_all_torrents()
        (
            ep_by_tid,
            mv_by_tid,
            ep_lookup,
            show_ctx,
            movie_ctx,
        ) = await self._prefetch_import_status_data(torrents)
        matching: list[ImportStatusEntry] = []
        for t in torrents:
            episode_files = ep_by_tid.get(t.id, [])
            movie_files = mv_by_tid.get(t.id, [])
            progress = self.compute_import_progress_from_files(
                list(episode_files) + list(movie_files)
            )
            if progress.total == 0:
                continue
            if bucket not in self._bucket_progress(progress):
                continue
            matching.append(
                self._compose_import_status_entry(
                    t,
                    episode_files=episode_files,
                    movie_files=movie_files,
                    ep_lookup=ep_lookup.get(t.id, {}),
                    show_ctx=show_ctx.get(t.id),
                    movie_ctx=movie_ctx.get(t.id),
                )
            )

        sentinel = datetime.min.replace(tzinfo=UTC)

        def sort_key(entry: ImportStatusEntry) -> tuple[bool, float]:
            ts = entry.progress.last_attempt_at or sentinel
            return (
                entry.progress.failed == 0,
                -ts.timestamp() if ts != sentinel else 0,
            )

        matching.sort(key=sort_key)
        total = len(matching)
        return matching[offset : offset + limit], total

    async def get_import_status_counts(self) -> ImportStatusCounts:
        """Cheap aggregate counts per bucket (used for dashboard badges)."""
        torrents = await self.torrent_repository.get_all_torrents()
        ep_by_tid, mv_by_tid, _, _, _ = await self._prefetch_import_status_data(
            torrents
        )
        counts = ImportStatusCounts()
        for t in torrents:
            files = ep_by_tid.get(t.id, []) + mv_by_tid.get(t.id, [])
            progress = self.compute_import_progress_from_files(files)
            if progress.total == 0:
                continue
            buckets = self._bucket_progress(progress)
            counts.all += 1
            if ImportStatusFilter.failed in buckets:
                counts.failed += 1
            if ImportStatusFilter.ambiguous in buckets:
                counts.ambiguous += 1
            if ImportStatusFilter.partial in buckets:
                counts.partial += 1
            if ImportStatusFilter.pending in buckets:
                counts.pending += 1
            if ImportStatusFilter.recent in buckets:
                counts.recent += 1
        return counts

    async def is_due_for_retry(self, torrent: Torrent) -> bool:
        """Return True if enough time has passed to re-attempt the import.

        Uses an exponential backoff keyed off the highest per-file attempt
        count: 1m, 2m, 4m, ... capped at 120m. Files that haven't been
        attempted yet (``attempt_count == 0``) are always due.
        """
        episode_files = await self.torrent_repository.get_episode_files_of_torrent(
            torrent_id=torrent.id
        )
        movie_files = await self.torrent_repository.get_movie_files_of_torrent(
            torrent_id=torrent.id
        )
        files = list(episode_files) + list(movie_files)
        if not files:
            return False

        max_attempts = 0
        latest_attempt: datetime | None = None
        any_unattempted = False
        for f in files:
            if f.import_status == ImportOutcome.imported:
                continue
            attempts = getattr(f, "attempt_count", 0) or 0
            last = getattr(f, "last_attempt_at", None)
            if attempts == 0 or last is None:
                any_unattempted = True
                continue
            if attempts > max_attempts:
                max_attempts = attempts
            if latest_attempt is None or last > latest_attempt:
                latest_attempt = last

        if any_unattempted:
            return True
        if latest_attempt is None:
            return True
        backoff_minutes = min(2 ** max(max_attempts - 1, 0), 120)
        # ``last_attempt_at`` is a TIMESTAMPTZ column, so asyncpg returns it
        # tz-aware. Compare against a tz-aware ``now`` — subtracting a naive
        # ``datetime.utcnow()`` from the aware value raises ``TypeError: can't
        # subtract offset-naive and offset-aware datetimes`` and aborts the
        # whole import sweep once any torrent has at least one attempted-but-
        # unimported file (the steady state after a first failed import).
        now = datetime.now(UTC)
        if latest_attempt.tzinfo is None:
            latest_attempt = latest_attempt.replace(tzinfo=UTC)
        return now - latest_attempt >= timedelta(minutes=backoff_minutes)

    async def list_source_files(self, torrent: Torrent) -> "list[TorrentSourceFile]":
        """Enumerate on-disk source files for a torrent with parser hints.

        Imported lazily so the heavy parser/utils modules don't pull in at
        service construction time.
        """
        from miramedia.imports.files import list_files_recursively
        from miramedia.torrents.parsing import (
            is_subtitle_file,
            is_video_file,
            parse_release,
        )
        from miramedia.torrents.schemas import TorrentSourceFile
        from miramedia.torrents.utils import get_torrent_filepath

        root = get_torrent_filepath(torrent)
        files: list[TorrentSourceFile] = []
        if not root.exists():
            return files

        show = await self.torrent_repository.get_show_of_torrent(torrent.id)
        movie = await self.torrent_repository.get_movie_of_torrent(torrent.id)

        # Build a quick lookup for existing episode targets so we can suggest
        # ``suggested_episode_id`` for show torrents.
        ep_by_se: dict[tuple[int, int], uuid.UUID] = {}
        if show is not None:
            for season in show.seasons:
                for ep in season.episodes:
                    ep_by_se[(season.number, ep.number)] = ep.id

        for path in list_files_recursively(root):
            try:
                rel = str(path.relative_to(root))
            except ValueError:
                rel = path.name
            video = is_video_file(path)
            subtitle = is_subtitle_file(path)
            info = parse_release(path.name) if (video or subtitle) else None

            seasons: list[int] = info.seasons if info else []
            episodes: list[int] = info.episodes if info else []
            quality = info.quality if info else Quality.unknown

            suggested_episode = None
            suggested_movie = None
            if (
                show is not None
                and seasons
                and episodes
                and (seasons[0], episodes[0]) in ep_by_se
            ):
                suggested_episode = ep_by_se[(seasons[0], episodes[0])]
            elif movie is not None and video:
                suggested_movie = movie.id

            try:
                size = path.stat().st_size
            except OSError:
                size = 0

            files.append(
                TorrentSourceFile(
                    relative_path=rel,
                    size=size,
                    is_video=video,
                    is_subtitle=subtitle,
                    seasons=seasons,
                    episodes=episodes,
                    quality=quality,
                    suggested_episode_id=suggested_episode,
                    suggested_movie_id=suggested_movie,
                )
            )
        return files

    async def reset_import_status(self, torrent: Torrent) -> int:
        """Reset every linked file back to ``ImportOutcome.pending``.

        Used by the retry-import endpoint so the next scheduler sweep (or
        manual call) tries the torrent fresh. Returns count of rows reset.
        """
        from sqlalchemy import update

        from miramedia.movies.models import MovieFile
        from miramedia.shows.models import EpisodeFile

        db = self.torrent_repository.db
        ep_count = (
            await db.execute(
                update(EpisodeFile)
                .where(EpisodeFile.torrent_id == torrent.id)
                .values(import_status=ImportOutcome.pending, import_error=None)
            )
        ).rowcount
        mv_count = (
            await db.execute(
                update(MovieFile)
                .where(MovieFile.torrent_id == torrent.id)
                .values(import_status=ImportOutcome.pending, import_error=None)
            )
        ).rowcount
        await db.commit()
        # Signal the imports dashboard to refresh the per-torrent file
        # status grid + bucket counts.
        get_event_bus().publish(
            Event(type="import.updated", data={"torrent_id": str(torrent.id)})
        )
        from miramedia.imports.queue_hooks import schedule_torrent_queue_sync

        schedule_torrent_queue_sync(torrent.id)
        return (ep_count or 0) + (mv_count or 0)

    async def _fetch_live_torrent_statuses(
        self, torrents: list[Torrent]
    ) -> list[Torrent]:
        """Live libtorrent/qBittorrent status for a bounded torrent list."""
        if not torrents:
            return []
        # ``torrents`` are pydantic schema instances (TorrentSchema), not
        # session-attached ORM rows, so get_torrent_status(persist=False)
        # mutates plain objects with no risk of an unrelated commit flushing
        # the transient live fields. No session detach needed.
        sem = asyncio.Semaphore(_TORRENT_RPC_CONCURRENCY)

        async def _fetch(t: Torrent) -> Torrent:
            async with sem:
                try:
                    return await self.get_torrent_status(t, persist=False)
                except Exception:
                    return t

        return list(await asyncio.gather(*(_fetch(t) for t in torrents)))

    def _build_rich_torrents(
        self,
        torrents: list[Torrent],
        *,
        show_ctx: dict,
        movie_ctx: dict,
        progress_rows: dict,
    ) -> list[RichTorrent]:
        result: list[RichTorrent] = []
        for t in torrents:
            media = None
            variant = ""
            if t.id in show_ctx:
                ctx = show_ctx[t.id]
                variant = ctx["variant"]
                media = TorrentMediaContext(
                    media_type="show",
                    media_id=ctx["show_id"],
                    media_name=ctx["show_name"],
                    media_year=ctx["show_year"],
                    metadata_provider=ctx["metadata_provider"],
                    seasons=sorted(ctx["seasons"]) if ctx["seasons"] else None,
                    episodes=sorted(ctx["episodes"]) if ctx["episodes"] else None,
                )
            elif t.id in movie_ctx:
                ctx = movie_ctx[t.id]
                variant = ctx["variant"]
                media = TorrentMediaContext(
                    media_type="movie",
                    media_id=ctx["movie_id"],
                    media_name=ctx["movie_name"],
                    media_year=ctx["movie_year"],
                    metadata_provider=ctx["metadata_provider"],
                )
            result.append(
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
                    media=media,
                    import_progress=self._build_progress_from_rows(
                        progress_rows.get(t.id, [])
                    ),
                )
            )
        return result

    async def _rich_torrents_for_ids(
        self, torrents: list[Torrent], *, live_status: bool
    ) -> list[RichTorrent]:
        if not torrents:
            return []
        if live_status:
            torrents = await self._fetch_live_torrent_statuses(torrents)
        torrent_ids = [t.id for t in torrents]
        show_ctx = await self.torrent_repository.get_show_contexts_for_torrents(
            torrent_ids
        )
        movie_ctx = await self.torrent_repository.get_movie_contexts_for_torrents(
            torrent_ids
        )
        progress_rows = (
            await self.torrent_repository.get_import_status_aggregates_for_torrents(
                torrent_ids
            )
        )
        return self._build_rich_torrents(
            torrents,
            show_ctx=show_ctx,
            movie_ctx=movie_ctx,
            progress_rows=progress_rows,
        )

    async def get_all_torrents_with_context(
        self, *, live_status: bool = False
    ) -> list[RichTorrent]:
        """Build the full torrents list payload with batched queries.

        Defaults to DB-only (``live_status=False``): live status RPC over the
        entire torrent list thrashes the download client and threadpool, so the
        unbounded path relies on the scheduler's periodic refresh for liveness.
        Live RPC stays available for the bounded paginated path or behind an
        explicit ``live=true`` opt-in.
        """
        # DB-only fetch from the repository — never the service ``get_all_torrents``,
        # which fans out a live client RPC per torrent. ``_rich_torrents_for_ids``
        # performs the bounded live fan-out itself only when ``live_status`` is true.
        torrents = await self.torrent_repository.get_all_torrents()
        return await self._rich_torrents_for_ids(torrents, live_status=live_status)

    async def get_paginated_torrents_with_context(
        self,
        *,
        offset: int,
        limit: int,
        cursor: str | None = None,
        live_status: bool = True,
    ) -> tuple[list[RichTorrent], int, str | None]:
        """SQL page + optional live RPC only for rows on that page."""
        (
            db_torrents,
            total,
            next_cursor,
        ) = await self.torrent_repository.get_torrents_paginated(
            offset=offset, limit=limit, cursor=cursor
        )
        if not db_torrents:
            return [], total, None
        page = await self._rich_torrents_for_ids(db_torrents, live_status=live_status)
        return page, total, next_cursor

    @staticmethod
    def _build_progress_from_rows(rows: list[tuple]) -> ImportProgress:
        if not rows:
            return ImportProgress()
        progress = ImportProgress(total=len(rows))
        for status_val, err, attempt in rows:
            if status_val == ImportOutcome.imported:
                progress.imported += 1
            elif status_val == ImportOutcome.ambiguous:
                progress.ambiguous += 1
            elif status_val in (ImportOutcome.failed_no_match, ImportOutcome.failed_io):
                progress.failed += 1
                if not progress.last_error:
                    progress.last_error = err
            else:
                progress.pending += 1
            if attempt and (
                progress.last_attempt_at is None or attempt > progress.last_attempt_at
            ):
                progress.last_attempt_at = attempt
        return progress

    async def download_and_link(
        self,
        indexer_result: IndexerQueryResult,
        media_type: MediaType,
        media_id: uuid.UUID,
        variant: str = "",
        quality_override: Quality | None = None,
        show_repository: "ShowRepository | None" = None,
        movie_repository: "MovieRepository | None" = None,
        episode_target: "tuple[int, int] | None" = None,
    ) -> Torrent:
        """
        Consolidated download + media linking.  Handles both TV and movie
        downloads through a single code path.

        Repositories are passed as method args (not constructor) to avoid
        circular DI between TorrentService and TV/Movie services.

        *episode_target* is an explicit ``(season_number, episode_number)`` link
        target used when the release title can't be parsed into season/episode —
        chiefly Season 0 specials, which release groups name by title rather than
        ``S00E00``. When set, linking targets exactly that episode instead of
        deriving the target from ``indexer_result.season/episode``.
        """
        # Verify the destination is still wanted BEFORE the download starts.
        # A torrent may have been queued by an auto-download sweep moments
        # before the user marks the show / season / episode / movie as
        # skipped; without this guard the download completes and gets
        # linked anyway, contradicting the skip choice.
        destination_wanted, seasons_by_number = await self._is_destination_wanted(
            indexer_result=indexer_result,
            media_type=media_type,
            media_id=media_id,
            show_repository=show_repository,
            movie_repository=movie_repository,
            episode_target=episode_target,
        )
        if not destination_wanted:
            log.info(
                "Skipping download for %s (%s) — destination is marked skipped",
                indexer_result.title,
                media_type.value,
            )
            msg = f"Destination for {indexer_result.title} is skipped"
            raise MediaSkippedError(msg)

        torrent = await self.download(indexer_result=indexer_result)
        await self.pause_download(torrent=torrent)

        try:
            if media_type == MediaType.show:
                if show_repository is None:
                    msg = "show_repository is required for show downloads"
                    raise ValueError(msg)  # noqa: TRY301 — local control flow, triggers cleanup in except below
                linked_rows = await self._link_show(
                    torrent=torrent,
                    indexer_result=indexer_result,
                    show_id=media_id,
                    variant=variant,
                    quality_override=quality_override,
                    show_repository=show_repository,
                    seasons_by_number=seasons_by_number,
                    episode_target=episode_target,
                )
                if linked_rows == 0:
                    # Every targeted episode is skipped or already linked.
                    # Tear the torrent back down so we don't leave it spinning
                    # in the client with nothing to import (the import poller
                    # also can't reach it — is_due_for_retry skips torrents
                    # with zero linked files).
                    log.info(
                        "No episodes linked for torrent %s (all targeted "
                        "episodes skipped or already present); cancelling "
                        "download.",
                        torrent.title,
                    )
                    await self.cancel_download(torrent=torrent, delete_files=True)
                    msg = f"No wanted episodes for {indexer_result.title}"
                    raise MediaSkippedError(  # noqa: TRY301 — local control flow, triggers cleanup in except below
                        msg
                    )
            elif media_type == MediaType.movie:
                if movie_repository is None:
                    msg = "movie_repository is required for movie downloads"
                    raise ValueError(msg)  # noqa: TRY301 — local control flow, triggers cleanup in except below
                await self._link_movie(
                    torrent=torrent,
                    indexer_result=indexer_result,
                    movie_id=media_id,
                    variant=variant,
                    quality_override=quality_override,
                    movie_repository=movie_repository,
                )
        except Exception as exc:
            # ``download()`` already committed the torrent row, so any failure
            # between there and a successful link leaves an UNLINKED torrent
            # (the "ghost" on the torrents page). The common case is an
            # IntegrityError: an auto/continuous/manual re-download for a
            # media+quality that's already linked to a different torrent hits
            # the movie_file/episode_file PK. Tear the just-created torrent
            # fully back down — client copy AND DB row — not just the client.
            if isinstance(exc, IntegrityError):
                log.exception(
                    f"Media file already exists for torrent {torrent.title}; "
                    "removing the duplicate torrent"
                )
            else:
                log.exception(
                    f"Linking failed for torrent {torrent.title}; "
                    "removing the orphaned torrent"
                )
            try:
                await self.cancel_download(torrent=torrent, delete_files=True)
            except Exception:
                log.warning(
                    f"Failed to stop torrent {torrent.hash} in client during "
                    "link-failure cleanup",
                    exc_info=True,
                )
            # Best-effort row removal. ``add_*_file`` rolls back the session on
            # IntegrityError so the connection is usable here; for other errors
            # the session may be poisoned — don't let cleanup mask the original
            # exception.
            try:
                if media_type == MediaType.show and show_repository:
                    await show_repository.remove_episode_files_by_torrent_id(torrent.id)
                await self.delete_torrent(torrent_id=torrent.id)
            except Exception:
                log.warning(
                    f"Failed to delete orphaned torrent row {torrent.hash} "
                    "after link failure",
                    exc_info=True,
                )
            raise
        else:
            log.info(
                f"Successfully linked torrent {torrent.title} to {media_type.value} {media_id}"
            )
            # Record the grab in the durable history log (refreshed with the
            # outcome + file snapshot at import time). Best-effort.
            try:
                await self.torrent_repository.record_torrent_downloaded(
                    torrent, media_type=media_type.value, media_id=media_id
                )
            except Exception:
                log.warning(
                    "Failed to record torrent history for %s",
                    torrent.hash,
                    exc_info=True,
                )
            # Re-read DB status to respect user-pause that may have happened
            # between download() and now
            db_torrent = await self.torrent_repository.get_torrent_by_id(torrent.id)
            if db_torrent.status == TorrentStatus.paused:
                log.info(f"Torrent {torrent.title} is user-paused, not resuming")
            else:
                await self.resume_download(torrent=torrent)
            # Fire-and-forget metadata verifier. Torrent payloads we could
            # inspect upfront were already gated in download(); this catches
            # magnets (whose file dict needs swarm metadata) and any case
            # where the pre-add fetch failed. No-ops fast when the backend
            # already has the file list.
            if not indexer_result.usenet:
                self._spawn_post_add_verifier(torrent)

        return torrent

    async def _is_destination_wanted(
        self,
        indexer_result: IndexerQueryResult,
        media_type: MediaType,
        media_id: uuid.UUID,
        show_repository: "ShowRepository | None" = None,
        movie_repository: "MovieRepository | None" = None,
        episode_target: "tuple[int, int] | None" = None,
    ) -> "tuple[bool, dict | None]":
        """Return (wanted, seasons_by_number) for the show/movie destination.

        *wanted* is False when the show / movie / target seasons + episodes are
        all currently skipped. The auto-download path may queue a torrent
        moments before the user marks the destination skipped; this guard
        catches that race before the download burns disk + import time.

        *seasons_by_number* is the ``{season.number: season}`` dict built from
        the already-fetched show (eager-loaded by ``get_show_by_id``). Callers
        can pass it straight to ``_link_show`` to skip redundant DB round-trips.
        It is ``None`` for the movie path or when the show could not be loaded.
        """
        if media_type == MediaType.movie:
            if movie_repository is None:
                return True, None
            try:
                movie = await movie_repository.get_movie_by_id(movie_id=media_id)
            except Exception:
                return True, None
            return not getattr(movie, "skipped", False), None

        if media_type != MediaType.show or show_repository is None:
            return True, None
        try:
            show = await show_repository.get_show_by_id(show_id=media_id)
        except Exception:
            return True, None
        if getattr(show, "skipped", False):
            return False, None

        # Build a season lookup from the eager-loaded show — avoids per-season
        # DB round-trips in both loops below and in the hot _link_show path.
        seasons_by_number: dict = {season.number: season for season in show.seasons}

        # Explicit target (e.g. a Season 0 special grabbed by title): wanted is
        # decided solely by that episode's / season's skip state, not the
        # release's parsed season/episode.
        if episode_target is not None:
            target_season_number, target_episode_number = episode_target
            season = seasons_by_number.get(target_season_number)
            if season is None:
                return True, seasons_by_number
            if getattr(season, "skipped", False):
                return False, seasons_by_number
            ep = next(
                (
                    e
                    for e in season.episodes
                    if e.number == EpisodeNumber(target_episode_number)
                ),
                None,
            )
            if ep is None:
                return True, seasons_by_number
            return (not getattr(ep, "skipped", False)), seasons_by_number

        target_seasons = list(indexer_result.season or [])
        if not target_seasons:
            return True, seasons_by_number

        # If every targeted season is skipped → no download. If at least one
        # season is wanted, proceed (per-episode skip is enforced below).
        # A missing season (not in map) is treated as wanted (permissive).
        any_season_wanted = False
        for season_number in target_seasons:
            season = seasons_by_number.get(season_number)
            if season is None:
                any_season_wanted = True
                continue
            if not getattr(season, "skipped", False):
                any_season_wanted = True
        if not any_season_wanted:
            return False, seasons_by_number

        # For episode-specific torrents, drop when every targeted episode in
        # every targeted season is skipped (e.g. user pruned the multi-pack
        # while it was already in flight).
        target_episodes = list(indexer_result.episode or [])
        if not target_episodes:
            return True, seasons_by_number
        any_episode_wanted = False
        for season_number in target_seasons:
            season = seasons_by_number.get(season_number)
            if season is None:
                any_episode_wanted = True
                continue
            ep_map = {ep.number: ep for ep in season.episodes}
            for ep_number in target_episodes:
                ep = ep_map.get(EpisodeNumber(ep_number))
                if ep is None:
                    continue
                if not getattr(ep, "skipped", False):
                    any_episode_wanted = True
        return any_episode_wanted, seasons_by_number

    async def _link_show(
        self,
        torrent: Torrent,
        indexer_result: IndexerQueryResult,
        show_id: uuid.UUID,
        variant: str,
        show_repository: "ShowRepository",
        quality_override: Quality | None = None,
        seasons_by_number: "dict | None" = None,
        episode_target: "tuple[int, int] | None" = None,
    ) -> int:
        """Create EpisodeFile records linking torrent to show episodes.

        Returns the number of newly created EpisodeFile rows (existing rows
        are skipped). A return value of 0 means every targeted episode is
        either skipped or already linked, and the caller may want to abort
        the download.

        *seasons_by_number* is an optional pre-built ``{season.number: season}``
        dict (from the show already loaded by ``_is_destination_wanted``).  When
        provided, per-season DB round-trips are skipped; a missing entry raises
        the same ``NotFoundError`` that ``get_season_by_number`` would.  When
        ``None``, falls back to the repository query.
        """
        from miramedia.exceptions import NotFoundError

        rows_created = 0
        quality = (
            quality_override if quality_override is not None else indexer_result.quality
        )

        # Explicit-target link (Season 0 specials and any other release whose
        # title can't be parsed into S/E). Link exactly the targeted episode,
        # bypassing the indexer_result.season parse that would otherwise be
        # empty and create zero rows.
        if episode_target is not None:
            target_season_number, target_episode_number = episode_target
            if seasons_by_number is not None:
                season = seasons_by_number.get(target_season_number)
                if season is None:
                    msg = (
                        f"Season number {target_season_number} for show_id "
                        f"{show_id} not found."
                    )
                    raise NotFoundError(msg)
            else:
                season = await show_repository.get_season_by_number(
                    season_number=target_season_number, show_id=show_id
                )
            if getattr(season, "skipped", False):
                return 0
            ep = next(
                (
                    e
                    for e in season.episodes
                    if e.number == EpisodeNumber(target_episode_number)
                ),
                None,
            )
            if ep is None or getattr(ep, "skipped", False):
                return 0
            existing = {
                (f.episode_id, f.quality, f.variant)
                for f in await show_repository.get_episode_files_by_season_id(
                    season_id=season.id
                )
            }
            if (ep.id, quality, variant) in existing:
                return 0
            await show_repository.add_episode_file(
                episode_file=EpisodeFile(
                    episode_id=ep.id,
                    quality=quality,
                    torrent_id=torrent.id,
                    variant=variant,
                )
            )
            return 1

        for season_number in indexer_result.season:
            if seasons_by_number is not None:
                season = seasons_by_number.get(season_number)
                if season is None:
                    msg = f"Season number {season_number} for show_id {show_id} not found."
                    raise NotFoundError(msg)
            else:
                season = await show_repository.get_season_by_number(
                    season_number=season_number, show_id=show_id
                )
            season_skipped = getattr(season, "skipped", False)
            episodes_by_number = {
                episode.number: episode for episode in season.episodes
            }

            def _is_skipped(
                ep: Episode, _season_skipped: bool = season_skipped
            ) -> bool:
                return _season_skipped or getattr(ep, "skipped", False)

            if indexer_result.episode:
                episode_ids = []
                missing_episodes = []
                skipped_episodes = []
                for ep_number in indexer_result.episode:
                    ep = episodes_by_number.get(EpisodeNumber(ep_number))
                    if ep is None:
                        missing_episodes.append(ep_number)
                        continue
                    if _is_skipped(ep):
                        skipped_episodes.append(ep_number)
                        continue
                    episode_ids.append(ep.id)
                if missing_episodes:
                    log.warning(
                        "Some episodes from indexer result were not found in season %s "
                        "for show %s and will be skipped: %s",
                        season.id,
                        show_id,
                        ", ".join(str(ep) for ep in missing_episodes),
                    )
                if skipped_episodes:
                    log.info(
                        "Skipping linking for episodes marked skipped in season %s "
                        "of show %s: %s",
                        season.id,
                        show_id,
                        ", ".join(str(ep) for ep in skipped_episodes),
                    )
            else:
                episode_ids = [
                    episode.id
                    for episode in season.episodes
                    if not _is_skipped(episode)
                ]
                skipped_in_pack = [
                    episode.number
                    for episode in season.episodes
                    if _is_skipped(episode)
                ]
                if skipped_in_pack:
                    log.info(
                        "Season pack for season %s of show %s: omitting %d skipped "
                        "episode(s) from import: %s",
                        season.id,
                        show_id,
                        len(skipped_in_pack),
                        ", ".join(str(ep) for ep in skipped_in_pack),
                    )

            existing_files = {
                (f.episode_id, f.quality, f.variant)
                for f in await show_repository.get_episode_files_by_season_id(
                    season_id=season.id
                )
            }

            for episode_id in episode_ids:
                if (episode_id, quality, variant) in existing_files:
                    log.debug(
                        "Episode file already exists for episode %s, skipping",
                        episode_id,
                    )
                    continue
                episode_file = EpisodeFile(
                    episode_id=episode_id,
                    quality=quality,
                    torrent_id=torrent.id,
                    variant=variant,
                )
                await show_repository.add_episode_file(episode_file=episode_file)
                rows_created += 1

        return rows_created

    async def _link_movie(
        self,
        torrent: Torrent,
        indexer_result: IndexerQueryResult,
        movie_id: uuid.UUID,
        variant: str,
        movie_repository: "MovieRepository",
        quality_override: Quality | None = None,
    ) -> None:
        """Create MovieFile record linking torrent to movie."""
        from miramedia.movies.schemas import MovieFile as MovieFileSchema

        quality = (
            quality_override if quality_override is not None else indexer_result.quality
        )
        existing = {
            (f.quality, f.variant)
            for f in await movie_repository.get_movie_files_by_movie_id(
                movie_id=movie_id
            )
        }
        if (quality, variant) in existing:
            log.debug("Movie file already exists for movie %s, skipping", movie_id)
            return

        movie_file = MovieFileSchema(
            movie_id=movie_id,
            quality=quality,
            torrent_id=torrent.id,
            variant=variant,
        )
        await movie_repository.add_movie_file(movie_file=movie_file)

    # ---- Integrity mismatch (SHA1 audit) ---------------------------------

    async def list_integrity_mismatches(
        self,
        *,
        offset: int,
        limit: int,
        show_service: "ShowService",
        movie_service: "MovieService",
    ) -> PaginatedIntegrityMismatches:
        """List imported files whose integrity audit recorded a SHA1 mismatch.

        Global order is shows first (by file id), then movies (by file id).
        Only the requested page slice is loaded from the database.
        """
        page_limit = min(limit, INTEGRITY_MISMATCH_MAX_LIMIT)
        show_repo = show_service.show_repository
        movie_repo = movie_service.movie_repository

        show_total = await show_repo.count_sha1_mismatch_files()
        movie_total = await movie_repo.count_sha1_mismatch_files()
        total = show_total + movie_total

        out: list[IntegrityMismatch] = []

        show_offset = min(offset, show_total)
        show_take = 0
        if offset < show_total:
            show_take = min(page_limit, show_total - show_offset)
            show_rows = await show_repo.list_sha1_mismatch_files(
                offset=show_offset, limit=show_take
            )
            episode_context = await show_repo.batch_episodes_with_context(
                [row.episode_id for row in show_rows]
            )
            shows = await show_repo.get_shows_by_ids(
                list({ctx.show_id for ctx in episode_context.values()})
            )
            paths = await show_service.batch_resolve_episode_file_paths(
                show_rows, episode_context, shows
            )
            for row in show_rows:
                media_title = ""
                episode_label: str | None = None
                try:
                    ctx = episode_context[row.episode_id]
                    media_title = ctx.show_name
                    episode_label = f"S{ctx.season_number:02d}E{ctx.episode_number:02d}"
                except Exception:
                    log.exception(
                        "Failed to resolve show title for mismatched episode_file %s",
                        row.id,
                    )
                path = paths.get(row.id)
                out.append(
                    IntegrityMismatch(
                        file_id=row.id,
                        media_type="show",
                        media_title=media_title,
                        episode=episode_label,
                        path=str(path) if path is not None else None,
                        quality=Quality(row.quality),
                        variant_tag=row.variant or "",
                        import_error=row.import_error or "",
                        detected_at=row.last_attempt_at,
                    )
                )

        movie_take = page_limit - len(out)
        if movie_take > 0:
            movie_offset = max(0, offset - show_total)
            movie_rows = await movie_repo.list_sha1_mismatch_files(
                offset=movie_offset, limit=movie_take
            )
            movie_names = await movie_repo.get_movie_names_by_ids(
                [row.movie_id for row in movie_rows]
            )
            movies = await movie_repo.get_movies_by_ids(
                [row.movie_id for row in movie_rows]
            )
            paths = await movie_service.batch_resolve_movie_file_paths(
                movie_rows, movies
            )
            for row in movie_rows:
                media_title = ""
                try:
                    media_title = movie_names[row.movie_id]
                except Exception:
                    log.exception(
                        "Failed to resolve movie title for mismatched movie_file %s",
                        row.id,
                    )
                path = paths.get(row.id)
                out.append(
                    IntegrityMismatch(
                        file_id=row.id,
                        media_type="movie",
                        media_title=media_title,
                        episode=None,
                        path=str(path) if path is not None else None,
                        quality=Quality(row.quality),
                        variant_tag=row.variant or "",
                        import_error=row.import_error or "",
                        detected_at=row.last_attempt_at,
                    )
                )

        next_offset = offset + len(out) if offset + len(out) < total else None
        return PaginatedIntegrityMismatches(
            items=out,
            total=total,
            offset=offset,
            limit=page_limit,
            next_offset=next_offset,
        )

    async def rebaseline_file(
        self,
        *,
        media_type: MediaType,
        file_id: uuid.UUID,
        show_service: "ShowService",
        movie_service: "MovieService",
    ) -> IntegrityActionResult:
        """Accept the on-disk file: clear error + sha1 so the next audit re-baselines."""
        await self._clear_integrity_state(
            media_type=media_type,
            file_id=file_id,
            reset_sha1=True,
            show_service=show_service,
            movie_service=movie_service,
        )
        return IntegrityActionResult(ok=True)

    async def dismiss_mismatch(
        self,
        *,
        media_type: MediaType,
        file_id: uuid.UUID,
        show_service: "ShowService",
        movie_service: "MovieService",
    ) -> IntegrityActionResult:
        """Clear the mismatch error only; keep sha1 so the next audit re-verifies."""
        await self._clear_integrity_state(
            media_type=media_type,
            file_id=file_id,
            reset_sha1=False,
            show_service=show_service,
            movie_service=movie_service,
        )
        return IntegrityActionResult(ok=True)

    async def _clear_integrity_state(
        self,
        *,
        media_type: MediaType,
        file_id: uuid.UUID,
        reset_sha1: bool,
        show_service: "ShowService",
        movie_service: "MovieService",
    ) -> None:
        if media_type == MediaType.show:
            row = await show_service.show_repository.get_episode_file_by_id(file_id)
            if row is None:
                msg = f"File {file_id} not found"
                raise NotFoundError(msg)
            import_error = row.import_error or ""
            if (
                row.import_status != ImportOutcome.imported
                or not import_error.startswith("sha1 mismatch")
            ):
                msg = "Integrity mismatch is no longer present for this file"
                raise ConflictError(msg)
            cleared = await show_service.show_repository.clear_file_integrity_state(
                file_id,
                expected_sha1=row.sha1,
                expected_import_error=import_error,
                reset_sha1=reset_sha1,
            )
        elif media_type == MediaType.movie:
            row = await movie_service.movie_repository.get_movie_file_by_id(file_id)
            if row is None:
                msg = f"File {file_id} not found"
                raise NotFoundError(msg)
            import_error = row.import_error or ""
            if (
                row.import_status != ImportOutcome.imported
                or not import_error.startswith("sha1 mismatch")
            ):
                msg = "Integrity mismatch is no longer present for this file"
                raise ConflictError(msg)
            cleared = await movie_service.movie_repository.clear_file_integrity_state(
                file_id,
                expected_sha1=row.sha1,
                expected_import_error=import_error,
                reset_sha1=reset_sha1,
            )
        else:
            msg = f"File {file_id} not found"
            raise NotFoundError(msg)

        if not cleared:
            msg = "Integrity mismatch is no longer present for this file"
            raise ConflictError(msg)
