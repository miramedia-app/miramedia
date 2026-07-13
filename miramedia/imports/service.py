"""Imports service.

Composes per-torrent import status (already produced by ``TorrentService``)
and library-scan candidates (cached in ``scan_result_cache``) into a single
import stream, grouped into four user-facing tabs:

* ``review``  — needs human decision (ambiguous / no-match torrents + scan)
* ``retry``   — stuck or currently retrying download imports
* ``done``    — recently imported torrents (confirmation log)
* ``all``     — every ImportItem

The service does not own the import or scan logic itself; it delegates to
``TorrentService`` / ``ShowService`` / ``MovieService`` and surfaces a thin
unified API for the imports UI.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi.exceptions import HTTPException
from pydantic import TypeAdapter

from miramedia.file_status import ImportOutcome
from miramedia.imports.repository import ImportsRepository
from miramedia.imports.schemas import (
    IgnoreRequest,
    ImportCounts,
    ImportItem,
    ImportTab,
    MediaImportItem,
    PaginatedImports,
    ResolveRequest,
    ResolveResult,
    ScanImportItem,
    ScanResult,
    ScanRunState,
    ScanRunStatus,
    ScanTriggerResult,
    TorrentImportItem,
    TorrentResolveAction,
)
from miramedia.movies.service import MovieService
from miramedia.shows.service import ShowService
from miramedia.torrents.schemas import (
    ImportFileDetail,
    ImportProgress,
    ImportStatusEntry,
    MediaType,
)
from miramedia.torrents.service import TorrentService

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

_RETRY_CAP_ATTEMPTS = 5  # past this many file-level attempts we call it "stuck"
_IMPORT_ITEM_ADAPTER = TypeAdapter(ImportItem)

# Process-wide "the queue has been built at least once" marker. An empty queue
# is otherwise indistinguishable from a never-built one, so a steady-state empty
# library would rebuild the whole queue (DELETE + scan all torrents + COMMIT) on
# every counts/list poll from the dashboard. Once we have built — or confirmed a
# non-empty — queue in this process, an empty result is treated as genuinely
# empty rather than stale. Real changes still rebuild via the event-driven
# queue_hooks / request_import_queue_rebuild paths.
_queue_built_at: datetime | None = None
# Re-confirm (cheap COUNT, not a rebuild) at most this often after the marker is
# set, so a queue wiped out-of-band still gets repopulated within the window.
_QUEUE_BUILT_TTL = timedelta(minutes=15)

_QUEUE_BUILD_LOCK: asyncio.Lock | None = None


def _get_queue_build_lock() -> asyncio.Lock:
    """Lazy-init the queue-build lock so it's bound to the running event loop."""
    global _QUEUE_BUILD_LOCK
    if _QUEUE_BUILD_LOCK is None:
        _QUEUE_BUILD_LOCK = asyncio.Lock()
    return _QUEUE_BUILD_LOCK


class ImportsService:
    def __init__(
        self,
        *,
        repository: ImportsRepository,
        torrent_service: TorrentService,
        show_service: ShowService,
        movie_service: MovieService,
    ) -> None:
        self.repository = repository
        self.torrent_service = torrent_service
        self.show_service = show_service
        self.movie_service = movie_service

    # ---- listing ----------------------------------------------------------

    async def _ensure_queue_populated(self, db: AsyncSession) -> None:
        """Populate-on-first-call guard shared by ``list_imports`` and
        ``get_counts``.

        Rebuilds the queue only when it has never been built in this process
        (or the build marker has gone stale) *and* the queue is currently
        empty. A genuinely empty library no longer triggers a rebuild on every
        poll: once a build is confirmed, the empty result is trusted until the
        marker expires.
        """
        global _queue_built_at
        from miramedia.imports.queue.sync import (
            import_queue_is_empty,
            rebuild_import_queue,
        )

        now = datetime.now(UTC)
        if _queue_built_at is not None and (now - _queue_built_at) < _QUEUE_BUILT_TTL:
            return
        async with _get_queue_build_lock():
            now = datetime.now(UTC)
            if (
                _queue_built_at is not None
                and (now - _queue_built_at) < _QUEUE_BUILT_TTL
            ):
                return
            if await import_queue_is_empty(db):
                await rebuild_import_queue(db, self)
            _queue_built_at = datetime.now(UTC)

    async def list_imports(
        self, *, tab: ImportTab, offset: int, limit: int
    ) -> PaginatedImports:
        from miramedia.imports.queue.sync import list_queue_page

        db = self.repository.db
        await self._ensure_queue_populated(db)
        payloads, total = await list_queue_page(db, tab=tab, offset=offset, limit=limit)
        items = [_IMPORT_ITEM_ADAPTER.validate_python(p) for p in payloads]
        return PaginatedImports(
            items=items,
            total=total,
            offset=offset,
            limit=limit,
        )

    async def get_counts(self) -> ImportCounts:
        from miramedia.imports.queue.sync import count_queue_by_tab

        db = self.repository.db
        await self._ensure_queue_populated(db)
        by_tab = await count_queue_by_tab(db)
        return ImportCounts(
            review=by_tab.get(ImportTab.review.value, 0),
            retry=by_tab.get(ImportTab.retry.value, 0),
            done=by_tab.get(ImportTab.done.value, 0),
            all=by_tab.get(ImportTab.all.value, 0),
            importing=await self.repository.count_queued_scans(),
            import_total=await self.repository.get_import_batch_total(),
        )

    async def _collect_items(self) -> list[TorrentImportItem | ScanImportItem]:
        out: list[TorrentImportItem | ScanImportItem] = []

        entries = await self.torrent_service.build_all_import_status_entries()
        for entry in entries:
            if entry.progress.total == 0:
                continue
            out.append(
                TorrentImportItem(
                    id=str(entry.torrent_id),
                    entry=entry,
                    backoff_seconds=self._backoff_seconds(entry),
                )
            )

        # Scan items: read the persisted cache, not the filesystem.
        for raw in await self.repository.list_scan_cache():
            try:
                result = ScanResult.model_validate(raw)
            except Exception:
                log.exception("Skipping corrupt scan_result_cache row")
                continue
            out.append(ScanImportItem(id=result.directory, result=result))

        # Done entries are torrent-independent: they come from the durable
        # ``torrent_history`` log, so successful imports survive
        # cleanup_after_import deleting the live torrent row.
        out.extend(await self._collect_history_items())
        return out

    async def _collect_history_items(self) -> list[MediaImportItem]:
        items: list[MediaImportItem] = []
        rows = await self.torrent_service.torrent_repository.list_imported_torrent_history()
        for h in rows:
            if not h.media_type:
                continue  # no media context — nothing useful to show
            try:
                files = [ImportFileDetail.model_validate(f) for f in (h.files or [])]
            except Exception:
                files = []
            progress = ImportProgress(
                total=h.files_total,
                imported=h.files_imported,
                last_attempt_at=h.imported_at,
            )
            items.append(
                MediaImportItem(
                    id=str(h.id),
                    media_type=MediaType(h.media_type),
                    media_name=h.media_name or h.title,
                    media_year=h.media_year,
                    torrent_title=h.title or "",
                    imported_at=h.imported_at,
                    progress=progress,
                    files=files,
                )
            )
        return items

    def _tab_matches(
        self, item: TorrentImportItem | ScanImportItem | MediaImportItem, tab: ImportTab
    ) -> bool:
        # A fully-imported live torrent is represented by its torrent_history
        # row (kind=media). Hide the live torrent from every tab so Done isn't
        # duplicated and All doesn't show both the torrent and its history.
        if isinstance(item, TorrentImportItem) and item.entry.progress.all_imported:
            return False

        if tab == ImportTab.all:
            return True

        if isinstance(item, ScanImportItem):
            # Imported scans are finished → Done; pending ones need a human
            # pick → Review.
            if item.result.status == "imported":
                return tab == ImportTab.done
            return tab == ImportTab.review

        if isinstance(item, MediaImportItem):
            # Durable history of a finished import → Done only.
            return tab == ImportTab.done

        entry = item.entry
        progress = entry.progress
        has_failed_no_match = any(
            f.import_status == ImportOutcome.failed_no_match for f in entry.files
        )
        has_failed_io = any(
            f.import_status == ImportOutcome.failed_io for f in entry.files
        )
        is_capped = any(
            (f.attempt_count or 0) >= _RETRY_CAP_ATTEMPTS
            and f.import_status != ImportOutcome.imported
            for f in entry.files
        )
        # Done is sourced from torrent_history, not live torrents.
        if tab == ImportTab.review:
            return progress.ambiguous > 0 or has_failed_no_match
        if tab == ImportTab.retry:
            return has_failed_io or is_capped
        return False

    @staticmethod
    def _backoff_seconds(entry: ImportStatusEntry) -> int | None:
        """Approximate seconds until next auto-retry sweep, or None if N/A."""
        if not entry.files:
            return None
        max_attempts = 0
        latest: datetime | None = None
        for f in entry.files:
            if f.import_status == ImportOutcome.imported:
                continue
            if (f.attempt_count or 0) > max_attempts:
                max_attempts = f.attempt_count or 0
            if f.last_attempt_at is not None:
                if latest is None or f.last_attempt_at > latest:
                    latest = f.last_attempt_at
        if latest is None:
            return 0
        backoff_minutes = min(2 ** max(max_attempts - 1, 0), 120)
        elapsed = datetime.now(UTC).replace(tzinfo=None) - ImportsService._naive(latest)
        remaining = timedelta(minutes=backoff_minutes) - elapsed
        return max(int(remaining.total_seconds()), 0)

    @staticmethod
    def _naive(ts: datetime) -> datetime:
        return ts.astimezone(tz=None).replace(tzinfo=None) if ts.tzinfo else ts

    def _sort_key(self, item: TorrentImportItem | ScanImportItem) -> tuple:
        # Highest-urgency first within each tab. Scan items rank between
        # ambiguous torrents and the rest.
        if isinstance(item, ScanImportItem):
            top = item.result.candidates[0] if item.result.candidates else None
            return (1, -(top.confidence if top else 0.0), item.result.directory)
        entry = item.entry
        progress = entry.progress
        urgency = 0 if (progress.ambiguous or progress.failed) else 2
        ts = progress.last_attempt_at
        ts_score = -self._naive(ts).timestamp() if ts is not None else 0.0
        return (urgency, ts_score, entry.torrent_title)

    # ---- resolve ----------------------------------------------------------

    async def resolve(self, body: ResolveRequest) -> ResolveResult:
        if body.kind == "torrent":
            return await self._resolve_torrent(body)
        if body.kind == "scan":
            return await self._resolve_scan(body)
        raise HTTPException(400, f"Unknown kind: {body.kind}")

    async def _resolve_torrent(self, body: ResolveRequest) -> ResolveResult:
        try:
            torrent_id = uuid.UUID(body.id)
        except ValueError:
            raise HTTPException(400, "torrent id must be a uuid") from None
        torrent = await self.torrent_service.torrent_repository.get_torrent_by_id(
            torrent_id=torrent_id
        )
        show = await self.torrent_service.get_show_of_torrent(torrent=torrent)
        movie = await self.torrent_service.get_movie_of_torrent(torrent=torrent)
        if show is None and movie is None:
            raise HTTPException(400, "torrent not linked to any media")

        action = body.action or TorrentResolveAction.retry
        if action == TorrentResolveAction.retry:
            await self.torrent_service.reset_import_status(torrent=torrent)
            if show is not None:
                await self.show_service.import_show_from_torrent(
                    torrent=torrent, show=show
                )
            else:
                assert movie is not None  # noqa: S101 — invariant guard
                await self.movie_service.import_movie_from_torrent(
                    torrent=torrent, movie=movie
                )
            return ResolveResult(ok=True, detail="retried")
        if action == TorrentResolveAction.map:
            # Map flow is interactive on the frontend; it uses the existing
            # /torrents/{id}/files + /torrents/{id}/map endpoints.
            return ResolveResult(
                ok=False,
                detail="map action must use /torrents/{id}/map dialog",
            )
        raise HTTPException(400, f"Unknown action: {action}")

    async def _resolve_scan(self, body: ResolveRequest) -> ResolveResult:
        if body.media_type is None:
            raise HTTPException(400, "media_type required for scan resolve")
        directory = body.id
        path = Path(directory)
        if not path.exists():  # noqa: ASYNC240 — cheap stat, intentional
            raise HTTPException(400, "directory no longer exists on disk")

        imported_media = None
        if body.media_id is not None:
            # Pick an existing tracked show/movie.
            if body.media_type == MediaType.show:
                show = await self.show_service.get_show_by_id(show_id=body.media_id)
                ok = await self.show_service.import_show_from_directory(
                    show=show, source_directory=path
                )
                imported_media = show
            else:
                movie = await self.movie_service.get_movie_by_id(body.media_id)
                ok = await self.movie_service.import_movie_from_directory(
                    movie=movie, source_directory=path
                )
                imported_media = movie
        elif body.external_id and body.metadata_provider:
            # Create the media from a metadata-provider hit, then import.
            from miramedia.metadata.dependencies import (
                get_metadata_provider,
            )

            metadata_provider = get_metadata_provider(body.metadata_provider)
            try:
                if body.media_type == MediaType.show:
                    show = await self.show_service.add_show(
                        external_id=body.external_id,
                        metadata_provider=metadata_provider,
                    )
                    ok = await self.show_service.import_show_from_directory(
                        show=show, source_directory=path
                    )
                    imported_media = show
                else:
                    movie = await self.movie_service.add_movie(
                        external_id=body.external_id,
                        metadata_provider=metadata_provider,
                    )
                    ok = await self.movie_service.import_movie_from_directory(
                        movie=movie, source_directory=path
                    )
                    imported_media = movie
            except ValueError as exc:
                # Metadata provider couldn't resolve the chosen candidate
                # (TVMaze missing the IMDb ID, TMDB API key wrong, etc.).
                # Surface as a 422 with the provider's message + leave the
                # scan row in failed state so the user can pick a different
                # candidate without retrying the broken one.
                await self.repository.mark_scan_cache_failed(directory, error=str(exc))
                raise HTTPException(422, str(exc)) from exc
        else:
            raise HTTPException(
                400,
                "scan resolve needs either media_id or external_id + metadata_provider",
            )

        if not ok:
            # Keep the row visible as a needs-attention entry, never finished.
            await self.repository.mark_scan_cache_failed(
                directory, error="import failed"
            )
            raise HTTPException(400, "import failed")
        # Keep the row visible as a finished entry (status: imported) instead
        # of deleting it.
        await self.repository.mark_scan_cache_imported(
            directory,
            imported_name=getattr(imported_media, "name", None),
            imported_media_id=str(getattr(imported_media, "id", "")) or None,
            imported_media_type=body.media_type.value
            if body.media_type is not None
            else None,
        )

        # Re-apply global-default behaviour so a partial import is completed
        # (missing episodes / movie) and subtitles are fetched.
        from miramedia.imports.followup import run_post_import_completion

        await run_post_import_completion(
            db=self.repository.db,
            media_type=body.media_type,
            media=imported_media,
            show_service=self.show_service,
            movie_service=self.movie_service,
        )
        return ResolveResult(ok=True, detail="imported")

    # ---- ignore -----------------------------------------------------------

    async def ignore(self, body: IgnoreRequest) -> ResolveResult:
        if body.kind == "torrent":
            try:
                torrent_id = uuid.UUID(body.id)
            except ValueError:
                raise HTTPException(400, "torrent id must be a uuid") from None
            await self.torrent_service.torrent_repository.delete_torrent(
                torrent_id=torrent_id,
                delete_associated_media_files=body.delete_files,
            )
            return ResolveResult(ok=True, detail="torrent removed")
        if body.kind == "scan":
            await self.repository.add_ignored_path(body.id)
            await self.repository.delete_scan_cache_entry(body.id)
            return ResolveResult(ok=True, detail="path ignored")
        raise HTTPException(400, f"Unknown kind: {body.kind}")

    # ---- scan trigger -----------------------------------------------------

    async def trigger_scan(self) -> ScanTriggerResult:
        run = await self.repository.get_scan_run()
        if run.state == ScanRunState.running:
            return ScanTriggerResult(
                state=ScanRunState.running, detail="scan already in progress"
            )
        # Flip the singleton up-front so the UI reflects state even before
        # the worker picks the task up.
        await self.repository.set_scan_run(
            state=ScanRunState.running,
            started_at=datetime.now(UTC).replace(tzinfo=None),
            items_found=0,
            last_error=None,
        )
        from miramedia.imports.tasks import run_library_scan_task

        try:
            await run_library_scan_task.kiq()
        except Exception as exc:
            await self.repository.set_scan_run(
                state=ScanRunState.error,
                finished_at=datetime.now(UTC).replace(tzinfo=None),
                last_error=str(exc),
            )
            log.exception("Failed to enqueue scan task")
            return ScanTriggerResult(state=ScanRunState.error, detail=str(exc))
        return ScanTriggerResult(state=ScanRunState.running, detail="enqueued")

    async def get_scan_status(self) -> ScanRunStatus:
        return await self.repository.get_scan_run()


__all__ = ["_IMPORT_ITEM_ADAPTER", "ImportsService"]
