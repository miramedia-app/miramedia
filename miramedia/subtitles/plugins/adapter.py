"""Adapter that exposes Provider-Hub catalog plugins as subliminal providers.

The plugins under ``vendored/`` come from the MIT-licensed
``LavX/bazarr-provider-catalog`` and implement a small "Provider Hub v1"
contract — a class with::

    search(video: dict, languages: list[dict], config: dict) -> list[dict]
    download(provider_payload: dict, language: str, config: dict) -> dict

They are pure-stdlib and carry zero imports from subliminal or Bazarr, so we
run them in-process (no worker subprocess / no live catalog fetch — that's
Bazarr-Hub infra we don't need) and translate the contract to subliminal's
``Provider`` / ``Subtitle`` interface here.

One generic :class:`PluginProvider` / :class:`PluginSubtitle` pair does the
mapping; :func:`build_plugin_provider` stamps out a concrete provider class
per plugin (subliminal identifies providers by class, registered by name).
"""

from __future__ import annotations

import base64
import logging
from typing import Any, ClassVar

from babelfish import Language
from subliminal.providers import Provider
from subliminal.subtitle import Subtitle, fix_line_ending
from subliminal.video import Episode, Movie, Video

# Package the per-provider loggers hang off. Each provider logs under
# ``miramedia.subtitles.subliminal.plugins.<provider_id>`` — the same
# ``miramedia.subtitles.subliminal.*`` umbrella the built-in subliminal
# providers are rewritten into (see miramedia.logging), so all subtitle
# providers group together. Not derived from ``__name__`` (that would be
# ``miramedia.subtitles.plugins``).
_LOGGER_PACKAGE = "miramedia.subtitles.subliminal.plugins"


def _video_label(video: Video) -> str:
    """Short human label for log lines, matching the other providers' style."""
    if isinstance(video, Episode):
        return f"{video.series} S{(video.season or 0):02d}E{(video.episode or 0):02d}"
    year = f" ({video.year})" if video.year else ""
    return f"{video.title}{year}"


def _language_set(alpha3_codes: list[str]) -> set[Language]:
    """Build the babelfish language set for a plugin from its ISO 639-3 list.

    Codes babelfish can't parse (e.g. region tags like ``pt-BR`` or codes
    outside ISO 639-3) are skipped — the plugin still filters by its own
    language table at search time, this set only gates which requests reach it.
    """
    langs: set[Language] = set()
    for code in alpha3_codes:
        try:
            langs.add(Language(code))
        except Exception:  # noqa: S112 — unknown/region code, just skip it
            continue
    return langs


def _lang_to_dict(language: Language) -> dict[str, Any]:
    """babelfish Language -> the ``{"alpha2","alpha3",...}`` dict the plugins
    expect in their ``languages`` argument (see each plugin's ``_alpha2_for``)."""
    try:
        alpha2 = language.alpha2
    except Exception:
        alpha2 = None
    return {
        "alpha3": language.alpha3,
        "alpha2": alpha2,
        "hi": False,
        "forced": False,
    }


def _video_to_dict(video: Video) -> dict[str, Any]:
    """subliminal Video -> the plain dict the plugins read via ``video.get(...)``."""
    common = {
        "year": video.year,
        "release_group": video.release_group,
        "resolution": video.resolution,
        "source": video.source,
        "video_codec": video.video_codec,
        "audio_codec": video.audio_codec,
        "edition": getattr(video, "edition", None),
        "streaming_service": getattr(video, "streaming_service", None),
        # On-disk path (subliminal.scan_video sets ``name`` to the full path).
        # Used by the embeddedsubtitles plugin to read in-file subtitle tracks.
        "path": getattr(video, "name", None),
    }
    if isinstance(video, Episode):
        return {
            **common,
            "kind": "episode",
            "series": video.series,
            "title": video.title,
            "episode_title": video.title,
            "season": video.season,
            "episode": video.episode,
            "imdb_id": getattr(video, "imdb_id", None),
            "series_imdb_id": getattr(video, "series_imdb_id", None),
        }
    return {
        **common,
        "kind": "movie",
        "title": video.title,
        "imdb_id": getattr(video, "imdb_id", None),
    }


class PluginSubtitle(Subtitle):
    """A subtitle backed by a Provider-Hub search result dict."""

    provider_name = "plugin"  # overridden per concrete subclass

    def __init__(self, language: Language, result: dict[str, Any]) -> None:
        super().__init__(
            language,
            str(result.get("id", "")),
            hearing_impaired=bool((result.get("language") or {}).get("hi")),
            page_link=result.get("page_link"),
        )
        self.result = result
        # The plugin already computed subliminal-shaped match tokens; unknown
        # tokens are ignored by the scorer (scores.get(match, 0)).
        self._matches = {str(m) for m in (result.get("matches") or [])}

    def get_matches(self, video: Video) -> set[str]:  # noqa: ARG002 — subliminal API signature
        return set(self._matches)

    @property
    def info(self) -> str:
        return str(self.result.get("release_info") or self.result.get("id") or "")


class PluginProvider(Provider):
    """Generic subliminal provider that delegates to a Hub-v1 plugin class."""

    # Set by build_plugin_provider on each concrete subclass.
    hub_class: ClassVar[type | None] = None
    provider_id: ClassVar[str] = ""
    languages: ClassVar[set[Language]] = set()
    video_types: ClassVar[tuple[type[Video], ...]] = (Episode, Movie)
    subtitle_class: ClassVar[type[PluginSubtitle]] = PluginSubtitle

    def __init__(self) -> None:
        self._impl: Any = None

    @property
    def _log(self) -> logging.Logger:
        return logging.getLogger(f"{_LOGGER_PACKAGE}.{self.provider_id}")

    def initialize(self) -> None:
        self._impl = self.hub_class()  # type: ignore[misc]

    def terminate(self) -> None:
        self._impl = None

    def list_subtitles(
        self, video: Video, languages: set[Language]
    ) -> list[PluginSubtitle]:
        lang_dicts = [_lang_to_dict(lang) for lang in languages]
        video_dict = _video_to_dict(video)
        try:
            results = self._impl.search(video_dict, lang_dicts, {}) or []
        except Exception:
            self._log.warning("Search failed", exc_info=True)
            return []

        subtitles: list[PluginSubtitle] = []
        for result in results:
            alpha3 = ((result.get("language") or {}).get("alpha3")) or ""
            try:
                language = Language(alpha3)
            except Exception:  # noqa: S112 — plugin returned an unmappable code
                continue
            if language not in languages:
                continue
            subtitles.append(self.subtitle_class(language, result))
        self._log.info("Found %d subtitles for %s", len(subtitles), _video_label(video))
        return subtitles

    def download_subtitle(self, subtitle: PluginSubtitle) -> None:
        payload = subtitle.result.get("provider_payload") or {}
        alpha3 = (subtitle.result.get("language") or {}).get("alpha3") or ""
        try:
            out = self._impl.download(payload, alpha3, {}) or {}
        except Exception:
            self._log.warning("Download failed", exc_info=True)
            return
        if out.get("empty") or not out.get("content_b64"):
            return
        try:
            content = base64.b64decode(out["content_b64"])
        except Exception:
            self._log.warning("Returned undecodable content", exc_info=True)
            return
        subtitle.content = fix_line_ending(content)


def build_plugin_provider(
    provider_id: str,
    hub_class: type,
    alpha3_codes: list[str],
    video_types: tuple[type[Video], ...] = (Episode, Movie),
) -> type[PluginProvider]:
    """Create a concrete subliminal provider class for one Hub plugin."""
    subtitle_cls = type(
        f"{provider_id.title().replace('_', '')}PluginSubtitle",
        (PluginSubtitle,),
        {"provider_name": provider_id},
    )
    return type(
        f"{provider_id.title().replace('_', '')}PluginProvider",
        (PluginProvider,),
        {
            "provider_id": provider_id,
            "hub_class": hub_class,
            "languages": _language_set(alpha3_codes),
            "video_types": video_types,
            "subtitle_class": subtitle_cls,
        },
    )
