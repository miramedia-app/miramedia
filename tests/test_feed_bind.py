"""Tests for feed bind matrix."""

from unittest.mock import patch
from uuid import uuid4

from miramedia.feeds.bind import (
    bind_feed_envelope,
    bind_feed_envelope_indexed,
    build_feed_catalog,
)
from miramedia.feeds.envelope import FeedTorznabParser
from miramedia.feeds.schemas import FeedEnvelope
from miramedia.indexers.schemas import IndexerQueryResult
from miramedia.movies.schemas import Movie, MovieId
from miramedia.shows.schemas import Show, ShowId

_PARSER = FeedTorznabParser()


def _movie(
    name: str,
    year: int,
    *,
    imdb: str | None = None,
    external_id: str = "1",
    metadata_provider: str = "tmdb",
    skipped: bool = False,
    continuous_download: bool | None = True,
) -> Movie:
    return Movie(
        id=MovieId(uuid4()),
        name=name,
        year=year,
        library="default",
        overview="",
        metadata_provider=metadata_provider,
        external_id=external_id,
        imdb_id=imdb,
        skipped=skipped,
        continuous_download=continuous_download,
    )


def _show(
    name: str,
    year: int,
    *,
    imdb: str | None = None,
    external_id: str = "2",
    metadata_provider: str = "tmdb",
    skipped: bool = False,
    continuous_download: bool | None = True,
) -> Show:
    return Show(
        id=ShowId(uuid4()),
        name=name,
        year=year,
        library="default",
        overview="",
        metadata_provider=metadata_provider,
        external_id=external_id,
        imdb_id=imdb,
        skipped=skipped,
        continuous_download=continuous_download,
        seasons=[],
    )


def _envelope_from_result(
    title: str,
    *,
    imdb_id: str | None = None,
    tmdb_id: str | None = None,
    tvdb_id: str | None = None,
    season: list[int] | None = None,
) -> FeedEnvelope:
    return FeedEnvelope(
        result=IndexerQueryResult(
            title=title,
            download_url="magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567",
            seeders=10,
            flags=[],
            size=1000,
            usenet=False,
            age=0,
            indexer="test",
            season=season or [],
        ),
        imdb_id=imdb_id,
        tmdb_id=tmdb_id,
        tvdb_id=tvdb_id,
    )


def _catalog(
    movies: list[Movie] | None = None,
    shows: list[Show] | None = None,
    *,
    global_continuous_download: bool = True,
):
    return build_feed_catalog(
        movies=movies or [],
        shows=shows or [],
        global_continuous_download=global_continuous_download,
    )


def test_bind_by_imdb_id():
    movie = _movie("Inception", 2010, imdb="tt1375666")
    envelope = _PARSER.process_feed_search_result(
        """<?xml version="1.0"?><rss xmlns:torznab="http://torznab.com/schemas/2015/feed">
        <channel><item>
          <title>Inception 2010 1080p</title><size>1000</size>
          <enclosure url="magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567" type="application/x-bittorrent" length="1000"/>
          <torznab:attr name="imdbid" value="tt1375666"/>
        </item></channel></rss>"""
    )[0]
    bind = bind_feed_envelope(
        envelope, movies=[movie], shows=[], global_continuous_download=True
    )
    assert bind.media_type == "movie"
    assert bind.media_id == movie.id


def test_skipped_show_not_wanted_for_bind():
    show = _show("Breaking Bad", 2008, imdb="tt0903747")
    show.skipped = True
    envelope = _PARSER.process_feed_search_result(
        """<?xml version="1.0"?><rss xmlns:torznab="http://torznab.com/schemas/2015/feed">
        <channel><item>
          <title>Breaking Bad S01E01 1080p</title><size>1000</size>
          <enclosure url="magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567" type="application/x-bittorrent" length="1000"/>
          <torznab:attr name="imdbid" value="tt0903747"/>
        </item></channel></rss>"""
    )[0]
    bind = bind_feed_envelope(
        envelope, movies=[], shows=[show], global_continuous_download=True
    )
    assert bind.media_type is None


def test_tv_markers_without_show_match_unmatched():
    envelope = _envelope_from_result("Some Show S01E01 1080p", season=[1])
    bind = bind_feed_envelope(
        envelope, movies=[], shows=[], global_continuous_download=True
    )
    assert bind.media_type is None


def test_duplicate_imdb_id_is_ambiguous():
    first = _movie("Alpha", 2020, imdb="tt1111111", external_id="111")
    second = _movie("Beta", 2021, imdb="tt1111111", external_id="222")
    envelope = _envelope_from_result("Alpha 2020 1080p", imdb_id="tt1111111")
    catalog = _catalog(movies=[first, second])
    bind = bind_feed_envelope_indexed(envelope, catalog)
    assert bind.media_type is None
    assert bind.media_id is None


def test_duplicate_tmdb_id_is_ambiguous():
    first = _movie("Alpha", 2020, external_id="42")
    second = _movie("Beta", 2021, external_id="42")
    envelope = _envelope_from_result("Alpha 2020 1080p", tmdb_id="42")
    catalog = _catalog(movies=[first, second])
    bind = bind_feed_envelope_indexed(envelope, catalog)
    assert bind.media_type is None


def test_duplicate_tvdb_id_is_ambiguous():
    first = _show("Alpha", 2020, metadata_provider="tvdb", external_id="99")
    second = _show("Beta", 2021, metadata_provider="tvdb", external_id="99")
    envelope = _envelope_from_result("Alpha S01E01 1080p", tvdb_id="99", season=[1])
    catalog = _catalog(shows=[first, second])
    bind = bind_feed_envelope_indexed(envelope, catalog)
    assert bind.media_type is None


def test_ambiguous_normalized_title_unmatched():
    first = _show("The Bear", 2022)
    second = _show("The Bear", 2024)
    envelope = _envelope_from_result("The Bear S01E01 1080p", season=[1])
    catalog = _catalog(shows=[first, second])
    bind = bind_feed_envelope_indexed(envelope, catalog)
    assert bind.media_type is None


def test_unique_title_with_year_binds_movie():
    movie = _movie("Blade Runner 2049", 2017)
    envelope = _envelope_from_result("Blade Runner 2049 2017 1080p BluRay")
    catalog = _catalog(movies=[movie])
    bind = bind_feed_envelope_indexed(envelope, catalog)
    assert bind.media_type == "movie"
    assert bind.media_id == movie.id


def test_wrong_year_in_title_does_not_bind_movie():
    movie = _movie("Supergirl", 1984)
    envelope = _envelope_from_result("Supergirl 2026 1080p WEB-DL")
    catalog = _catalog(movies=[movie])
    bind = bind_feed_envelope_indexed(envelope, catalog)
    assert bind.media_type is None


def test_skipped_movie_excluded_from_title_bind():
    movie = _movie("Dune", 2021, skipped=True)
    envelope = _envelope_from_result("Dune 2021 2160p")
    catalog = _catalog(movies=[movie])
    bind = bind_feed_envelope_indexed(envelope, catalog)
    assert bind.media_type is None


def test_per_title_continuous_download_false_overrides_global_default():
    movie = _movie("Dune", 2021, imdb="tt1160419", continuous_download=False)
    envelope = _envelope_from_result("Dune 2021 2160p", imdb_id="tt1160419")
    catalog = _catalog(movies=[movie], global_continuous_download=True)
    bind = bind_feed_envelope_indexed(envelope, catalog)
    assert bind.media_type is None


def test_per_title_continuous_download_true_overrides_global_default_off():
    movie = _movie("Dune", 2021, imdb="tt1160419", continuous_download=True)
    envelope = _envelope_from_result("Dune 2021 2160p", imdb_id="tt1160419")
    catalog = _catalog(movies=[movie], global_continuous_download=False)
    bind = bind_feed_envelope_indexed(envelope, catalog)
    assert bind.media_type == "movie"
    assert bind.media_id == movie.id


def test_global_continuous_download_default_when_per_title_none():
    movie = _movie("Dune", 2021, imdb="tt1160419", continuous_download=None)
    envelope = _envelope_from_result("Dune 2021 2160p", imdb_id="tt1160419")
    catalog_on = _catalog(movies=[movie], global_continuous_download=True)
    catalog_off = _catalog(movies=[movie], global_continuous_download=False)
    assert bind_feed_envelope_indexed(envelope, catalog_on).media_type == "movie"
    assert bind_feed_envelope_indexed(envelope, catalog_off).media_type is None


def test_external_id_precedence_before_title():
    movie = _movie("Wrong Title", 2020, imdb="tt9999999")
    envelope = _envelope_from_result(
        "Completely Different 2020 1080p", imdb_id="tt9999999"
    )
    catalog = _catalog(movies=[movie])
    bind = bind_feed_envelope_indexed(envelope, catalog)
    assert bind.media_type == "movie"
    assert bind.media_id == movie.id


def test_tv_title_bind_requires_season_marker():
    show = _show("Severance", 2022)
    envelope = _envelope_from_result("Severance 2022 1080p")
    catalog = _catalog(shows=[show])
    bind = bind_feed_envelope_indexed(envelope, catalog)
    assert bind.media_type is None


def test_tv_title_bind_with_season_marker():
    show = _show("Severance", 2022)
    envelope = _envelope_from_result("Severance S01E01 1080p", season=[1])
    catalog = _catalog(shows=[show])
    bind = bind_feed_envelope_indexed(envelope, catalog)
    assert bind.media_type == "show"
    assert bind.media_id == show.id


def test_movie_title_not_considered_when_tv_markers_present():
    movie = _movie("Supergirl", 1984)
    envelope = _envelope_from_result("Supergirl S05E12 1080p", season=[5])
    catalog = _catalog(movies=[movie])
    bind = bind_feed_envelope_indexed(envelope, catalog)
    assert bind.media_type is None


def test_name_normalization_called_once_per_library_row_not_per_envelope():
    movies = [_movie(f"Movie {index}", 2000 + index) for index in range(5)]
    shows = [_show(f"Show {index}", 2010 + index) for index in range(4)]
    envelopes = [
        _envelope_from_result(f"Movie {index} 1080p") for index in range(5)
    ] + [
        _envelope_from_result(f"Show {index} S01E01 1080p", season=[1])
        for index in range(4)
    ]

    call_count = 0
    original = build_feed_catalog.__globals__["_normalized_name_variants"]

    def counting_variants(name: str) -> list[str]:
        nonlocal call_count
        call_count += 1
        return original(name)

    with patch(
        "miramedia.feeds.bind._normalized_name_variants",
        side_effect=counting_variants,
    ):
        rebuilt = build_feed_catalog(
            movies=movies,
            shows=shows,
            global_continuous_download=True,
        )
        call_count = 0
        for envelope in envelopes:
            bind_feed_envelope_indexed(envelope, rebuilt)

    assert call_count == 0
