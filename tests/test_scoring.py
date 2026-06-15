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
