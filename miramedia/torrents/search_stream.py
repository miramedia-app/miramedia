"""SSE torrent search orchestration — domain chunks, no HTTP serialization."""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from miramedia.indexers.schemas import IndexerQueryResult
from miramedia.indexers.utils import (
    evaluate_indexer_query_results,
    search_name_variants,
)
from miramedia.shows.service import filter_results_to_episode, filter_results_to_season
from miramedia.torrents.schemas import MediaType

if TYPE_CHECKING:
    from miramedia.indexers.service import IndexerService
    from miramedia.movies.schemas import Movie
    from miramedia.shows.schemas import Show

log = logging.getLogger(__name__)

_DONE = object()


@dataclass(frozen=True, slots=True)
class SearchStreamResultsChunk:
    source: str
    results: list[IndexerQueryResult]


def filter_results_by_options(
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
        title_lower = title.lower()
        return any(
            re.search(r"\b" + re.escape(k.lower()) + r"\b", title_lower) for k in kws
        )

    return [
        result
        for result in results
        if matches(result.title, quality_kws) and matches(result.title, codec_kws)
    ]


class TorrentSearchStreamOrchestrator:
    """Runs indexer fan-out and yields scored chunks for SSE serialization."""

    def __init__(
        self,
        *,
        indexer_service: IndexerService,
        media_obj: Show | Movie | None,
        media_type: MediaType,
        is_tv: bool,
        season_number: int | None,
        episode_number: int | None,
        query_override: str | None,
        quality: list[str] | None,
        codec: list[str] | None,
        abort: threading.Event | None = None,
    ) -> None:
        self._indexer_service = indexer_service
        self._media_obj = media_obj
        self._media_type = media_type
        self._is_tv = is_tv
        self._season_number = season_number
        self._episode_number = episode_number
        self._query_override = query_override
        self._quality = quality
        self._codec = codec
        self.abort = abort or threading.Event()

    def make_partial_callback(
        self,
        main_loop: asyncio.AbstractEventLoop,
        enqueue: Callable[[object], None],
    ) -> Callable[[str, list[IndexerQueryResult]], None]:
        seen_urls: set[str] = set()
        seen_lock = threading.Lock()
        media_obj = self._media_obj
        query_override = self._query_override
        quality = self._quality
        codec = self._codec
        media_type = self._media_type
        season_number = self._season_number
        episode_number = self._episode_number
        abort = self.abort

        def _safe_put(item: object) -> None:
            enqueue(item)

        def on_partial(source_name: str, results: list[IndexerQueryResult]) -> None:
            if abort.is_set():
                return
            try:
                with seen_lock:
                    fresh = [r for r in results if r.download_url not in seen_urls]
                    seen_urls.update(r.download_url for r in fresh)
                if not fresh or media_obj is None:
                    return
                score_quality = (
                    media_obj.preferred_quality if query_override else quality
                )
                score_codec = media_obj.preferred_codec if query_override else codec
                scored = evaluate_indexer_query_results(
                    query_results=fresh,
                    media=media_obj,
                    is_tv=self._is_tv,
                    quality_allowed=score_quality,
                    codec_allowed=score_codec,
                    query_override=query_override,
                )
                scored = filter_results_by_options(scored, quality, codec)
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
                main_loop.call_soon_threadsafe(_safe_put, (source_name, scored))
            except Exception:
                log.exception("Failed to serialize partial result chunk")

        return on_partial

    async def _run_search(self, enqueue: Callable[[object], None]) -> None:
        on_partial = self.make_partial_callback(asyncio.get_running_loop(), enqueue)
        media = self._media_obj
        try:
            if self._query_override:
                await self._indexer_service.search(
                    query=self._query_override, is_tv=self._is_tv, on_partial=on_partial
                )
            elif self._media_type == MediaType.show and media is not None:
                show = cast("Show", media)
                if self._episode_number is not None and self._season_number is not None:
                    await self._indexer_service.search_episode(
                        show=show,
                        season_number=self._season_number,
                        episode_number=self._episode_number,
                        on_partial=on_partial,
                    )
                elif self._season_number is not None:
                    await self._indexer_service.search_season(
                        show=show,
                        season_number=self._season_number,
                        on_partial=on_partial,
                    )
                else:
                    if show.year is not None:
                        queries = [
                            f"{name} {show.year}"
                            for name in search_name_variants(show.name)
                        ]
                    else:
                        queries = [show.name]
                    await asyncio.gather(
                        *(
                            self._indexer_service.search(
                                query=query, is_tv=True, on_partial=on_partial
                            )
                            for query in queries
                        )
                    )
            elif self._media_type == MediaType.movie and media is not None:
                await self._indexer_service.search_movie(
                    movie=cast("Movie", media), on_partial=on_partial
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("SSE indexer search failed")
        finally:
            enqueue(_DONE)

    @staticmethod
    async def persist_chunk(results: list[IndexerQueryResult]) -> None:
        from miramedia.database import background_session
        from miramedia.indexers.repository import IndexerRepository

        async with background_session() as db:
            repo = IndexerRepository(db)
            await repo.save_results(results)

    async def stream(self) -> AsyncGenerator[SearchStreamResultsChunk]:
        chunk_queue: asyncio.Queue = asyncio.Queue(maxsize=50)

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

        search_task = asyncio.create_task(self._run_search(_safe_put))
        try:
            while True:
                item = await chunk_queue.get()
                if item is _DONE:
                    return
                source_name, scored = item
                try:
                    await self.persist_chunk(scored)
                except Exception:
                    log.exception("Failed to persist streamed indexer results")
                yield SearchStreamResultsChunk(source=source_name, results=scored)
        finally:
            self.abort.set()
            search_task.cancel()
