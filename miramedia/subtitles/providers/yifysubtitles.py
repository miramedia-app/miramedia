"""YIFY Subtitles provider for movie subtitles.

Adapted from Bazarr's yifysubtitles provider to work with standard subliminal.
Searches yifysubtitles.ch by IMDB ID — complements the YTS torrent indexer.
"""

from __future__ import annotations

import io
import logging
import re
import zipfile
from typing import TYPE_CHECKING, ClassVar
from urllib.parse import urljoin

from babelfish import Language
from requests import Session
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import Timeout
from subliminal.providers import Provider
from subliminal.subtitle import Subtitle, fix_line_ending
from subliminal.video import Movie, Video

if TYPE_CHECKING:
    from bs4 import Tag

log = logging.getLogger(__name__)

SERVER_URLS = ["https://yifysubtitles.ch"]
SERVER_URL = SERVER_URLS[0]

# (display name, alpha3, country)
YIFY_LANGUAGES = [
    ("Albanian", "sqi", None),
    ("Arabic", "ara", None),
    ("Bengali", "ben", None),
    ("Brazilian Portuguese", "por", "BR"),
    ("Bulgarian", "bul", None),
    ("Chinese", "zho", None),
    ("Croatian", "hrv", None),
    ("Czech", "ces", None),
    ("Danish", "dan", None),
    ("Dutch", "nld", None),
    ("English", "eng", None),
    ("Farsi/Persian", "fas", None),
    ("Finnish", "fin", None),
    ("French", "fra", None),
    ("German", "deu", None),
    ("Greek", "ell", None),
    ("Hebrew", "heb", None),
    ("Hungarian", "hun", None),
    ("Indonesian", "ind", None),
    ("Italian", "ita", None),
    ("Japanese", "jpn", None),
    ("Korean", "kor", None),
    ("Lithuanian", "lit", None),
    ("Macedonian", "mkd", None),
    ("Malay", "msa", None),
    ("Norwegian", "nor", None),
    ("Polish", "pol", None),
    ("Portuguese", "por", None),
    ("Romanian", "ron", None),
    ("Russian", "rus", None),
    ("Serbian", "srp", None),
    ("Slovenian", "slv", None),
    ("Spanish", "spa", None),
    ("Swedish", "swe", None),
    ("Thai", "tha", None),
    ("Turkish", "tur", None),
    ("Urdu", "urd", None),
    ("Vietnamese", "vie", None),
]

# Map display name (lowercased) -> Language. Page renders names in lowercase.
_LANG_MAP: dict[str, Language] = {}
for _name, _alpha3, _country in YIFY_LANGUAGES:
    _LANG_MAP[_name.lower()] = (
        Language(_alpha3, _country) if _country else Language(_alpha3)
    )


class YifySubtitle(Subtitle):
    provider_name = "yifysubtitles"

    def __init__(
        self,
        language: Language,
        subtitle_id: str,
        *,
        page_link: str,
        release: str,
        rating: int,
        hearing_impaired: bool = False,
    ) -> None:
        super().__init__(
            language,
            subtitle_id,
            hearing_impaired=hearing_impaired,
            page_link=page_link,
        )
        self.release = release
        self.rating = rating

    def get_matches(self, video: Video) -> set[str]:
        matches = set()
        if isinstance(video, Movie) and video.imdb_id:
            matches.add("imdb_id")
        return matches


class YifySubtitlesProvider(Provider):
    """YIFY Subtitles provider — movie subtitles searched by IMDB ID."""

    languages: ClassVar[set[Language]] = set(_LANG_MAP.values())
    video_types: ClassVar[tuple[type[Video], ...]] = (Movie,)
    subtitle_class: ClassVar[type[YifySubtitle]] = YifySubtitle

    def initialize(self) -> None:
        self.session = Session()
        self.session.trust_env = False
        self.session.headers.update(
            {
                "User-Agent": "Subliminal/2.6",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
            }
        )

    def terminate(self) -> None:
        self.session.close()

    def query(self, languages: set[Language], imdb_id: str) -> list[YifySubtitle]:
        log.debug("Searching YIFY subtitles for %s", imdb_id)

        for base_url in SERVER_URLS:
            try:
                response = self.session.get(
                    urljoin(base_url, f"/movie-imdb/{imdb_id}"),
                    allow_redirects=False,
                    timeout=10,
                    headers={"Referer": base_url},
                )
            except (RequestsConnectionError, Timeout) as e:
                log.warning(
                    "YIFY mirror %s unreachable for %s: %s", base_url, imdb_id, e
                )
                continue
            except Exception:
                log.exception("YIFY request failed for %s on %s", imdb_id, base_url)
                continue

            if response.status_code == 404:
                log.debug("No YIFY subtitles page for %s on %s", imdb_id, base_url)
                continue
            if response.status_code != 200:
                log.warning(
                    "YIFY mirror %s returned status %d for %s",
                    base_url,
                    response.status_code,
                    imdb_id,
                )
                continue

            results = self._parse_page(response.content, languages, base_url)
            if results:
                return results

        return []

    def _parse_page(
        self, content: bytes, languages: set[Language], base_url: str
    ) -> list[YifySubtitle]:
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            log.exception("beautifulsoup4 is required for yifysubtitles provider")
            return []

        soup = BeautifulSoup(content, "html.parser")
        table = soup.find("table", {"class": "other-subs"})
        if not table:
            return []
        tbody = table.find("tbody")
        if not tbody:
            return []

        subtitles: list[YifySubtitle] = []
        for row in tbody.find_all("tr"):
            try:
                sub = self._parse_row(row, languages, base_url)
                if sub:
                    subtitles.append(sub)
            except Exception:  # noqa: S112 — best-effort, non-fatal
                continue

        subtitles.sort(key=lambda s: s.rating, reverse=True)
        log.info("Found %d YIFY subtitles on %s", len(subtitles), base_url)
        return subtitles

    def _parse_row(
        self, row: Tag, languages: set[Language], base_url: str
    ) -> YifySubtitle | None:
        cells = row.find_all("td")
        if len(cells) < 5:
            return None

        rating = int(cells[0].text.strip())
        lang_name = cells[1].text.strip()
        release = re.sub(r"^\nsubtitle\s*", "", cells[2].text).strip()
        link_tag = cells[2].find("a")
        page_link = urljoin(base_url, link_tag["href"]) if link_tag else ""
        hi = bool(cells[3].find("span", {"class": "hi-subtitle"}))

        lang = _LANG_MAP.get(lang_name.lower())
        if lang is None or lang not in languages:
            return None

        return YifySubtitle(
            language=lang,
            subtitle_id=page_link,
            page_link=page_link,
            release=release,
            rating=rating,
            hearing_impaired=hi,
        )

    def list_subtitles(
        self, video: Video, languages: set[Language]
    ) -> list[YifySubtitle]:
        if isinstance(video, Movie) and video.imdb_id:
            return self.query(languages, video.imdb_id)
        return []

    def download_subtitle(self, subtitle: YifySubtitle) -> None:
        log.debug("Downloading YIFY subtitle %s", subtitle.page_link)

        try:
            response = self.session.get(
                subtitle.page_link,
                timeout=10,
                headers={"Referer": SERVER_URL},
            )
            response.raise_for_status()
        except (RequestsConnectionError, Timeout) as e:
            log.warning("YIFY subtitle page unreachable %s: %s", subtitle.page_link, e)
            return
        except Exception:
            log.exception("Failed to load YIFY subtitle page %s", subtitle.page_link)
            return

        try:
            from bs4 import BeautifulSoup
        except ImportError:
            return

        soup = BeautifulSoup(response.content, "html.parser")
        download_btn = soup.find("a", {"class": "download-subtitle"})
        if not download_btn:
            log.error("No download link on YIFY page %s", subtitle.page_link)
            return

        try:
            dl_response = self.session.get(
                urljoin(subtitle.page_link, download_btn["href"]),
                timeout=30,
                headers={"Referer": subtitle.page_link},
            )
            dl_response.raise_for_status()
        except (RequestsConnectionError, Timeout) as e:
            log.warning("YIFY subtitle download unreachable: %s", e)
            return
        except Exception:
            log.exception("Failed to download YIFY subtitle file")
            return

        archive_stream = io.BytesIO(dl_response.content)
        if zipfile.is_zipfile(archive_stream):
            with zipfile.ZipFile(archive_stream) as zf:
                for name in zf.namelist():
                    if name.lower().endswith((".srt", ".sub", ".ass", ".ssa", ".vtt")):
                        subtitle.content = fix_line_ending(zf.read(name))
                        return
        else:
            # Maybe raw subtitle content
            subtitle.content = fix_line_ending(dl_response.content)
