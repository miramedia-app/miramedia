"""Optional mediainfo-backed file analysis.

Wraps :mod:`pymediainfo` so we can pull authoritative resolution, codec,
duration, and HDR info from real files (rather than guessing from the
filename). Falls back to filename parsing via :mod:`miramedia.torrents.parsing`
when libmediainfo isn't installed on the host.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from miramedia.torrents.parsing import ReleaseInfo, parse_release
from miramedia.torrents.schemas import Quality

log = logging.getLogger(__name__)

# Cap concurrent libmediainfo invocations. ``MediaInfo.parse`` is a blocking
# C call that forks/IOs heavily — a bulk import (e.g. a fresh library scan
# fanning out across hundreds of files) without a cap saturates the NAS disk
# queue and starves user-facing requests of threadpool slots. Sized for
# spinning-rust NAS by default; bump on SSD arrays.
_MEDIAINFO_CONCURRENCY = max(1, int(os.getenv("MIRAMEDIA_MEDIAINFO_CONCURRENCY", "2")))
_MEDIAINFO_SEM: asyncio.Semaphore | None = None


def _get_mediainfo_semaphore() -> asyncio.Semaphore:
    """Lazy-init so the semaphore binds to the running event loop.

    Constructing at import would attach it to whichever loop happened to be
    current at import time, which produces "different loop" errors when the
    task body runs under a different loop (the taskiq receiver's, etc.).
    """
    global _MEDIAINFO_SEM
    if _MEDIAINFO_SEM is None:
        _MEDIAINFO_SEM = asyncio.Semaphore(_MEDIAINFO_CONCURRENCY)
    return _MEDIAINFO_SEM


@dataclass
class MediaFileInfo:
    """Authoritative info about a media file."""

    quality: Quality = Quality.unknown
    height: int | None = None
    width: int | None = None
    duration_s: float | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    container: str | None = None
    hdr: bool = False
    source: str = "unknown"  # "mediainfo" | "filename" | "unknown"
    extra: dict = field(default_factory=dict)


def _quality_from_height(height: int | None) -> Quality:
    if height is None:
        return Quality.unknown
    if height >= 2000:
        return Quality.uhd
    if height >= 1000:
        return Quality.fullhd
    if height >= 700:
        return Quality.hd
    if height > 0:
        return Quality.sd
    return Quality.unknown


@lru_cache(maxsize=1)
def _mediainfo_available() -> bool:
    try:
        from pymediainfo import MediaInfo

        # ``MediaInfo.parse`` requires the ``libmediainfo`` shared library at
        # runtime; the import alone won't catch a missing native dep, so we
        # probe it here once.
        MediaInfo.can_parse()
    except Exception:
        log.info(
            "pymediainfo / libmediainfo unavailable, falling back to filename parsing"
        )
        return False
    return True


def _parse_with_mediainfo(path: Path) -> MediaFileInfo | None:
    if not _mediainfo_available():
        return None

    from pymediainfo import MediaInfo

    try:
        mi = MediaInfo.parse(str(path))
    except Exception:
        log.exception("mediainfo parse failed for %s", path)
        return None

    video = next((t for t in mi.tracks if t.track_type == "Video"), None)
    audio = next((t for t in mi.tracks if t.track_type == "Audio"), None)
    general = next((t for t in mi.tracks if t.track_type == "General"), None)

    height = getattr(video, "height", None) if video else None
    width = getattr(video, "width", None) if video else None
    duration_ms = getattr(general, "duration", None) if general else None

    hdr_format = (getattr(video, "hdr_format", None) or "") if video else ""
    color_primaries = (getattr(video, "color_primaries", None) or "") if video else ""
    hdr = bool(hdr_format) or "BT.2020" in color_primaries

    return MediaFileInfo(
        quality=_quality_from_height(height),
        height=height,
        width=width,
        duration_s=(duration_ms / 1000.0) if duration_ms else None,
        video_codec=getattr(video, "format", None) if video else None,
        audio_codec=getattr(audio, "format", None) if audio else None,
        container=getattr(general, "format", None) if general else None,
        hdr=hdr,
        source="mediainfo",
    )


def _first_str(value: str | list[str] | None) -> str | None:
    """Coerce a guessit codec value to a single string.

    guessit returns a LIST when a name carries several tokens for the same
    property (see :func:`miramedia.torrents.parsing._normalize_token`). The
    raw ``MediaFileInfo`` codec fields hold a single string, so pick the first
    token. Returns ``None`` when absent.
    """
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _parse_with_filename(
    path: Path, fallback_title: str | None = None
) -> MediaFileInfo:
    info: ReleaseInfo = parse_release(path.name)
    quality = info.quality
    if quality == Quality.unknown and fallback_title:
        quality = parse_release(fallback_title).quality
    return MediaFileInfo(
        quality=quality,
        height=info.height,
        video_codec=_first_str(info.video_codec),
        audio_codec=_first_str(info.audio_codec),
        container=info.container or path.suffix.lstrip(".").lower(),
        source="filename",
    )


def analyze(path: Path, *, fallback_title: str | None = None) -> MediaFileInfo:
    """Inspect ``path`` and return a :class:`MediaFileInfo`.

    Prefers libmediainfo for accuracy; falls back to filename parsing if the
    binary isn't available, the parse fails, or libmediainfo couldn't infer
    a height.
    """
    if path.exists() and path.is_file():
        from_mediainfo = _parse_with_mediainfo(path)
        if from_mediainfo and from_mediainfo.height:
            return from_mediainfo

    return _parse_with_filename(path, fallback_title=fallback_title)


def detect_quality(path: Path, *, fallback_title: str | None = None) -> Quality:
    """Convenience wrapper returning just the Quality enum."""
    return analyze(path, fallback_title=fallback_title).quality


async def analyze_async(
    path: Path, *, fallback_title: str | None = None
) -> MediaFileInfo:
    """Async wrapper around :func:`analyze` that respects the global
    libmediainfo concurrency cap (``MIRAMEDIA_MEDIAINFO_CONCURRENCY``).

    Prefer this from async callers over a bare ``asyncio.to_thread(analyze, ...)``
    so a bulk import doesn't spawn N concurrent libmediainfo invocations.
    """
    sem = _get_mediainfo_semaphore()
    async with sem:
        return await asyncio.to_thread(analyze, path, fallback_title=fallback_title)


async def detect_quality_async(
    path: Path, *, fallback_title: str | None = None
) -> Quality:
    """Async wrapper around :func:`detect_quality` honoring the mediainfo cap."""
    sem = _get_mediainfo_semaphore()
    async with sem:
        return await asyncio.to_thread(
            detect_quality, path, fallback_title=fallback_title
        )
