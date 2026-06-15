"""1337x — Popular torrent site. Cloudflare-protected, requires HTML scraping."""

import logging
import re

from selectolax.parser import HTMLParser, Node

from miramedia.indexers.schemas import IndexerQueryResult
from miramedia.indexers.sites.base import BaseSite
from miramedia.indexers.utils import sanitize_search_query
from miramedia.movies.schemas import Movie
from miramedia.shows.schemas import Show

log = logging.getLogger(__name__)


class X1337Site(BaseSite):
    name = "1337x"
    url = "https://1337x.to"
    supports_tv = True
    supports_movies = True
    cloudflare_protected = True
    # Disabled by default: Cloudflare-walled and unreliable without a working
    # bypass. Users can enable it from the indexer settings.
    default_enabled = False

    def search(self, query: str, category: str) -> list[IndexerQueryResult]:
        cat_path = "TV" if category == "tv" else "Movies"
        results, hard_error = self._search(query, cat_path)
        # Trending is a noisy last resort — only worth it when the search
        # actually FAILED (bad request / unparseable page), not when it
        # legitimately returned 0 matches.
        if not results and hard_error:
            results = self._search_trending(cat_path, query)
        return results

    def search_show(
        self, query: str, show: Show, season_number: int
    ) -> list[IndexerQueryResult]:
        # Use the caller-provided query as-is — the indexer service builds
        # it with full episode context (``Show Name 2019 S05E08``) so a
        # specific-episode search actually matches single-episode releases.
        # Falls back to ``show + season`` if the full query returns nothing
        # (catches season packs that don't carry SxxEyy in their title),
        # and to the trending listing only when a search actually errored.
        results, hard_error = self._search(query, "TV")
        if results:
            return results

        clean_name = sanitize_search_query(show.name)
        short_query = f"{clean_name} S{season_number:02d}"
        if short_query != query:
            log.info(
                "1337x: no results for %r, falling back to broader query %r",
                query,
                short_query,
            )
            broad_results, broad_error = self._search(short_query, "TV")
            if broad_results:
                return broad_results
            hard_error = hard_error or broad_error

        # Only reach for trending on a real failure — a genuine 0-hit search
        # should return empty, not a list of unrelated trending torrents.
        if hard_error:
            log.info("1337x: search errored for %r, trying trending fallback", query)
            return self._search_trending("TV", short_query)
        return []

    def search_movie(
        self,
        query: str,
        movie: Movie,
    ) -> list[IndexerQueryResult]:
        # 1337x can't handle long queries — use just the movie name + year
        clean_name = sanitize_search_query(movie.name)
        short_query = f"{clean_name} {movie.year}" if movie.year else clean_name
        results, hard_error = self._search(short_query, "Movies")
        if not results and hard_error:
            results = self._search_trending("Movies", short_query)
        return results

    def _search(
        self, query: str, category: str
    ) -> tuple[list[IndexerQueryResult], bool]:
        # Try the category-filtered + seeder-sorted view first, then fall back
        # to the plain /search/. URL scheme follows the py1337x project
        # (github.com/hemantapkh/1337x): ``sort-category-search`` ranks
        # high-seed releases up top and filters by media type, while plain
        # ``/search/`` WITHOUT a category can come back empty (a known 1337x
        # quirk). Trying sorted-category first then plain gets the best of both
        # — and downstream scoring still filters by media type either way.
        from urllib.parse import quote_plus

        encoded = quote_plus(query)
        search_urls = (
            f"{self.url}/sort-category-search/{encoded}/{category}/seeders/desc/1/",
            f"{self.url}/search/{encoded}/1/",
        )
        hard_error = False
        for search_url in search_urls:
            try:
                html = self._fetch(search_url)
            except Exception as exc:
                # A fetch-layer failure (CF couldn't be bypassed, HTTP error)
                # IS a hard error — let the caller fall back to trending.
                log.warning("1337x search request failed for %r: %s", search_url, exc)
                hard_error = True
                continue
            results, err = self._parse_results(html, query)
            if results:
                return results, False
            hard_error = hard_error or err
        return [], hard_error

    def _search_trending(
        self, category: str, filter_query: str
    ) -> list[IndexerQueryResult]:
        """Fallback: fetch the trending/top page and filter results locally."""
        cat_path = category.lower()
        # 1337x trending uses "television" not "tv" in the URL slug
        # (1337x.to/top-100-television, 1337x.to/top-100-movies).
        trending_slug = "television" if cat_path == "tv" else cat_path
        trending_url = f"{self.url}/top-100-{trending_slug}"
        try:
            html = self._fetch(trending_url)
        except Exception as exc:
            log.warning("1337x trending fetch failed: %s", exc)
            return []

        all_results, _ = self._parse_results(html, f"trending/{cat_path}")
        if not all_results or not filter_query:
            return all_results

        # Filter results that match any word from the query
        query_words = filter_query.lower().split()
        filtered = [
            r
            for r in all_results
            if any(word in r.title.lower() for word in query_words)
        ]
        if filtered:
            log.info(
                f"1337x trending fallback: {len(filtered)} results matching '{filter_query}'"
            )
        return filtered

    def _parse_results(
        self, html: str, label: str
    ) -> tuple[list[IndexerQueryResult], bool]:
        """Parse search/trending HTML into ``(results, hard_error)``.

        1337x sets the page ``<title>`` to "Error something went wrong." for
        BOTH a legitimate 0-results search AND a real failure — so the title
        is useless as a discriminator. The real status lives in the body copy
        (roughly ``/html/body/main/.../p``, but that path moves), so we scan
        the rendered body text instead:

          * a result table present  → parse it (``hard_error=False``)
          * "no results"            → genuine 0 hits (``hard_error=False``)
          * "bad request"           → real failure (``hard_error=True``)
          * anything else w/o rows  → unknown page, e.g. CF interstitial or a
                                       DOM change (``hard_error=True``)

        ``hard_error`` lets the caller decide whether the noisy trending
        fallback is warranted — it is on a real failure, but not on a search
        that simply matched nothing.
        """
        tree = HTMLParser(html)

        rows = tree.css("table.table-list tbody tr")
        if rows:
            return self._parse_rows(rows, label), False

        body = tree.body
        page_text = (
            body.text(separator=" ", strip=True) if body else (html or "")
        ).lower()

        if "no results" in page_text:
            log.info("1337x: 0 results (no matches) for: %s", label)
            return [], False
        if "bad request" in page_text:
            log.warning("1337x: bad request for: %s", label)
            return [], True

        # Unknown page with no result table — surface context to tell apart a
        # CF interstitial that slipped through vs a 1337x DOM change.
        title_el = tree.css_first("title")
        page_title = title_el.text(strip=True) if title_el else "N/A"
        snippet = (html or "")[:400].replace("\n", " ").strip()
        log.info(
            "1337x returned no result table for: %s (page_title=%r, html_bytes=%d, snippet=%s)",
            label,
            page_title,
            len(html or ""),
            snippet,
        )
        return [], True

    def _parse_rows(self, rows: list[Node], label: str) -> list[IndexerQueryResult]:
        """Turn result-table rows into IndexerQueryResults (with magnets)."""
        # Parse metadata from search page first (no network calls)
        parsed_rows = []
        for row in rows[:10]:  # limit — each needs a detail page fetch for magnet link
            try:
                meta = self._parse_row_metadata(row)
                if meta:
                    parsed_rows.append(meta)
            except Exception:
                log.debug("Failed to parse 1337x row", exc_info=True)

        # Fetch detail pages sequentially to get magnet links
        # (parallel fetching causes thread pool deadlocks when called from
        # the native indexer's own ThreadPoolExecutor)
        results: list[IndexerQueryResult] = []
        for meta in parsed_rows:
            try:
                magnet = self._fetch_magnet(meta)
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
                            indexer="1337x",
                        )
                    )
            except Exception:
                log.debug("Failed to fetch magnet for %s", meta["title"], exc_info=True)

        log.info(f"1337x returned {len(results)} results for: {label}")
        return results

    @staticmethod
    def _parse_row_metadata(row: Node) -> dict | None:
        """Parse metadata from a search result row (no network calls)."""
        # The torrent link is the anchor whose href contains ``/torrent/`` —
        # more robust than positional nth-anchor (the name cell leads with a
        # category-icon link). Mirrors py1337x's ``a[href*="/torrent/"]``.
        link = row.css_first('a[href*="/torrent/"]')
        if not link:
            return None

        title = link.text(strip=True)
        detail_path = link.attributes.get("href") or ""
        if not detail_path:
            return None

        seeders = 0
        seed_cell = row.css_first("td.coll-2")
        if seed_cell:
            try:
                seeders = int(seed_cell.text(strip=True))
            except ValueError:
                pass

        size_bytes = 0
        size_cell = row.css_first("td.coll-4")
        if size_cell:
            size_bytes = X1337Site._parse_size(size_cell.text(strip=True))

        return {
            "title": title,
            "detail_path": detail_path,
            "seeders": seeders,
            "size": size_bytes,
        }

    def _fetch_magnet(self, meta: dict) -> str | None:
        """Fetch a detail page and extract the magnet link.

        1337x detail pages render the magnet inside ``ul.dropdown-menu`` or
        a direct ``<a class="magnet" href="magnet:?xt=urn:btih:...">``.
        Either way ``a[href^='magnet:']`` finds the first hit.
        """
        detail_url = f"{self.url}{meta['detail_path']}"
        try:
            detail_html = self._fetch(detail_url)
        except Exception as exc:
            log.warning("Failed to fetch 1337x detail page %s: %s", detail_url, exc)
            return None

        detail_tree = HTMLParser(detail_html)
        magnet_link = detail_tree.css_first("a[href^='magnet:']")
        if not magnet_link:
            log.warning(
                "No magnet link on 1337x detail page %s (parser may need updating)",
                detail_url,
            )
            return None

        href = magnet_link.attributes.get("href") or ""
        return href or None

    @staticmethod
    def _parse_size(size_str: str) -> int:
        """Parse size strings like '1.5 GB' to bytes."""
        match = re.match(r"([\d.]+)\s*(GB|MB|KB|TB)", size_str, re.IGNORECASE)
        if not match:
            return 0
        value = float(match.group(1))
        unit = match.group(2).upper()
        multipliers = {"KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}
        return int(value * multipliers.get(unit, 1))
