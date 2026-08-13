import asyncio
import html
import logging
import re
import socket
import threading
import time
from collections import deque
from typing import ClassVar, override

import requests
import urllib3.util.connection
from urllib3.util.retry import Retry

import miramedia.metadata.utils
from miramedia.config import MiraMediaConfig
from miramedia.exceptions import MetadataProviderUnavailableError
from miramedia.metadata.backends.generic import AbstractMetadataProvider
from miramedia.metadata.cache import cached
from miramedia.metadata.schemas import MetaDataProviderSearchResult
from miramedia.metadata.utils import is_provider_unreachable
from miramedia.movies.schemas import Movie
from miramedia.shows.schemas import Episode, EpisodeNumber, Season, SeasonNumber, Show

log = logging.getLogger(__name__)

TVMAZE_BASE = "https://api.tvmaze.com"
CINEMETA_BASE = "https://v3-cinemeta.strem.io"


_ipv4_session_lock = threading.Lock()
_ipv4_session_singleton: requests.Session | None = None


def _ipv4_session() -> requests.Session:
    """Return a process-wide ``requests.Session`` pinned to AF_INET so the
    cinemeta + similar lookups don't trip over IPv6-less container bridges."""
    global _ipv4_session_singleton
    with _ipv4_session_lock:
        if _ipv4_session_singleton is not None:
            return _ipv4_session_singleton

        class _Ipv4Adapter(requests.adapters.HTTPAdapter):
            def init_poolmanager(self, *args: object, **kwargs: object) -> None:
                kwargs["socket_options"] = [
                    (socket.IPPROTO_TCP, socket.TCP_NODELAY, 1),
                ]
                # urllib3 default already picks v4 when this is patched.
                kwargs["source_address"] = None
                return super().init_poolmanager(*args, **kwargs)

        # urllib3's address-family helper — flip globally so any pool manager
        # spawned by our adapter prefers AF_INET. (Other unrelated requests
        # calls won't be affected because they go through their own sessions
        # that we don't modify.)
        urllib3.util.connection.allowed_gai_family = lambda: socket.AF_INET

        # Retry transient failures (DNS hiccups, dropped connections, 5xx)
        # a couple of times with exponential backoff before giving up. A NAS
        # bridge network resolving cinemeta intermittently is the common case.
        retry = Retry(
            total=2,
            connect=2,
            read=2,
            backoff_factor=0.5,
            status_forcelist=(502, 503, 504),
            allowed_methods=frozenset({"GET"}),
            raise_on_status=False,
        )
        session = requests.Session()
        session.mount("http://", _Ipv4Adapter(max_retries=retry))
        session.mount("https://", _Ipv4Adapter(max_retries=retry))
        _ipv4_session_singleton = session
        return session


def close_ipv4_session() -> None:
    """Close the IPv4-pinned requests.Session. Call from app lifespan finally.

    Idempotent + never raises so it's safe in shutdown ``finally`` blocks.
    """
    global _ipv4_session_singleton
    try:
        with _ipv4_session_lock:
            if _ipv4_session_singleton is not None:
                _ipv4_session_singleton.close()
                _ipv4_session_singleton = None
    except Exception:  # noqa: S110 — best-effort shutdown cleanup, failure non-fatal
        pass


# ISO 639-1 code → TVMaze language name (for language filtering)
_ISO_TO_TVMAZE_LANG: dict[str, str] = {
    "en": "English",
    "ja": "Japanese",
    "ko": "Korean",
    "zh": "Chinese",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "it": "Italian",
    "pt": "Portuguese",
    "ru": "Russian",
    "ar": "Arabic",
    "hi": "Hindi",
    "th": "Thai",
    "tr": "Turkish",
    "nl": "Dutch",
    "sv": "Swedish",
    "da": "Danish",
    "no": "Norwegian",
    "fi": "Finnish",
    "pl": "Polish",
    "cs": "Czech",
    "he": "Hebrew",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _strip_html(text: str | None) -> str:
    """Strip HTML tags from TVmaze summary text."""
    if not text:
        return ""
    return re.sub(r"<[^>]+>", "", text).strip()


def _parse_cinemeta_runtime(runtime_str: str | None) -> int | None:
    """Parse Cinemeta runtime string like '2h 10min' or '110 min' to minutes."""
    if not runtime_str:
        return None
    # Try "Xh Ymin" format
    match = re.match(r"(\d+)\s*h\s*(\d+)", runtime_str)
    if match:
        return int(match.group(1)) * 60 + int(match.group(2))
    # Try "X min" format
    match = re.match(r"(\d+)\s*min", runtime_str)
    if match:
        return int(match.group(1))
    # Try pure number
    try:
        return int(runtime_str)
    except ValueError:
        return None


def _parse_cinemeta_cast(cast_value: object) -> list[str]:
    """Parse cast from Cinemeta which may be a list or comma-separated string."""
    if cast_value is None:
        return []
    if isinstance(cast_value, list):
        return [html.unescape(str(c)).strip() for c in cast_value if c]
    if isinstance(cast_value, str):
        return [html.unescape(c).strip() for c in cast_value.split(",") if c.strip()]
    return []


def _parse_cinemeta_year(year_val: object) -> int | None:
    """Parse Cinemeta year which may be int, string like '2020' or '2020–'."""  # noqa: RUF002 — en dash is the literal Cinemeta range separator
    if isinstance(year_val, int):
        return year_val
    if isinstance(year_val, str) and len(year_val) >= 4:
        try:
            return int(year_val[:4])
        except ValueError:
            return None
    return None


def _rating_sort_key(m: dict) -> float:
    """Extract IMDb rating as float for sorting, defaulting to 0."""
    try:
        return float(m.get("imdbRating") or 0)
    except (ValueError, TypeError):
        return 0.0


# ---------------------------------------------------------------------------
# Rate limiter for TVmaze (20 requests / 10 seconds)
# ---------------------------------------------------------------------------


class RateLimiter:
    def __init__(self, max_requests: int = 20, period: float = 10.0) -> None:
        self._timestamps: deque[float] = deque()
        self._max = max_requests
        self._period = period
        self._lock = threading.Lock()

    def _reserve(self) -> float:
        """Reserve a slot and return the delay (seconds) the caller should
        sleep before issuing the request. Thread-safe; does not block."""
        with self._lock:
            now = time.monotonic()
            while self._timestamps and now - self._timestamps[0] >= self._period:
                self._timestamps.popleft()
            sleep_time = 0.0
            if len(self._timestamps) >= self._max:
                sleep_time = self._period - (now - self._timestamps[0])
                if sleep_time < 0:
                    sleep_time = 0.0
                self._timestamps.popleft()
            # Record the (post-sleep) timestamp so subsequent reservations
            # compute the window correctly.
            self._timestamps.append(now + sleep_time)
            return sleep_time

    def wait(self) -> None:
        """Sync wait — safe to call from worker threads (e.g. ``to_thread``)."""
        delay = self._reserve()
        if delay > 0:
            time.sleep(delay)

    async def wait_async(self) -> None:
        """Async wait — use from coroutines so the event loop stays free."""
        delay = self._reserve()
        if delay > 0:
            await asyncio.sleep(delay)


# ---------------------------------------------------------------------------
# Native metadata provider
# ---------------------------------------------------------------------------


class NativeMetadataProvider(AbstractMetadataProvider):
    name = "native"

    def __init__(self) -> None:
        self._tvmaze_limiter = RateLimiter(max_requests=20, period=10.0)
        config = MiraMediaConfig().metadata
        self._desired_languages = config.desired_languages
        self._tvmaze_enabled = config.native.tvmaze.enabled
        self._cinemeta_enabled = config.native.cinemeta.enabled

    # ------------------------------------------------------------------
    # Internal HTTP helpers
    # ------------------------------------------------------------------

    def _tvmaze_get(self, path: str, params: dict | None = None) -> dict | list:
        """Rate-limited GET against TVmaze API."""
        if not self._tvmaze_enabled:
            msg = "TVmaze provider is disabled"
            raise RuntimeError(msg)
        self._tvmaze_limiter.wait()
        url = f"{TVMAZE_BASE}{path}"
        response = _ipv4_session().get(url, params=params, timeout=60)
        response.raise_for_status()
        return response.json()

    def _cinemeta_get(self, path: str) -> dict:
        """GET against Cinemeta (Stremio) API.

        Uses an IPv4-only session because container bridge networks
        often lack an IPv6 default route while DNS returns AAAA records
        first — without forcing AF_INET the request fails with ENETUNREACH
        before falling back to A.
        """
        if not self._cinemeta_enabled:
            msg = "Cinemeta provider is disabled"
            raise RuntimeError(msg)
        url = f"{CINEMETA_BASE}{path}"
        response = _ipv4_session().get(url, timeout=60)
        response.raise_for_status()
        return response.json()

    def _resolve_imdb_to_tvmaze(self, imdb_id: str) -> dict | None:
        """Resolve an IMDb ID to a TVMaze show dict via the lookup endpoint."""
        try:
            data = self._tvmaze_get("/lookup/shows", params={"imdb": imdb_id})
            if isinstance(data, dict) and data.get("id"):
                return data
        except Exception:
            log.debug("TVmaze lookup failed for IMDb ID %s", imdb_id)
        return None

    # ------------------------------------------------------------------
    # Language filtering helpers
    # ------------------------------------------------------------------

    def _tvmaze_language_matches(self, tvmaze_language: str | None) -> bool:
        """Check if a TVMaze language name matches the desired languages."""
        if not self._desired_languages or not tvmaze_language:
            return True
        tvmaze_allowed = {
            _ISO_TO_TVMAZE_LANG.get(code, code) for code in self._desired_languages
        }
        return tvmaze_language in tvmaze_allowed

    # ------------------------------------------------------------------
    # TV Shows
    # ------------------------------------------------------------------

    @override
    @cached("native.search_show", ttl=60 * 60)
    def search_show(
        self, query: str | None = None, skip: int = 0
    ) -> list[MetaDataProviderSearchResult]:
        try:
            if query is None:
                return self._trending_shows(skip=skip)
            return self._search_shows_cinemeta_first(query)
        except MetadataProviderUnavailableError:
            raise
        except Exception as exc:
            if is_provider_unreachable(exc):
                log.warning("Show search: provider unreachable: %s", exc)
                raise MetadataProviderUnavailableError from exc
            log.warning("Error searching shows", exc_info=True)
            return []

    def _search_shows_cinemeta_first(
        self, query: str
    ) -> list[MetaDataProviderSearchResult]:
        """Show search with Cinemeta as the primary source and TVMaze as the
        fallback — fall through on no-results or an unreachable primary.

        A reachable-but-empty Cinemeta is a legitimate "no matches": if the
        TVMaze fallback then turns out to be unreachable we return [] rather
        than a 503, so a single dead fallback doesn't mask a real empty result.
        """
        cinemeta_ok = False
        cinemeta_unreachable: BaseException | None = None
        if self._cinemeta_enabled:
            try:
                hits = self._cinemeta_search_shows(query)
                cinemeta_ok = True
                if hits:
                    return hits
            except Exception as exc:
                if is_provider_unreachable(exc):
                    cinemeta_unreachable = exc
                else:
                    log.warning("Cinemeta show search failed", exc_info=True)

        if self._tvmaze_enabled:
            try:
                return self._search_shows(query)
            except Exception as exc:
                if is_provider_unreachable(exc):
                    if cinemeta_ok:
                        return []
                    raise MetadataProviderUnavailableError from exc
                log.warning("TVmaze show search failed", exc_info=True)
                return []

        if cinemeta_unreachable is not None:
            raise MetadataProviderUnavailableError from cinemeta_unreachable
        return []

    def _cinemeta_search_shows(self, query: str) -> list[MetaDataProviderSearchResult]:
        """Search shows via Cinemeta's series catalog, returning IMDb IDs."""
        data = self._cinemeta_get(
            f"/catalog/series/top/search={requests.utils.quote(query)}.json"
        )
        metas = data.get("metas") or []
        return self._parse_cinemeta_search_results(metas)

    def _search_shows(self, query: str) -> list[MetaDataProviderSearchResult]:
        """Search shows via TVMaze, returning IMDb IDs."""
        data = self._tvmaze_get("/search/shows", params={"q": query})
        results: list[MetaDataProviderSearchResult] = []
        for entry in data:
            try:
                show = entry["show"]
                externals = show.get("externals") or {}
                imdb_id = externals.get("imdb")
                if not imdb_id:
                    continue

                poster_url = (show.get("image", {}) or {}).get("original")
                premiered = show.get("premiered") or ""
                year = int(premiered[:4]) if len(premiered) >= 4 else None

                results.append(
                    MetaDataProviderSearchResult(
                        poster_path=poster_url,
                        overview=_strip_html(show.get("summary")),
                        name=show["name"],
                        external_id=imdb_id,
                        imdb_id=imdb_id,
                        year=year,
                        metadata_provider=self.name,
                        added=False,
                        vote_average=(show.get("rating") or {}).get("average"),
                        original_language=show.get("language"),
                    )
                )
            except Exception:
                log.warning("Error processing TVmaze search result", exc_info=True)
        return results

    _EXCLUDED_GENRES: ClassVar[set[str]] = {
        "Reality-TV",
        "Talk-Show",
        "Game-Show",
        "News",
    }

    def _trending_shows(self, skip: int = 0) -> list[MetaDataProviderSearchResult]:
        """Trending shows from Cinemeta, filtered by desired languages."""
        path = (
            f"/catalog/series/top/skip={skip}.json"
            if skip
            else "/catalog/series/top.json"
        )
        data = self._cinemeta_get(path)
        metas = data.get("metas") or []

        filtered: list[dict] = []
        for meta in metas:
            genres = set(meta.get("genre") or meta.get("genres") or [])
            if genres & self._EXCLUDED_GENRES:
                continue
            filtered.append(meta)

        return self._parse_cinemeta_search_results(filtered)

    @override
    @cached("native.get_show_metadata_by_imdb")
    def get_show_metadata_by_imdb(
        self, imdb_id: str, language: str | None = None
    ) -> Show | None:
        """Look up a show by IMDb ID. Cinemeta first (same as native search),
        TVMaze as fallback."""
        show = self._build_show_from_cinemeta(imdb_id, language)
        if show is not None:
            return show
        try:
            show_data = self._resolve_imdb_to_tvmaze(imdb_id)
            if show_data:
                return self._build_show_from_tvmaze(show_data["id"], imdb_id, language)
        except Exception:
            log.warning(f"TVmaze IMDb lookup failed for {imdb_id}")
        return None

    @override
    @cached("native.get_movie_metadata_by_imdb")
    def get_movie_metadata_by_imdb(
        self, imdb_id: str, language: str | None = None
    ) -> Movie | None:
        """Look up a movie by IMDb ID via Cinemeta."""
        try:
            data = self._cinemeta_get(f"/meta/movie/{imdb_id}.json")
            meta = data.get("meta") or {}
            if not meta.get("name"):
                return None
            return Movie(
                external_id=imdb_id,
                name=meta.get("name", ""),
                overview=meta.get("description") or "",
                year=meta.get("year"),
                metadata_provider=self.name,
                imdb_id=imdb_id,
                runtime=_parse_cinemeta_runtime(meta.get("runtime")),
                genres=meta.get("genres")
                if isinstance(meta.get("genres"), list)
                else None,
                cast=_parse_cinemeta_cast(meta.get("cast")),
            )
        except Exception:
            log.warning(f"Cinemeta IMDb lookup failed for {imdb_id}")
        return None

    @override
    @cached("native.get_show_metadata")
    def get_show_metadata(self, show_id: str, language: str | None = None) -> Show:
        """Fetch full show metadata. show_id is an IMDb ID (e.g. 'tt0903747')
        or a legacy 'tvmaze:<id>' from pre-migration data."""
        if show_id.startswith("tvmaze:"):
            # Legacy TVMaze ID from pre-migration — resolve directly
            tvmaze_id = int(show_id.removeprefix("tvmaze:"))
            show_data = self._tvmaze_get(f"/shows/{tvmaze_id}")
            imdb_id = (show_data.get("externals") or {}).get("imdb") or show_id
            return self._build_show_from_tvmaze(tvmaze_id, imdb_id, language)

        # Standard path: IMDb ID. Same source order as native search — Cinemeta
        # first (one call, full season/episode tree incl. native Season 0
        # specials), TVMaze only as fallback.
        if not show_id.startswith("tt"):
            msg = f"Native provider requires an IMDb ID (tt...), got: {show_id}"
            raise ValueError(msg)
        show = self._build_show_from_cinemeta(show_id, language)
        if show is not None:
            return show
        tvmaze_data = self._resolve_imdb_to_tvmaze(show_id)
        if not tvmaze_data:
            msg = f"Could not resolve IMDb ID {show_id} via Cinemeta or TVMaze"
            raise ValueError(msg)
        return self._build_show_from_tvmaze(tvmaze_data["id"], show_id, language)

    def _build_show_from_cinemeta(
        self, imdb_id: str, _language: str | None = None
    ) -> Show | None:
        """Build a full Show (seasons + episodes) from Cinemeta, keyed by IMDb.

        Cinemeta's series meta returns the whole episode list in one call, with
        specials under a native Season 0 — no per-season fan-out and no
        synthetic-specials workaround. Returns ``None`` (not raise) on any
        failure so callers fall back to TVMaze.
        """
        if not self._cinemeta_enabled:
            return None
        try:
            data = self._cinemeta_get(f"/meta/series/{imdb_id}.json")
        except Exception:
            log.warning(f"Cinemeta series lookup failed for {imdb_id}", exc_info=True)
            return None

        meta = data.get("meta") or {}
        if not meta.get("name"):
            return None

        # Group episodes by season number.
        videos_by_season: dict[int, list[dict]] = {}
        for v in meta.get("videos") or []:
            season_no = v.get("season")
            episode_no = v.get("number", v.get("episode"))
            if season_no is None or episode_no is None:
                continue
            videos_by_season.setdefault(int(season_no), []).append(v)

        season_list: list[Season] = []
        for season_number in sorted(videos_by_season):
            episodes = []
            for v in sorted(
                videos_by_season[season_number],
                key=lambda x: x.get("number", x.get("episode")) or 0,
            ):
                episode_no = v.get("number", v.get("episode"))
                # Keep the full value (may be a UTC datetime): parse_iso_date
                # converts it to the local calendar date. Truncating to [:10] here
                # would freeze the UTC date and reintroduce the air-date off-by-one.
                released = v.get("released") or v.get("firstAired") or ""
                episodes.append(
                    Episode(
                        title=v.get("name") or f"Episode {episode_no}",
                        number=EpisodeNumber(int(episode_no)),
                        overview=_strip_html(v.get("overview") or v.get("description")),
                        air_date=miramedia.metadata.utils.parse_iso_date(released),
                        air_time=miramedia.metadata.utils.parse_iso_time(released),
                    )
                )
            season_list.append(
                Season(
                    number=SeasonNumber(season_number),
                    episodes=episodes,
                )
            )

        rating = meta.get("imdbRating")
        try:
            vote_average = float(rating) if rating not in (None, "") else None
        except (TypeError, ValueError):
            vote_average = None

        return Show(
            external_id=imdb_id,
            name=meta["name"],
            overview=_strip_html(meta.get("description")),
            year=_parse_cinemeta_year(meta.get("year") or meta.get("releaseInfo")),
            seasons=season_list,
            metadata_provider=self.name,
            ended=meta.get("status") == "Ended",
            original_language=None,
            imdb_id=imdb_id,
            vote_average=vote_average,
            content_rating=None,
            genres=meta.get("genres") if isinstance(meta.get("genres"), list) else None,
            cast=_parse_cinemeta_cast(meta.get("cast")),
        )

    def _build_show_from_tvmaze(
        self, tvmaze_id: int, imdb_id: str, _language: str | None = None
    ) -> Show:
        """Build a Show from TVMaze data, keyed by IMDb ID."""
        show_data = self._tvmaze_get(
            f"/shows/{tvmaze_id}", params={"embed[]": ["seasons", "cast"]}
        )

        embedded = show_data.get("_embedded", {})

        # --- Seasons & Episodes ---
        season_list: list[Season] = []
        for s in embedded.get("seasons", []):
            try:
                season_id = s["id"]
                eps_data = self._tvmaze_get(f"/seasons/{season_id}/episodes")
                episode_list = [
                    Episode(
                        title=ep.get("name") or f"Episode {ep['number']}",
                        number=EpisodeNumber(ep["number"]),
                        air_date=miramedia.metadata.utils.parse_iso_date(
                            ep.get("airdate")
                        ),
                        # airstamp is the UTC datetime; airdate is date-only.
                        air_time=miramedia.metadata.utils.parse_iso_time(
                            ep.get("airstamp")
                        ),
                    )
                    for ep in eps_data
                    if ep.get("number") is not None
                ]
                season_list.append(
                    Season(
                        number=SeasonNumber(s["number"]),
                        episodes=episode_list,
                    )
                )
            except Exception:
                log.warning(
                    f"Error processing TVmaze season {s.get('number')} for show {imdb_id}",
                    exc_info=True,
                )

        # --- Specials (synthetic Season 0) ---
        # TVMaze has no "Season 0": specials are episodes flagged
        # ``type: *_special`` with ``number: null``, nested under their airing
        # season (and thus dropped by the number-is-not-None filter above).
        # Collect them into a synthetic Season 0 so they surface as "Specials"
        # and can be matched by title at download/import time.
        try:
            all_episodes = self._tvmaze_get(
                f"/shows/{tvmaze_id}/episodes", params={"specials": "1"}
            )
            specials = [
                ep
                for ep in all_episodes
                if ep.get("number") is None
                and "special" in str(ep.get("type") or "").lower()
            ]
            specials.sort(key=lambda ep: ep.get("airdate") or "")
            if specials:
                special_episodes = [
                    Episode(
                        title=ep.get("name") or f"Special {i}",
                        number=EpisodeNumber(i),
                        air_date=miramedia.metadata.utils.parse_iso_date(
                            ep.get("airdate")
                        ),
                        air_time=miramedia.metadata.utils.parse_iso_time(
                            ep.get("airstamp")
                        ),
                    )
                    for i, ep in enumerate(specials, start=1)
                ]
                season_list.append(
                    Season(
                        number=SeasonNumber(0),
                        episodes=special_episodes,
                    )
                )
        except Exception:
            log.warning(
                f"Error processing TVmaze specials for show {imdb_id}",
                exc_info=True,
            )

        # --- Cast ---
        cast_names: list[str] = []
        for member in (embedded.get("cast") or [])[:10]:
            try:
                person_name = member.get("person", {}).get("name")
                if person_name:
                    cast_names.append(html.unescape(person_name))
            except Exception:  # noqa: S110 — best-effort cast parse, failure non-fatal
                pass

        # --- Other fields ---
        premiered = show_data.get("premiered") or ""
        year = int(premiered[:4]) if len(premiered) >= 4 else None
        # Prefer the passed imdb_id; fall back to externals
        externals = show_data.get("externals") or {}
        resolved_imdb = imdb_id or externals.get("imdb")
        genres = show_data.get("genres") or []
        ended = show_data.get("status") == "Ended"
        vote_average = (show_data.get("rating") or {}).get("average")

        return Show(
            external_id=resolved_imdb,
            name=show_data["name"],
            overview=_strip_html(show_data.get("summary")),
            year=year,
            seasons=season_list,
            metadata_provider=self.name,
            ended=ended,
            original_language=show_data.get("language"),
            imdb_id=resolved_imdb,
            vote_average=vote_average,
            content_rating=None,
            genres=genres,
            cast=cast_names,
        )

    @override
    def download_show_poster_image(self, show: Show) -> bool:
        try:
            imdb_id = show.imdb_id or show.external_id
            if not imdb_id.startswith("tt"):
                # Legacy tvmaze: prefix — resolve to get IMDb ID
                if imdb_id.startswith("tvmaze:"):
                    tvmaze_id = int(imdb_id.removeprefix("tvmaze:"))
                    tvmaze_data = self._tvmaze_get(f"/shows/{tvmaze_id}")
                    imdb_id = (tvmaze_data.get("externals") or {}).get("imdb") or ""
                if not imdb_id.startswith("tt"):
                    log.warning(
                        f"No IMDb ID for show {show.name}, cannot download poster"
                    )
                    return False

            # Use Cinemeta/metahub poster (same source as trending results)
            poster_url = f"https://images.metahub.space/poster/medium/{imdb_id}/img"
            if miramedia.metadata.utils.download_poster_image(
                storage_path=self.storage_path,
                poster_url=poster_url,
                uuid=show.id,
            ):
                log.info(f"Successfully downloaded poster image for show {show.name}")
                return True
            log.warning(f"Download for image of show {show.name} failed")
            return False  # noqa: TRY300 — must stay in try; preceding download call is what we guard
        except Exception:
            log.warning(f"Error downloading poster for show {show.name}", exc_info=True)
            return False

    # ------------------------------------------------------------------
    # Movies — Cinemeta (Stremio)
    # ------------------------------------------------------------------

    @override
    @cached("native.search_movie", ttl=60 * 60)
    def search_movie(
        self, query: str | None = None, skip: int = 0
    ) -> list[MetaDataProviderSearchResult]:
        try:
            if query is None:
                return self._trending_movies(skip=skip)
            return self._search_movies(query)
        except Exception as exc:
            if is_provider_unreachable(exc):
                log.warning("Movie search: provider unreachable: %s", exc)
                raise MetadataProviderUnavailableError from exc
            log.warning("Error searching movies via Cinemeta", exc_info=True)
            return []

    def _search_movies(self, query: str) -> list[MetaDataProviderSearchResult]:
        data = self._cinemeta_get(
            f"/catalog/movie/top/search={requests.utils.quote(query)}.json"
        )
        return self._parse_cinemeta_search_results(data.get("metas") or [])

    def enrich_movie_result(self, imdb_id: str) -> dict | None:
        """Fetch rich metadata for a single movie by IMDb ID."""
        try:
            data = self._cinemeta_get(f"/meta/movie/{imdb_id}.json")
            meta = data.get("meta") or {}
            return {
                "overview": meta.get("description"),
                "vote_average": meta.get("imdbRating") or None,
                "year": _parse_cinemeta_year(meta.get("year")),
            }
        except Exception:
            return None

    def _trending_movies(self, skip: int = 0) -> list[MetaDataProviderSearchResult]:
        """Popular movies from Cinemeta's top catalog."""
        path = (
            f"/catalog/movie/top/skip={skip}.json"
            if skip
            else "/catalog/movie/top.json"
        )
        data = self._cinemeta_get(path)
        metas = data.get("metas") or []
        return self._parse_cinemeta_search_results(metas)

    def _parse_cinemeta_search_results(
        self, metas: list[dict]
    ) -> list[MetaDataProviderSearchResult]:
        results: list[MetaDataProviderSearchResult] = []
        for meta in metas:
            try:
                imdb_id = meta.get("id") or meta.get("imdb_id") or ""
                if not imdb_id.startswith("tt"):
                    continue
                if not meta.get("poster"):
                    continue

                year_val = _parse_cinemeta_year(meta.get("year"))

                results.append(
                    MetaDataProviderSearchResult(
                        poster_path=meta.get("poster"),
                        overview=meta.get("description"),
                        name=meta.get("name") or "Unknown",
                        external_id=imdb_id,
                        imdb_id=imdb_id,
                        year=year_val,
                        metadata_provider=self.name,
                        added=False,
                        vote_average=meta.get("imdbRating") or None,
                        original_language=None,
                    )
                )
            except Exception:
                log.warning("Error processing Cinemeta search result", exc_info=True)
        return results

    @override
    @cached("native.get_movie_metadata")
    def get_movie_metadata(self, movie_id: str, language: str | None = None) -> Movie:
        """Fetch full movie metadata. movie_id is an IMDb ID (e.g. 'tt1234567')."""
        if not movie_id.startswith("tt"):
            msg = f"Native provider requires an IMDb ID (tt...), got: {movie_id}"
            raise ValueError(msg)
        data = self._cinemeta_get(f"/meta/movie/{movie_id}.json")
        meta = data.get("meta") or {}

        year_val = _parse_cinemeta_year(meta.get("year"))

        runtime_str = meta.get("runtime")
        runtime = _parse_cinemeta_runtime(str(runtime_str) if runtime_str else None)

        genres = meta.get("genres") or []
        cast = _parse_cinemeta_cast(meta.get("cast"))

        vote_average: float | None = None
        raw_rating = meta.get("imdbRating")
        if raw_rating is not None:
            try:
                vote_average = float(raw_rating)
            except (ValueError, TypeError):
                pass

        return Movie(
            external_id=movie_id,
            name=meta.get("name") or "Unknown",
            overview=meta.get("description") or "",
            year=year_val,
            release_date=miramedia.metadata.utils.parse_iso_date(meta.get("released")),
            metadata_provider=self.name,
            original_language=None,
            imdb_id=movie_id,
            vote_average=vote_average,
            content_rating=None,
            runtime=runtime,
            genres=genres,
            cast=cast,
        )

    @override
    def download_movie_poster_image(self, movie: Movie) -> bool:
        try:
            imdb_id = movie.imdb_id or movie.external_id
            if not imdb_id.startswith("tt"):
                log.warning(
                    f"No IMDb ID for movie {movie.name}, cannot download poster"
                )
                return False
            data = self._cinemeta_get(f"/meta/movie/{imdb_id}.json")
            meta = data.get("meta") or {}
            poster_url = meta.get("poster")
            if not poster_url:
                log.warning(f"No poster available for movie {movie.name}")
                return False
            if miramedia.metadata.utils.download_poster_image(
                storage_path=self.storage_path,
                poster_url=poster_url,
                uuid=movie.id,
            ):
                log.info(f"Successfully downloaded poster image for movie {movie.name}")
                return True
            log.warning(f"Download for image of movie {movie.name} failed")
            return False  # noqa: TRY300 — must stay in try; preceding download call is what we guard
        except Exception:
            log.warning(
                f"Error downloading poster for movie {movie.name}", exc_info=True
            )
            return False
