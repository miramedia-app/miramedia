"""Core API routes extracted from main.py (health, features, dashboard, static images)."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select, text
from starlette.responses import FileResponse

from miramedia.auth.users import current_active_user, current_superuser
from miramedia.config import MiraMediaConfig
from miramedia.database import DbSessionDependency

log = logging.getLogger(__name__)
config = MiraMediaConfig()

router = APIRouter()

_VARIANT_WIDTHS = (200, 300, 400, 600, 800)
# Deliberately never removed: key space is bounded (poster files x clamped
# widths), and eager pop reintroduces a duplicate-generation race for queued waiters.
_variant_locks: dict[tuple[str, int], asyncio.Lock] = {}

_EXPECTED_ALEMBIC_HEAD: str | None = None


def _get_expected_alembic_head() -> str | None:
    global _EXPECTED_ALEMBIC_HEAD
    if _EXPECTED_ALEMBIC_HEAD is None:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        cfg = Config("alembic.ini")
        _EXPECTED_ALEMBIC_HEAD = ScriptDirectory.from_config(cfg).get_current_head()
    return _EXPECTED_ALEMBIC_HEAD


async def _detailed_health() -> dict:
    payload: dict = {
        "status": "ok",
        "version": os.getenv("PUBLIC_VERSION"),
        "message": "Hello World!",
    }

    db_section: dict = {"ok": False}
    try:
        from miramedia.database import (
            background_engine,
            export_pool_gauges,
            get_engine,
            healthcheck_engine,
        )

        if healthcheck_engine is None:
            db_section["error"] = "healthcheck engine not initialised"
        else:
            try:
                async with healthcheck_engine.connect() as conn:
                    await asyncio.wait_for(conn.execute(text("SELECT 1")), timeout=2.0)
                db_section["ok"] = True
            except TimeoutError:
                db_section["error"] = "timeout"

        pools: dict[str, dict] = {}
        for name, eng in (("request", get_engine()), ("background", background_engine)):
            if eng is None:
                continue
            try:
                p = eng.pool
                pools[name] = {
                    "size": p.size(),
                    "checked_out": p.checkedout(),
                    "overflow": p.overflow(),
                }
            except Exception as exc:
                log.warning("health check section failed", exc_info=exc)
                pools[name] = {"error": "unavailable"}
        db_section["pools"] = pools

        # Refresh pool gauges for the /metrics endpoint while we're here —
        # cheap, idempotent, and means consumers see fresh values without
        # an extra scrape-hook hookup.
        try:
            export_pool_gauges()
        except Exception:  # noqa: S110 — best-effort gauge refresh, non-fatal
            pass
    except Exception as exc:
        log.warning("health check section failed", exc_info=exc)
        db_section = {"ok": False, "error": "unavailable"}
    payload["db"] = db_section

    # Alembic head vs DB head — divergence means the deployed image expects a
    # schema the DB doesn't have. Expected head is memoized (filesystem scan is
    # immutable for the process). Current revision is read with a raw SELECT to
    # avoid MigrationContext.configure(), which logs at INFO on every call.
    alembic_section: dict = {"ok": False}
    try:
        from miramedia.database import healthcheck_engine as _hc

        expected = _get_expected_alembic_head()
        async with _hc.connect() as conn:  # type: ignore[union-attr]
            row = (
                await conn.execute(text("SELECT version_num FROM alembic_version"))
            ).first()
        current = row[0] if row else None
        alembic_section = {
            "ok": current == expected,
            "expected_head": expected,
            "current_revision": current,
        }
    except Exception as exc:
        log.warning("health check section failed", exc_info=exc)
        alembic_section = {"ok": False, "error": "unavailable"}
    payload["alembic"] = alembic_section

    # Metadata cache stats — uses get_all_cache_stats() from the cache module.
    cache_section: dict = {"ok": False}
    try:
        from miramedia.metadata.cache import get_all_cache_stats

        stats = get_all_cache_stats()
        sum_hits = sum(s["hits"] for s in stats.values())
        sum_misses = sum(s["misses"] for s in stats.values())
        total = sum_hits + sum_misses
        cache_section = {
            "ok": True,
            "metadata": {
                "sum_hits": sum_hits,
                "sum_misses": sum_misses,
                "hit_rate": round(sum_hits / total, 4) if total > 0 else 0.0,
                "cache_count": len(stats),
                "per_cache": stats,
            },
        }
    except Exception as exc:
        log.warning("health check section failed", exc_info=exc)
        cache_section = {"ok": False, "error": "unavailable"}
    payload["cache"] = cache_section

    return payload


@router.get("/health")
async def hello_world() -> dict:
    """Healthcheck must never raise — partial degradation is reported via
    per-section ``.ok`` flags so the docker compose healthcheck does not flap
    when an optional subsystem (cache, pool) is temporarily unavailable.

    DB pings use a dedicated ``NullPool`` engine so a saturated request /
    background pool cannot stall the liveness probe. Both real pools' stats
    are reported. An ``alembic`` section flags head-mismatch (deployed app
    expects revision X but DB is at Y) which would otherwise silently
    surface as ``UndefinedColumn`` errors on the first hot path.

    Anonymous callers receive liveness plus per-section ``ok`` booleans only;
    use ``GET /health/details`` (superuser) for the full diagnostic payload.
    """
    detailed = await _detailed_health()
    return {
        "status": detailed["status"],
        "db": {"ok": detailed["db"]["ok"]},
        "alembic": {"ok": detailed["alembic"]["ok"]},
        "cache": {"ok": detailed["cache"]["ok"]},
    }


@router.get(
    "/health/details",
    dependencies=[Depends(current_superuser)],
)
async def health_details() -> dict:
    """Full health diagnostics for operators (superuser only)."""
    return await _detailed_health()


class FeatureFlags(BaseModel):
    requests: bool
    subtitles: bool
    notifications: bool
    watchlists: bool
    custom_lists: bool
    watch_next: bool
    watch_next_include_specials: bool
    upcoming: bool
    upcoming_default_past_days: int
    upcoming_default_future_days: int
    continue_watching: bool
    streaming: bool
    downloads: bool


@router.get("/features")
async def get_features() -> FeatureFlags:
    return FeatureFlags(
        requests=config.requests.enabled,
        subtitles=config.subtitles.enabled,
        notifications=config.notifications.native.enabled,
        watchlists=config.watchlists.enabled,
        custom_lists=config.watchlists.custom_lists_enabled,
        watch_next=config.watchlists.watch_next_enabled,
        watch_next_include_specials=config.watchlists.native.watch_next_include_specials,
        upcoming=config.watchlists.upcoming_enabled,
        upcoming_default_past_days=config.watchlists.native.upcoming_default_past_days,
        upcoming_default_future_days=config.watchlists.native.upcoming_default_future_days,
        continue_watching=config.playback.continue_watching,
        streaming=config.streams.enabled,
        downloads=config.streams.downloads,
    )


class DashboardSummary(BaseModel):
    shows: int = 0
    movies: int = 0
    torrents: int = 0
    requests_pending: int = 0
    imports_failed: int = 0
    imports_ambiguous: int = 0


@router.get(
    "/dashboard/summary",
    dependencies=[Depends(current_active_user)],
)
async def get_dashboard_summary(db: DbSessionDependency) -> DashboardSummary:
    """One cheap dashboard-count read instead of several parallel requests."""
    from miramedia.file_status import ImportOutcome
    from miramedia.movies.models import Movie, MovieFile
    from miramedia.requests.models import MediaRequest, RequestStatus
    from miramedia.shows.models import EpisodeFile, Show
    from miramedia.torrents.models import Torrent
    from miramedia.torrents.schemas import TorrentStatus

    failed_statuses = (ImportOutcome.failed_io, ImportOutcome.failed_no_match)
    show_count = await db.scalar(select(func.count()).select_from(Show))
    movie_count = await db.scalar(select(func.count()).select_from(Movie))
    torrent_count = await db.scalar(
        select(func.count())
        .select_from(Torrent)
        .where(Torrent.status != TorrentStatus.finished)
    )
    pending_requests = await db.scalar(
        select(func.count())
        .select_from(MediaRequest)
        .where(MediaRequest.status == RequestStatus.pending)
    )
    # Collapse the failed/ambiguous counts per table into a single SELECT each
    # using conditional aggregates, so we issue one round-trip per table instead
    # of four.
    movie_failed, movie_ambiguous = (
        await db.execute(
            select(
                func.count().filter(MovieFile.import_status.in_(failed_statuses)),
                func.count().filter(MovieFile.import_status == ImportOutcome.ambiguous),
            ).select_from(MovieFile)
        )
    ).one()
    episode_failed, episode_ambiguous = (
        await db.execute(
            select(
                func.count().filter(EpisodeFile.import_status.in_(failed_statuses)),
                func.count().filter(
                    EpisodeFile.import_status == ImportOutcome.ambiguous
                ),
            ).select_from(EpisodeFile)
        )
    ).one()
    return DashboardSummary(
        shows=int(show_count or 0),
        movies=int(movie_count or 0),
        torrents=int(torrent_count or 0),
        requests_pending=int(pending_requests or 0) if config.requests.enabled else 0,
        imports_failed=int(movie_failed or 0) + int(episode_failed or 0),
        imports_ambiguous=int(movie_ambiguous or 0) + int(episode_ambiguous or 0),
    )


# Poster filenames are content-hashed (id-based), so a long cache + revalidate
# on metadata refresh is safe. Browsers can serve repeat hits from disk.
@router.get("/static/image/{filename}")
async def serve_image(
    filename: str,
    w: Annotated[int | None, Query(ge=64, le=1200)] = None,
) -> FileResponse:
    base = config.misc.image_directory.resolve()
    file_path = (config.misc.image_directory / filename).resolve()
    if not file_path.is_relative_to(base):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Image not found"
        )
    if not file_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Image not found"
        )
    if w is not None:
        variant = _fresh_poster_variant(file_path, w)
        if variant is None:
            variant = await _poster_variant_async(file_path, w)
        if variant is not None:
            file_path = variant
    media_type = "image/jpeg"
    if filename.endswith(".avif"):
        media_type = "image/avif"
    elif filename.endswith(".webp"):
        media_type = "image/webp"
    elif filename.endswith(".png"):
        media_type = "image/png"
    return FileResponse(
        file_path,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )


def _clamp_variant_width(width: int) -> int:
    return min(_VARIANT_WIDTHS, key=lambda candidate: abs(candidate - width))


def _variant_cache_path(file_path: Path, width: int) -> Path:
    clamped = _clamp_variant_width(width)
    variant_dir = config.misc.image_directory / ".variants"
    return variant_dir / f"{file_path.stem}-{clamped}{file_path.suffix}"


def _fresh_poster_variant(file_path: Path, width: int) -> Path | None:
    """Return a cached variant when it exists and is newer than the source."""
    variant = _variant_cache_path(file_path, width)
    if variant.exists() and variant.stat().st_mtime_ns >= file_path.stat().st_mtime_ns:
        return variant
    return None


def _generate_poster_variant(file_path: Path, width: int) -> Path | None:
    """Generate/cache a resized poster variant next to the image cache."""
    try:
        from PIL import Image

        clamped = _clamp_variant_width(width)
        variant_dir = config.misc.image_directory / ".variants"
        variant_dir.mkdir(parents=True, exist_ok=True)
        variant = _variant_cache_path(file_path, width)
        with Image.open(file_path) as image:
            image.thumbnail((clamped, int(clamped * 1.5)))
            save_kwargs = {"quality": 82, "optimize": True}
            if file_path.suffix.lower() in {".jpg", ".jpeg"}:
                save_kwargs["progressive"] = True
            image.save(variant, **save_kwargs)
    except Exception:
        log.debug("poster resize failed for %s", file_path, exc_info=True)
        return None
    else:
        return variant


async def _poster_variant_async(file_path: Path, width: int) -> Path | None:
    key = (str(file_path), width)
    lock = _variant_locks.setdefault(key, asyncio.Lock())
    async with lock:
        fresh = _fresh_poster_variant(file_path, width)
        if fresh is not None:
            return fresh
        return await asyncio.to_thread(_generate_poster_variant, file_path, width)
