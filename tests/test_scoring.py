"""Default junk-rejection ships as a Title Rule + Rule Set (not a bespoke gate).

The default ``title_scoring_rules`` entry "Reject cam/workprint" (-10000),
applied via the default ``scoring_rule_sets`` "Reject low-quality sources"
(ALL_TV + ALL_MOVIES), pushes pre-retail releases below the ``score > 0`` floor
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


def test_movie_drops_tv_episode_release() -> None:
    # The bug: movie "Supergirl" grabbed the TV episode "Supergirl S05E12 ...".
    media = _movie_named("Supergirl", 2026)
    episode = _result(
        "Supergirl.S05E12.Back.from.the.Future.-.Part.Two.1080p.AMZN.WEB-DL.DDP5.1.H.264-TRB"
    )
    movie = _result("Supergirl 2026 1080p WEB-DL x265-GRP")
    kept = evaluate_indexer_query_results([episode, movie], media, is_tv=False)
    titles = [r.title for r in kept]
    assert episode.title not in titles
    assert movie.title in titles


@pytest.mark.parametrize(
    "tv_title",
    [
        "Supergirl S05E12 1080p WEB-DL x264-GRP",
        "Supergirl 5x12 1080p WEB-DL x264-GRP",
        "Supergirl Season 5 1080p WEB-DL x264-GRP",
        "Supergirl S01-S06 Complete Series 1080p WEB-DL",
        "Supergirl.S05.COMPLETE.720p.AMZN.WEBRip.x264-GalaxyTV",
        "Supergirl S05 1080p WEB-DL x264-GRP",
    ],
)
def test_movie_drops_various_tv_markers(tv_title) -> None:
    media = _movie_named("Supergirl", 2026)
    kept = evaluate_indexer_query_results([_result(tv_title)], media, is_tv=False)
    assert kept == []


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


def _show_named(name: str, year: int):
    from miramedia.shows.schemas import Show

    return Show(
        name=name,
        year=year,
        library="/tv",
        overview="",
        external_id="tt-sub",
        metadata_provider="native",
    )


def test_subtitled_name_matches_main_title_release() -> None:
    # The bug: metadata name "The Agency: Central Intelligence" filtered out
    # every release because groups name them "The.Agency.S01E01...".
    show = _show_named("The Agency: Central Intelligence", 2024)
    r = _result("The.Agency.2024.S01E01.1080p.WEB-DL.x265-GRP")
    kept = evaluate_indexer_query_results([r], show, is_tv=True)
    assert [x.title for x in kept] == [r.title]


def test_subtitled_name_still_matches_full_title_release() -> None:
    show = _show_named("The Agency: Central Intelligence", 2024)
    r = _result("The Agency Central Intelligence S01E01 1080p WEB-DL x265-GRP")
    kept = evaluate_indexer_query_results([r], show, is_tv=True)
    assert [x.title for x in kept] == [r.title]


def test_subtitled_name_rejects_different_show_sharing_main_title() -> None:
    # Main-title fallback must not accept a different show: after "star trek"
    # the next token is a word, not a release marker.
    show = _show_named("Star Trek: Discovery", 2017)
    r = _result("Star.Trek.Strange.New.Worlds.S01E01.1080p.WEB-DL.x265-GRP")
    kept = evaluate_indexer_query_results([r], show, is_tv=True)
    assert kept == []


def test_zero_score_excluded() -> None:
    # Neutralize quality/codec bonuses so the final score stays at 0.
    r = _result("Some Movie 2024 1080p BluRay x264-GRP")
    kept = evaluate_indexer_query_results(
        [r], _movie(), is_tv=False, quality_allowed=[], codec_allowed=[]
    )
    assert kept == []


def test_positive_score_survives() -> None:
    r = _result("Some Movie 2024 1080p BluRay x264-GRP")
    kept = evaluate_indexer_query_results([r], _movie(), is_tv=False)
    assert [x.title for x in kept] == [r.title]
    assert kept[0].score > 0


def test_negative_score_dropped() -> None:
    junk = _result("Some Movie 2024 1080p HDCAM x264-GRP")
    kept = evaluate_indexer_query_results([junk], _movie(), is_tv=False)
    assert kept == []


def test_search_name_variants() -> None:
    from miramedia.indexers.utils import search_name_variants

    assert search_name_variants("The Agency: Central Intelligence") == [
        "The Agency: Central Intelligence",
        "The Agency",
    ]
    assert search_name_variants("Supergirl") == ["Supergirl"]
