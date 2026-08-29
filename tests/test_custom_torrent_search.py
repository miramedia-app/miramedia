"""Regression tests for custom torrent search filtering and scoring."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest

from miramedia.indexers.schemas import IndexerQueryResult
from miramedia.shows.schemas import Show
from miramedia.shows.service import ShowService
from miramedia.torrents.router import search_torrents_stream
from miramedia.torrents.schemas import MediaType


def _result(title: str) -> IndexerQueryResult:
    return IndexerQueryResult(
        title=title,
        download_url=f"magnet:?xt=urn:btih:{title}",
        seeders=10,
        flags=[],
        size=2_000_000_000,
        usenet=False,
        age=1,
        indexer="fixture",
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("query", "expected_titles"),
    [
        (
            "Lioness",
            {
                "Special Ops Lioness S01E01 1080p WEB-DL x264-GRP",
                "Special Ops Lioness S03E01 1080p WEB-DL x264-GRP",
            },
        ),
        (
            "Lioness S03",
            {"Special Ops Lioness S03E01 1080p WEB-DL x264-GRP"},
        ),
    ],
)
async def test_stream_custom_query_filters_and_scores_results(
    query: str, expected_titles: set[str]
) -> None:
    show = Show(
        name="Lioness",
        year=2023,
        library="/tv",
        overview="",
        external_id="lioness",
        metadata_provider="native",
        preferred_quality=["1080p (Full HD)"],
    )
    results = [
        _result("Special Ops Lioness S01E01 1080p WEB-DL x264-GRP"),
        _result("Special Ops Lioness S03E01 1080p WEB-DL x264-GRP"),
        _result("Special Ops Lioness S03E02 720p WEB-DL x264-GRP"),
        _result("Married at First Sight S20E07 1080p WEB-DL x264-GRP"),
        _result("Big Brother US S28E11 1080p WEB-DL x264-GRP"),
        _result("Lioness PC Game 1080p x264"),
        _result("Lioness 2018 1080p BluRay x264-GRP"),
    ]

    class ShowRepository:
        async def get_show_by_id(self, show_id):  # noqa: ARG002
            return show

    class ShowService:
        show_repository = ShowRepository()

        async def get_show_by_id(self, show_id):  # noqa: ARG002
            return show

    class IndexerService:
        async def search(self, query, is_tv, on_partial):  # noqa: ARG002
            on_partial("fixture", results)
            await asyncio.sleep(0)
            return results

    @asynccontextmanager
    async def background_session():
        yield object()

    async def save_results(self, results):  # noqa: ARG001
        return results

    with (
        patch("miramedia.database.background_session", background_session),
        patch(
            "miramedia.indexers.repository.IndexerRepository.save_results",
            save_results,
        ),
    ):
        response = await search_torrents_stream(
            request=object(),
            indexer_service=IndexerService(),
            media_type=MediaType.show,
            media_id=show.id,
            show_service=ShowService(),
            movie_service=object(),
            query_override=query,
        )
        events = [event async for event in response.body_iterator]

    result_events = [event for event in events if event.event == "results"]
    assert len(result_events) == 1
    payload = json.loads(result_events[0].data)
    assert {item["title"] for item in payload["results"]} == expected_titles
    assert all(item["score"] > 0 for item in payload["results"])


@pytest.mark.anyio
async def test_rest_custom_query_uses_same_filtering_and_scoring() -> None:
    show = Show(
        name="Lioness",
        year=2023,
        library="/tv",
        overview="",
        external_id="lioness",
        metadata_provider="native",
        preferred_quality=["1080p (Full HD)"],
    )
    results = [
        _result("Special Ops Lioness S03E01 1080p WEB-DL x264-GRP"),
        _result("Special Ops Lioness S03E02 720p WEB-DL x264-GRP"),
        _result("Special Ops Lioness S01E01 1080p WEB-DL x264-GRP"),
        _result("Married at First Sight S20E07 1080p WEB-DL x264-GRP"),
    ]

    class ShowRepository:
        db = object()

        async def get_show_by_id(self, show_id):  # noqa: ARG002
            return show

    class IndexerService:
        async def search(self, query, is_tv):  # noqa: ARG002
            return results

    async def release_session(db):  # noqa: ARG001
        return None

    service = ShowService(
        show_repository=ShowRepository(),
        torrent_service=None,
        indexer_service=IndexerService(),
        notification_service=None,
    )
    with patch(
        "miramedia.database.release_session_before_external_io", release_session
    ):
        matched = await service.get_all_available_torrents_for_a_season(
            season_number=1,
            show_id=show.id,
            search_query_override="Lioness S03",
        )

    assert [result.title for result in matched] == [
        "Special Ops Lioness S03E01 1080p WEB-DL x264-GRP"
    ]
    assert matched[0].score > 0
