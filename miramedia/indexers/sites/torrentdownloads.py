"""TorrentDownloads — Public torrent site with HTML scraping.

The search results page lists titles + size but shows seed health only as an
image, and magnets live on the per-torrent detail page. So we parse the list
first (no network), then fetch each detail page to pull the magnet and the
numeric seed count.
"""

import logging
import re

from selectolax.parser import HTMLParser, Node

from miramedia.indexers.schemas import IndexerQueryResult
from miramedia.indexers.sites.base import BaseSite
from miramedia.movies.schemas import Movie
from miramedia.shows.schemas import Show

log = logging.getLogger(__name__)


class TorrentDownloadsSite(BaseSite):
    name = "torrentdownloads"
    url = "https://www.torrentdownloads.pro"
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

    # Real result rows link to ``/torrent/<id>/<slug>``; ad / disqus links
    # (e.g. ``/torrent/Name#disqus_thread``) are skipped by this shape.
    _DETAIL_RE = re.compile(r"^/torrent/\d+/")
    _SIZE_RE = re.compile(r"([\d.]+)\s*(GB|MB|KB|TB)", re.IGNORECASE)
    _MAGNET_RE = re.compile(r'magnet:\?xt=urn:btih:[^"\'\s]+')
    _SEEDS_RE = re.compile(r"Seeds:\s*</span>\s*(\d+)")

    def _search(self, query: str) -> list[IndexerQueryResult]:
        params = {"search": query}
        try:
            html = self._fetch_over_mirrors("/search/", params=params)
        except Exception:
            log.exception("TorrentDownloads search failed")
            return []

        tree = HTMLParser(html)
        rows = tree.css("div.grey_bar3")
        if not rows:
            return []

        parsed: list[dict] = []
        for row in rows:
            try:
                meta = self._parse_row_metadata(row)
                if meta:
                    parsed.append(meta)
            except Exception:
                log.debug("Failed to parse TorrentDownloads row", exc_info=True)

        # Detail-page fetch per row is expensive — bound the fan-out.
        results: list[IndexerQueryResult] = []
        for meta in parsed[:15]:
            try:
                magnet, seeders = self._fetch_detail(meta["detail_path"])
                if magnet:
                    results.append(
                        IndexerQueryResult(
                            title=meta["title"],
                            download_url=magnet,
                            seeders=seeders,
                            flags=[],
                            size=meta["size"],
                            usenet=False,
                            age=0,
                            indexer="torrentdownloads",
                        )
                    )
            except Exception:
                log.debug(
                    "Failed to fetch TorrentDownloads magnet for %s",
                    meta["title"],
                    exc_info=True,
                )

        log.info("TorrentDownloads returned %s results for: %s", len(results), query)
        return results

    def _parse_row_metadata(self, row: Node) -> dict | None:
        link = row.css_first('a[href^="/torrent/"]')
        if not link:
            return None
        detail_path = link.attributes.get("href") or ""
        if not self._DETAIL_RE.match(detail_path):
            return None
        # Some rows are prefixed with cosmetic dashes ("-   Inception ..."),
        # which would break the downstream "title starts with media name"
        # relevance check — strip leading punctuation/whitespace.
        title = re.sub(r"^[\s.\-]+", "", link.text(strip=True)).strip()
        if not title:
            return None

        # Size is the last plain <span> in the row (e.g. "3.73 GB").
        size_bytes = 0
        for span in row.css("span"):
            match = self._SIZE_RE.search(span.text(strip=True))
            if match:
                size_bytes = self._parse_size(match.group(0))

        return {"title": title, "detail_path": detail_path, "size": size_bytes}

    def _fetch_detail(self, detail_path: str) -> tuple[str | None, int]:
        # Fetch the detail page from the last-known-good mirror the search used.
        base = self._get_mirror_pref().ordered()[0]
        try:
            html = self._fetch(f"{base}{detail_path}")
        except Exception:
            log.debug("Failed to fetch TorrentDownloads detail page: %s", detail_path)
            return None, 0

        magnet_match = self._MAGNET_RE.search(html)
        magnet = magnet_match.group(0) if magnet_match else None

        seeders = 0
        seed_match = self._SEEDS_RE.search(html)
        if seed_match:
            try:
                seeders = int(seed_match.group(1))
            except ValueError:
                pass

        return magnet, seeders

    def _parse_size(self, size_str: str) -> int:
        match = self._SIZE_RE.match(size_str)
        if not match:
            return 0
        value = float(match.group(1))
        unit = match.group(2).upper()
        multipliers = {"KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}
        return int(value * multipliers.get(unit, 1))
