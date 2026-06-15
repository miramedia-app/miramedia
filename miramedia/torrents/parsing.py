"""Filename parsing utilities backed by guessit.

Centralises every regex that previously decided whether a file belongs to a
given episode/movie, so the heuristics stay consistent across the import
pipeline, manual mapping, and library scanner.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from guessit import guessit

from miramedia.torrents.schemas import Quality

log = logging.getLogger(__name__)

_SAMPLE_RE = re.compile(
    r"\b(sample|trailer|extra|extras|featurette|behind[. _-]?the[. _-]?scenes|"
    r"deleted[. _-]?scenes|bonus|nfo|sponsor|advertis(e|ing))\b",
    re.IGNORECASE,
)

_VIDEO_EXTS = {
    ".mkv",
    ".mp4",
    ".avi",
    ".mov",
    ".wmv",
    ".m4v",
    ".flv",
    ".webm",
    ".mpg",
    ".mpeg",
    ".ts",
    ".m2ts",
    ".vob",
    ".iso",
}

_SUBTITLE_EXTS = {".srt", ".ass", ".ssa", ".sub", ".vtt", ".idx"}

_THREE_TO_TWO_LANG = {
    "eng": "en",
    "spa": "es",
    "fre": "fr",
    "fra": "fr",
    "ger": "de",
    "deu": "de",
    "ita": "it",
    "por": "pt",
    "rus": "ru",
    "jpn": "ja",
    "kor": "ko",
    "chi": "zh",
    "zho": "zh",
    "ara": "ar",
    "hin": "hi",
    "dut": "nl",
    "nld": "nl",
    "swe": "sv",
    "nor": "no",
    "dan": "da",
    "fin": "fi",
    "pol": "pl",
    "tur": "tr",
    "ukr": "uk",
    "ces": "cs",
    "cze": "cs",
    "ell": "el",
    "gre": "el",
    "heb": "he",
    "hun": "hu",
    "ron": "ro",
    "rum": "ro",
    "tha": "th",
    "vie": "vi",
    "ind": "id",
    "may": "ms",
    "msa": "ms",
    "bul": "bg",
    "hrv": "hr",
    "srp": "sr",
    "slk": "sk",
    "slo": "sk",
    "slv": "sl",
    "lit": "lt",
    "lav": "lv",
    "est": "et",
    "cat": "ca",
    "tam": "ta",
    "tel": "te",
    "ben": "bn",
}

# ISO 639-1 two-letter codes the subtitle parser will accept. Filters out
# unrelated two-letter tokens like "HD", "BD", "WS" that often appear in
# release names.
_ISO_639_1 = frozenset(
    set(_THREE_TO_TWO_LANG.values())
    | {
        "af",
        "am",
        "as",
        "az",
        "be",
        "bg",
        "bn",
        "bs",
        "ca",
        "cs",
        "cy",
        "da",
        "de",
        "el",
        "en",
        "es",
        "et",
        "eu",
        "fa",
        "fi",
        "fr",
        "ga",
        "gd",
        "gl",
        "gu",
        "he",
        "hi",
        "hr",
        "hu",
        "hy",
        "id",
        "is",
        "it",
        "ja",
        "ka",
        "kk",
        "km",
        "kn",
        "ko",
        "ky",
        "lo",
        "lt",
        "lv",
        "mk",
        "ml",
        "mn",
        "mr",
        "ms",
        "my",
        "ne",
        "nl",
        "no",
        "or",
        "pa",
        "pl",
        "ps",
        "pt",
        "ro",
        "ru",
        "si",
        "sk",
        "sl",
        "sq",
        "sr",
        "sv",
        "sw",
        "ta",
        "te",
        "th",
        "tl",
        "tr",
        "uk",
        "ur",
        "uz",
        "vi",
        "yi",
        "zh",
    }
)


@dataclass
class ReleaseInfo:
    """Normalised metadata extracted from a release / file name."""

    title: str | None = None
    type: str | None = None  # "episode" | "movie" | "unknown"
    year: int | None = None
    seasons: list[int] = field(default_factory=list)
    episodes: list[int] = field(default_factory=list)
    absolute_episode: int | None = None
    quality: Quality = Quality.unknown
    height: int | None = None
    # guessit returns a list when a name carries several tokens for one
    # property (e.g. "WORKPRINT WEB-DL" -> source=['Workprint', 'Web']). Pass
    # these through normalize_codec/normalize_source, which accept either shape.
    video_codec: str | list[str] | None = None
    audio_codec: str | list[str] | None = None
    source: str | list[str] | None = None
    container: str | None = None
    release_group: str | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class SubtitleInfo:
    """Normalised metadata for a subtitle file."""

    language: str | None  # ISO 639-1 (2-char)
    container: str  # extension without dot, lowercase: "srt"/"ass"/...
    forced: bool = False
    sdh: bool = False
    cc: bool = False


def _coerce_int_list(value: object) -> list[int]:
    if value is None:
        return []
    if isinstance(value, int):
        return [value]
    if isinstance(value, list):
        return [int(v) for v in value if isinstance(v, (int, str)) and str(v).isdigit()]
    if isinstance(value, str) and value.isdigit():
        return [int(value)]
    return []


def _quality_from_screen_size(screen_size: str | None) -> tuple[Quality, int | None]:
    if not screen_size:
        return Quality.unknown, None
    s = screen_size.lower()
    if "2160" in s or "4k" in s or "uhd" in s:
        return Quality.uhd, 2160
    if "1440" in s:
        return Quality.fullhd, 1440
    if "1080" in s:
        return Quality.fullhd, 1080
    if "720" in s:
        return Quality.hd, 720
    if "480" in s or "576" in s or "sd" in s:
        return Quality.sd, 480 if "480" in s else 576
    return Quality.unknown, None


@lru_cache(maxsize=4096)
def _guessit_cached(name: str) -> dict:
    """Cache the expensive guessit call. A directory import calls
    ``match_episode_file`` (→ ``parse_release``) once per (episode x file)
    candidate and then re-parses each imported file 2-3 more times; guessit is
    ~100-500ms a pop, so without this a large show imports in O(ExF) parses and
    appears hung. guessit is deterministic for a given name, so the cache is
    safe. Callers get a fresh dict copy below so they can't mutate the cache."""
    return dict(guessit(name))


def parse_release(name: str, *, options: dict | None = None) -> ReleaseInfo:
    """Parse a release/file name into a ReleaseInfo dataclass.

    Wraps guessit and normalises its output so callers don't need to know
    about the underlying library's quirks.
    """
    # Only the options-less path is cached (it's the hot one); a bespoke
    # options dict bypasses the cache.
    raw = (
        dict(guessit(name, options=options)) if options else dict(_guessit_cached(name))
    )
    quality, height = _quality_from_screen_size(raw.get("screen_size"))

    return ReleaseInfo(
        title=raw.get("title"),
        type=raw.get("type"),
        year=raw.get("year"),
        seasons=_coerce_int_list(raw.get("season")),
        episodes=_coerce_int_list(raw.get("episode")),
        absolute_episode=raw.get("absolute_episode"),
        quality=quality,
        height=height,
        video_codec=raw.get("video_codec"),
        audio_codec=raw.get("audio_codec"),
        source=raw.get("source"),
        container=raw.get("container"),
        release_group=raw.get("release_group"),
        raw=raw,
    )


_CODEC_NORMALIZE = {
    "h264": "h264",
    "h.264": "h264",
    "avc": "h264",
    "x264": "h264",
    "h265": "h265",
    "h.265": "h265",
    "hevc": "h265",
    "x265": "h265",
    "av1": "av1",
    "vp9": "vp9",
    "mpeg-4": "mpeg4",
    "mpeg4": "mpeg4",
    "xvid": "xvid",
    "divx": "divx",
}
_SOURCE_NORMALIZE = {
    "blu-ray": "bluray",
    "bluray": "bluray",
    "bdrip": "bdrip",
    "bdremux": "remux",
    "remux": "remux",
    "web-dl": "web",
    "webdl": "web",
    "web": "web",
    "webrip": "webrip",
    "hdtv": "hdtv",
    "dvdrip": "dvdrip",
    "dvd": "dvd",
    "hdrip": "hdrip",
    "uhdtv": "hdtv",
}


_KNOWN_CODECS = set(_CODEC_NORMALIZE.values())
_KNOWN_SOURCES = set(_SOURCE_NORMALIZE.values())


def _normalize_token(value: str | list[str] | None, mapping: dict[str, str]) -> str:
    """Map a guessit value to a canonical slug.

    guessit returns a plain string for a single match but a LIST when a name
    carries several tokens for the same property — e.g. ``"WORKPRINT WEB-DL"``
    yields ``source=['Workprint', 'Web']``. Accept either shape and return the
    first token that maps to a known slug (so the meaningful ``web`` wins over
    the unrecognised ``workprint``). Returns ``""`` when unknown/absent.
    """
    if not value:
        return ""
    items = value if isinstance(value, list) else [value]
    for item in items:
        slug = mapping.get(str(item).lower().replace(" ", ""), "")
        if slug:
            return slug
    return ""


def normalize_codec(video_codec: str | list[str] | None) -> str:
    """Normalize a raw codec string (e.g. ``"x265"``, ``"HEVC"``) to a canonical
    slug (``"h265"``). Returns ``""`` when unknown/absent."""
    return _normalize_token(video_codec, _CODEC_NORMALIZE)


def normalize_source(source: str | list[str] | None) -> str:
    """Normalize a raw source string (e.g. ``"WEB-DL"``) to a canonical slug
    (``"web"``). Returns ``""`` when unknown/absent."""
    return _normalize_token(source, _SOURCE_NORMALIZE)


def match_episode_file(
    filename: str, season: int, episode: int, *, anime_absolute: int | None = None
) -> bool:
    """Decide whether a file belongs to (season, episode).

    Accepts S01E01, 1x01, "Season 1 Episode 1", and anime absolute numbering
    when ``anime_absolute`` is provided. Falls back to direct regex when
    guessit can't decide so we still match aggressive group-tagged anime
    releases.
    """
    info = parse_release(filename)

    if season in info.seasons and episode in info.episodes:
        return True

    if anime_absolute is not None and info.absolute_episode == anime_absolute:
        return True

    # Fallback: explicit S##E## or ##x## even when guessit's "type" defaults
    # to movie because the show name happens to look like a movie title.
    pattern = (
        rf"(?<![A-Za-z0-9])"
        rf"(?:s0?{season}[ ._-]?e0?{episode}|0?{season}x0?{episode})"
        rf"(?![0-9])"
    )
    return re.search(pattern, filename, re.IGNORECASE) is not None


def match_special_file(
    filename: str,
    *,
    episode_title: str,
    show_name: str = "",
    accept_lone_file: bool = False,
) -> bool:
    """Decide whether a file is a Season 0 special's video by title overlap.

    Specials are rarely named ``S00E00`` — release groups use the special's own
    title — so :func:`match_episode_file` can't place them. Compare the special's
    title words (minus the show's own words, which carry no signal) against the
    filename. When the special has no distinguishing title, accept only if it is
    the lone candidate (``accept_lone_file``). Mirrors the download-time matching
    in ``ShowService._search_special_episode`` so a release we grabbed for a
    special also imports to that special.
    """
    from miramedia.imports.matching import _normalize_title_for_matching

    title_words = set(
        _normalize_title_for_matching(episode_title or "").lower().split()
    )
    show_words = set(_normalize_title_for_matching(show_name or "").lower().split())
    match_words = title_words - show_words
    if not match_words:
        return accept_lone_file

    stem_words = set(_normalize_title_for_matching(Path(filename).stem).lower().split())
    if not stem_words:
        return False
    overlap = match_words & stem_words
    return len(overlap) / len(match_words) >= 0.6


def parse_subtitle_filename(filename: str) -> SubtitleInfo | None:
    """Extract language + flags from a subtitle filename.

    Returns ``None`` if the file isn't a recognised subtitle container.
    """
    p = Path(filename)
    if p.suffix.lower() not in _SUBTITLE_EXTS:
        return None

    stem = p.stem
    flags = {"forced": False, "sdh": False, "cc": False}
    for flag in flags:
        if re.search(rf"[. _-]{flag}\b", stem, re.IGNORECASE):
            flags[flag] = True

    language = None
    # Try a 3-letter ISO 639-2 code first ("eng", "spa", ...) then a
    # 2-letter ISO 639-1 ("en", "es"). Anchor near the end so we don't
    # mistake show name fragments for language tags. The 2-letter fallback
    # is gated on a known ISO 639-1 set so tokens like "HD"/"BD" don't
    # masquerade as languages.
    for token in reversed(re.split(r"[. _-]+", stem)):
        token_lower = token.lower()
        if len(token_lower) == 3 and token_lower in _THREE_TO_TWO_LANG:
            language = _THREE_TO_TWO_LANG[token_lower]
            break
        if len(token_lower) == 2 and token_lower in _ISO_639_1:
            language = token_lower
            break

    return SubtitleInfo(
        language=language,
        container=p.suffix.lower().lstrip("."),
        forced=flags["forced"],
        sdh=flags["sdh"],
        cc=flags["cc"],
    )


def match_subtitle_file(
    filename: str, season: int, episode: int
) -> SubtitleInfo | None:
    """Return SubtitleInfo if this subtitle belongs to (season, episode)."""
    if not match_episode_file(filename, season=season, episode=episode):
        return None
    return parse_subtitle_filename(filename)


def is_sample_or_extra(path: Path) -> bool:
    """True for sample/trailer/extras/etc. files we should ignore."""
    if _SAMPLE_RE.search(path.name):
        return True
    # Files under an "extras" / "featurettes" subdir
    return any(_SAMPLE_RE.search(part) for part in path.parts[:-1])


def is_video_file(path: Path) -> bool:
    return path.suffix.lower() in _VIDEO_EXTS


def is_subtitle_file(path: Path) -> bool:
    return path.suffix.lower() in _SUBTITLE_EXTS
