from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator, Callable

import requests
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse, ServerSentEvent

from miramedia.auth.users import current_superuser
from miramedia.database import DbSessionDependency, release_session_before_external_io
from miramedia.indexers.dependencies import indexer_repository_dep
from miramedia.indexers.repository import IndexerRepository
from miramedia.indexers.schemas import (
    IndexerSiteCreate,
    IndexerSiteId,
    IndexerSiteRead,
    IndexerSiteTestResult,
    IndexerSiteUpdate,
    mask_indexer_site_read,
    strip_indexer_api_key_sentinel,
)
from miramedia.indexers.utils import preview_score
from miramedia.settings.validation import SECRET_MASK

log = logging.getLogger(__name__)

# Whole-op cap for the CF solve a connectivity test drives. We let the test
# run a GENUINE solve (so the result reflects what real searches will get) but
# bound it to the solve budget rather than the full ``total_timeout_seconds``
# (≈580s, which front-loads a 240s cold-browser-launch worst case). The browser
# is normally pre-warmed, so this covers page-load + the full solve loop. Must
# stay under the dev proxy's ``proxyTimeout`` (web/next.config.ts) so the proxy
# waits for the backend instead of resetting the socket into a 500.
_TEST_CF_SOLVE_TIMEOUT_SECONDS = 180.0

router = APIRouter(
    prefix="/indexers",
    tags=["indexers"],
    dependencies=[Depends(current_superuser)],
)


@router.get("/sites")
async def list_indexer_sites(
    repo: indexer_repository_dep,
) -> list[IndexerSiteRead]:
    """List all configured indexer sites."""
    sites = await repo.get_all_sites()
    return [mask_indexer_site_read(site) for site in sites]


@router.post("/sites", status_code=status.HTTP_201_CREATED)
async def create_indexer_site(
    data: IndexerSiteCreate,
    repo: indexer_repository_dep,
) -> IndexerSiteRead:
    """Add a new custom indexer site."""
    create_data = data
    if data.api_key == SECRET_MASK:
        create_data = data.model_copy(update={"api_key": ""})
    return mask_indexer_site_read(await repo.create_site(create_data))


@router.get("/sites/{site_id}")
async def get_indexer_site(
    site_id: IndexerSiteId,
    repo: indexer_repository_dep,
) -> IndexerSiteRead:
    """Get details of a specific indexer site."""
    try:
        return mask_indexer_site_read(await repo.get_site(site_id))
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Site not found"
        ) from None


@router.put("/sites/{site_id}")
async def update_indexer_site(
    site_id: IndexerSiteId,
    data: IndexerSiteUpdate,
    repo: indexer_repository_dep,
) -> IndexerSiteRead:
    """Update an indexer site configuration."""
    try:
        return mask_indexer_site_read(
            await repo.update_site(site_id, strip_indexer_api_key_sentinel(data))
        )
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Site not found"
        ) from None


@router.delete("/sites/{site_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_indexer_site(
    site_id: IndexerSiteId,
    repo: indexer_repository_dep,
) -> None:
    """Delete a custom indexer site. Preloaded sites cannot be deleted."""
    try:
        site = await repo.get_site(site_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Site not found"
        ) from None

    if site.is_preloaded:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Preloaded sites cannot be deleted. Disable them instead.",
        )
    await repo.delete_site(site_id)


class ScoringPreviewRequest(BaseModel):
    title: str = Field(min_length=1)
    flags: list[str] = []
    seeders: int = 1
    age_days: int = 0


class ScoringPreviewBreakdownEntry(BaseModel):
    rule: str
    matched: bool
    delta: int
    reason: str


class ScoringPreviewResponse(BaseModel):
    total: int
    breakdown: list[ScoringPreviewBreakdownEntry]


@router.post("/scoring/preview")
def scoring_preview(body: ScoringPreviewRequest) -> ScoringPreviewResponse:
    """Walk every configured scoring rule against a synthetic torrent and report deltas.

    Lets users tune scoring rules without needing live indexer results.
    """
    result = preview_score(
        body.title,
        flags=body.flags,
        seeders=body.seeders,
        age_days=body.age_days,
    )
    return ScoringPreviewResponse(
        total=result["total"],
        breakdown=[
            ScoringPreviewBreakdownEntry(**entry) for entry in result["breakdown"]
        ],
    )


@router.get("/sites/{site_id}/test/stream")
async def test_indexer_site_stream(
    site_id: IndexerSiteId, db: DbSessionDependency
) -> EventSourceResponse:
    """SSE variant of the site test.

    Streams human-readable ``status`` events as the (possibly minutes-long)
    Cloudflare solve progresses, then a terminal ``result`` event carrying the
    ``IndexerSiteTestResult`` JSON, then ``done``. The solve itself runs on a
    short background session (see ``_run``), but the router-level
    ``current_superuser`` auth lookup pins this request's ``get_session``
    connection idle-in-transaction for the whole SSE lifetime — long enough to
    be reaped by ``idle_in_transaction_session_timeout``, killing the finalizer
    commit. Release it up front; we never touch ``db`` here again.
    """
    await release_session_before_external_io(db)
    main_loop = asyncio.get_running_loop()
    progress_queue: asyncio.Queue = asyncio.Queue(maxsize=100)
    DONE = object()  # noqa: N806 — module-style sentinel local to closure

    def _safe_put(item: object) -> None:
        # Drop-oldest if the client stalls: status messages are advisory, so
        # losing an intermediate phase never corrupts the terminal result.
        try:
            progress_queue.put_nowait(item)
        except asyncio.QueueFull:
            try:
                progress_queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                progress_queue.put_nowait(item)
            except asyncio.QueueFull:
                pass

    def progress(msg: str) -> None:
        # Called from the request loop AND from the bypass worker-loop thread —
        # hop onto the request loop before touching the asyncio.Queue.
        main_loop.call_soon_threadsafe(_safe_put, ("status", msg))

    async def _run() -> None:
        from miramedia.database import background_session

        try:
            async with background_session() as db:
                repo = IndexerRepository(db)
                try:
                    site = await repo.get_site(site_id)
                except ValueError:
                    _safe_put(
                        (
                            "result",
                            IndexerSiteTestResult(
                                success=False, message="Site not found"
                            ).model_dump_json(),
                        )
                    )
                    return
                result = await _run_site_test(site, site_id, repo, progress)
                await repo.record_site_test(
                    site_id, "ok" if result.success else "error"
                )
                if result.success:
                    await repo.record_site_success(site_id)
            _safe_put(("result", result.model_dump_json()))
        except Exception:
            log.exception("SSE indexer test failed for %s", site_id)
            _safe_put(
                (
                    "result",
                    IndexerSiteTestResult(
                        success=False, message="Unexpected error — see server logs"
                    ).model_dump_json(),
                )
            )
        finally:
            _safe_put(DONE)

    async def event_publisher() -> AsyncGenerator[ServerSentEvent]:
        task = asyncio.create_task(_run())
        try:
            while True:
                item = await progress_queue.get()
                if item is DONE:
                    yield ServerSentEvent(event="done", data="{}")
                    return
                kind, data = item
                if kind == "status":
                    yield ServerSentEvent(
                        event="status", data=json.dumps({"message": data})
                    )
                else:  # "result"
                    yield ServerSentEvent(event="result", data=data)
        finally:
            # Client disconnected or stream ended; cancel the orchestrator. The
            # bounded solve thread it may have spawned finishes on its own cap.
            task.cancel()

    return EventSourceResponse(
        event_publisher(),
        headers={"X-Accel-Buffering": "no", "Content-Encoding": "identity"},
    )


async def _run_site_test(
    site: IndexerSiteRead,
    site_id: IndexerSiteId,
    repo: IndexerRepository,
    progress: Callable[[str], None] | None = None,
) -> IndexerSiteTestResult:
    def _emit(msg: str) -> None:
        if progress is not None:
            progress(msg)

    _emit(f"Connecting to {site.url}…")

    ua = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    )

    # For preloaded native sites the class is the source of truth for both the
    # Cloudflare flag and what URL is representative to probe. Probing the bare
    # root misreads sites whose root is Cloudflare-gated while their data
    # endpoint serves fine (e.g. apibay.org/ 403s, apibay.org/q.php?q= is 200).
    from miramedia.indexers.sites import get_preloaded_sites

    site_cls = (
        get_preloaded_sites().get(site.name) if site.site_type != "torznab" else None
    )

    try:
        if site.site_type == "torznab":
            params = {"t": "caps"}
            if site.api_key:
                params["apikey"] = site.api_key
            response = await asyncio.to_thread(
                lambda: requests.get(
                    site.url,
                    params=params,
                    timeout=15,
                    headers={"User-Agent": ua},
                )
            )
        else:
            test_path = getattr(site_cls, "test_path", "") or ""
            probe_url = site.url.rstrip("/") + test_path if test_path else site.url
            response = await asyncio.to_thread(
                lambda: requests.get(
                    probe_url,
                    timeout=15,
                    headers={"User-Agent": ua},
                )
            )

        from miramedia.cloudflare import get_cloudflare_bypass, is_cloudflare_challenge

        cf_detected = is_cloudflare_challenge(response)

        # Persist the Cloudflare flag. For preloaded native sites trust the
        # class declaration — a single probe can't decide it (a 200 now doesn't
        # mean never-challenged; a gated landing page doesn't mean the data
        # endpoint is walled). Fall back to probe detection for custom/torznab.
        flag_value = (
            site_cls.cloudflare_protected if site_cls is not None else cf_detected
        )
        if site.cloudflare_protected != flag_value:
            await repo.update_site(
                site_id, IndexerSiteUpdate(cloudflare_protected=flag_value)
            )

        if cf_detected:
            _emit("Cloudflare challenge detected")
            bypass = get_cloudflare_bypass()
            if not bypass.config.enabled:
                log.warning(
                    "Indexer test %s (%s): Cloudflare detected but bypass is disabled",
                    site.name,
                    site.url,
                )
                return IndexerSiteTestResult(
                    success=False,
                    message=(
                        f"{site.name} is behind Cloudflare but the Cloudflare "
                        "bypass is disabled. Enable it in System Settings."
                    ),
                    cloudflare_detected=True,
                    cloudflare_solved=False,
                )
            from urllib.parse import urlparse

            domain = urlparse(site.url).netloc
            cached = bypass.get_cached_session(domain)
            if cached:
                log.info(
                    "Indexer test %s (%s): OK via cached CF bypass", site.name, site.url
                )
                _emit("Using cached Cloudflare session")
                return IndexerSiteTestResult(
                    success=True,
                    message=f"Successfully connected to {site.name} (using cached Cloudflare bypass)",
                    cloudflare_detected=True,
                    cloudflare_solved=True,
                )

            # Drive a genuine solve, bounded so the request finishes before the
            # dev proxy's timeout (see _TEST_CF_SOLVE_TIMEOUT_SECONDS). The
            # frontend streams progress over SSE and shows the live phase, then
            # the real pass/fail.
            session = await asyncio.to_thread(
                bypass.solve, site.url, _TEST_CF_SOLVE_TIMEOUT_SECONDS, progress
            )
            if session:
                log.info(
                    "Indexer test %s (%s): OK via fresh CF bypass", site.name, site.url
                )
                return IndexerSiteTestResult(
                    success=True,
                    message=f"Successfully connected to {site.name}",
                    cloudflare_detected=True,
                    cloudflare_solved=True,
                )
            log.warning(
                "Indexer test %s (%s): FAILED — Cloudflare bypass returned no "
                "session within %.0fs",
                site.name,
                site.url,
                _TEST_CF_SOLVE_TIMEOUT_SECONDS,
            )
            return IndexerSiteTestResult(
                success=False,
                message=f"Connection failed — couldn't clear {site.name}'s Cloudflare challenge",
                cloudflare_detected=True,
                cloudflare_solved=False,
            )

        if response.ok:
            log.info(
                "Indexer test %s (%s): OK (HTTP %s)",
                site.name,
                site.url,
                response.status_code,
            )
            return IndexerSiteTestResult(
                success=True,
                message=f"Successfully connected to {site.name}",
                cloudflare_detected=False,
            )

        log.warning(
            "Indexer test %s (%s): FAILED — HTTP %s %s",
            site.name,
            site.url,
            response.status_code,
            response.reason,
        )
        return IndexerSiteTestResult(
            success=False,
            message=f"HTTP {response.status_code}: {response.reason}",
        )

    except requests.Timeout:
        log.warning(
            "Indexer test %s (%s): FAILED — connection timed out (15s)",
            site.name,
            site.url,
        )
        return IndexerSiteTestResult(
            success=False,
            message="Connection timed out",
        )
    except requests.ConnectionError as exc:
        log.warning(
            "Indexer test %s (%s): FAILED — connection error: %s",
            site.name,
            site.url,
            exc,
        )
        return IndexerSiteTestResult(
            success=False,
            message="Connection failed — check the URL",
        )
    except Exception:
        log.exception(
            "Indexer test %s (%s): FAILED — unexpected error",
            site.name,
            site.url,
        )
        return IndexerSiteTestResult(
            success=False,
            message="Unexpected error — see server logs",
        )
