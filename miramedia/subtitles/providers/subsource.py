"""Subsource.net subtitle provider.

Uses the keyed JSON API at ``https://api.subsource.net/api/v1`` (as of 2026-05).
The pre-2025 anonymous POST endpoints (``/api/searchMovie`` etc.) were
removed; the current API requires registering at https://subsource.net and
supplying an ``X-API-Key`` header.

Flow:
  1. GET /api/v1/movies/search?searchType=text&q=<title>&year=...&type=...
  2. GET /api/v1/subtitles?movieId=<id>&language=<name>&...
  3. GET /api/v1/subtitles/<subtitleId>/download   -> zip bytes
"""

from __future__ import annotations

import logging
from difflib import SequenceMatcher
from typing import Any, ClassVar

from babelfish import Language
from curl_cffi.requests.exceptions import ConnectionError as CurlConnectionError
from curl_cffi.requests.exceptions import Timeout
from subliminal.exceptions import ConfigurationError
from subliminal.providers import Provider
from subliminal.subtitle import Subtitle, fix_line_ending
from subliminal.video import Episode, Movie, Video

from miramedia.cloudflare import CloudflareSession
from miramedia.subtitles.bounded_decode import decode_bounded_subtitle_content

log = logging.getLogger(__name__)

API_BASE = "https://api.subsource.net/api/v1"

# Display name (lowercase) -> (alpha3, country)
_LANGUAGE_TABLE: list[tuple[str, str, str | None]] = [
    ("albanian", "sqi", None),
    ("arabic", "ara", None),
    ("bengali", "ben", None),
    ("brazilian portuguese", "por", "BR"),
    ("portuguese (brazil)", "por", "BR"),
    ("bulgarian", "bul", None),
    ("chinese bg code", "zho", None),
    ("chinese (simplified)", "zho", "CN"),
    ("chinese (traditional)", "zho", "TW"),
    ("croatian", "hrv", None),
    ("czech", "ces", None),
    ("danish", "dan", None),
    ("dutch", "nld", None),
    ("english", "eng", None),
    ("estonian", "est", None),
    ("farsi/persian", "fas", None),
    ("persian", "fas", None),
    ("finnish", "fin", None),
    ("french", "fra", None),
    ("german", "deu", None),
    ("greek", "ell", None),
    ("hebrew", "heb", None),
    ("hindi", "hin", None),
    ("hungarian", "hun", None),
    ("icelandic", "isl", None),
    ("indonesian", "ind", None),
    ("italian", "ita", None),
    ("japanese", "jpn", None),
    ("korean", "kor", None),
    ("latvian", "lav", None),
    ("lithuanian", "lit", None),
    ("macedonian", "mkd", None),
    ("malay", "msa", None),
    ("norwegian", "nor", None),
    ("polish", "pol", None),
    ("portuguese", "por", None),
    ("romanian", "ron", None),
    ("russian", "rus", None),
    ("serbian", "srp", None),
    ("slovak", "slk", None),
    ("slovenian", "slv", None),
    ("spanish", "spa", None),
    ("swedish", "swe", None),
    ("tamil", "tam", None),
    ("thai", "tha", None),
    ("turkish", "tur", None),
    ("ukrainian", "ukr", None),
    ("urdu", "urd", None),
    ("vietnamese", "vie", None),
]

_NAME_TO_LANG: dict[str, Language] = {}
_LANG_TO_NAME: dict[Language, str] = {}
for _name, _alpha3, _country in _LANGUAGE_TABLE:
    _lang = Language(_alpha3, _country) if _country else Language(_alpha3)
    _NAME_TO_LANG[_name] = _lang
    # First occurrence wins for the reverse map — keeps canonical names like
    # "english" over later synonyms.
    _LANG_TO_NAME.setdefault(_lang, _name)


def _name_to_language(name: str) -> Language | None:
    return _NAME_TO_LANG.get((name or "").strip().lower())


def _language_to_name(lang: Language) -> str | None:
    return _LANG_TO_NAME.get(lang)


class SubsourceSubtitle(Subtitle):
    provider_name = "subsource"

    def __init__(
        self,
        language: Language,
        subtitle_id: int,
        *,
        release_info: list[str],
        page_link: str,
        hearing_impaired: bool = False,
        season: int | None = None,
        episode: int | None = None,
    ) -> None:
        super().__init__(
            language,
            str(subtitle_id),
            hearing_impaired=hearing_impaired,
            page_link=page_link,
        )
        self.subsource_id = subtitle_id
        self.release_info = release_info
        self.season = season
        self.episode = episode

    def get_matches(self, video: Video) -> set[str]:
        matches: set[str] = set()
        if isinstance(video, Episode):
            matches.add("series")
            if self.season is not None and self.season == video.season:
                matches.add("season")
            if self.episode is not None and self.episode == video.episode:
                matches.add("episode")
        elif isinstance(video, Movie):
            matches.add("title")
        # Release-info matches let subliminal score resolution / source / group
        if self.release_info:
            matches.add("release_group")
        return matches


class SubsourceProvider(Provider):
    """Subsource.net via the keyed v1 JSON API."""

    languages: ClassVar[set[Language]] = set(_NAME_TO_LANG.values())
    video_types: ClassVar[tuple[type[Video], ...]] = (Episode, Movie)
    subtitle_class: ClassVar[type[SubsourceSubtitle]] = SubsourceSubtitle

    def __init__(self, api_key: str = "") -> None:
        if not api_key:
            msg = (
                "Subsource requires an api_key. Register at https://subsource.net "
                "and copy the API key from your profile."
            )
            raise ConfigurationError(msg)
        self.api_key = api_key

    def initialize(self) -> None:
        self.session = CloudflareSession()
        self.session.headers.update(
            {
                "Accept": "application/json",
                "Accept-Language": "en-US,en;q=0.9",
                "X-API-Key": self.api_key,
                "Origin": "https://subsource.net",
                "Referer": "https://subsource.net/",
            }
        )

    def terminate(self) -> None:
        self.session.close()

    def _get_json(self, path: str, params: dict[str, Any] | None = None) -> dict | None:
        url = f"{API_BASE}{path}"
        try:
            response = self.session.get(url, params=params, timeout=20)
        except (CurlConnectionError, Timeout) as e:
            log.warning("Subsource unreachable %s: %s", path, e)
            return None
        except Exception:
            log.exception("Subsource request failed %s", path)
            return None

        if response.status_code == 401:
            log.error("Subsource: invalid or missing API key (401)")
            return None
        if response.status_code == 403:
            log.warning(
                "Subsource blocked (403) on %s — Cloudflare or auth issue", path
            )
            return None
        if response.status_code == 404:
            return None
        if response.status_code == 429:
            log.warning("Subsource rate limited on %s", path)
            return None
        if response.status_code != 200:
            log.warning("Subsource %s returned %d", path, response.status_code)
            return None

        try:
            return response.json()
        except Exception:
            log.warning("Subsource %s returned non-JSON", path)
            return None

    def _search_titles(
        self,
        title: str,
        *,
        year: int | None,
        want_tv: bool,
        season: int | None,
    ) -> list[dict]:
        params: dict[str, Any] = {
            "searchType": "text",
            "q": title,
            "type": "tv" if want_tv else "movie",
        }
        if year:
            params["year"] = year
        if want_tv and season is not None:
            params["season"] = season
        data = self._get_json("/movies/search", params)
        if not data or not data.get("success"):
            return []
        return data.get("data", []) or []

    def _pick_title(
        self,
        candidates: list[dict],
        title: str,
        year: int | None,
        *,
        want_tv: bool,
    ) -> dict | None:
        normalized = title.lower().strip()
        best: tuple[float, dict] | None = None
        for item in candidates:
            name = (item.get("title") or "").lower().strip()
            if not name:
                continue
            item_type = (item.get("type") or "").lower()
            if item_type:
                if want_tv and item_type not in {"tv", "tvseries", "series"}:
                    continue
                if not want_tv and item_type not in {"movie", "film"}:
                    continue
            score = SequenceMatcher(None, name, normalized).ratio()
            if year and item.get("releaseYear"):
                try:
                    if int(item["releaseYear"]) == int(year):
                        score += 0.1
                except (TypeError, ValueError):
                    pass
            if score < 0.7:
                continue
            if best is None or score > best[0]:
                best = (score, item)
        return best[1] if best else None

    def _list_subs(
        self,
        movie_id: int,
        language_name: str,
        *,
        page_limit: int = 200,
    ) -> list[dict]:
        params: dict[str, Any] = {
            "movieId": movie_id,
            "language": language_name,
            "limit": page_limit,
            "sort": "newest",
        }
        data = self._get_json("/subtitles", params)
        if not data or not data.get("success"):
            return []
        return data.get("data", []) or []

    def query(self, video: Video, languages: set[Language]) -> list[SubsourceSubtitle]:
        if isinstance(video, Episode):
            title = video.series
            year = video.year
            want_tv = True
            season = video.season
            episode = video.episode
        elif isinstance(video, Movie):
            title = video.title
            year = video.year
            want_tv = False
            season = None
            episode = None
        else:
            return []

        log.debug("Subsource: searching '%s' (tv=%s, year=%s)", title, want_tv, year)
        candidates = self._search_titles(
            title, year=year, want_tv=want_tv, season=season
        )
        if not candidates:
            log.info("Subsource: no title matches for '%s'", title)
            return []

        picked = self._pick_title(candidates, title, year, want_tv=want_tv)
        if not picked:
            log.info("Subsource: no acceptable title match for '%s'", title)
            return []

        movie_id = picked.get("movieId") or picked.get("id")
        if not movie_id:
            log.warning("Subsource: title match missing movieId: %s", picked)
            return []

        results: list[SubsourceSubtitle] = []
        for lang in languages:
            lang_name = _language_to_name(lang)
            if not lang_name:
                continue
            subs_raw = self._list_subs(int(movie_id), lang_name)
            if not subs_raw:
                continue

            for item in subs_raw:
                sub_id = item.get("subtitleId") or item.get("id")
                if not sub_id:
                    continue

                release_info_raw = item.get("releaseInfo") or []
                if isinstance(release_info_raw, str):
                    release_info = [release_info_raw]
                else:
                    release_info = list(release_info_raw)

                # TV: filter to releases containing the SxxExx tag
                if want_tv and season is not None and episode is not None:
                    tag = f"S{season:02d}E{episode:02d}".lower()
                    if not any(tag in (r or "").lower() for r in release_info):
                        continue

                results.append(
                    SubsourceSubtitle(
                        language=lang,
                        subtitle_id=int(sub_id),
                        release_info=release_info,
                        page_link=f"https://subsource.net/subtitle/{sub_id}",
                        hearing_impaired=bool(item.get("hearingImpaired")),
                        season=season,
                        episode=episode,
                    )
                )

        log.info("Found %d Subsource subtitles for '%s'", len(results), title)
        return results

    def list_subtitles(
        self, video: Video, languages: set[Language]
    ) -> list[SubsourceSubtitle]:
        return self.query(video, languages)

    def download_subtitle(self, subtitle: SubsourceSubtitle) -> None:
        url = f"{API_BASE}/subtitles/{subtitle.subsource_id}/download"
        try:
            response = self.session.get(url, timeout=30, allow_redirects=True)
        except (CurlConnectionError, Timeout) as e:
            log.warning(
                "Subsource download unreachable for %s: %s", subtitle.subsource_id, e
            )
            return
        except Exception:
            log.exception("Subsource download failed for %s", subtitle.subsource_id)
            return

        if response.status_code != 200:
            log.warning(
                "Subsource download returned %d for id=%s",
                response.status_code,
                subtitle.subsource_id,
            )
            return

        decoded = decode_bounded_subtitle_content(response.content)
        if decoded.content is not None:
            subtitle.content = fix_line_ending(decoded.content)
            return
        if decoded.kind == "zip":
            if decoded.zip_failure == "bad":
                log.warning("Subsource: corrupted zip for id=%s", subtitle.subsource_id)
            elif decoded.zip_failure == "no_member":
                log.warning(
                    "Subsource zip had no subtitle file for id=%s",
                    subtitle.subsource_id,
                )
            else:
                log.warning(
                    "Subsource: rejected unsafe zip for id=%s", subtitle.subsource_id
                )
