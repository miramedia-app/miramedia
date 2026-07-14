"""Unit tests for post-scan-import follow-up invariants in ``imports/followup.py``."""

from __future__ import annotations

import uuid
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, call, patch

from miramedia.imports.followup import run_post_import_completion
from miramedia.shows.schemas import (
    Episode,
    EpisodeFile,
    EpisodeId,
    EpisodeNumber,
    Season,
    SeasonId,
    SeasonNumber,
    Show,
    ShowId,
)
from miramedia.torrents.schemas import MediaType, Quality
from tests.fakes import run_async
from tests.fakes.repositories import make_movie, make_show


def _episode_file(episode_id: EpisodeId) -> EpisodeFile:
    return EpisodeFile(
        episode_id=episode_id,
        quality=Quality.fullhd,
        torrent_id=None,
    )


def _make_show_with_mixed_episodes() -> Show:
    show_id = ShowId(uuid.uuid4())
    season1_id = SeasonId(uuid.uuid4())
    season2_id = SeasonId(uuid.uuid4())

    ep_with_file_1 = EpisodeId(uuid.uuid4())
    ep_without_file_1 = EpisodeId(uuid.uuid4())
    ep_with_file_2 = EpisodeId(uuid.uuid4())
    ep_without_file_2 = EpisodeId(uuid.uuid4())

    season1 = Season(
        id=season1_id,
        show_id=show_id,
        number=SeasonNumber(1),
        episodes=[
            Episode(
                id=ep_with_file_1,
                number=EpisodeNumber(1),
                title="S01E01",
                episode_files=[_episode_file(ep_with_file_1)],
            ),
            Episode(
                id=ep_without_file_1,
                number=EpisodeNumber(2),
                title="S01E02",
                episode_files=[],
            ),
        ],
    )
    season2 = Season(
        id=season2_id,
        show_id=show_id,
        number=SeasonNumber(2),
        episodes=[
            Episode(
                id=ep_with_file_2,
                number=EpisodeNumber(1),
                title="S02E01",
                episode_files=[_episode_file(ep_with_file_2)],
            ),
            Episode(
                id=ep_without_file_2,
                number=EpisodeNumber(2),
                title="S02E02",
                episode_files=[],
            ),
        ],
    )
    return Show(
        id=show_id,
        name="Test Show",
        overview="",
        year=2020,
        external_id="ext-1",
        metadata_provider="native",
        seasons=[season1, season2],
    )


def _subtitle_patches() -> tuple[MagicMock, MagicMock]:
    subtitle_instance = MagicMock()
    subtitle_instance.search_episode_subtitles = AsyncMock()
    subtitle_instance.search_movie_subtitles = AsyncMock()
    subtitle_cls = MagicMock(return_value=subtitle_instance)
    return subtitle_cls, subtitle_instance


def _enter_subtitle_patches(stack: ExitStack) -> tuple[MagicMock, MagicMock]:
    subtitle_cls, subtitle_instance = _subtitle_patches()
    stack.enter_context(
        patch("miramedia.subtitles.service.SubtitleService", subtitle_cls)
    )
    stack.enter_context(
        patch(
            "miramedia.subtitles.repository.SubtitleRepository",
            return_value=MagicMock(),
        )
    )
    return subtitle_cls, subtitle_instance


class TestRunPostImportCompletion:
    def test_show_path_only_searches_episodes_with_files(self) -> None:
        stale_show = make_show(name="Stale")
        refetched_show = _make_show_with_mixed_episodes()
        refetched_show.id = stale_show.id
        show_service = MagicMock()
        show_service.get_show_by_id = AsyncMock(return_value=refetched_show)
        movie_service = MagicMock()

        with ExitStack() as stack:
            _, subtitle_instance = _enter_subtitle_patches(stack)
            run_async(
                run_post_import_completion(
                    db=MagicMock(),
                    media_type=MediaType.show,
                    media=stale_show,
                    show_service=show_service,
                    movie_service=movie_service,
                )
            )

        show_service.get_show_by_id.assert_awaited_once_with(show_id=stale_show.id)
        searched_ids = [
            c.args[0]
            for c in subtitle_instance.search_episode_subtitles.await_args_list
        ]
        expected_ids = [season.episodes[0].id for season in refetched_show.seasons]
        assert searched_ids == expected_ids
        subtitle_instance.search_movie_subtitles.assert_not_awaited()

    def test_movie_path_searches_movie_subtitles_once(self) -> None:
        stale_movie = make_movie(name="Stale Movie")
        refetched_movie = make_movie(name="Refetched Movie")
        refetched_movie.id = stale_movie.id
        show_service = MagicMock()
        movie_service = MagicMock()
        movie_service.get_movie_by_id = AsyncMock(return_value=refetched_movie)

        with ExitStack() as stack:
            _, subtitle_instance = _enter_subtitle_patches(stack)
            run_async(
                run_post_import_completion(
                    db=MagicMock(),
                    media_type=MediaType.movie,
                    media=stale_movie,
                    show_service=show_service,
                    movie_service=movie_service,
                )
            )

        movie_service.get_movie_by_id.assert_awaited_once_with(stale_movie.id)
        subtitle_instance.search_movie_subtitles.assert_awaited_once_with(
            refetched_movie.id
        )
        subtitle_instance.search_episode_subtitles.assert_not_awaited()

    def test_refetch_failure_returns_without_subtitle_search(self) -> None:
        show = make_show(name="Broken")
        show_service = MagicMock()
        show_service.get_show_by_id = AsyncMock(side_effect=RuntimeError("db down"))
        movie_service = MagicMock()

        with ExitStack() as stack:
            subtitle_cls, _ = _enter_subtitle_patches(stack)
            run_async(
                run_post_import_completion(
                    db=MagicMock(),
                    media_type=MediaType.show,
                    media=show,
                    show_service=show_service,
                    movie_service=movie_service,
                )
            )

        subtitle_cls.assert_not_called()
        movie_service.assert_not_called()

    def test_per_episode_subtitle_failure_does_not_abort_loop(self) -> None:
        refetched_show = _make_show_with_mixed_episodes()
        show_service = MagicMock()
        show_service.get_show_by_id = AsyncMock(return_value=refetched_show)
        movie_service = MagicMock()

        with ExitStack() as stack:
            _, subtitle_instance = _enter_subtitle_patches(stack)
            subtitle_instance.search_episode_subtitles = AsyncMock(
                side_effect=[RuntimeError("provider down"), None]
            )
            run_async(
                run_post_import_completion(
                    db=MagicMock(),
                    media_type=MediaType.show,
                    media=make_show(name="Stale"),
                    show_service=show_service,
                    movie_service=movie_service,
                )
            )

        assert subtitle_instance.search_episode_subtitles.await_count == 2

    def test_no_continuous_download_trigger(self) -> None:
        stale_show = make_show(name="Stale")
        refetched_show = _make_show_with_mixed_episodes()
        refetched_show.id = stale_show.id
        show_service = MagicMock()
        show_service.get_show_by_id = AsyncMock(return_value=refetched_show)
        movie_service = MagicMock()

        with ExitStack() as stack:
            _enter_subtitle_patches(stack)
            run_async(
                run_post_import_completion(
                    db=MagicMock(),
                    media_type=MediaType.show,
                    media=stale_show,
                    show_service=show_service,
                    movie_service=movie_service,
                )
            )

        assert show_service.method_calls == [call.get_show_by_id(show_id=stale_show.id)]
        assert movie_service.method_calls == []
