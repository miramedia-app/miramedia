"""EZTV — Public TV torrent site with a free JSON API."""

import logging
import re
from typing import ClassVar

from miramedia.indexers.schemas import IndexerQueryResult
from miramedia.indexers.sites.base import BaseSite, build_magnet
from miramedia.indexers.utils import is_search_query_relevant
from miramedia.movies.schemas import Movie
from miramedia.shows.schemas import Show

log = logging.getLogger(__name__)


class EztvSite(BaseSite):
    name = "eztv"
    url = "https://eztvx.to"
    # First-party EZTV mirrors. The JSON API is served same-origin on each, so
    # failover just retries the same path.
    available_urls: ClassVar[list[str]] = [
        "https://eztvx.to",
        "https://eztv.wf",
        "https://eztv.tf",
        "https://eztv.yt",
        "https://eztv1.xyz",
    ]
    supports_tv = True
    supports_movies = False
    cloudflare_protected = False
    # Probe the JSON API, not the (CF-frontable) website root.
    test_path = "/api/get-torrents?limit=1"

    def search(self, query: str, category: str) -> list[IndexerQueryResult]:
        return self._search_eztv(query)

    def search_show(
        self, query: str, show: Show, season_number: int
    ) -> list[IndexerQueryResult]:
        params: dict[str, str | int] = {"limit": 100, "page": 1}

        # EZTV supports IMDB ID search (strip 'tt' prefix if present)
        if show.imdb_id:
            imdb_num = show.imdb_id.lstrip("t")
            params["imdb_id"] = imdb_num
        else:
            # Fall back to generic search — EZTV API doesn't have a query param
            return self._search_eztv(query)

        results = self._fetch_eztv_api(params)

        # Filter for the requested season
        season_str = f"S{season_number:02d}"
        return [r for r in results if season_str.upper() in r.title.upper()]

    def search_movie(
        self,
        query: str,
        movie: Movie,
    ) -> list[IndexerQueryResult]:
        return []  # EZTV is TV only

    def _search_eztv(self, query: str) -> list[IndexerQueryResult]:
        """Fallback text-based search — EZTV API doesn't support query text,
        so we search by page and filter client-side."""
        params: dict[str, str | int] = {"limit": 100, "page": 1}
        results = self._fetch_eztv_api(params)
        # Show-wide searches append the metadata premiere year, but episode
        # releases normally omit it (or contain an episode air year instead).
        # Remove only that final appended token: a year may itself be the show
        # title (``1923 2022``), and season queries do not append a year.
        title_query = re.sub(r"\s+(?:19|20)\d{2}\s*$", "", query).strip()
        return [
            result
            for result in results
            if is_search_query_relevant(result, title_query)
        ]

    def _fetch_eztv_api(self, params: dict[str, str | int]) -> list[IndexerQueryResult]:
        try:
            data = self._fetch_over_mirrors(
                "/api/get-torrents", params=params, fetch=self._fetch_json
            )
        except Exception:
            log.exception("EZTV search failed")
            return []

        torrents = data.get("torrents") or []
        results: list[IndexerQueryResult] = []

        for t in torrents:
            title = t.get("filename", t.get("title", ""))
            if not title:
                continue

            magnet_url = t.get("magnet_url", "")
            torrent_url = t.get("torrent_url", "")
            download_url = magnet_url or torrent_url
            if not download_url:
                info_hash = t.get("hash", "")
                if info_hash:
                    try:
                        download_url = build_magnet(info_hash, title)
                    except ValueError:
                        log.warning(
                            "%s: dropping row with invalid info hash (len=%d)",
                            "eztv",
                            len(info_hash.strip()),
                        )
                        continue
                else:
                    continue

            size_bytes = t.get("size_bytes", 0)
            try:
                size_bytes = int(size_bytes)
            except (ValueError, TypeError):
                size_bytes = 0

            # EZTV's API has shipped this field as "seeds" historically but
            # some responses use "seeders" / "se". Try them all; leave the
            # field None when nothing matches so the UI can render "—"
            # instead of falsely claiming "0 seeders".
            raw_seed = None
            for key in ("seeds", "seeders", "se"):
                if key in t and t[key] is not None:
                    raw_seed = t[key]
                    break
            seeders: int | None
            if raw_seed is None:
                seeders = None
            else:
                try:
                    seeders = int(raw_seed)
                except (ValueError, TypeError):
                    seeders = None

            results.append(
                IndexerQueryResult(
                    title=title,
                    download_url=download_url,
                    seeders=seeders,
                    flags=[],
                    size=size_bytes,
                    usenet=False,
                    age=0,
                    indexer="eztv",
                )
            )

        log.info("EZTV returned %s results", len(results))
        return results
