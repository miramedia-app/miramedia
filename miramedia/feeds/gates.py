"""Observe-only gate evaluation without downloads (design 385 §3.3)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

from miramedia.config import MiraMediaConfig
from miramedia.feeds.schemas import FeedDecision, FeedEnvelope
from miramedia.indexers.utils import evaluate_indexer_query_results
from miramedia.movies.schemas import Movie
from miramedia.shows.schemas import Show
from miramedia.torrents.schemas import MediaType

if TYPE_CHECKING:
    from miramedia.movies.service import MovieService
    from miramedia.shows.service import ShowService
    from miramedia.torrents.service import TorrentService

log = logging.getLogger(__name__)


def _effective_preferences(
    media: Movie | Show,
    svc: MovieService | ShowService,
    is_tv: bool,
) -> tuple[list[str] | None, list[str] | None]:
    if is_tv:
        from miramedia.shows.service import ShowService

        show_svc = cast(ShowService, svc)
        show = cast(Show, media)
        return show_svc._get_effective_preferences(show)
    from miramedia.movies.service import MovieService

    movie_svc = cast(MovieService, svc)
    movie = cast(Movie, media)
    return movie_svc._get_effective_preferences(movie)


async def evaluate_observe_gates(
    envelope: FeedEnvelope,
    *,
    media_type: str,
    movie: Movie | None,
    show: Show | None,
    movie_service: MovieService | None,
    show_service: ShowService | None,
    torrent_service: TorrentService,
) -> tuple[FeedDecision, int | None]:
    """Score and apply download safety gates without calling download_and_link."""
    result = envelope.result
    is_tv = media_type == "show"
    media = show if is_tv else movie
    if media is None:
        return FeedDecision.error, None

    svc = show_service if is_tv else movie_service
    if svc is None:
        return FeedDecision.error, None

    if media.skipped:
        return FeedDecision.not_wanted, None

    global_cd = MiraMediaConfig().misc.continuous_download
    if media.continuous_download is False or (
        media.continuous_download is None and not global_cd
    ):
        return FeedDecision.not_wanted, None

    today = datetime.now(UTC).astimezone().date()
    if not is_tv and movie is not None:
        if movie.release_date is not None and movie.release_date > today:
            return FeedDecision.not_wanted, None
    elif show is not None:
        # Unreleased episodes are handled inside scoring/title relevance.
        pass

    quality_allowed, codec_allowed = _effective_preferences(media, svc, is_tv)
    scored = evaluate_indexer_query_results(
        query_results=[result],
        media=media,
        is_tv=is_tv,
        quality_allowed=quality_allowed,
        codec_allowed=codec_allowed,
    )
    if not scored:
        return FeedDecision.drop_score, 0

    scored_result = scored[0]
    score = scored_result.score

    if is_tv and show_service is not None and show is not None:
        season_nums = scored_result.season or []
        episode_nums = scored_result.episode or []
        if season_nums and episode_nums:
            season_number = season_nums[0]
            episode_number = episode_nums[0]
            season = next((s for s in show.seasons if s.number == season_number), None)
            if season is not None:
                episode = next(
                    (e for e in season.episodes if e.number == episode_number), None
                )
                if episode is not None:
                    if await show_service.is_episode_downloaded(
                        episode=episode, season=season, show=show
                    ):
                        return FeedDecision.already_have, score
                    if any(ef.torrent_id is not None for ef in episode.episode_files):
                        return FeedDecision.active_download, score
        # Season pack or ambiguous: any active torrent on the show blocks.
        for season in show.seasons:
            for episode in season.episodes:
                if any(ef.torrent_id is not None for ef in episode.episode_files):
                    return FeedDecision.active_download, score
    elif movie_service is not None and movie is not None:
        if await movie_service.is_movie_downloaded(movie=movie):
            return FeedDecision.already_have, score
        movie_files = await movie_service.movie_repository.get_movie_files_by_movie_id(
            movie_id=movie.id
        )
        if any(mf.torrent_id is not None for mf in movie_files):
            return FeedDecision.active_download, score

    deny_filtered = await torrent_service.filter_deny_listed([scored_result])
    if not deny_filtered:
        return FeedDecision.deny_listed, score

    destination_wanted, _ = await torrent_service._is_destination_wanted(
        indexer_result=scored_result,
        media_type=MediaType.show if is_tv else MediaType.movie,
        media_id=media.id,
        show_repository=show_service.show_repository if show_service else None,
        movie_repository=movie_service.movie_repository if movie_service else None,
    )
    if not destination_wanted:
        return FeedDecision.skipped_destination, score

    return FeedDecision.would_grab, score


async def evaluate_unmatched_observe(
    envelope: FeedEnvelope,
) -> FeedDecision:
    """Items with no library bind are recorded as unmatched."""
    _ = envelope
    return FeedDecision.unmatched
