"""Unit tests for season/episode membership filters shared by REST and SSE search."""

from miramedia.indexers.schemas import IndexerQueryResult
from miramedia.shows.service import filter_results_to_episode, filter_results_to_season


def _result(title: str) -> IndexerQueryResult:
    return IndexerQueryResult(
        title=title,
        download_url=f"magnet:?xt=urn:btih:{title}",
        seeders=10,
        flags=[],
        size=2_000_000_000,
        usenet=False,
        age=1,
        indexer="x",
    )


def test_filter_results_to_episode_keeps_exact_episode_and_season_pack() -> None:
    exact = _result("Show S01E05 1080p WEB-DL")
    season_pack = _result("Show S01 1080p WEB-DL")
    other_episode = _result("Show S01E06 1080p WEB-DL")
    other_season = _result("Show S02E05 1080p WEB-DL")

    kept = filter_results_to_episode(
        [exact, season_pack, other_episode, other_season],
        season_number=1,
        episode_number=5,
    )

    assert [r.title for r in kept] == [exact.title, season_pack.title]


def test_filter_results_to_season_keeps_requested_season_only() -> None:
    season_one = _result("Show S01E05 1080p WEB-DL")
    season_one_pack = _result("Show S01 1080p WEB-DL")
    adjacent_season = _result("Show S02E01 1080p WEB-DL")

    kept = filter_results_to_season(
        [season_one, season_one_pack, adjacent_season],
        season_number=1,
    )

    assert [r.title for r in kept] == [season_one.title, season_one_pack.title]
