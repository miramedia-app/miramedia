"""Parsing tests for the TorrentDownloads native indexer scraper."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from selectolax.parser import HTMLParser

from miramedia.indexers.sites.torrentdownloads import TorrentDownloadsSite

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "indexer_sites"


def _load_fixture(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


class TestTorrentDownloadsParsing:
    @pytest.fixture
    def site(self) -> TorrentDownloadsSite:
        return TorrentDownloadsSite()

    def test_parse_row_metadata_skips_header_and_ads(
        self, site: TorrentDownloadsSite
    ) -> None:
        rows = HTMLParser(_load_fixture("torrentdownloads.html")).css("div.grey_bar3")
        # header + ad + 2 real rows
        assert len(rows) == 4

        # header row (no /torrent link) is skipped
        assert site._parse_row_metadata(rows[0]) is None
        # ad/disqus link (no numeric id) is skipped
        assert site._parse_row_metadata(rows[1]) is None

        meta = site._parse_row_metadata(rows[2])
        assert meta is not None
        # leading cosmetic dashes are stripped
        assert meta["title"] == "Inception 2010 1080p AMZN WEB-DL DDP5 1 H 265-ViSTA"
        assert meta["detail_path"].startswith("/torrent/1707082768/")
        assert meta["size"] == int(3.73 * 1024**3)

    def test_search_fetches_magnet_and_seeders_from_detail(
        self, site: TorrentDownloadsSite
    ) -> None:
        list_html = _load_fixture("torrentdownloads.html")
        detail_html = _load_fixture("torrentdownloads_detail.html")

        # _search fetches the list page once, then a detail page per row.
        # First _fetch call returns the list; the rest return the detail page.
        with patch.object(
            site, "_fetch", side_effect=[list_html, detail_html, detail_html]
        ):
            results = site._search("inception")

        assert len(results) == 2
        first = results[0]
        assert first.title == "Inception 2010 1080p AMZN WEB-DL DDP5 1 H 265-ViSTA"
        assert first.download_url.startswith(
            "magnet:?xt=urn:btih:AAC2293760E0BB60DB966E755A2E34BBBA26ED75"
        )
        assert first.seeders == 16
        assert first.indexer == "torrentdownloads"

    def test_empty_page_returns_nothing(self, site: TorrentDownloadsSite) -> None:
        with patch.object(site, "_fetch", return_value="<html><body></body></html>"):
            assert site._search("nothing here") == []
