"""LimeTorrents — Public torrent site with HTML scraping."""

import logging
import re

from selectolax.parser import HTMLParser, Node

from miramedia.indexers.schemas import IndexerQueryResult
from miramedia.indexers.sites.base import BaseSite, build_magnet
from miramedia.movies.schemas import Movie
from miramedia.shows.schemas import Show

log = logging.getLogger(__name__)


class LimeTorrentsSite(BaseSite):
    name = "limetorrents"
    url = "https://www.limetorrents.lol"
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
        search_query = query.replace(" ", "-")
        search_url = f"{self.url}/search/all/{search_query}/"
        try:
            html = self._fetch(search_url)
        except Exception:
            log.exception("LimeTorrents search failed")
            return []

        tree = HTMLParser(html)
        rows = tree.css("table.table2 tr")
        if not rows:
            return []

        results: list[IndexerQueryResult] = []
        for row in rows[:50]:
            try:
                result = self._parse_row(row)
                if result:
                    results.append(result)
            except Exception:
                log.debug("Failed to parse LimeTorrents row", exc_info=True)

        log.info(f"LimeTorrents returned {len(results)} results for: {query}")
        return results

    _HASH_RE = re.compile(r"/torrent/([A-Fa-f0-9]{40})\.torrent")

    def _parse_row(self, row: Node) -> IndexerQueryResult | None:
        name_cell = row.css_first("td.tdleft div.tt-name")
        if not name_cell:
            return None

        anchors = name_cell.css("a")
        if len(anchors) < 2:
            return None

        # First anchor: itorrents.net/.../{HASH}.torrent
        hash_match = self._HASH_RE.search(anchors[0].attributes.get("href") or "")
        if not hash_match:
            return None
        info_hash = hash_match.group(1).lower()

        # Second anchor: title + detail page link
        title = anchors[1].text(strip=True)
        if not title:
            return None

        size_cell = row.css("td.tdnormal")
        size_text = size_cell[1].text(strip=True) if len(size_cell) > 1 else ""
        size_bytes = self._parse_size(size_text)

        seeders = 0
        seed_cell = row.css_first("td.tdseed")
        if seed_cell:
            seed_text = seed_cell.text(strip=True).replace(",", "")
            try:
                seeders = int(seed_text)
            except ValueError:
                pass

        return IndexerQueryResult(
            title=title,
            download_url=build_magnet(info_hash, title),
            seeders=seeders,
            flags=[],
            size=size_bytes,
            usenet=False,
            age=0,
            indexer="limetorrents",
        )

    @staticmethod
    def _parse_size(size_str: str) -> int:
        match = re.match(r"([\d.]+)\s*(GB|MB|KB|TB)", size_str, re.IGNORECASE)
        if not match:
            return 0
        value = float(match.group(1))
        unit = match.group(2).upper()
        multipliers = {"KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}
        return int(value * multipliers.get(unit, 1))
