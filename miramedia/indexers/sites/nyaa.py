"""Nyaa — Public anime torrent site with HTML scraping."""

import logging
import re
from typing import ClassVar

from selectolax.parser import HTMLParser, Node

from miramedia.indexers.schemas import IndexerQueryResult
from miramedia.indexers.sites.base import BaseSite
from miramedia.movies.schemas import Movie
from miramedia.shows.schemas import Show

log = logging.getLogger(__name__)


class NyaaSite(BaseSite):
    name = "nyaa"
    url = "https://nyaa.si"
    # First-party Nyaa mirrors — same HTML layout, so the scraper works
    # unchanged on each.
    available_urls: ClassVar[list[str]] = [
        "https://nyaa.si",
        "https://nyaa.iss.ink",
        "https://nyaa.land",
        "https://nyaa.mom",
        "https://nyaa.media",
    ]
    supports_tv = True
    supports_movies = True
    cloudflare_protected = False

    def search(self, query: str, category: str) -> list[IndexerQueryResult]:
        return self._search(query)

    def search_show(
        self,
        query: str,
        show: Show,
        season_number: int,
    ) -> list[IndexerQueryResult]:
        return self._search(query)

    def search_movie(
        self,
        query: str,
        movie: Movie,
    ) -> list[IndexerQueryResult]:
        return self._search(query)

    def _search(self, query: str) -> list[IndexerQueryResult]:
        # f=0 (no filter), c=0_0 (all categories), s=seeders, o=desc
        params = {"f": "0", "c": "0_0", "q": query, "s": "seeders", "o": "desc"}
        try:
            html = self._fetch_over_mirrors("/", params=params)
        except Exception:
            log.exception("Nyaa search failed")
            return []

        tree = HTMLParser(html)
        rows = tree.css("table.torrent-list tbody tr")
        if not rows:
            return []

        results: list[IndexerQueryResult] = []
        for row in rows[:50]:
            try:
                result = self._parse_row(row)
                if result:
                    results.append(result)
            except Exception:
                log.debug("Failed to parse Nyaa row", exc_info=True)

        log.info("Nyaa returned %s results for: %s", len(results), query)
        return results

    def _parse_row(self, row: Node) -> IndexerQueryResult | None:
        cells = row.css("td")
        if len(cells) < 7:
            return None

        # Title (column 2) — selectolax has no :last-of-type, so iterate.
        title_cell = cells[1]
        anchors = title_cell.css("a")
        if not anchors:
            return None
        title_link = anchors[-1]
        title = title_link.text(strip=True)

        # Download links (column 3) — magnet is the second link
        link_cell = cells[2]
        magnet_link = link_cell.css_first("a[href^='magnet:']")
        if not magnet_link:
            return None
        download_url = magnet_link.attributes.get("href") or ""
        if not download_url:
            return None

        # Size (column 4)
        size_text = cells[3].text(strip=True)
        size_bytes = self._parse_size(size_text)

        # Seeders (column 6)
        seeders = 0
        try:
            seeders = int(cells[5].text(strip=True))
        except (ValueError, IndexError):
            pass

        return IndexerQueryResult(
            title=title,
            download_url=download_url,
            seeders=seeders,
            flags=[],
            size=size_bytes,
            usenet=False,
            age=0,
            indexer="nyaa",
        )

    @staticmethod
    def _parse_size(size_str: str) -> int:
        match = re.match(
            r"([\d.]+)\s*(GiB|MiB|KiB|TiB|GB|MB|KB|TB)", size_str, re.IGNORECASE
        )
        if not match:
            return 0
        value = float(match.group(1))
        unit = match.group(2).upper()
        multipliers = {
            "KIB": 1024,
            "KB": 1024,
            "MIB": 1024**2,
            "MB": 1024**2,
            "GIB": 1024**3,
            "GB": 1024**3,
            "TIB": 1024**4,
            "TB": 1024**4,
        }
        return int(value * multipliers.get(unit, 1))
