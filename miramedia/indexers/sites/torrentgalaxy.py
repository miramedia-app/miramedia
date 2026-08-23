"""TorrentGalaxy — Public torrent site with HTML scraping."""

import logging
import re
import urllib.parse

from selectolax.parser import HTMLParser, Node

from miramedia.indexers.schemas import IndexerQueryResult
from miramedia.indexers.sites.base import BaseSite
from miramedia.movies.schemas import Movie
from miramedia.shows.schemas import Show

log = logging.getLogger(__name__)


class TorrentGalaxySite(BaseSite):
    name = "torrentgalaxy"
    url = "https://torrentgalaxy.one"
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
        encoded = urllib.parse.quote(query)
        try:
            html = self._fetch(f"{self.url}/get-posts/keywords:{encoded}/")
        except Exception:
            log.exception("TorrentGalaxy search failed")
            return []

        tree = HTMLParser(html)
        rows = tree.css("div.tgxtablerow")
        if not rows:
            return []

        # Parse metadata first (no network), then fetch detail pages sequentially
        # to extract magnets. Limit fan-out — detail page per row is expensive.
        parsed: list[dict] = []
        for row in rows[:15]:
            try:
                meta = self._parse_row_metadata(row)
                if meta:
                    parsed.append(meta)
            except Exception:
                log.debug("Failed to parse TorrentGalaxy row", exc_info=True)

        results: list[IndexerQueryResult] = []
        for meta in parsed:
            try:
                magnet = self._fetch_magnet(meta["detail_path"])
                if magnet:
                    results.append(
                        IndexerQueryResult(
                            title=meta["title"],
                            download_url=magnet,
                            seeders=meta["seeders"],
                            flags=[],
                            size=meta["size"],
                            usenet=False,
                            age=0,
                            indexer="torrentgalaxy",
                        )
                    )
            except Exception:
                log.debug(
                    "Failed to fetch TorrentGalaxy magnet for %s",
                    meta["title"],
                    exc_info=True,
                )

        log.info("TorrentGalaxy returned %s results for: %s", len(results), query)
        return results

    _HEALTH_RE = re.compile(r"\[\s*(\d+)\s*/\s*(\d+)\s*\]")
    _SIZE_RE = re.compile(r"([\d.]+)\s*(GB|MB|KB|TB)", re.IGNORECASE)

    def _parse_row_metadata(self, row: Node) -> dict | None:
        title_link = row.css_first("a.txlight[title]")
        if not title_link:
            return None
        title = title_link.attributes.get("title") or title_link.text(strip=True)
        detail_path = title_link.attributes.get("href") or ""
        if not title or not detail_path:
            return None

        # selectolax Node.text(separator=...) collapses whitespace differently
        # than BS4's get_text — explicit separator keeps tokens apart for the
        # health/size regexes below.
        row_text = row.text(separator=" ", strip=True)

        seeders = 0
        health = self._HEALTH_RE.search(row_text)
        if health:
            try:
                seeders = int(health.group(1))
            except ValueError:
                pass

        size_bytes = 0
        size_match = self._SIZE_RE.search(row_text)
        if size_match:
            size_bytes = self._parse_size(size_match.group(0))

        return {
            "title": title,
            "detail_path": detail_path,
            "seeders": seeders,
            "size": size_bytes,
        }

    def _fetch_magnet(self, detail_path: str) -> str | None:
        if detail_path.startswith("/"):
            detail_url = f"{self.url}{detail_path}"
        else:
            detail_url = detail_path
        try:
            html = self._fetch(detail_url)
        except Exception:
            log.debug("Failed to fetch TorrentGalaxy detail page: %s", detail_url)
            return None
        tree = HTMLParser(html)
        magnet = tree.css_first("a[href^='magnet:']")
        if not magnet:
            return None
        href = magnet.attributes.get("href") or ""
        return href or None

    @staticmethod
    def _parse_size(size_str: str) -> int:
        match = re.match(r"([\d.]+)\s*(GB|MB|KB|TB)", size_str, re.IGNORECASE)
        if not match:
            return 0
        value = float(match.group(1))
        unit = match.group(2).upper()
        multipliers = {"KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}
        return int(value * multipliers.get(unit, 1))
