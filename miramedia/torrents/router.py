import asyncio
import json
import logging
import threading
import uuid
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from sse_starlette.sse import EventSourceResponse, ServerSentEvent

from miramedia.auth.users import current_active_user, current_superuser
from miramedia.exceptions import NotFoundError
from miramedia.imports.matching import find_candidate_media_matches
from miramedia.imports.schemas import ManualParseCandidate, ManualParseResponse
from miramedia.indexers.dependencies import indexer_service_dep
from miramedia.indexers.schemas import (
    IndexerQueryResult,
    IndexerQueryResultId,
    SearchStreamChunk,
)
from miramedia.movies.dependencies import movie_repository_dep, movie_service_dep
from miramedia.naming import episode_file_stem, movie_file_stem
from miramedia.shows.dependencies import show_repository_dep, show_service_dep
from miramedia.shows.service import (
    filter_results_to_episode,
    filter_results_to_season,
)
from miramedia.torrents.dependencies import (
    torrent_dep,
    torrent_repository_dep,
    torrent_service_dep,
)
from miramedia.torrents.quality_naming import NameParts
from miramedia.torrents.schemas import (
    BulkRetryImportFailure,
    BulkRetryImportRequest,
    BulkRetryImportResult,
    DryRunImportPlanItem,
    DryRunImportResult,
    ImportStatusCounts,
    ImportStatusFilter,
    IntegrityActionResult,
    IntegrityMismatch,
    ManualDownloadRequest,
    ManualMapRequest,
    ManualMapResult,
    ManualMapTargetType,
    MediaType,
    PaginatedTorrentImports,
    Quality,
    RetryImportResult,
    RichTorrent,
    Torrent,
    TorrentFilesResponse,
    TorrentStatus,
    UnifiedDownloadRequest,
)
from miramedia.torrents.utils import parse_magnet_or_torrent_file

if TYPE_CHECKING:
    from miramedia.movies.service import MovieService
    from miramedia.shows.service import ShowService

log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/torrents",
    tags=["torrents"],
    dependencies=[Depends(current_active_user)],
)

# A real .torrent metainfo file is a few KB to low-hundreds of KB even for
# large multi-file torrents. Cap uploads well above that to reject oversized
# bodies before buffering them into memory.
MAX_TORRENT_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MiB


# -----------------------------------------------------------------------------
# UNIFIED SEARCH & DOWNLOAD
# -----------------------------------------------------------------------------


@router.get(
    "/search/stream",
    dependencies=[Depends(current_superuser)],
    response_model=None,
    responses={
        200: {
            "model": SearchStreamChunk,
            "description": "SSE stream of SearchStreamChunk events",
        }
    },
)
async def search_torrents_stream(
    request: Request,  # noqa: ARG001 — required by route signature
    indexer_service: indexer_service_dep,
    media_type: Annotated[MediaType, Query()],
    media_id: Annotated[uuid.UUID, Query()],
    show_service: show_service_dep,
    movie_service: movie_service_dep,
    season_number: Annotated[int | None, Query()] = None,
    episode_number: Annotated[int | None, Query()] = None,
    query_override: Annotated[str | None, Query()] = None,
    quality: Annotated[list[str] | None, Query()] = None,
    codec: Annotated[list[str] | None, Query()] = None,
) -> EventSourceResponse:
    """Server-Sent Events variant of /search.

    Emits one ``SearchStreamChunk`` (event: ``results``) per indexer / native
    backend site as it completes so the UI can render results progressively
    instead of waiting for the slowest backend. Stream ends with a ``done``
    event. When the client disconnects the search task is cancelled and
    ``abort`` is set so in-flight backend workers stop publishing.
    """
    media_obj = None
    if media_type == MediaType.show:
        show = await show_service.show_repository.get_show_by_id(show_id=media_id)
        media_obj = show
        is_tv = True
    else:
        movie = await movie_service.get_movie_by_id(media_id)
        media_obj = movie
        is_tv = False

    from miramedia.indexers.utils import (
        evaluate_indexer_query_results,
        search_name_variants,
    )

    # Bounded queue with drop-oldest semantics. Caps backend memory if the
    # client stalls without observable harm: search results are append-only
    # so dropping the head only delays the slowest backends' final
    # rendering, never erases earlier chunks the client already saw.
    chunk_queue: asyncio.Queue = asyncio.Queue(maxsize=50)
    DONE = object()  # noqa: N806 — module-style sentinel constant, local to closure
    abort = threading.Event()
    main_loop = asyncio.get_running_loop()

    def _safe_put(item: object) -> None:
        try:
            chunk_queue.put_nowait(item)
        except asyncio.QueueFull:
            try:
                chunk_queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                chunk_queue.put_nowait(item)
            except asyncio.QueueFull:
                pass

    # Variant queries (full title vs pre-colon main title) can surface the
    # same torrent from more than one fan-out — dedupe across chunks.
    seen_urls: set[str] = set()
    seen_lock = threading.Lock()

    def _on_partial(source_name: str, results: list[IndexerQueryResult]) -> None:
        # Called from indexer backend threadpool workers — must hop back
        # onto the event loop before touching the asyncio.Queue.
        if abort.is_set():
            return
        try:
            with seen_lock:
                fresh = [r for r in results if r.download_url not in seen_urls]
                seen_urls.update(r.download_url for r in fresh)
            if not fresh:
                return
            if query_override:
                # Manual query: the user asked for exactly this search — don't
                # second-guess the results against the media name (mirrors the
                # non-streaming /search override behavior).
                scored = fresh
            else:
                scored = evaluate_indexer_query_results(
                    query_results=fresh,
                    media=media_obj,
                    is_tv=is_tv,
                    quality_allowed=quality,
                    codec_allowed=codec,
                )
            scored = _filter_results_by_options(scored, quality, codec)
            if not query_override and media_type == MediaType.show:
                if season_number is not None and episode_number is not None:
                    scored = filter_results_to_episode(
                        scored, season_number, episode_number
                    )
                elif season_number is not None:
                    scored = filter_results_to_season(scored, season_number)
            log.debug(
                "SSE chunk: source=%s raw=%d scored=%d",
                source_name,
                len(results),
                len(scored),
            )
            if not scored:
                return
            # Queue the raw scored objects; the consumer task persists them
            # to the DB before serializing + yielding so the /download
            # endpoint can resolve their ids the moment the client sees them.
            main_loop.call_soon_threadsafe(_safe_put, (source_name, scored))
        except Exception:
            log.exception("Failed to serialize partial result chunk")

    async def _run_search() -> None:
        media = media_obj
        try:
            if query_override:
                # Manual query wins for every media type / season / episode
                # combination — previously the typed season/episode/movie
                # searches ignored it and re-derived a query from the name.
                await indexer_service.search(
                    query=query_override, is_tv=is_tv, on_partial=_on_partial
                )
            elif media_type == MediaType.show and media is not None:
                if episode_number is not None and season_number is not None:
                    await indexer_service.search_episode(
                        show=media,
                        season_number=season_number,
                        episode_number=episode_number,
                        on_partial=_on_partial,
                    )
                elif season_number is not None:
                    await indexer_service.search_season(
                        show=media,
                        season_number=season_number,
                        on_partial=_on_partial,
                    )
                else:
                    queries = [
                        f"{name} {media.year}"
                        for name in search_name_variants(media.name)
                    ]
                    await asyncio.gather(
                        *(
                            indexer_service.search(
                                query=q, is_tv=True, on_partial=_on_partial
                            )
                            for q in queries
                        )
                    )
            elif media_type == MediaType.movie and media is not None:
                await indexer_service.search_movie(movie=media, on_partial=_on_partial)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("SSE indexer search failed")
        finally:
            _safe_put(DONE)

    async def event_publisher() -> AsyncGenerator[ServerSentEvent]:
        from miramedia.database import background_session
        from miramedia.indexers.repository import IndexerRepository

        search_task = asyncio.create_task(_run_search())
        try:
            while True:
                item = await chunk_queue.get()
                if item is DONE:
                    log.debug("SSE stream finished, sending done event")
                    yield ServerSentEvent(event="done", data="{}")
                    return
                source_name, scored = item
                # Persist on a dedicated session so we don't contend with
                # the request-scoped session the search task is using for
                # its end-of-search save. ``save_result`` is idempotent so
                # the redundant second write is a no-op.
                try:
                    async with background_session() as db:
                        repo = IndexerRepository(db)
                        await repo.save_results(scored)
                except Exception:
                    log.exception("Failed to persist streamed indexer results")
                chunk = SearchStreamChunk(
                    source=source_name, results=scored
                ).model_dump(mode="json")
                payload = json.dumps(chunk)
                log.debug(
                    "SSE chunk yielding: source=%s results=%d bytes=%d",
                    source_name,
                    len(scored),
                    len(payload),
                )
                yield ServerSentEvent(event="results", data=payload)
        finally:
            abort.set()
            search_task.cancel()

    # ``Content-Encoding: identity`` opts out of GZipMiddleware so chunks
    # reach the browser as the producer yields them.
    return EventSourceResponse(
        event_publisher(),
        headers={
            "X-Accel-Buffering": "no",
            "Content-Encoding": "identity",
        },
    )


@router.get(
    "/search",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(current_superuser)],
)
async def search_torrents(
    show_service: show_service_dep,
    movie_service: movie_service_dep,
    media_type: Annotated[MediaType, Query()],
    media_id: Annotated[uuid.UUID, Query()],
    season_number: Annotated[int | None, Query()] = None,
    episode_number: Annotated[int | None, Query()] = None,
    query_override: Annotated[str | None, Query()] = None,
    quality: Annotated[list[str] | None, Query()] = None,
    codec: Annotated[list[str] | None, Query()] = None,
) -> list[IndexerQueryResult]:
    """
    Unified torrent search endpoint for both shows and movies.

    ``quality`` / ``codec`` are lists of configured option *names*
    (``IndexerConfig.quality_options`` / ``codec_options``). A result is kept
    when its title matches at least one keyword of a selected option in each
    non-empty filter. Empty / omitted = no restriction for that dimension.
    """
    if media_type == MediaType.show:
        if episode_number is not None and season_number is not None:
            results = await show_service.get_all_available_torrents_for_an_episode(
                season_number=season_number,
                episode_number=episode_number,
                show_id=media_id,
                search_query_override=query_override,
            )
        else:
            results = await show_service.get_all_available_torrents_for_a_season(
                season_number=season_number or 1,
                show_id=media_id,
                search_query_override=query_override,
            )
    elif media_type == MediaType.movie:
        movie = await movie_service.get_movie_by_id(media_id)
        results = await movie_service.get_all_available_torrents_for_movie(
            movie=movie, search_query_override=query_override
        )
    else:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"Unknown media_type: {media_type}"
        )

    return _filter_results_by_options(results, quality, codec)


def _filter_results_by_options(
    results: list[IndexerQueryResult],
    quality_names: list[str] | None,
    codec_names: list[str] | None,
) -> list[IndexerQueryResult]:
    """Keep results whose title matches selected quality/codec option keywords."""
    if not quality_names and not codec_names:
        return results

    import re

    from miramedia.config import MiraMediaConfig

    cfg = MiraMediaConfig().indexers

    def keywords_for(options: list, selected: list[str] | None) -> list[str]:
        sel = set(selected or [])
        kws: list[str] = []
        for opt in options:
            if opt.name in sel:
                kws.extend(opt.keywords)
        return kws

    quality_kws = keywords_for(cfg.quality_options, quality_names)
    codec_kws = keywords_for(cfg.codec_options, codec_names)

    def matches(title: str, kws: list[str]) -> bool:
        if not kws:
            return True
        t = title.lower()
        return any(re.search(r"\b" + re.escape(k.lower()) + r"\b", t) for k in kws)

    return [
        r
        for r in results
        if matches(r.title, quality_kws) and matches(r.title, codec_kws)
    ]


@router.post(
    "/download",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(current_superuser)],
)
async def download_torrent_unified(
    body: UnifiedDownloadRequest,
    torrent_service: torrent_service_dep,
    indexer_service: indexer_service_dep,
    show_repository: show_repository_dep,
    movie_repository: movie_repository_dep,
    show_service: show_service_dep,
    movie_service: movie_service_dep,
) -> RichTorrent:
    """
    Unified torrent download endpoint for both shows and movies.
    """
    indexer_result = await indexer_service.get_result(
        result_id=IndexerQueryResultId(body.indexer_result_id)
    )
    if body.library is not None:
        await _apply_library_override(
            media_type=body.media_type,
            media_id=body.media_id,
            library=body.library,
            show_service=show_service,
            movie_service=movie_service,
        )
    variant, quality_override = _resolve_variant_quality(body)
    torrent = await torrent_service.download_and_link(
        indexer_result=indexer_result,
        media_type=body.media_type,
        media_id=body.media_id,
        variant=variant,
        quality_override=quality_override,
        show_repository=show_repository,
        movie_repository=movie_repository,
    )
    # Return a RichTorrent with basic context
    return RichTorrent(
        id=torrent.id,
        status=torrent.status,
        progress=torrent.progress,
        num_peers=torrent.num_peers,
        num_seeds=torrent.num_seeds,
        title=torrent.title,
        quality=torrent.quality,
        hash=torrent.hash,
        usenet=torrent.usenet,
        variant=variant,
        import_progress=await torrent_service.compute_import_progress(torrent),
    )


# -----------------------------------------------------------------------------
# MANUAL ADD
# -----------------------------------------------------------------------------


def _resolve_variant_quality(body) -> tuple[str, Quality | None]:  # noqa: ANN001
    """Extract ``(variant, quality_override)`` from a download/map body.

    ``variant`` is the user-supplied free-text differentiator. ``codec``,
    ``hdr`` and ``source`` are detected at import time, not here.
    """
    variant = getattr(body, "variant", "") or ""
    quality_override = getattr(body, "quality_override", None)
    return variant, quality_override


async def _apply_library_override(
    media_type: MediaType,
    media_id: uuid.UUID,
    library: str,
    show_service: "ShowService",
    movie_service: "MovieService",
) -> None:
    """Reassign the show/movie's library before linking so files land under it.

    Validates the library name against the configured libraries to avoid silent
    misroutes; default library passes through.
    """
    from miramedia.config import MiraMediaConfig

    cfg = MiraMediaConfig().misc
    if media_type == MediaType.show:
        valid = {"Default", *(lib.name for lib in cfg.show_libraries)}
        if library not in valid:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Unknown show library '{library}'",
            )
        show = await show_service.get_show_by_id(media_id)
        if show.library != library:
            await show_service.set_show_library(show=show, library=library)
    else:
        valid = {"Default", *(lib.name for lib in cfg.movie_libraries)}
        if library not in valid:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Unknown movie library '{library}'",
            )
        movie = await movie_service.get_movie_by_id(media_id)
        if movie.library != library:
            await movie_service.set_movie_library(movie=movie, library=library)


@router.post(
    "/manual/parse",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(current_superuser)],
)
async def manual_parse(
    show_service: show_service_dep,
    movie_service: movie_service_dep,
    torrent_repository: torrent_repository_dep,
    magnet_link: Annotated[str | None, Form()] = None,
    torrent_file: Annotated[UploadFile | None, File()] = None,
) -> ManualParseResponse:
    """
    Parse a magnet link or .torrent file upload.
    Returns parsed metadata and candidate media matches.
    """
    if not magnet_link and not torrent_file:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Either magnet_link or torrent_file must be provided",
        )

    torrent_file_content = None
    if torrent_file:
        if (
            torrent_file.size is not None
            and torrent_file.size > MAX_TORRENT_UPLOAD_BYTES
        ):
            raise HTTPException(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                "Torrent file too large",
            )
        torrent_file_content = await torrent_file.read(MAX_TORRENT_UPLOAD_BYTES + 1)
        if len(torrent_file_content) > MAX_TORRENT_UPLOAD_BYTES:
            raise HTTPException(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                "Torrent file too large",
            )

    try:
        name, _info_hash, magnet_uri = parse_magnet_or_torrent_file(
            magnet_link=magnet_link,
            torrent_file_content=torrent_file_content,
        )
    except Exception:
        log.exception("Failed to parse manual torrent input")
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Failed to parse torrent input",
        ) from None

    # Build a synthetic IndexerQueryResult
    synthetic_result = IndexerQueryResult(
        title=name,
        download_url=magnet_uri,
        seeders=0,
        flags=[],
        size=0,
        usenet=False,
        age=0,
        indexer="manual",
    )

    # Store for later download (DB-backed, multi-replica safe).
    # Build payload explicitly because IndexerQueryResult.download_url is
    # Field(exclude=True) and would be dropped by model_dump().
    download_token = uuid.uuid4()
    await torrent_repository.save_manual_parse_token(
        token_id=download_token,
        payload={
            "id": str(synthetic_result.id),
            "title": synthetic_result.title,
            "download_url": synthetic_result.download_url,
            "seeders": synthetic_result.seeders,
            "flags": synthetic_result.flags,
            "size": synthetic_result.size,
            "usenet": synthetic_result.usenet,
            "age": synthetic_result.age,
            "score": synthetic_result.score,
            "indexer": synthetic_result.indexer,
        },
    )

    # Find candidate media matches
    shows = await show_service.get_all_shows()
    movies = await movie_service.get_all_movies()
    raw_candidates = find_candidate_media_matches(name, shows, movies)

    candidates = [
        ManualParseCandidate(
            media_type=MediaType(c["media_type"]),
            media_id=c["media_id"],
            media_name=c["media_name"],
            media_year=c["media_year"],
            confidence=c["confidence"],
            breakdown=c.get("breakdown"),
        )
        for c in raw_candidates
    ]

    return ManualParseResponse(
        download_token=download_token,
        title=name,
        quality=synthetic_result.quality,
        seasons=synthetic_result.season,
        episodes=synthetic_result.episode,
        candidates=candidates,
    )


@router.post(
    "/manual/download",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(current_superuser)],
)
async def manual_download(
    body: ManualDownloadRequest,
    torrent_service: torrent_service_dep,
    indexer_service: indexer_service_dep,
    show_repository: show_repository_dep,
    movie_repository: movie_repository_dep,
    show_service: show_service_dep,
    movie_service: movie_service_dep,
) -> RichTorrent:
    """
    Download a previously-parsed manual torrent and link it to chosen media.
    """
    payload = await torrent_service.torrent_repository.pop_manual_parse_token(
        body.download_token
    )
    if payload is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Download token not found or already used. Please re-parse.",
        )
    synthetic_result = IndexerQueryResult.model_validate(payload)

    if body.library is not None:
        await _apply_library_override(
            media_type=body.media_type,
            media_id=body.media_id,
            library=body.library,
            show_service=show_service,
            movie_service=movie_service,
        )

    # Save the synthetic result to the indexer repository so download_and_link
    # can find it via the standard flow
    await indexer_service.repository.save_result(result=synthetic_result)

    variant, quality_override = _resolve_variant_quality(body)
    torrent = await torrent_service.download_and_link(
        indexer_result=synthetic_result,
        media_type=body.media_type,
        media_id=body.media_id,
        variant=variant,
        quality_override=quality_override,
        show_repository=show_repository,
        movie_repository=movie_repository,
    )

    return RichTorrent(
        id=torrent.id,
        status=torrent.status,
        progress=torrent.progress,
        num_peers=torrent.num_peers,
        num_seeds=torrent.num_seeds,
        title=torrent.title,
        quality=torrent.quality,
        hash=torrent.hash,
        usenet=torrent.usenet,
        variant=variant,
        import_progress=await torrent_service.compute_import_progress(torrent),
    )


# -----------------------------------------------------------------------------
# LIST + LITERAL ROUTES (must precede /{torrent_id} to avoid UUID-validation 422s)
# -----------------------------------------------------------------------------


@router.get("", status_code=status.HTTP_200_OK)
async def get_all_torrents(
    service: torrent_service_dep,
    response: Response,
    limit: Annotated[int | None, Query(gt=0, le=500)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    cursor: Annotated[str | None, Query()] = None,
    live: Annotated[bool | None, Query()] = None,
) -> list[RichTorrent]:
    """List torrents with media context and download status.

    Omit ``limit`` for the legacy full-list response; that path is DB-only by
    default (live status comes from the scheduler's periodic refresh) because
    live RPC over every torrent thrashes the download client and threadpool.
    When ``limit`` is set, pagination runs in SQL and live status RPC runs only
    for that bounded page (default on). Pass ``live=true``/``live=false`` to
    force or suppress live RPC on either path. Supplying ``cursor`` switches
    from offset pagination to the keyset cursor returned in ``X-Next-Cursor``.
    """
    if limit is None:
        # Full-list path defaults to DB-only; only an explicit live=true opts in.
        torrents = await service.get_all_torrents_with_context(live_status=bool(live))
        return torrents[offset:] if offset else torrents
    try:
        # Bounded page defaults to live RPC; an explicit live=false suppresses it.
        page, total, next_cursor = await service.get_paginated_torrents_with_context(
            offset=offset, limit=limit, cursor=cursor, live_status=live is not False
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    response.headers["X-Total-Count"] = str(total)
    if next_cursor:
        response.headers["X-Next-Cursor"] = next_cursor
    return page


@router.get("/count", status_code=status.HTTP_200_OK)
async def get_torrent_count(repo: torrent_repository_dep) -> int:
    """Get the count of active (non-imported) torrents."""
    return await repo.get_active_torrent_count()


@router.get("/import-status", status_code=status.HTTP_200_OK)
async def list_import_status(
    service: torrent_service_dep,
    bucket: Annotated[ImportStatusFilter, Query()] = ImportStatusFilter.all,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(gt=0, le=200)] = 50,
) -> PaginatedTorrentImports:
    """Paginated list of torrents grouped by import outcome bucket."""
    items, total = await service.list_import_statuses(
        bucket=bucket, offset=offset, limit=limit
    )
    return PaginatedTorrentImports(items=items, total=total, offset=offset, limit=limit)


@router.get("/import-status/counts", status_code=status.HTTP_200_OK)
async def get_import_status_counts(service: torrent_service_dep) -> ImportStatusCounts:
    """Bucket counts for the imports dashboard widget."""
    return await service.get_import_status_counts()


@router.get(
    "/integrity/mismatches",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(current_superuser)],
)
async def list_integrity_mismatches(
    service: torrent_service_dep,
    show_service: show_service_dep,
    movie_service: movie_service_dep,
) -> list[IntegrityMismatch]:
    """Imported files whose integrity audit recorded a SHA1 mismatch."""
    return await service.list_integrity_mismatches(
        show_service=show_service,
        movie_service=movie_service,
    )


@router.post(
    "/integrity/{media_type}/{file_id}/rebaseline",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(current_superuser)],
)
async def rebaseline_integrity_file(
    media_type: MediaType,
    file_id: uuid.UUID,
    service: torrent_service_dep,
    show_service: show_service_dep,
    movie_service: movie_service_dep,
) -> IntegrityActionResult:
    """Accept current on-disk bytes: clear error + sha1 for next audit baseline."""
    try:
        return await service.rebaseline_file(
            media_type=media_type,
            file_id=file_id,
            show_service=show_service,
            movie_service=movie_service,
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None


@router.post(
    "/integrity/{media_type}/{file_id}/dismiss",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(current_superuser)],
)
async def dismiss_integrity_mismatch(
    media_type: MediaType,
    file_id: uuid.UUID,
    service: torrent_service_dep,
    show_service: show_service_dep,
    movie_service: movie_service_dep,
) -> IntegrityActionResult:
    """Clear the mismatch stamp only; keep sha1 so the next audit re-verifies."""
    try:
        return await service.dismiss_mismatch(
            media_type=media_type,
            file_id=file_id,
            show_service=show_service,
            movie_service=movie_service,
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None


@router.post(
    "/bulk-retry-import",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(current_superuser)],
)
async def bulk_retry_import(
    body: BulkRetryImportRequest,
    service: torrent_service_dep,
    repo: torrent_repository_dep,
    show_service: show_service_dep,
    movie_service: movie_service_dep,
) -> BulkRetryImportResult:
    """Phase 8.2 — retry import for many torrents in one call.

    Each torrent is reset + re-imported sequentially; failures don't abort
    the batch.
    """
    succeeded = 0
    failed: list[BulkRetryImportFailure] = []
    for tid in body.torrent_ids:
        try:
            torrent = await repo.get_torrent_by_id(torrent_id=tid)
            show = await service.get_show_of_torrent(torrent=torrent)
            movie = await service.get_movie_of_torrent(torrent=torrent)
            if show is None and movie is None:
                msg = "torrent not linked to any media"
                raise ValueError(msg)  # noqa: TRY301 — local control flow, caught per-item below
            await service.reset_import_status(torrent=torrent)
            if show is not None:
                await show_service.import_show_from_torrent(torrent=torrent, show=show)
            else:
                await movie_service.import_movie_from_torrent(
                    torrent=torrent, movie=movie
                )
            succeeded += 1
        except Exception as exc:
            failed.append(BulkRetryImportFailure(torrent_id=str(tid), error=str(exc)))
    return BulkRetryImportResult(succeeded=succeeded, failed=failed)


# -----------------------------------------------------------------------------
# PER-TORRENT ROUTES (must come AFTER literal routes above)
# -----------------------------------------------------------------------------


@router.post(
    "/{torrent_id}/pause",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(current_superuser)],
)
async def pause_torrent_download(
    service: torrent_service_dep,
    torrent: torrent_dep,
) -> Torrent:
    return await service.user_pause_download(torrent=torrent)


@router.post(
    "/{torrent_id}/resume",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(current_superuser)],
)
async def resume_torrent_download(
    service: torrent_service_dep,
    torrent: torrent_dep,
) -> Torrent:
    return await service.user_resume_download(torrent=torrent)


@router.get("/{torrent_id}", status_code=status.HTTP_200_OK)
async def get_torrent(service: torrent_service_dep, torrent: torrent_dep) -> Torrent:
    return await service.get_torrent_by_id(torrent_id=torrent.id)


@router.delete(
    "/{torrent_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(current_superuser)],
)
async def delete_torrent(
    service: torrent_service_dep,
    repo: torrent_repository_dep,
    torrent_id: uuid.UUID,
    block_hash: Annotated[bool, Query()] = False,
) -> None:
    """Idempotent: 204 even if the torrent no longer exists.

    The list endpoint used to surface ghost rows (orphan torrents that had
    lost their media context) and clicking delete on them would 404 in the
    detail dependency, leaving the UI stuck. Tolerate the missing row and
    let invalidate refetch the cleaned list.

    The download client's working copy is always wiped — library files are
    hardlinks to separate inodes so they survive. When ``block_hash`` is
    true the torrent's info-hash is added to the deny-list so the same
    release can't be re-queued.
    """
    try:
        torrent = await repo.get_torrent_by_id(torrent_id=torrent_id)
    except NotFoundError:
        return
    if block_hash:
        try:
            await repo.add_blocked_hash(
                torrent.hash, title=torrent.title, reason="user_blocked"
            )
        except Exception:
            log.exception(
                "Failed to add %s to deny-list during user delete", torrent.hash
            )
    try:
        await service.cancel_download(torrent=torrent, delete_files=True)
    except RuntimeError:
        pass

    await service.delete_torrent(torrent_id=torrent.id)


@router.post(
    "/{torrent_id}/retry",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(current_superuser)],
)
async def retry_torrent_download(
    service: torrent_service_dep,
    torrent: torrent_dep,
) -> None:
    await service.pause_download(torrent=torrent)
    await service.resume_download(torrent=torrent)


@router.get(
    "/{torrent_id}/files",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(current_superuser)],
)
async def list_torrent_files(
    service: torrent_service_dep,
    torrent: torrent_dep,
) -> TorrentFilesResponse:
    """Enumerate on-disk source files for a torrent (manual map UI)."""
    files = await service.list_source_files(torrent=torrent)

    media = None
    show = await service.get_show_of_torrent(torrent=torrent)
    if show is not None:
        from miramedia.torrents.schemas import TorrentMediaContext

        media = TorrentMediaContext(
            media_type="show",
            media_id=show.id,
            media_name=show.name,
            media_year=show.year,
            metadata_provider=show.metadata_provider,
        )
    else:
        movie = await service.get_movie_of_torrent(torrent=torrent)
        if movie is not None:
            from miramedia.torrents.schemas import TorrentMediaContext

            media = TorrentMediaContext(
                media_type="movie",
                media_id=movie.id,
                media_name=movie.name,
                media_year=movie.year,
                metadata_provider=movie.metadata_provider,
            )

    return TorrentFilesResponse(
        torrent_id=torrent.id,
        torrent_title=torrent.title,
        media=media,
        files=files,
    )


@router.post(
    "/{torrent_id}/map",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(current_superuser)],
)
async def map_torrent_files(
    body: ManualMapRequest,
    service: torrent_service_dep,  # noqa: ARG001 — required by route signature
    torrent: torrent_dep,
    show_service: show_service_dep,
    movie_service: movie_service_dep,
) -> ManualMapResult:
    """Apply a user-supplied mapping of source files → target media."""
    from miramedia.file_status import ImportOutcome
    from miramedia.torrents.utils import get_torrent_filepath, resolve_within

    root = get_torrent_filepath(torrent)
    result = ManualMapResult(mapped=0, skipped=0, failed=0, errors=[])

    for item in body.items:
        if item.target_type == ManualMapTargetType.skip:
            result.skipped += 1
            continue

        source = resolve_within(root, item.relative_path)
        if source is None:
            result.failed += 1
            result.errors.append(f"path escapes torrent root: {item.relative_path}")
            continue
        if not source.exists() or not source.is_file():
            result.failed += 1
            result.errors.append(f"missing source: {item.relative_path}")
            continue

        item_variant, _ = _resolve_variant_quality(item)
        outcome: ImportOutcome = ImportOutcome.failed_io
        error: str | None = None
        try:
            if item.target_type == ManualMapTargetType.episode:
                if item.episode_id is None:
                    msg = "episode_id required for target_type=episode"
                    raise ValueError(msg)  # noqa: TRY301 — local control flow, caught per-item below
                episode = await show_service.get_episode(episode_id=item.episode_id)
                season = await show_service.get_season_by_episode(
                    episode_id=item.episode_id
                )
                show = await show_service.get_show_by_id(show_id=season.show_id)
                outcome, error = await show_service.import_episode_from_file(
                    show=show,
                    season=season,
                    episode=episode,
                    source_file=source,
                    torrent_id=torrent.id,
                    variant=item_variant,
                )
            elif item.target_type == ManualMapTargetType.movie:
                if item.movie_id is None:
                    msg = "movie_id required for target_type=movie"
                    raise ValueError(msg)  # noqa: TRY301 — local control flow, caught per-item below
                movie = await movie_service.get_movie_by_id(movie_id=item.movie_id)
                outcome, error = await movie_service.import_movie_from_file(
                    movie=movie,
                    source_file=source,
                    torrent_id=torrent.id,
                    variant=item_variant,
                )
            else:
                msg = f"unknown target_type {item.target_type}"
                raise ValueError(msg)  # noqa: TRY301 — local control flow, caught per-item below
        except Exception as exc:
            result.failed += 1
            result.errors.append(f"{item.relative_path}: {exc}")
            continue

        if outcome == ImportOutcome.imported:
            result.mapped += 1
        else:
            result.failed += 1
            if error:
                result.errors.append(f"{item.relative_path}: {error}")

    return result


@router.post(
    "/{torrent_id}/retry-import",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(current_superuser)],
)
async def retry_torrent_import(
    service: torrent_service_dep,
    torrent: torrent_dep,
    show_service: show_service_dep,
    movie_service: movie_service_dep,
) -> RetryImportResult:
    """Reset per-file import status for a torrent and re-run the import."""
    show = await service.get_show_of_torrent(torrent=torrent)
    movie = await service.get_movie_of_torrent(torrent=torrent)
    if show is None and movie is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Torrent is not linked to any show or movie",
        )

    reset = await service.reset_import_status(torrent=torrent)
    if show is not None:
        await show_service.import_show_from_torrent(torrent=torrent, show=show)
    else:
        await movie_service.import_movie_from_torrent(torrent=torrent, movie=movie)

    progress = await service.compute_import_progress(torrent)
    return RetryImportResult(reset=reset, progress=progress)


@router.post(
    "/{torrent_id}/import",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(current_superuser)],
    # Operator / CLI audit endpoint — the dashboard uses
    # /retry-import + /map for its real import flows. Hidden from
    # OpenAPI to keep the generated frontend client lean.
    include_in_schema=False,
)
async def import_torrent_dry_run(
    service: torrent_service_dep,
    torrent: torrent_dep,
    show_service: show_service_dep,
    movie_service: movie_service_dep,
    dry_run: Annotated[bool, Query()] = True,
) -> DryRunImportResult:
    """Phase 8.3 — preview the import plan for a torrent without touching disk.

    Returns the per-source-file mapping the importer *would* apply (resolved
    from ``list_source_files``: parser hints, suggested target ID, computed
    on-disk target path). When ``dry_run=False``, runs the real import after
    returning the plan so callers can audit what just happened.
    """
    sources = await service.list_source_files(torrent=torrent)
    plan = []
    show = await service.get_show_of_torrent(torrent=torrent)
    movie = await service.get_movie_of_torrent(torrent=torrent)
    for src in sources:
        target_path = None
        if src.suggested_episode_id is not None and show is not None:
            try:
                episode = await show_service.get_episode(
                    episode_id=src.suggested_episode_id
                )
                season = await show_service.get_season_by_episode(
                    episode_id=src.suggested_episode_id
                )
                target_path = str(
                    show_service.get_root_season_directory(
                        show=show, season_number=season.number
                    )
                    / episode_file_stem(
                        show,
                        season_number=season.number,
                        episode_number=episode.number,
                        quality=src.quality or Quality.unknown,
                        parts=NameParts(),
                    )
                )
            except Exception:
                target_path = None
        elif src.suggested_movie_id is not None and movie is not None:
            target_path = str(
                movie_service.get_movie_root_path(movie=movie)
                / movie_file_stem(movie, src.quality or Quality.unknown, NameParts())
            )

        plan.append(
            DryRunImportPlanItem(
                relative_path=src.relative_path,
                size=src.size,
                is_video=src.is_video,
                is_subtitle=src.is_subtitle,
                suggested_episode_id=(
                    str(src.suggested_episode_id) if src.suggested_episode_id else None
                ),
                suggested_movie_id=(
                    str(src.suggested_movie_id) if src.suggested_movie_id else None
                ),
                target_path=target_path,
                quality=src.quality.value
                if hasattr(src.quality, "value")
                else src.quality,
            )
        )

    if not dry_run:
        if show is not None:
            await show_service.import_show_from_torrent(torrent=torrent, show=show)
        elif movie is not None:
            await movie_service.import_movie_from_torrent(torrent=torrent, movie=movie)

    return DryRunImportResult(
        dry_run=dry_run,
        torrent_id=torrent.id,
        torrent_title=torrent.title,
        plan=plan,
    )


@router.patch(
    "/{torrent_id}/status",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(current_superuser)],
)
async def update_torrent_status(
    rep: torrent_repository_dep,
    torrent: torrent_dep,
    state: Annotated[TorrentStatus | None, Query()] = None,
) -> Torrent:
    if state is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No status value provided",
        )

    torrent.status = state
    await rep.save_torrent(torrent=torrent)
    return torrent
