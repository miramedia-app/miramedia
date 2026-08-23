"""Background tasks for the imports feature.

* ``run_library_scan_task`` — wraps the pure ``imports.scan.scan_libraries``
  walk in a taskiq task, performs optional auto-import of high-confidence
  matches, persists the remaining candidates into ``scan_result_cache``, and
  updates the ``scan_run`` singleton so the imports UI can poll progress.

``scan_libraries`` itself is side-effect free (walk + match + provider
search). All create/import side effects live here in the task.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from miramedia.imports.schemas import ScanRunState
from miramedia.scheduler import interactive_broker

if TYPE_CHECKING:
    from miramedia.torrents.schemas import MediaType

log = logging.getLogger(__name__)

_SCAN_LOCK: asyncio.Lock | None = None


def _get_scan_lock() -> asyncio.Lock:
    """Lazy-init the scan lock so it's bound to the running event loop."""
    global _SCAN_LOCK
    if _SCAN_LOCK is None:
        _SCAN_LOCK = asyncio.Lock()
    return _SCAN_LOCK


async def _auto_import_item(
    item,  # noqa: ANN001 - ScanResult
    min_confidence: float,
) -> tuple[str, MediaType | None, object] | None:
    """Try to auto-import a scan item using its single highest-confidence
    candidate (existing library item OR metadata-provider hit).

    Session lifetime: this helper owns its own short-lived ``bg_show_service``
    / ``bg_movie_service`` sessions — one per phase (lookup, then import). The
    multi-second-to-minute hardlink + mediainfo + repo-write work that the
    underlying ``import_*_from_directory`` calls do happens INSIDE a single
    bg session per item (acceptable: single item, bounded I/O); the previous
    code held one session across the whole scan.

    Returns:
    * ``("imported", MediaType, media)`` — imported OK (run follow-up).
    * ``("failed", None, None)`` — attempted but the import did not succeed.
    * ``None`` — not attempted (below the confidence threshold).
    """
    from pathlib import Path

    from miramedia.background_services import bg_movie_service, bg_show_service
    from miramedia.metadata.dependencies import get_metadata_provider
    from miramedia.torrents.schemas import MediaType

    top_existing = item.candidates[0].confidence if item.candidates else 0.0
    top_provider = (
        item.provider_candidates[0].confidence if item.provider_candidates else 0.0
    )
    best = max(top_existing, top_provider)
    if best < min_confidence:
        return None

    source_dir = Path(item.directory)
    try:
        if top_existing >= top_provider:
            cand = item.candidates[0]
            if cand.media_type == MediaType.show:
                async with bg_show_service() as show_service:
                    show = await show_service.get_show_by_id(show_id=cand.media_id)
                    ok = await show_service.import_show_from_directory(
                        show=show, source_directory=source_dir
                    )
                return (
                    ("imported", MediaType.show, show)
                    if ok
                    else (
                        "failed",
                        None,
                        None,
                    )
                )
            async with bg_movie_service() as movie_service:
                movie = await movie_service.get_movie_by_id(cand.media_id)
                ok = await movie_service.import_movie_from_directory(
                    movie=movie, source_directory=source_dir
                )
            return (
                ("imported", MediaType.movie, movie)
                if ok
                else (
                    "failed",
                    None,
                    None,
                )
            )

        cand = item.provider_candidates[0]
        metadata_provider = get_metadata_provider(cand.metadata_provider)
        if cand.media_type == MediaType.show:
            async with bg_show_service() as show_service:
                show = await show_service.add_show(
                    external_id=cand.external_id,
                    metadata_provider=metadata_provider,
                )
                ok = await show_service.import_show_from_directory(
                    show=show, source_directory=source_dir
                )
            return (
                ("imported", MediaType.show, show)
                if ok
                else (
                    "failed",
                    None,
                    None,
                )
            )
        async with bg_movie_service() as movie_service:
            movie = await movie_service.add_movie(
                external_id=cand.external_id,
                metadata_provider=metadata_provider,
            )
            ok = await movie_service.import_movie_from_directory(
                movie=movie, source_directory=source_dir
            )
    except Exception:
        log.exception("Auto-import failed for %s", source_dir)
        return ("failed", None, None)
    else:
        return (
            ("imported", MediaType.movie, movie)
            if ok
            else (
                "failed",
                None,
                None,
            )
        )


async def _scan_and_cache() -> None:
    """Run the pure scan, optionally auto-import strong matches, cache the
    rest, and maintain the scan_run singleton.

    Session lifetime: each phase opens a SHORT bg session and releases it
    before the next slow-I/O phase. Previously a single outer
    ``SessionLocalBackground()`` + outer ``bg_show_service`` + outer
    ``bg_movie_service`` were held for the entire scan (walk + provider HTTP
    + per-item auto-import), pinning 3 connections in ``idle in transaction``
    for minutes. Now:

    * Phase 1 (DB write): mark scan_run as running.
    * Phase 2 (no DB): ``scan_libraries`` walks the filesystem + fans out
      provider HTTP. It self-manages a short read-phase session internally;
      no service-bound session crosses the walk.
    * Phase 3 (DB read): list terminal scan cache + tracked-id sets.
    * Phase 4 (per-item, no outer DB): auto-import loop opens a fresh
      bg session per item via ``_auto_import_item`` + follow-up.
    * Phase 5 (DB write): replace_scan_cache + mark scan_run done.
    """
    lock = _get_scan_lock()
    if lock.locked():
        log.info("Library scan already running; skipping this trigger")
        return
    async with lock:
        await _scan_and_cache_body()


async def _scan_and_cache_body() -> None:
    from miramedia.background_services import (
        bg_movie_service,
        bg_show_service,
    )
    from miramedia.config import MiraMediaConfig
    from miramedia.database import background_session
    from miramedia.imports.repository import ImportsRepository

    imports_cfg = MiraMediaConfig().imports

    # Phase 1: mark scan_run running (short session, released before walk).
    async with background_session() as db:
        repo = ImportsRepository(db=db)
        await repo.set_scan_run(
            state=ScanRunState.running,
            started_at=datetime.now(UTC).replace(tzinfo=None),
            items_found=0,
            last_error=None,
        )

    try:
        # Phase 2: walk + provider HTTP. ``scan_libraries`` opens its own
        # short bg sessions internally just to snapshot existing media,
        # then releases them before the multi-minute walk. We read the
        # ignored-paths list (a short session, released before the walk) and
        # hand it off to the torrent-agnostic scan.
        from miramedia.imports.scan import scan_libraries

        async with background_session() as db:
            ignored_paths = set(await ImportsRepository(db=db).list_ignored_paths())
        response = await scan_libraries(ignored_paths)

        # Phase 3: short read to compute terminal-cache reconciliation.
        async with background_session() as db:
            repo = ImportsRepository(db=db)
            terminal_rows = await repo.list_terminal_scan_cache()
        async with bg_show_service() as show_service:
            tracked_show_ids = {
                str(show_id) for show_id in await show_service.get_all_show_ids()
            }
        async with bg_movie_service() as movie_service:
            tracked_movie_ids = {
                str(movie_id) for movie_id in await movie_service.get_all_movie_ids()
            }

        imported_snapshot: dict[str, dict] = {
            d: p for d, p in terminal_rows if (p or {}).get("status") == "imported"
        }

        def _still_resolved(item) -> bool:  # noqa: ANN001
            snap = imported_snapshot.get(item.directory)
            if not snap:
                return False
            media_id = snap.get("imported_media_id")
            media_type = snap.get("imported_media_type")
            if not media_id or not media_type:
                pass
            else:
                pool = tracked_show_ids if media_type == "show" else tracked_movie_ids
                if media_id not in pool:
                    return False
            return (
                int(snap.get("file_count") or 0) == item.file_count
                and int(snap.get("size_bytes") or 0) == item.size_bytes
            )

        active_items = [it for it in response.items if not _still_resolved(it)]
        new_dirs: set[str] = {it.directory for it in active_items}
        pairs: list[tuple[str, dict]] = [
            (d, p) for d, p in terminal_rows if d not in new_dirs
        ]
        auto_imported = 0

        # Phase 4: per-item auto-import. Each item opens its own short bg
        # sessions inside ``_auto_import_item`` and the follow-up.
        for item in active_items:
            result = (
                await _auto_import_item(
                    item,
                    imports_cfg.auto_import_min_confidence,
                )
                if imports_cfg.auto_import_on_scan
                else None
            )
            if result is not None and result[0] == "imported":
                _, media_type, media = result
                auto_imported += 1
                log.info("Auto-imported %s", item.directory)
                from miramedia.imports.followup import (
                    run_post_import_completion,
                )

                # Follow-up opens its own short bg session — the previous
                # outer session was held across continuous-download + per-
                # episode subtitle HTTP fan-out, the slowest part of scan.
                async with (
                    background_session() as followup_db,
                    bg_show_service() as fu_show_service,
                    bg_movie_service() as fu_movie_service,
                ):
                    await run_post_import_completion(
                        db=followup_db,
                        media_type=media_type,
                        media=media,
                        show_service=fu_show_service,
                        movie_service=fu_movie_service,
                    )
                item.status = "imported"
                item.imported_name = getattr(media, "name", None)
                media_id = getattr(media, "id", None)
                item.imported_media_id = str(media_id) if media_id else None
                item.imported_media_type = media_type
                pairs.append((item.directory, item.model_dump(mode="json")))
                continue
            if result is not None and result[0] == "failed":
                item.status = "failed"
                item.import_error = "auto-import failed"
                pairs.append((item.directory, item.model_dump(mode="json")))
                continue
            pairs.append((item.directory, item.model_dump(mode="json")))

        # Phase 5: writeback + mark scan_run done (short session).
        async with background_session() as db:
            repo = ImportsRepository(db=db)
            await repo.replace_scan_cache(pairs)
            actionable_count = sum(
                1 for _, p in pairs if (p or {}).get("status") != "imported"
            )
            await repo.set_scan_run(
                state=ScanRunState.done,
                finished_at=datetime.now(UTC).replace(tzinfo=None),
                items_found=actionable_count,
                last_error=None,
            )
        log.info(
            "Library scan complete: %d actionable candidate(s) (%d total cached, %d auto-imported)",
            actionable_count,
            len(pairs),
            auto_imported,
        )
    except Exception as exc:
        log.exception("Library scan failed")
        async with background_session() as db:
            repo = ImportsRepository(db=db)
            await repo.set_scan_run(
                state=ScanRunState.error,
                finished_at=datetime.now(UTC).replace(tzinfo=None),
                last_error=str(exc),
            )


# Manual scan trigger (user clicks "Scan now" in /dashboard/imports). The
# scheduled equivalent ``scheduled_library_scan_task`` lives on the background
# broker and calls the same underlying ``_scan_and_cache`` helper — but when
# a user explicitly clicks Scan they want it to start NOW, not queue behind
# a multi-minute auto-import sweep.
@interactive_broker.task(labels={"priority": "interactive"})
async def run_library_scan_task() -> None:
    """Walk all library roots, auto-import strong matches, cache the rest.

    No outer bg session — ``_scan_and_cache`` owns short-lived sessions per
    phase. See its docstring for the phase split.
    """
    await _scan_and_cache()


# Per-item user-driven resolve from /dashboard/imports (manual map, retry,
# ignore). Bounded I/O per call, but the user is staring at the UI waiting
# for it to flip out of "queued" — interactive lane.
@interactive_broker.task(labels={"priority": "interactive"})
async def resolve_import_task(body_json: dict) -> None:
    """Resolve a single imports-page item in the background.

    Builds services from a single short-lived background session; previously
    inherited request-pool sessions via ``TaskiqDepends`` that stayed open
    for the duration of the resolve (which may include disk-walk and import).
    """
    from miramedia.database import background_session
    from miramedia.imports.repository import (
        ImportsRepository,
        ScanWorkerBeginResult,
    )
    from miramedia.imports.schemas import ResolveImportTaskPayload
    from miramedia.imports.service import ImportsService
    from miramedia.indexers.repository import IndexerRepository
    from miramedia.indexers.service import IndexerService
    from miramedia.movies.repository import MovieRepository
    from miramedia.movies.service import MovieService
    from miramedia.notifications.repository import NotificationRepository
    from miramedia.notifications.service import NotificationService
    from miramedia.shows.repository import ShowRepository
    from miramedia.shows.service import ShowService
    from miramedia.torrents.repository import TorrentRepository
    from miramedia.torrents.service import TorrentService

    payload = ResolveImportTaskPayload.model_validate(body_json)
    body = payload.body
    # Collapse the previous 4-session fan-out into one shared bg session.
    # Holding it for a single resolve (bounded I/O) is cheap; the prior
    # nesting could pin 4 connections per concurrent resolve and exhaust
    # the background pool under a bulk-retry burst.
    async with background_session() as db:
        repo = ImportsRepository(db=db)
        torrent_service = TorrentService(torrent_repository=TorrentRepository(db))
        indexer_service = IndexerService(IndexerRepository(db))
        notification_service = NotificationService(NotificationRepository(db))
        show_service = ShowService(
            show_repository=ShowRepository(db),
            torrent_service=torrent_service,
            indexer_service=indexer_service,
            notification_service=notification_service,
        )
        movie_service = MovieService(
            movie_repository=MovieRepository(db),
            torrent_service=torrent_service,
            indexer_service=indexer_service,
            notification_service=notification_service,
        )
        service = ImportsService(
            repository=repo,
            torrent_service=torrent_service,
            show_service=show_service,
            movie_service=movie_service,
        )
        worker_began = False
        try:
            if body.kind == "scan":
                if payload.scan_claim_token is None:
                    log.error(
                        "Scan resolve for %s missing claim token; refusing to mutate",
                        body.id,
                    )
                    return
                if body.media_type is None:
                    log.error(
                        "Scan resolve for %s missing media_type; refusing to mutate",
                        body.id,
                    )
                    return
                began = await repo.begin_manual_scan_worker(
                    body.id,
                    claim_token=payload.scan_claim_token,
                    media_type=body.media_type.value,
                )
                if began.result is ScanWorkerBeginResult.duplicate:
                    log.info(
                        "Duplicate scan delivery for %s; skipping mutation",
                        body.id,
                    )
                    return
                if began.result is ScanWorkerBeginResult.stale:
                    log.info(
                        "Stale scan claim for %s; skipping mutation",
                        body.id,
                    )
                    return
                if began.worker_started_at is None:
                    log.error(
                        "Scan resolve for %s missing worker lease; refusing to mutate",
                        body.id,
                    )
                    return
                worker_began = True
                result = await service.resolve_manual_scan(
                    body, claim_token=payload.scan_claim_token
                )
            else:
                result = await service.resolve(body)
            log.info(
                "Queued import for %s resolved: ok=%s detail=%s",
                body.id,
                result.ok,
                result.detail,
            )
            from miramedia.imports.queue_hooks import (
                schedule_scan_queue_sync,
                schedule_torrent_queue_sync,
            )

            if body.kind == "torrent":
                from uuid import UUID

                schedule_torrent_queue_sync(UUID(body.id))
            else:
                schedule_scan_queue_sync(body.id)
        except Exception as exc:
            log.exception("Queued import failed for %s", body.id)
            if body.kind == "scan":
                if payload.scan_claim_token is None or not worker_began:
                    return
                try:
                    await repo.fail_manual_scan_import(
                        body.id,
                        claim_token=payload.scan_claim_token,
                        error=str(exc),
                    )
                except Exception:
                    log.exception(
                        "Failed to mark scan cache row %s as failed",
                        body.id,
                    )
