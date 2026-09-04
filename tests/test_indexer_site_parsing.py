"""Golden-fixture parse tests for native indexer site scrapers.

Fixtures are synthetic HTML/JSON shaped to match each parser's selectors —
authored offline from the site modules, never fetched live.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch
from urllib.parse import parse_qs, quote, unquote, urlparse

import pytest
from selectolax.parser import HTMLParser

from miramedia.indexers.schemas import IndexerQueryResult
from miramedia.indexers.sites.base import DEFAULT_TRACKERS, build_magnet
from miramedia.indexers.sites.bittorrented import BitTorrentedSite
from miramedia.indexers.sites.eztv import EztvSite
from miramedia.indexers.sites.limetorrents import LimeTorrentsSite
from miramedia.indexers.sites.nyaa import NyaaSite
from miramedia.indexers.sites.thepiratebay import ThePirateBaySite
from miramedia.indexers.sites.x1337 import (
    UNKNOWN_AGE_DAYS,
    X1337Site,
    parse_upload_age_days,
)
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
    def test_default_trackers_are_deduplicated(self) -> None:
        assert len(DEFAULT_TRACKERS) == len(set(DEFAULT_TRACKERS))

    def test_default_trackers_include_udp_and_http_endpoints(self) -> None:
        assert any(t.startswith("udp://") for t in DEFAULT_TRACKERS)
        assert any(t.startswith(("http://", "https://")) for t in DEFAULT_TRACKERS)

    def test_each_tracker_encoded_as_separate_tr_parameter(self) -> None:
        magnet = build_magnet(_VALID_HEX_HASH, "title")
        params = _magnet_query_params(magnet)

        assert len(params["tr"]) == len(DEFAULT_TRACKERS)
        assert set(params["tr"]) == set(DEFAULT_TRACKERS)
        for tracker in DEFAULT_TRACKERS:
            assert f"tr={quote(tracker, safe='')}" in magnet

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

        def fake_fetch_magnet(
            meta: dict, *, origin: str = "", **_kwargs: Any
        ) -> tuple[str, int]:
            _ = origin
            return magnets[meta["detail_path"]], 0

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


class TestX1337MirrorFailover:
    @pytest.fixture
    def site(self) -> X1337Site:
        return X1337Site(bypass=None, timeout=5)

    @staticmethod
    def _response(
        *,
        status_code: int = 200,
        text: str = "",
        url: str = "",
        headers: dict[str, str] | None = None,
    ) -> Any:
        return type(
            "Resp",
            (),
            {
                "status_code": status_code,
                "text": text,
                "headers": headers or {},
                "url": url,
            },
        )()

    def test_configured_host_success_skips_other_mirrors(self, site: X1337Site) -> None:
        site.url = "https://mirror-a.example"
        html = _load_fixture("x1337.html")
        calls: list[str] = []

        def fake_plain_get(url: str, **_kwargs: Any) -> Any:
            calls.append(url)
            return self._response(
                text=html,
                url=url,
            )

        with (
            patch.object(site, "_plain_get", side_effect=fake_plain_get),
            patch.object(site, "_fetch_magnet", return_value=(None, UNKNOWN_AGE_DAYS)),
        ):
            results, hard_error = site._search("show", "TV")

        assert hard_error is False
        assert results == []
        assert len(calls) == 1
        assert calls[0].startswith("https://mirror-a.example/")

    def test_cloudflare_mirror_falls_through_to_later_plain_mirror(
        self, site: X1337Site
    ) -> None:
        site.url = "https://mirror-a.example"
        site.available_urls = [
            "https://mirror-a.example",
            "https://mirror-b.example",
        ]
        site._mirror_pref = None
        html = _load_fixture("x1337.html")
        challenge = _load_fixture("x1337_challenge.html")

        def fake_plain_get(url: str, **_kwargs: Any) -> Any:
            if url.startswith("https://mirror-a.example"):
                return self._response(
                    status_code=403,
                    text=challenge,
                    url=url,
                    headers={"server": "cloudflare"},
                )
            return self._response(text=html, url=url)

        with (
            patch.object(site, "_plain_get", side_effect=fake_plain_get),
            patch.object(site, "_fetch_magnet", return_value=(None, UNKNOWN_AGE_DAYS)),
            patch.object(site, "_fetch_with_bypass") as bypass_fetch,
        ):
            results, hard_error = site._search("show", "TV")

        bypass_fetch.assert_not_called()
        assert hard_error is False
        assert results == []
        assert site._get_mirror_pref().ordered()[0] == "https://mirror-b.example"

    def test_offlist_redirect_falls_back_to_requested_mirror(
        self, site: X1337Site
    ) -> None:
        site.url = "https://1337xx.to"
        site.available_urls = ["https://1337xx.to"]
        site._mirror_pref = None
        html = _load_fixture("x1337.html")
        detail_html = _load_fixture("x1337_detail_dated.html")
        detail_calls: list[str] = []

        def fake_plain_get(url: str, **_kwargs: Any) -> Any:
            if "/torrent/" in url:
                detail_calls.append(url)
                return self._response(text=detail_html, url=url)
            return self._response(
                text=html,
                url="https://www.1337xx.to/sort-category-search/show/TV/seeders/desc/1/",
            )

        with patch.object(site, "_plain_get", side_effect=fake_plain_get):
            results, hard_error = site._search("show", "TV")

        assert hard_error is False
        assert len(results) == 3
        assert detail_calls
        assert all(url.startswith("https://1337xx.to/") for url in detail_calls)

    def test_onlist_redirect_origin_is_adopted(self, site: X1337Site) -> None:
        site.url = "https://1337x.to"
        site.available_urls = ["https://1337x.to", "https://1337x.st"]
        site._mirror_pref = None
        html = _load_fixture("x1337.html")
        detail_html = _load_fixture("x1337_detail_dated.html")
        detail_calls: list[str] = []

        def fake_plain_get(url: str, **_kwargs: Any) -> Any:
            if "/torrent/" in url:
                detail_calls.append(url)
                return self._response(text=detail_html, url=url)
            return self._response(
                text=html,
                url="https://1337x.st/sort-category-search/show/TV/seeders/desc/1/",
            )

        with patch.object(site, "_plain_get", side_effect=fake_plain_get):
            results, hard_error = site._search("show", "TV")

        assert hard_error is False
        assert len(results) == 3
        assert detail_calls
        assert all(url.startswith("https://1337x.st/") for url in detail_calls)

    def test_trending_offlist_redirect_does_not_raise(self, site: X1337Site) -> None:
        site.url = "https://1337xx.to"
        site.available_urls = ["https://1337xx.to"]
        site._mirror_pref = None
        html = _load_fixture("x1337.html")

        def fake_plain_get(_url: str, **_kwargs: Any) -> Any:
            return self._response(
                text=html,
                url="https://evil.example/top-100-television",
            )

        with (
            patch.object(site, "_plain_get", side_effect=fake_plain_get),
            patch.object(
                site,
                "_fetch_magnet",
                return_value=("magnet:?xt=urn:btih:test", UNKNOWN_AGE_DAYS),
            ),
        ):
            results = site._search_trending("TV", "show")

        assert results

    def test_valid_empty_result_stops_without_other_mirrors(
        self, site: X1337Site
    ) -> None:
        site.url = "https://mirror-a.example"
        empty = _load_fixture("x1337_empty.html")
        calls: list[str] = []

        def fake_plain_get(url: str, **_kwargs: Any) -> Any:
            calls.append(url)
            return self._response(text=empty, url=url)

        with patch.object(site, "_plain_get", side_effect=fake_plain_get):
            results, hard_error = site._search("missing", "TV")

        assert results == []
        assert hard_error is False
        assert len(calls) == 1

    def test_all_plain_fail_without_bypass_never_calls_solver(
        self, site: X1337Site
    ) -> None:
        site.url = "https://mirror-a.example"
        site.available_urls = [
            "https://mirror-a.example",
            "https://mirror-b.example",
        ]
        site._mirror_pref = None
        challenge = _load_fixture("x1337_challenge.html")

        def fake_plain_get(url: str, **_kwargs: Any) -> Any:
            return self._response(
                status_code=403,
                text=challenge,
                url=url,
                headers={"server": "cloudflare"},
            )

        with (
            patch.object(site, "_plain_get", side_effect=fake_plain_get),
            patch.object(site, "_fetch_with_bypass") as bypass_fetch,
            patch(
                "miramedia.indexers.sites.x1337.CloudflareSession",
                create=True,
            ) as session_cls,
        ):
            results, hard_error = site._search("show", "TV")

        bypass_fetch.assert_not_called()
        session_cls.assert_not_called()
        assert results == []
        assert hard_error is True

    def test_bypass_second_phase_uses_shared_deadline(self, site: X1337Site) -> None:
        bypass = type(
            "Bypass",
            (),
            {
                "config": type("Cfg", (), {"enabled": True})(),
                "solve": lambda _url, _timeout=None: None,
            },
        )()
        site = X1337Site(bypass=bypass, timeout=5)
        site.url = "https://mirror-a.example"
        site.available_urls = ["https://mirror-a.example"]
        site._mirror_pref = None
        challenge = _load_fixture("x1337_challenge.html")
        html = _load_fixture("x1337.html")

        def fake_plain_get(url: str, **_kwargs: Any) -> Any:
            return self._response(
                status_code=403,
                text=challenge,
                url=url,
                headers={"server": "cloudflare"},
            )

        solve_calls: list[float] = []

        def fake_bypass(_url: str, *, timeout: float) -> str | None:
            solve_calls.append(timeout)
            return html

        with (
            patch.object(site, "_plain_get", side_effect=fake_plain_get),
            patch.object(site, "_fetch_via_bypass_session", return_value=None),
            patch.object(site, "_fetch_with_bypass", side_effect=fake_bypass),
            patch.object(site, "_fetch_magnet", return_value=(None, UNKNOWN_AGE_DAYS)),
            patch.object(site, "_solver_deadline_seconds", return_value=100.0),
            patch(
                "miramedia.indexers.sites.x1337.time.monotonic", side_effect=[0.0, 50.0]
            ),
        ):
            results, hard_error = site._search("show", "TV")

        assert hard_error is False
        assert results == []
        assert len(solve_calls) == 1
        assert solve_calls[0] == pytest.approx(50.0)

    def test_concurrent_searches_keep_complete_mirror_snapshots(self) -> None:
        import threading

        site = X1337Site(bypass=None, timeout=5)
        site.url = "https://mirror-a.example"
        site.available_urls = [
            "https://mirror-a.example",
            "https://mirror-b.example",
            "https://mirror-c.example",
        ]
        site._mirror_pref = None
        empty = _load_fixture("x1337_empty.html")
        barrier = threading.Barrier(4)
        snapshots: list[tuple[str, ...]] = []

        def fake_plain_get(url: str, **_kwargs: Any) -> Any:
            return TestX1337MirrorFailover._response(text=empty, url=url)

        def worker() -> None:
            barrier.wait(timeout=5)
            for _ in range(20):
                snapshots.append(site._get_mirror_pref().ordered())
                site._get_mirror_pref().mark_success("https://mirror-b.example")

        with patch.object(site, "_plain_get", side_effect=fake_plain_get):
            threads = [threading.Thread(target=worker) for _ in range(4)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=30)
                assert not thread.is_alive()
            site._search("show", "TV")

        expected = {
            "https://mirror-a.example",
            "https://mirror-b.example",
            "https://mirror-c.example",
        }
        for snapshot in snapshots:
            assert set(snapshot) == expected
            assert len(snapshot) == 3


class TestX1337UploadAge:
    def test_valid_ordinal_date(self) -> None:
        html = _load_fixture("x1337_detail_dated.html")
        now = datetime(2026, 7, 1, tzinfo=UTC)
        assert parse_upload_age_days(html, now=now) == 5

    def test_year_boundary(self) -> None:
        html = "<span><strong>Date uploaded</strong></span><span>Dec. 31st '25</span>"
        now = datetime(2026, 1, 2, tzinfo=UTC)
        assert parse_upload_age_days(html, now=now) == 2

    def test_future_skew_clamped_to_zero(self) -> None:
        html = "<span><strong>Date uploaded</strong></span><span>Jan. 10th '27</span>"
        now = datetime(2026, 8, 9, tzinfo=UTC)
        assert parse_upload_age_days(html, now=now) == 0

    def test_missing_and_malformed_use_sentinel(self) -> None:
        assert parse_upload_age_days("<html></html>") == UNKNOWN_AGE_DAYS
        assert (
            parse_upload_age_days(
                "<span><strong>Date uploaded</strong></span><span>Foob. 99th '99</span>"
            )
            == UNKNOWN_AGE_DAYS
        )


# ---------------------------------------------------------------------------
# BitTorrented — JSON API
# ---------------------------------------------------------------------------


class TestBitTorrentedParsing:
    @pytest.fixture
    def site(self) -> BitTorrentedSite:
        return BitTorrentedSite()

    def test_map_fixture_rows(self, site: BitTorrentedSite) -> None:
        payload = _load_json_fixture("bittorrented.json")
        results = site._map_results(payload["results"])

        assert len(results) == 3
        assert results[0].title == "Sample Series S01E01 1080p"
        assert results[0].seeders == 42
        assert results[0].size == 1610612736
        assert results[0].indexer == "bittorrented"
        assert results[0].download_url == build_magnet(
            "aabbccddeeff00112233445566778899aabbccdd",
            "Sample Series S01E01 1080p",
        )

        assert results[1].seeders == 0
        assert results[2].title == "Release & Partner S02E03"
        params = _magnet_query_params(results[2].download_url)
        assert unquote(params["dn"][0]) == "Release & Partner S02E03"

    def test_short_query_skips_request(self, site: BitTorrentedSite) -> None:
        with patch.object(site, "_fetch_json") as fetch_json:
            assert site._search_api("ab") == []
        fetch_json.assert_not_called()

    def test_malformed_envelope_fails_safe(self, site: BitTorrentedSite) -> None:
        with patch.object(site, "_fetch_json", return_value={"results": "nope"}):
            assert site._search_api("valid query") == []

    def test_api_error_returns_empty(self, site: BitTorrentedSite) -> None:
        with patch.object(site, "_fetch_json", side_effect=RuntimeError("boom")):
            assert site._search_api("valid query") == []

    def test_search_uses_expected_params(self, site: BitTorrentedSite) -> None:
        payload = _load_json_fixture("bittorrented.json")
        with patch.object(site, "_fetch_json", return_value=payload) as fetch_json:
            site.search("sample show", "tv")

        fetch_json.assert_called_once()
        url, kwargs = fetch_json.call_args
        assert url == ("https://bittorrented.com/api/search/torrents",)
        assert kwargs["params"] == {
            "q": "sample show",
            "type": "video",
            "limit": 50,
            "sortBy": "seeders",
            "sortOrder": "desc",
        }


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
        with patch.object(site, "_fetch_yts_json", return_value=payload):
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
        with patch.object(site, "_fetch_yts_json", return_value=payload):
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
        with patch.object(site, "_fetch_yts_json", return_value=payload):
            results = site._search_yts("test movie")

        assert len(results) == 1
        assert results[0].title == "Test Movie (2024) 720p web"
        assert results[0].download_url == build_magnet(
            _VALID_HEX_HASH, "Test Movie (2024) 720p web"
        )


class TestYtsMirrorFailover:
    @pytest.fixture
    def site(self) -> YtsSite:
        return YtsSite(timeout=5)

    @staticmethod
    def _response(
        *,
        status_code: int = 200,
        json_data: dict | None = None,
        url: str = "",
    ) -> Any:
        return type(
            "Resp",
            (),
            {
                "status_code": status_code,
                "json": lambda _self: json_data,
                "url": url,
            },
        )()

    def test_configured_host_success_skips_other_mirrors(self, site: YtsSite) -> None:
        site.url = "https://mirror-a.example"
        payload = _load_json_fixture("yts.json")
        calls: list[str] = []

        def fake_yts_get(url: str, _params: dict | None, **_kwargs: Any) -> Any:
            calls.append(url)
            return self._response(json_data=payload, url=url)

        with patch.object(site, "_yts_get", side_effect=fake_yts_get):
            results = site._search_yts("test movie")

        assert len(results) == 2
        assert len(calls) == 1
        assert calls[0].startswith("https://mirror-a.example/")

    def test_redirect_normalized_https_origin_is_accepted(self, site: YtsSite) -> None:
        site.url = "https://yts.bz"
        site.available_urls = ["https://yts.bz", "https://yts.gg"]
        site._mirror_pref = None
        payload = _load_json_fixture("yts.json")

        def fake_yts_get(_url: str, _params: dict | None, **_kwargs: Any) -> Any:
            return self._response(
                json_data=payload,
                url="https://yts.gg/api/v2/list_movies.json?query_term=test+movie&limit=50",
            )

        with patch.object(site, "_yts_get", side_effect=fake_yts_get):
            results = site._search_yts("test movie")

        assert len(results) == 2
        assert site._get_mirror_pref().ordered()[0] == "https://yts.gg"

    def test_falls_through_every_mirror_before_failing(self, site: YtsSite) -> None:
        site.url = "https://mirror-a.example"
        site.available_urls = [
            "https://mirror-a.example",
            "https://mirror-b.example",
            "https://mirror-c.example",
        ]
        site._mirror_pref = None
        calls: list[str] = []

        def fake_yts_get(url: str, _params: dict | None, **_kwargs: Any) -> Any:
            calls.append(url)
            return self._response(status_code=503, url=url)

        with patch.object(site, "_yts_get", side_effect=fake_yts_get):
            results = site._search_yts("test movie")

        assert results == []
        assert len(calls) == 3
        assert {call.split("/")[2] for call in calls} == {
            "mirror-a.example",
            "mirror-b.example",
            "mirror-c.example",
        }

    def test_valid_empty_result_stops_without_other_mirrors(
        self, site: YtsSite
    ) -> None:
        site.url = "https://mirror-a.example"
        payload = _load_json_fixture("yts_empty.json")
        calls: list[str] = []

        def fake_yts_get(url: str, _params: dict | None, **_kwargs: Any) -> Any:
            calls.append(url)
            return self._response(json_data=payload, url=url)

        with patch.object(site, "_yts_get", side_effect=fake_yts_get):
            results = site._search_yts("nonexistent")

        assert results == []
        assert len(calls) == 1

    def test_api_error_response_advances_to_next_mirror(self, site: YtsSite) -> None:
        site.url = "https://mirror-a.example"
        site.available_urls = [
            "https://mirror-a.example",
            "https://mirror-b.example",
        ]
        site._mirror_pref = None
        payload = _load_json_fixture("yts.json")
        calls: list[str] = []

        def fake_yts_get(url: str, _params: dict | None, **_kwargs: Any) -> Any:
            calls.append(url)
            if url.startswith("https://mirror-a.example"):
                return self._response(
                    json_data={"status": "error", "status_message": "bad"},
                    url=url,
                )
            return self._response(json_data=payload, url=url)

        with patch.object(site, "_yts_get", side_effect=fake_yts_get):
            results = site._search_yts("test movie")

        assert len(results) == 2
        assert len(calls) == 2
        assert site._get_mirror_pref().ordered()[0] == "https://mirror-b.example"

    def test_malformed_json_advances_to_next_mirror(self, site: YtsSite) -> None:
        site.url = "https://mirror-a.example"
        site.available_urls = [
            "https://mirror-a.example",
            "https://mirror-b.example",
        ]
        site._mirror_pref = None
        payload = _load_json_fixture("yts.json")
        calls: list[str] = []

        def fake_yts_get(url: str, _params: dict | None, **_kwargs: Any) -> Any:
            calls.append(url)
            if url.startswith("https://mirror-a.example"):
                return type(
                    "Resp",
                    (),
                    {
                        "status_code": 200,
                        "json": lambda _self: (_ for _ in ()).throw(ValueError("bad")),
                        "url": url,
                    },
                )()
            return self._response(json_data=payload, url=url)

        with patch.object(site, "_yts_get", side_effect=fake_yts_get):
            results = site._search_yts("test movie")

        assert len(results) == 2
        assert len(calls) == 2

    def test_untrusted_redirect_advances_to_next_mirror(self, site: YtsSite) -> None:
        site.url = "https://mirror-a.example"
        site.available_urls = [
            "https://mirror-a.example",
            "https://mirror-b.example",
        ]
        site._mirror_pref = None
        payload = _load_json_fixture("yts.json")
        calls: list[str] = []

        def fake_yts_get(url: str, _params: dict | None, **_kwargs: Any) -> Any:
            calls.append(url)
            if url.startswith("https://mirror-a.example"):
                return self._response(
                    json_data=payload,
                    url="https://evil.example/api/v2/list_movies.json",
                )
            return self._response(json_data=payload, url=url)

        with patch.object(site, "_yts_get", side_effect=fake_yts_get):
            results = site._search_yts("test movie")

        assert len(results) == 2
        assert len(calls) == 2

    def test_all_mirrors_fail_returns_empty_list(self, site: YtsSite) -> None:
        site.url = "https://mirror-a.example"
        site.available_urls = [
            "https://mirror-a.example",
            "https://mirror-b.example",
        ]
        site._mirror_pref = None

        def fake_yts_get(_url: str, _params: dict | None, **_kwargs: Any) -> Any:
            msg = "down"
            raise ConnectionError(msg)

        with patch.object(site, "_yts_get", side_effect=fake_yts_get):
            results = site._search_yts("test movie")

        assert results == []

    def test_concurrent_searches_keep_complete_mirror_snapshots(self) -> None:
        import threading

        site = YtsSite(timeout=5)
        site.url = "https://mirror-a.example"
        site.available_urls = [
            "https://mirror-a.example",
            "https://mirror-b.example",
            "https://mirror-c.example",
        ]
        site._mirror_pref = None
        payload = _load_json_fixture("yts_empty.json")
        barrier = threading.Barrier(4)
        snapshots: list[tuple[str, ...]] = []

        def fake_yts_get(url: str, _params: dict | None, **_kwargs: Any) -> Any:
            return TestYtsMirrorFailover._response(json_data=payload, url=url)

        def worker() -> None:
            barrier.wait(timeout=5)
            for _ in range(20):
                snapshots.append(site._get_mirror_pref().ordered())
                site._get_mirror_pref().mark_success("https://mirror-b.example")

        with patch.object(site, "_yts_get", side_effect=fake_yts_get):
            threads = [threading.Thread(target=worker) for _ in range(4)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=30)
                assert not thread.is_alive()
            site._search_yts("test movie")

        expected = {
            "https://mirror-a.example",
            "https://mirror-b.example",
            "https://mirror-c.example",
        }
        for snapshot in snapshots:
            assert set(snapshot) == expected
            assert len(snapshot) == 3


# ---------------------------------------------------------------------------
# Shared empty-HTML assertion shape (HTML family sites)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fixture_name", "row_selector"),
    [
        ("x1337_empty.html", "table.table-list tbody tr"),
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
