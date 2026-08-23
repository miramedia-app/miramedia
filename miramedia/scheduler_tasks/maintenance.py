"""Self-contained scheduler maintenance tasks (logs, cache, Taskiq cleanup)."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from miramedia.config import MiraMediaConfig

log = logging.getLogger(__name__)

POSTER_VARIANT_WIDTHS = (200, 300, 400, 600, 800)
_POSTER_VARIANT_MAX_BYTES = 512 * 1024 * 1024


async def reclaim_stale_queued_imports() -> None:
    """Recover scan rows wedged in "queued" because their worker died.

    Two tiers: unstarted rows (dispatched but never began copying) after
    ``STALE_QUEUED_IMPORT_GRACE``; started rows (process died mid-copy) after
    ``STALLED_WORKER_GRACE``. Startup also runs both passes."""
    from miramedia.database import SessionLocalBackground
    from miramedia.imports.repository import (
        STALE_QUEUED_IMPORT_GRACE,
        STALLED_WORKER_GRACE,
        ImportsRepository,
    )

    async with SessionLocalBackground() as db:
        repo = ImportsRepository(db)
        reclaimed = await repo.reclaim_stale_queued_imports(
            older_than=STALE_QUEUED_IMPORT_GRACE
        )
        stalled = await repo.reclaim_stalled_worker_imports(
            older_than=STALLED_WORKER_GRACE
        )
    if reclaimed:
        log.warning("Reclaimed %d stale queued import(s)", reclaimed)
    if stalled:
        log.warning("Reclaimed %d stalled import worker row(s)", stalled)


async def cleanup_old_logs() -> None:
    from miramedia.database import SessionLocalBackground
    from miramedia.logs.repository import LogRepository

    retention_days = MiraMediaConfig().misc.log_retention_days
    log.info("Cleaning up activity logs older than %s days", retention_days)
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)

    async with SessionLocalBackground() as db:
        deleted = await LogRepository(db).delete_older_than(cutoff)
        await db.commit()
    log.info("Deleted %d old activity log entries", deleted)


def poster_source_for_variant(image_dir: Path, variant_path: Path) -> Path | None:
    stem = variant_path.stem
    suffix = variant_path.suffix
    for width in POSTER_VARIANT_WIDTHS:
        width_suffix = f"-{width}"
        if stem.endswith(width_suffix):
            source_stem = stem[: -len(width_suffix)]
            return image_dir / f"{source_stem}{suffix}"
    return None


def variant_access_time(path: Path) -> float:
    stat = path.stat()
    return stat.st_atime if stat.st_atime > 0 else stat.st_mtime


def evict_poster_variants(
    image_dir: Path,
    variant_dir: Path,
    *,
    max_total_bytes: int = _POSTER_VARIANT_MAX_BYTES,
) -> list[Path]:
    """Delete orphaned poster variants and enforce a total size cap."""
    if not variant_dir.is_dir():
        return []

    deleted: list[Path] = []
    remaining: list[tuple[Path, int]] = []

    for variant_path in variant_dir.iterdir():
        if not variant_path.is_file():
            continue
        source = poster_source_for_variant(image_dir, variant_path)
        if source is None or not source.is_file():
            try:
                variant_path.unlink()
                deleted.append(variant_path)
            except OSError:
                log.debug(
                    "failed to delete orphaned poster variant %s",
                    variant_path,
                    exc_info=True,
                )
            continue
        remaining.append((variant_path, variant_path.stat().st_size))

    total = sum(size for _, size in remaining)
    if total <= max_total_bytes:
        return deleted

    remaining.sort(key=lambda item: variant_access_time(item[0]))
    for variant_path, size in remaining:
        if total <= max_total_bytes:
            break
        try:
            variant_path.unlink()
            deleted.append(variant_path)
            total -= size
        except OSError:
            log.debug(
                "failed to delete poster variant %s during eviction",
                variant_path,
                exc_info=True,
            )

    return deleted


async def cleanup_poster_variants() -> None:
    image_dir = MiraMediaConfig().misc.image_directory
    variant_dir = image_dir / ".variants"
    log.info("Cleaning up poster variant cache in %s", variant_dir)
    deleted = await asyncio.to_thread(evict_poster_variants, image_dir, variant_dir)
    log.info("Deleted %d poster variant file(s)", len(deleted))


async def cleanup_hls_cache() -> None:
    from miramedia.streams.transcode import sweep_hls_cache

    cfg = MiraMediaConfig().streams
    max_bytes = int(cfg.hls_cache_max_gb * 1024 * 1024 * 1024)
    max_age_s = cfg.hls_cache_max_age_days * 86400
    if max_bytes <= 0 or max_age_s <= 0:
        return
    log.info(
        "Sweeping HLS cache (max %.1f GB, max age %d days)",
        cfg.hls_cache_max_gb,
        cfg.hls_cache_max_age_days,
    )
    summary = await asyncio.to_thread(sweep_hls_cache, max_bytes, max_age_s)
    log.info(
        "HLS cache sweep deleted %d dir(s), freed %d bytes (%d bytes remaining)",
        summary["deleted_dirs"],
        summary["freed_bytes"],
        summary["remaining_bytes"],
    )


async def purge_old_indexer_query_results() -> None:
    """Delete stale indexer search cache rows (unreferenced after download)."""
    from sqlalchemy import delete

    from miramedia.database import SessionLocalBackground
    from miramedia.indexers.models import IndexerQueryResult

    retention_days = MiraMediaConfig().misc.indexer_query_result_retention_days
    if retention_days <= 0:
        return
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    log.info("Purging indexer_query_result rows older than %d days", retention_days)
    async with SessionLocalBackground() as db:
        result = await db.execute(
            delete(IndexerQueryResult).where(IndexerQueryResult.created_at < cutoff)
        )
        await db.commit()
        deleted = result.rowcount or 0
    log.info("Purged %d indexer query result rows", deleted)


async def cleanup_old_notifications() -> None:
    """Delete read in-app notifications older than ``notifications.native.retention_days``."""
    from miramedia.database import SessionLocalBackground
    from miramedia.notifications.repository import NotificationRepository

    cfg = MiraMediaConfig().notifications.native
    if not cfg.enabled:
        return
    retention_days = cfg.retention_days
    log.info("Cleaning up read notifications older than %s days", retention_days)
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)

    async with SessionLocalBackground() as db:
        deleted = await NotificationRepository(db).delete_read_older_than(cutoff)
        await db.commit()
    log.info("Deleted %d old read notifications", deleted)


async def cleanup_expired_manual_parse_tokens() -> None:
    from miramedia.database import SessionLocalBackground
    from miramedia.torrents.repository import TorrentRepository

    async with SessionLocalBackground() as db:
        deleted = await TorrentRepository(db).delete_expired_manual_parse_tokens(
            ttl_minutes=30
        )
        await db.commit()
    if deleted:
        log.info("Deleted %d expired manual parse tokens", deleted)


async def save_native_resume_data() -> None:
    """Periodically snapshot libtorrent resume data to disk.

    The native client only persists resume data at graceful shutdown. An
    ungraceful kill (OOM, ``docker kill``, host crash) would otherwise lose
    all in-flight torrents — on restart they're never re-added to the session
    and every pause/resume/remove logs "Torrent not found". Checkpointing on a
    cron lets the next start recover torrents added since the last clean stop.
    """
    cfg = MiraMediaConfig().torrents.native
    if not cfg.enabled:
        return
    from miramedia.torrents.backends.native import NativeDownloadClient

    # save_resume_data() is a blocking libtorrent call (it pumps the alert
    # queue with sleeps) — run it off the event loop.
    await asyncio.to_thread(NativeDownloadClient().save_resume_data)


async def purge_old_taskiq_messages(*, taskiq_table_names: set[str]) -> None:
    """Delete stranded taskiq message rows older than 7 days.

    Each lane broker owns its own queue table — ``taskiq_messages_interactive``
    and ``taskiq_messages_background``. There is NO bare ``taskiq_messages``
    table, so we purge each broker's actual ``table_name`` rather than a
    hardcoded name.

    PostgresqlBroker stores each enqueued message as a row in its queue table;
    the listening worker atomically removes it via
    ``DELETE ... RETURNING`` when it claims the message. The table
    therefore should only contain rows that were enqueued but never
    claimed — either because no worker was running at the time or because
    a NOTIFY was lost. On a long-running instance these orphaned rows
    accumulate; this nightly purge bounds the growth.
    """
    from sqlalchemy import text

    from miramedia.database import SessionLocalBackground

    async with SessionLocalBackground() as db:
        for table_name in taskiq_table_names:
            try:
                result = await db.execute(
                    text(
                        f"DELETE FROM {table_name} "  # noqa: S608 (trusted identifier)
                        "WHERE created_at < NOW() - INTERVAL '7 days'"
                    )
                )
                await db.commit()
                deleted = result.rowcount or 0
                if deleted:
                    log.info("Purged %d stale rows from %s", deleted, table_name)
            except Exception:
                log.exception("Failed to purge %s", table_name)
                await db.rollback()
