"""SubDL provider for subtitle searching.

Adapted from Bazarr's subdl provider to work with standard subliminal.
Uses the SubDL API (api.subdl.com) — requires a free API key.
Supports movies and TV episodes with excellent coverage.
"""

from __future__ import annotations

import io
import logging
import zipfile
from typing import ClassVar

from babelfish import Language
from requests import Session
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import Timeout
from subliminal.exceptions import ConfigurationError
from subliminal.providers import Provider
from subliminal.subtitle import Subtitle, fix_line_ending
from subliminal.video import Episode, Movie, Video

log = logging.getLogger(__name__)

API_URL = "https://api.subdl.com/api/v1/subtitles"
DOWNLOAD_BASE = "https://dl.subdl.com"

# SubDL language code -> babelfish alpha3 mapping
# SubDL uses its own language names in responses
_SUBDL_TO_LANG: dict[str, Language] = {}
_LANG_TO_SUBDL: dict[str, str] = {}

_LANGUAGE_MAP = [
    ("english", "eng"),
    ("farsi_persian", "fas"),
    ("arabic", "ara"),
    ("bengali", "ben"),
    ("brazilian_portuguese", "por", "BR"),
    ("bulgarian", "bul"),
    ("burmese", "mya"),
    ("chinese_bg_code", "zho"),
    ("croatian", "hrv"),
    ("czech", "ces"),
    ("danish", "dan"),
    ("dutch", "nld"),
    ("estonian", "est"),
    ("finnish", "fin"),
    ("french", "fra"),
    ("german", "deu"),
    ("greek", "ell"),
    ("hebrew", "heb"),
    ("hungarian", "hun"),
    ("indonesian", "ind"),
    ("italian", "ita"),
    ("japanese", "jpn"),
    ("korean", "kor"),
    ("latvian", "lav"),
    ("lithuanian", "lit"),
    ("malay", "msa"),
    ("norwegian", "nor"),
    ("polish", "pol"),
    ("portuguese", "por"),
    ("romanian", "ron"),
    ("russian", "rus"),
    ("serbian", "srp"),
    ("slovak", "slk"),
    ("slovenian", "slv"),
    ("spanish", "spa"),
    ("swedish", "swe"),
    ("thai", "tha"),
    ("turkish", "tur"),
    ("ukrainian", "ukr"),
    ("urdu", "urd"),
    ("vietnamese", "vie"),
]

for _entry in _LANGUAGE_MAP:
    _subdl_name = _entry[0]
    _alpha3 = _entry[1]
    _country = _entry[2] if len(_entry) > 2 else None
    _lang = Language(_alpha3, _country) if _country else Language(_alpha3)
    _SUBDL_TO_LANG[_subdl_name] = _lang
    _key = f"{_alpha3}-{_country}" if _country else _alpha3
    _LANG_TO_SUBDL[_key] = _subdl_name


def _lang_to_subdl(lang: Language) -> str | None:
    """Convert a babelfish Language to a SubDL language name."""
    if lang.country:
        key = f"{lang.alpha3}-{lang.country.alpha2}"
        if key in _LANG_TO_SUBDL:
            return _LANG_TO_SUBDL[key]
    return _LANG_TO_SUBDL.get(lang.alpha3)


def _subdl_to_lang(name: str) -> Language | None:
    """Convert a SubDL language name to a babelfish Language."""
    return _SUBDL_TO_LANG.get(name.lower().replace(" ", "_"))


class SubDLSubtitle(Subtitle):
    provider_name = "subdl"

    def __init__(
        self,
        language: Language,
        subtitle_id: str,
        *,
        page_link: str,
        download_link: str,
        release_names: list[str],
        hearing_impaired: bool = False,
        season: int | None = None,
        episode: int | None = None,
    ) -> None:
        super().__init__(
            language,
            subtitle_id,
            hearing_impaired=hearing_impaired,
            page_link=page_link,
        )
        self.download_link = download_link
        self.release_names = release_names
        self.season = season
        self.episode = episode

    def get_matches(self, video: Video) -> set[str]:
        matches = set()
        if isinstance(video, Episode):
            matches.add("series")
            if video.season and video.season == self.season:
                matches.add("season")
            if video.episode and video.episode == self.episode:
                matches.add("episode")
            if video.series_imdb_id:
                matches.add("series_imdb_id")
        elif isinstance(video, Movie):
            matches.add("title")
            if video.imdb_id:
                matches.add("imdb_id")
        return matches


class SubDLProvider(Provider):
    """SubDL provider — API-based subtitle search with broad coverage."""

    languages: ClassVar[set[Language]] = set(_SUBDL_TO_LANG.values())
    video_types: ClassVar[tuple[type[Video], ...]] = (Episode, Movie)
    subtitle_class: ClassVar[type[SubDLSubtitle]] = SubDLSubtitle

    def __init__(self, api_key: str = "") -> None:
        if not api_key:
            msg = "SubDL requires an api_key"
            raise ConfigurationError(msg)
        self.api_key = api_key

    def initialize(self) -> None:
        self.session = Session()
        self.session.trust_env = False
        self.session.headers.update({"User-Agent": "Subliminal/2.6"})

    def terminate(self) -> None:
        self.session.close()

    def query(self, languages: set[Language], video: Video) -> list[SubDLSubtitle]:
        if isinstance(video, Episode):
            title = video.series
            imdb_id = getattr(video, "series_imdb_id", None)
            media_type = "tv"
        else:
            title = video.title
            imdb_id = video.imdb_id
            media_type = "movie"

        # Convert languages to SubDL codes
        lang_codes = set()
        for lang in languages:
            code = _lang_to_subdl(lang)
            if code:
                lang_codes.add(code)
        if not lang_codes:
            return []

        params: dict[str, str | int] = {
            "api_key": self.api_key,
            "languages": ",".join(sorted(lang_codes)),
            "subs_per_page": 30,
            "type": media_type,
        }

        if imdb_id:
            params["imdb_id"] = imdb_id
        else:
            params["film_name"] = title

        if isinstance(video, Episode):
            params["season_number"] = video.season
            params["episode_number"] = video.episode

        log.debug("Searching SubDL for %s (%s)", title, media_type)

        try:
            response = self.session.get(API_URL, params=params, timeout=30)
        except (RequestsConnectionError, Timeout) as e:
            log.warning("SubDL unreachable: %s", e)
            return []
        except Exception:
            log.exception("SubDL API request failed")
            return []

        if response.status_code == 429:
            log.warning("SubDL rate limited")
            return []
        if response.status_code == 403:
            log.error("SubDL API key is invalid")
            return []
        if response.status_code != 200:
            log.warning("SubDL returned status %d", response.status_code)
            return []

        try:
            data = response.json()
        except Exception:
            log.exception("SubDL returned non-JSON response")
            return []

        if not data.get("status") and not data.get("success"):
            error = data.get("error", "")
            if "can't find" in error.lower():
                log.debug("SubDL: %s not found", title)
                return []
            if error:
                log.warning("SubDL error: %s", error)
            return []

        subtitles: list[SubDLSubtitle] = []
        for item in data.get("subtitles", []):
            # Skip season packs
            if isinstance(video, Episode):
                if item.get("episode_from") != item.get("episode_end"):
                    continue

            lang_name = item.get("language", "")
            lang = _subdl_to_lang(lang_name)
            if lang is None or lang not in languages:
                continue

            hi = item.get("hi", False)
            # Additional HI detection from filename
            if not hi and "_hi_" in item.get("name", "").lower():
                hi = True

            sub = SubDLSubtitle(
                language=lang,
                subtitle_id=item.get("name", ""),
                page_link=f"https://subdl.com{item.get('subtitlePage', '')}",
                download_link=item.get("url", ""),
                release_names=item.get("releases", []),
                hearing_impaired=hi,
                season=item.get("season"),
                episode=item.get("episode"),
            )
            subtitles.append(sub)

        log.info("Found %d SubDL subtitles for %s", len(subtitles), title)
        return subtitles

    def list_subtitles(
        self, video: Video, languages: set[Language]
    ) -> list[SubDLSubtitle]:
        return self.query(languages, video)

    def download_subtitle(self, subtitle: SubDLSubtitle) -> None:
        if not subtitle.download_link:
            log.error("No download link for SubDL subtitle %s", subtitle.subtitle_id)
            return

        download_url = f"{DOWNLOAD_BASE}{subtitle.download_link}"
        log.debug("Downloading SubDL subtitle from %s", download_url)

        try:
            response = self.session.get(download_url, timeout=30)
        except (RequestsConnectionError, Timeout) as e:
            log.warning("SubDL download unreachable: %s", e)
            return
        except Exception:
            log.exception("Failed to download SubDL subtitle")
            return

        if response.status_code == 429:
            log.warning("SubDL download rate limited")
            return
        if response.status_code != 200:
            log.warning("SubDL download returned status %d", response.status_code)
            return

        archive_stream = io.BytesIO(response.content)
        if zipfile.is_zipfile(archive_stream):
            with zipfile.ZipFile(archive_stream) as zf:
                for name in zf.namelist():
                    if name.lower().endswith((".srt", ".sub", ".ass", ".ssa", ".vtt")):
                        subtitle.content = fix_line_ending(zf.read(name))
                        return
        else:
            # Try as raw subtitle content
            subtitle.content = fix_line_ending(response.content)
