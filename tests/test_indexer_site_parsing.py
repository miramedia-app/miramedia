"""Golden-fixture parse tests for native indexer site scrapers.

Fixtures are synthetic HTML/JSON shaped to match each parser's selectors —
authored offline from the site modules, never fetched live.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch
from urllib.parse import parse_qs, unquote, urlparse

import pytest
from selectolax.parser import HTMLParser

from miramedia.indexers.schemas import IndexerQueryResult
from miramedia.indexers.sites.base import DEFAULT_TRACKERS, build_magnet
from miramedia.indexers.sites.eztv import EztvSite
from miramedia.indexers.sites.limetorrents import LimeTorrentsSite
from miramedia.indexers.sites.nyaa import NyaaSite
from miramedia.indexers.sites.thepiratebay import ThePirateBaySite
from miramedia.indexers.sites.torrentgalaxy import TorrentGalaxySite
from miramedia.indexers.sites.x1337 import X1337Site
from miramedia.indexers.sites.yts import YtsSite

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "indexer_sites"

_VALID_HEX_HASH = "aabbccddeeff00112233445566778899aabbccdd"
_VALID_BASE32_HASH = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"


def _indexer_result(title: str) -> IndexerQueryResult:
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


def _magnet_query_params(magnet: str) -> dict[str, list[str]]:
    return parse_qs(urlparse(magnet).query)


def _load_fixture(name: str) -> str:
    return (FIXTURES_DIR / name).read_text()


def _load_json_fixture(name: str) -> Any:
    return json.loads(_load_fixture(name))


# ---------------------------------------------------------------------------
# build_magnet — URL-encode dn and validate info hashes
# ---------------------------------------------------------------------------


class TestBuildMagnet:
    def test_dn_encoding_blocks_injected_tracker_params(self) -> None:
        title = "Release &tr=udp://evil.example:80/announce"
        magnet = build_magnet(_VALID_HEX_HASH, title)
        params = _magnet_query_params(magnet)

        assert len(params["tr"]) == len(DEFAULT_TRACKERS)
        assert all("evil.example" not in tr for tr in params["tr"])
        assert unquote(params["dn"][0]) == title

    def test_dn_encoding_preserves_spaces_and_hash(self) -> None:
        title = "My Release 1080p #1"
        magnet = build_magnet(_VALID_HEX_HASH, title)

        assert " " not in magnet
        assert "#" not in magnet
        params = _magnet_query_params(magnet)
        assert unquote(params["dn"][0]) == title

    def test_accepts_valid_hex_and_base32_hashes(self) -> None:
        mixed_hex = "AaBbCcDdEeFf00112233445566778899AaBbCcDd"
        base32 = _VALID_BASE32_HASH.lower()
        assert build_magnet(mixed_hex, "title").startswith(
            f"magnet:?xt=urn:btih:{mixed_hex}&dn="
        )
        assert build_magnet(base32, "title").startswith(
            f"magnet:?xt=urn:btih:{base32}&dn="
        )

    @pytest.mark.parametrize(
        "info_hash",
        [
            "a" * 39,
            "g" * 40,
        ],
    )
    def test_rejects_invalid_info_hash(self, info_hash: str) -> None:
        with pytest.raises(ValueError, match=r"Invalid btih info hash"):
            build_magnet(info_hash, "title")

    def test_eztv_row_with_ampersand_in_title_uses_default_trackers(
        self,
    ) -> None:
        payload = {
            "torrents": [
                {
                    "id": 99,
                    "hash": _VALID_HEX_HASH,
                    "filename": "Show & Partner S01E01",
                    "size_bytes": 100,
                    "seeds": 10,
                }
            ]
        }
        site = EztvSite()
        with patch.object(site, "_fetch_json", return_value=payload):
            results = site._fetch_eztv_api({"limit": 100, "page": 1})

        assert len(results) == 1
        params = _magnet_query_params(results[0].download_url)
        assert len(params["tr"]) == len(DEFAULT_TRACKERS)
        assert set(params["tr"]) == set(DEFAULT_TRACKERS)
        assert unquote(params["dn"][0]) == "Show & Partner S01E01"


class TestEztvTextSearch:
    def test_eztv_text_search_filters_latest_feed_by_query(self) -> None:
        site = EztvSite()
        results = [
            _indexer_result("Special Ops Lioness S03E01 1080p WEB-DL x264"),
            _indexer_result("Special Ops Lioness S01E01 1080p WEB-DL x264"),
            _indexer_result("Married at First Sight S20E07 1080p WEB-DL x264"),
        ]

        with patch.object(site, "_fetch_eztv_api", return_value=results):
            matched = site._search_eztv("Lioness S03")

        assert [result.title for result in matched] == [
            "Special Ops Lioness S03E01 1080p WEB-DL x264"
        ]

    def test_eztv_text_search_does_not_require_show_premiere_year(self) -> None:
        site = EztvSite()
        result = _indexer_result("Special Ops Lioness S01E01 1080p WEB-DL x264")

        with patch.object(site, "_fetch_eztv_api", return_value=[result]):
            matched = site._search_eztv("Lioness 2023")

        assert matched == [result]

    @pytest.mark.parametrize("query", ["1923 2022", "1923 S02"])
    def test_eztv_text_search_preserves_year_in_show_title(self, query: str) -> None:
        site = EztvSite()
        expected = _indexer_result("1923 S02E01 1080p WEB-DL x264")
        unrelated = _indexer_result("Unrelated Show S02E01 1080p WEB-DL x264")

        with patch.object(site, "_fetch_eztv_api", return_value=[expected, unrelated]):
            matched = site._search_eztv(query)

        assert matched == [expected]


# ---------------------------------------------------------------------------
# 1337x — row metadata is pure; magnets require a mocked detail fetch
# ---------------------------------------------------------------------------


class TestX1337Parsing:
    @pytest.fixture
    def site(self) -> X1337Site:
        return X1337Site()

    def test_parse_row_metadata(self, site: X1337Site) -> None:
        html = _load_fixture("x1337.html")
        rows = HTMLParser(html).css("table.table-list tbody tr")
        assert len(rows) == 3

        meta0 = site._parse_row_metadata(rows[0])
        assert meta0 == {
            "title": "Show Name S01E01 1080p",
            "detail_path": "/torrent/1001/show-name-s01e01/",
            "seeders": 42,
            "size": 1610612736,
        }

        meta1 = site._parse_row_metadata(rows[1])
        assert meta1 is not None
        assert meta1["title"] == "Show Name S01E02 720p"
        assert meta1["seeders"] == 18
        assert meta1["size"] == 838860800

        meta2 = site._parse_row_metadata(rows[2])
        assert meta2 is not None
        assert meta2["title"] == "Movie 2024 1080p BluRay"
        assert meta2["seeders"] == 250
        assert meta2["size"] == 2684354560

    def test_parse_results_with_mocked_magnets(self, site: X1337Site) -> None:
        magnets = {
            "/torrent/1001/show-name-s01e01/": (
                "magnet:?xt=urn:btih:aaa111&dn=Show+Name+S01E01"
            ),
            "/torrent/1002/show-name-s01e02/": (
                "magnet:?xt=urn:btih:bbb222&dn=Show+Name+S01E02"
            ),
            "/torrent/1003/movie-2024/": ("magnet:?xt=urn:btih:ccc333&dn=Movie+2024"),
        }

        def fake_fetch_magnet(meta: dict) -> str:
            return magnets[meta["detail_path"]]

        html = _load_fixture("x1337.html")
        with patch.object(site, "_fetch_magnet", side_effect=fake_fetch_magnet):
            results, hard_error = site._parse_results(html, "fixture")

        assert hard_error is False
        assert len(results) == 3
        assert results[0].title == "Show Name S01E01 1080p"
        assert results[0].seeders == 42
        assert results[0].download_url == magnets["/torrent/1001/show-name-s01e01/"]
        assert results[0].indexer == "1337x"
        assert results[2].title == "Movie 2024 1080p BluRay"
        assert results[2].seeders == 250

    def test_empty_results(self, site: X1337Site) -> None:
        html = _load_fixture("x1337_empty.html")
        results, hard_error = site._parse_results(html, "fixture-empty")
        assert results == []
        assert hard_error is False


# ---------------------------------------------------------------------------
# TorrentGalaxy
# ---------------------------------------------------------------------------


class TestTorrentGalaxyParsing:
    @pytest.fixture
    def site(self) -> TorrentGalaxySite:
        return TorrentGalaxySite()

    def test_parse_row_metadata(self, site: TorrentGalaxySite) -> None:
        html = _load_fixture("torrentgalaxy.html")
        rows = HTMLParser(html).css("div.tgxtablerow")
        assert len(rows) == 3

        meta0 = site._parse_row_metadata(rows[0])
        assert meta0 is not None
        assert meta0["title"] == "Breaking Bad S01E01 1080p"
        assert meta0["detail_path"] == "/torrent/12345-breaking-bad-s01e01"
        assert meta0["seeders"] == 25
        assert meta0["size"] == int(1.2 * 1024**3)

        meta1 = site._parse_row_metadata(rows[1])
        assert meta1 is not None
        assert meta1["title"] == "Breaking Bad S01E02 720p"
        assert meta1["seeders"] == 10
        assert meta1["size"] == 943718400

        meta2 = site._parse_row_metadata(rows[2])
        assert meta2 is not None
        assert meta2["title"] == "Documentary 2024"
        assert meta2["seeders"] == 5
        assert meta2["size"] == int(4.0 * 1024**3)

    def test_search_with_mocked_fetch(self, site: TorrentGalaxySite) -> None:
        html = _load_fixture("torrentgalaxy.html")
        magnets = {
            "/torrent/12345-breaking-bad-s01e01": "magnet:?xt=urn:btih:tg111",
            "/torrent/12346-breaking-bad-s01e02": "magnet:?xt=urn:btih:tg222",
            "/torrent/99999-doc": "magnet:?xt=urn:btih:tg333",
        }

        with (
            patch.object(site, "_fetch", return_value=html),
            patch.object(site, "_fetch_magnet", side_effect=lambda path: magnets[path]),
        ):
            results = site._search("breaking bad")

        assert len(results) == 3
        assert results[0].title == "Breaking Bad S01E01 1080p"
        assert results[0].seeders == 25
        assert results[0].download_url == "magnet:?xt=urn:btih:tg111"
        assert results[0].indexer == "torrentgalaxy"

    def test_empty_results(self) -> None:
        html = _load_fixture("torrentgalaxy_empty.html")
        rows = HTMLParser(html).css("div.tgxtablerow")
        assert rows == []


# ---------------------------------------------------------------------------
# Nyaa
# ---------------------------------------------------------------------------


class TestNyaaParsing:
    @pytest.fixture
    def site(self) -> NyaaSite:
        return NyaaSite()

    def test_parse_rows(self, site: NyaaSite) -> None:
        html = _load_fixture("nyaa.html")
        rows = HTMLParser(html).css("table.torrent-list tbody tr")
        assert len(rows) == 2

        r0 = site._parse_row(rows[0])
        assert r0 is not None
        assert r0.title == "[SubsPlease] Anime Show - 01 (1080p)"
        assert r0.seeders == 100
        assert r0.size == 1610612736
        assert r0.download_url.startswith("magnet:?xt=urn:btih:aabbccddeeff")
        assert r0.indexer == "nyaa"

        r1 = site._parse_row(rows[1])
        assert r1 is not None
        assert r1.title == "[Erai-raws] Anime Show - 02 (720p)"
        assert r1.seeders == 42
        assert r1.size == 838860800
        assert r1.download_url.startswith("magnet:?xt=urn:btih:bbccddeeff")

    def test_empty_results(self) -> None:
        html = _load_fixture("nyaa_empty.html")
        rows = HTMLParser(html).css("table.torrent-list tbody tr")
        assert rows == []


# ---------------------------------------------------------------------------
# LimeTorrents — includes bounds-check regression for single td.tdnormal
# ---------------------------------------------------------------------------


class TestLimeTorrentsParsing:
    @pytest.fixture
    def site(self) -> LimeTorrentsSite:
        return LimeTorrentsSite()

    def test_parse_rows(self, site: LimeTorrentsSite) -> None:
        html = _load_fixture("limetorrents.html")
        rows = HTMLParser(html).css("table.table2 tr")
        data_rows = [r for r in rows if r.css_first("td.tdleft div.tt-name")]
        assert len(data_rows) == 2

        r0 = site._parse_row(data_rows[0])
        assert r0 is not None
        assert r0.title == "Linux Distro 2024 1080p"
        assert r0.seeders == 150
        assert r0.size == 2147483648
        assert r0.download_url == build_magnet(
            "aabbccddeeff00112233445566778899aabbccdd", "Linux Distro 2024 1080p"
        )
        assert r0.indexer == "limetorrents"

        r1 = site._parse_row(data_rows[1])
        assert r1 is not None
        assert r1.title == "Another Release 720p"
        assert r1.seeders == 42
        assert r1.size == 786432000

    def test_single_size_cell_bounds_check(self, site: LimeTorrentsSite) -> None:
        """Regression: len(size_cell) > 1 guard must not crash on one td.tdnormal."""
        html = _load_fixture("limetorrents_single_size_cell.html")
        rows = HTMLParser(html).css("table.table2 tr")
        data_rows = [r for r in rows if r.css_first("td.tdleft div.tt-name")]
        assert len(data_rows) == 1

        result = site._parse_row(data_rows[0])
        assert result is not None
        assert result.title == "Edge Case Single Size Cell"
        assert result.seeders == 5
        assert result.size == 0
        assert result.download_url.startswith("magnet:?xt=urn:btih:")

    def test_empty_results(self) -> None:
        html = _load_fixture("limetorrents_empty.html")
        rows = HTMLParser(html).css("table.table2 tr")
        data_rows = [r for r in rows if r.css_first("td.tdleft div.tt-name")]
        assert data_rows == []


# ---------------------------------------------------------------------------
# EZTV — JSON API; patch _fetch_json to stay offline
# ---------------------------------------------------------------------------


class TestEztvParsing:
    @pytest.fixture
    def site(self) -> EztvSite:
        return EztvSite()

    def test_fetch_eztv_api(self, site: EztvSite) -> None:
        payload = _load_json_fixture("eztv.json")
        with patch.object(site, "_fetch_json", return_value=payload):
            results = site._fetch_eztv_api({"limit": 100, "page": 1})

        assert len(results) == 3
        assert results[0].title == "Show.Name.S01E01.HDTV.x264"
        assert results[0].seeders == 50
        assert results[0].size == 536870912
        assert results[0].download_url.startswith("magnet:?xt=urn:btih:aabbccdd")
        assert results[0].indexer == "eztv"

        assert results[1].title == "Show.Name.S01E02.HDTV.x264"
        assert results[1].seeders == 30
        assert results[1].download_url == "https://eztvx.to/torrents/2.torrent"

        assert results[2].title == "Show.Name.S01E03.NoSeedField.x264"
        assert results[2].seeders is None

    def test_empty_results(self, site: EztvSite) -> None:
        payload = _load_json_fixture("eztv_empty.json")
        with patch.object(site, "_fetch_json", return_value=payload):
            results = site._fetch_eztv_api({"limit": 100, "page": 1})
        assert results == []

    def test_drops_row_with_invalid_info_hash(self, site: EztvSite) -> None:
        payload = {
            "torrents": [
                {
                    "id": 1,
                    "hash": "nothex",
                    "filename": "Bad.Hash.S01E01.x264",
                    "size_bytes": 100,
                    "seeds": 5,
                },
                {
                    "id": 2,
                    "hash": _VALID_HEX_HASH,
                    "filename": "Good.Hash.S01E02.x264",
                    "size_bytes": 200,
                    "seeds": 10,
                },
            ]
        }
        with patch.object(site, "_fetch_json", return_value=payload):
            results = site._fetch_eztv_api({"limit": 100, "page": 1})

        assert len(results) == 1
        assert results[0].title == "Good.Hash.S01E02.x264"
        assert results[0].download_url == build_magnet(
            _VALID_HEX_HASH, "Good.Hash.S01E02.x264"
        )


# ---------------------------------------------------------------------------
# The Pirate Bay — ApiBay JSON
# ---------------------------------------------------------------------------


class TestThePirateBayParsing:
    @pytest.fixture
    def site(self) -> ThePirateBaySite:
        return ThePirateBaySite()

    def test_search_tpb(self, site: ThePirateBaySite) -> None:
        payload = _load_json_fixture("thepiratebay.json")
        with patch.object(site, "_fetch_json", return_value=payload):
            results = site._search_tpb("ubuntu")

        assert len(results) == 2
        assert results[0].title == "Ubuntu 24.04 ISO"
        assert results[0].seeders == 200
        assert results[0].size == 4613734400
        assert results[0].download_url == build_magnet(
            "aabbccddeeff00112233445566778899aabbccdd", "Ubuntu 24.04 ISO"
        )
        assert results[0].indexer == "thepiratebay"

        assert results[1].title == "Documentary 2024 1080p"
        assert results[1].seeders == 75
        assert results[1].size == 2147483648

    def test_empty_results(self, site: ThePirateBaySite) -> None:
        payload = _load_json_fixture("thepiratebay_empty.json")
        with patch.object(site, "_fetch_json", return_value=payload):
            results = site._search_tpb("nonexistent")
        assert results == []

    def test_drops_row_with_invalid_info_hash(self, site: ThePirateBaySite) -> None:
        payload = [
            {
                "id": "1",
                "name": "Bad Hash Release",
                "info_hash": "nothex",
                "size": "100",
                "seeders": "5",
            },
            {
                "id": "2",
                "name": "Good Hash Release",
                "info_hash": _VALID_HEX_HASH,
                "size": "200",
                "seeders": "10",
            },
        ]
        with patch.object(site, "_fetch_json", return_value=payload):
            results = site._search_tpb("test")

        assert len(results) == 1
        assert results[0].title == "Good Hash Release"
        assert results[0].download_url == build_magnet(
            _VALID_HEX_HASH, "Good Hash Release"
        )


# ---------------------------------------------------------------------------
# YTS — JSON API
# ---------------------------------------------------------------------------


class TestYtsParsing:
    @pytest.fixture
    def site(self) -> YtsSite:
        return YtsSite()

    def test_search_yts(self, site: YtsSite) -> None:
        payload = _load_json_fixture("yts.json")
        with patch.object(site, "_fetch_json", return_value=payload):
            results = site._search_yts("test movie")

        assert len(results) == 2
        assert results[0].title == "Test Movie (2024) 1080p bluray"
        assert results[0].seeders == 120
        assert results[0].size == 1500000000
        assert results[0].download_url == build_magnet(
            "aabbccddeeff00112233445566778899aabbccdd",
            "Test Movie (2024) 1080p bluray",
        )
        assert results[0].indexer == "yts"

        assert results[1].title == "Test Movie (2024) 720p web"
        assert results[1].seeders == 45
        assert results[1].size == 800000000

    def test_empty_results(self, site: YtsSite) -> None:
        payload = _load_json_fixture("yts_empty.json")
        with patch.object(site, "_fetch_json", return_value=payload):
            results = site._search_yts("nonexistent")
        assert results == []

    def test_drops_row_with_invalid_info_hash(self, site: YtsSite) -> None:
        payload = {
            "status": "ok",
            "data": {
                "movies": [
                    {
                        "title": "Test Movie",
                        "title_long": "Test Movie (2024)",
                        "torrents": [
                            {
                                "hash": "nothex",
                                "quality": "1080p",
                                "type": "bluray",
                                "size_bytes": 100,
                                "seeds": 5,
                            },
                            {
                                "hash": _VALID_HEX_HASH,
                                "quality": "720p",
                                "type": "web",
                                "size_bytes": 200,
                                "seeds": 10,
                            },
                        ],
                    }
                ]
            },
        }
        with patch.object(site, "_fetch_json", return_value=payload):
            results = site._search_yts("test movie")

        assert len(results) == 1
        assert results[0].title == "Test Movie (2024) 720p web"
        assert results[0].download_url == build_magnet(
            _VALID_HEX_HASH, "Test Movie (2024) 720p web"
        )


# ---------------------------------------------------------------------------
# Shared empty-HTML assertion shape (HTML family sites)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fixture_name", "row_selector"),
    [
        ("x1337_empty.html", "table.table-list tbody tr"),
        ("torrentgalaxy_empty.html", "div.tgxtablerow"),
        ("nyaa_empty.html", "table.torrent-list tbody tr"),
        ("limetorrents_empty.html", "table.table2 tr"),
    ],
)
def test_html_sites_empty_fixture_returns_no_rows(
    fixture_name: str, row_selector: str
) -> None:
    html = _load_fixture(fixture_name)
    tree = HTMLParser(html)
    if fixture_name == "limetorrents_empty.html":
        rows = [
            r for r in tree.css(row_selector) if r.css_first("td.tdleft div.tt-name")
        ]
    else:
        rows = tree.css(row_selector)
    assert rows == []
