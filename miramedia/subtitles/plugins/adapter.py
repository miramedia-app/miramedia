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
import functools
import io
import logging
import urllib.request
import zipfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from types import ModuleType
from typing import Any, ClassVar, Protocol, Self

from babelfish import Language
from subliminal.providers import Provider
from subliminal.subtitle import Subtitle, fix_line_ending
from subliminal.video import Episode, Movie, Video

from miramedia.subtitles.bounded_decode import (
    MAX_SUBTITLE_MEMBER_BYTES,
    MAX_SUBTITLE_RESPONSE_BYTES,
    BoundedSubtitleContent,
    decode_bounded_subtitle_content,
    decode_bounded_zip_with_selector,
    read_bounded_stream,
)

# Package the per-provider loggers hang off. Each provider logs under
# ``miramedia.subtitles.subliminal.plugins.<provider_id>`` — the same
# ``miramedia.subtitles.subliminal.*`` umbrella the built-in subliminal
# providers are rewritten into (see miramedia.logging), so all subtitle
# providers group together. Not derived from ``__name__`` (that would be
# ``miramedia.subtitles.plugins``).
_LOGGER_PACKAGE = "miramedia.subtitles.subliminal.plugins"

_BOUNDED_EXTRACT_PATCHED = False


class _HTTPReadable(Protocol):
    def read(self, amt: int = -1) -> bytes: ...

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> bool | None: ...


class _BoundedHTTPResponse:
    """Wrap ``urlopen`` responses so ``read()`` honors subtitle byte limits."""

    def __init__(self, response: _HTTPReadable) -> None:
        self._response = response
        self._buffer = b""

    def read(self, amt: int = -1) -> bytes:
        if not self._buffer:
            self._buffer = read_bounded_stream(self._response)
        if amt == -1:
            data, self._buffer = self._buffer, b""
            return data
        chunk = self._buffer[:amt]
        self._buffer = self._buffer[amt:]
        return chunk

    def __enter__(self) -> Self:
        self._response.__enter__()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        self._response.__exit__(exc_type, exc_val, exc_tb)


@contextmanager
def _bounded_urlopen_context() -> Iterator[None]:
    real_urlopen = urllib.request.urlopen

    def bounded_urlopen(
        *open_args: object, **open_kwargs: object
    ) -> _BoundedHTTPResponse:
        return _BoundedHTTPResponse(real_urlopen(*open_args, **open_kwargs))

    urllib.request.urlopen = bounded_urlopen
    try:
        yield
    finally:
        urllib.request.urlopen = real_urlopen


def _wrap_bounded_http_method(impl: object, attr: str) -> None:
    """Cap vendored plugin HTTP reads without forking their request logic."""
    original = getattr(impl, attr, None)
    if original is None or getattr(original, "_bounded_http", False):
        return

    @functools.wraps(original)
    def wrapped(*args: object, **kwargs: object) -> bytes:
        with _bounded_urlopen_context():
            return original(*args, **kwargs)

    wrapped._bounded_http = True
    setattr(impl, attr, wrapped)


def _apply_plugin_http_bounds(impl: object) -> None:
    _wrap_bounded_http_method(impl, "_http_get")
    _wrap_bounded_http_method(impl, "_http_request")


def _empty_extract_payload(
    module: ModuleType, payload: object | None = None
) -> dict[str, Any]:
    payload_dict = payload if isinstance(payload, dict) else {}
    content_payload = module._content_payload
    extension = "srt"
    if hasattr(module, "_subtitle_extension"):
        extension = (
            module._subtitle_extension(payload_dict.get("filename", "")) or "srt"
        )
    if module.__name__.endswith("my_subs"):
        return content_payload(b"")
    if module.__name__.endswith("subf2m"):
        return content_payload(b"", extension, empty=True)
    return content_payload(b"", extension, empty=True)


def _bounded_zip_extract(
    module: ModuleType,
    body: bytes,
    payload: object,
    select_member: Callable[[list[str], object], str] | None,
) -> BoundedSubtitleContent:
    if module.__name__.endswith("tvsubtitles"):

        def tv_select(names: list[str], _select_payload: object) -> str:
            if len(names) != 1:
                msg = "tvsubtitles archive contains more than one file"
                raise ValueError(msg)
            name = names[0]
            subtitle_format = module._subtitle_extension(name)
            if not subtitle_format:
                msg = "tvsubtitles archive contains no supported subtitle file"
                raise ValueError(msg)
            return name

        decoded = decode_bounded_zip_with_selector(body, tv_select, payload)
        if decoded.content is None:
            return decoded
        normalized = module._normalize_line_endings(decoded.content)
        return BoundedSubtitleContent(
            content=normalized,
            kind="zip",
            member_name=decoded.member_name,
        )

    if select_member is not None:
        return decode_bounded_zip_with_selector(body, select_member, payload)
    return decode_bounded_subtitle_content(body)


def _patch_bounded_extract_download(module: ModuleType) -> None:
    if getattr(module.extract_download, "_bounded_extract", False):
        return

    original = module.extract_download
    select_member = getattr(module, "select_subtitle_file", None)

    @functools.wraps(original)
    def bounded_extract(body: bytes, payload: object | None = None) -> dict[str, Any]:
        payload_obj = payload if payload is not None else {}
        if not body:
            return original(body, payload_obj)
        if len(body) > MAX_SUBTITLE_RESPONSE_BYTES:
            return _empty_extract_payload(module, payload_obj)

        stream = io.BytesIO(body)
        if zipfile.is_zipfile(stream):
            decoded = _bounded_zip_extract(module, body, payload_obj, select_member)
            if decoded.content is None:
                return _empty_extract_payload(module, payload_obj)
            member_name = decoded.member_name or ""
            if hasattr(module, "_subtitle_extension"):
                extension = module._subtitle_extension(member_name) or "srt"
            else:
                extension = "srt"
            if module.__name__.endswith("subf2m"):
                return module._content_payload(decoded.content, extension)
            return module._content_payload(decoded.content, extension)

        if module.__name__.endswith("tvsubtitles"):
            msg = "tvsubtitles download did not return a zip archive"
            raise ValueError(msg)
        return original(body, payload_obj)

    bounded_extract._bounded_extract = True
    module.extract_download = bounded_extract


def install_bounded_vendored_extract_patches() -> None:
    """Patch vendored ZIP extractors once at import (adapter-level containment)."""
    global _BOUNDED_EXTRACT_PATCHED
    if _BOUNDED_EXTRACT_PATCHED:
        return
    from miramedia.subtitles.plugins.vendored import isubtitles, subf2m, tvsubtitles

    for module in (subf2m, isubtitles, tvsubtitles):
        _patch_bounded_extract_download(module)
    _BOUNDED_EXTRACT_PATCHED = True


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
        _apply_plugin_http_bounds(self._impl)

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
        if len(content) > MAX_SUBTITLE_MEMBER_BYTES:
            self._log.warning("Returned oversized subtitle content")
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
