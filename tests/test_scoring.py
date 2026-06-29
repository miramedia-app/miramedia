"""Default junk-rejection ships as a Title Rule + Rule Set (not a bespoke gate).

The default ``title_scoring_rules`` entry "Reject cam/workprint" (-10000),
applied via the default ``scoring_rule_sets`` "Reject low-quality sources"
(ALL_TV + ALL_MOVIES), pushes pre-retail releases below the ``score >= 0`` floor
in evaluate_indexer_query_results. These tests drive the real pipeline.
"""

import pytest

from miramedia.indexers.schemas import IndexerQueryResult
from miramedia.indexers.utils import evaluate_indexer_query_results
from miramedia.movies.schemas import Movie


def _movie() -> Movie:
    return Movie(
        name="Some Movie",
        year=2024,
        library="/movies",
        overview="",
        external_id="tt1",
        metadata_provider="native",
    )


def _result(title: str) -> IndexerQueryResult:
    return IndexerQueryResult(
        title=title,
        download_url="magnet:?xt=urn:btih:0",
        seeders=10,
        flags=[],
        size=2_000_000_000,
        usenet=False,
        age=1,
        indexer="x",
    )


JUNK = [
    "Some Movie 2024 1080p WORKPRiNT WEB-DL x264-DK",
    "Some Movie 2024 1080p HDCAM x264-GRP",
    "Some Movie 2024 1080p CamRip x264",
    "Some Movie 2024 1080p HQCAM x264",
    "Some Movie 2024 1080p TELESYNC x264",
    "Some Movie 2024 1080p TELECINE x264",
    "Some Movie 2024 1080p SCREENER x264",
    "Some Movie 2024 1080p DVDSCR x264",
    "Some Movie 2024 1080p PDVD x264",
]
LEGIT = [
    "Some Movie 2024 1080p BluRay x264-GRP",
    "Some Movie 2024 1080p WEB-DL x265-GRP",
    # Bare CAM is NOT a default keyword: the 2018 film *Cam* survives.
    "Cam 2018 1080p WEB-DL x264-GRP",
]


@pytest.mark.parametrize("junk", JUNK)
def test_default_rule_drops_junk(junk) -> None:
    kept = evaluate_indexer_query_results(
        [_result(junk), _result(LEGIT[0])], _movie(), is_tv=False
    )
    titles = [r.title for r in kept]
    assert junk not in titles
    assert LEGIT[0] in titles


@pytest.mark.parametrize("legit", LEGIT)
def test_default_rule_keeps_legit(legit) -> None:
    # "Cam 2018 …" is a different movie name; match it against its own title.
    media = _movie()
    if legit.startswith("Cam "):
        media = Movie(
            name="Cam",
            year=2018,
            library="/movies",
            overview="",
            external_id="tt2",
            metadata_provider="native",
        )
    kept = evaluate_indexer_query_results([_result(legit)], media, is_tv=False)
    assert [r.title for r in kept] == [legit]


def _movie_named(name: str, year: int) -> Movie:
    return Movie(
        name=name,
        year=year,
        library="/movies",
        overview="",
        external_id="tt-year",
        metadata_provider="native",
    )


def test_year_gate_drops_wrong_year_remake() -> None:
    # The bug: "Supergirl" (2026) was added but "Supergirl 1984" got picked.
    media = _movie_named("Supergirl", 2026)
    wrong = _result("Supergirl 1984 1080p BluRay x264-GRP")
    right = _result("Supergirl 2026 1080p WEB-DL x265-GRP")
    kept = evaluate_indexer_query_results([wrong, right], media, is_tv=False)
    titles = [r.title for r in kept]
    assert "Supergirl 1984 1080p BluRay x264-GRP" not in titles
    assert "Supergirl 2026 1080p WEB-DL x265-GRP" in titles


def test_year_gate_keeps_year_in_title() -> None:
    # A year that is part of the title must not false-reject (release year present).
    media = _movie_named("Blade Runner 2049", 2017)
    r = _result("Blade Runner 2049 2017 1080p BluRay x264-GRP")
    kept = evaluate_indexer_query_results([r], media, is_tv=False)
    assert [x.title for x in kept] == [r.title]


def test_year_gate_keeps_release_without_year() -> None:
    # No year in the title -> tolerant, keep it.
    media = _movie_named("Some Movie", 2024)
    r = _result("Some Movie 1080p WEB-DL x265-GRP")
    kept = evaluate_indexer_query_results([r], media, is_tv=False)
    assert [x.title for x in kept] == [r.title]


def test_year_gate_not_applied_to_tv() -> None:
    # TV release titles can carry per-episode air years; gate is movies-only.
    from miramedia.shows.schemas import Show

    show = Show(
        name="Supergirl",
        year=2015,
        library="/tv",
        overview="",
        external_id="tt-show",
        metadata_provider="native",
    )
    r = _result("Supergirl 2021 S06E01 1080p WEB-DL x265-GRP")
    kept = evaluate_indexer_query_results([r], show, is_tv=True)
    assert [x.title for x in kept] == [r.title]
