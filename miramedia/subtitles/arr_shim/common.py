"""Shared helpers for Sonarr/Radarr Bazarr compatibility shims."""

from __future__ import annotations

from miramedia.torrents.schemas import Quality

_LANGUAGE_NAMES: dict[str, str] = {
    "en": "English",
    "eng": "English",
    "ja": "Japanese",
    "jpn": "Japanese",
    "es": "Spanish",
    "spa": "Spanish",
    "fr": "French",
    "fre": "French",
    "fra": "French",
    "de": "German",
    "ger": "German",
    "deu": "German",
    "it": "Italian",
    "ita": "Italian",
    "ko": "Korean",
    "kor": "Korean",
    "zh": "Chinese",
    "zho": "Chinese",
    "chi": "Chinese",
    "pt": "Portuguese",
    "por": "Portuguese",
    "ru": "Russian",
    "rus": "Russian",
}

_QUALITY_ARR: dict[Quality, tuple[str, int]] = {
    Quality.uhd: ("WEBDL-2160p", 2160),
    Quality.fullhd: ("WEBDL-1080p", 1080),
    Quality.hd: ("WEBDL-720p", 720),
    Quality.sd: ("SDTV", 480),
    Quality.unknown: ("Unknown", 0),
}

DEFAULT_AUDIO_LANGUAGE = {"name": "English"}

MEDIA_INFO_EMPTY_KEYS: frozenset[str] = frozenset(
    {
        "videoCodec",
        "videoCodecID",
        "videoCodecLibrary",
        "audioCodec",
        "audioCodecID",
        "audioProfile",
        "audioAdditionalFeatures",
        "audioLanguages",
    }
)


def quality_to_arr(quality: Quality) -> tuple[str, int]:
    return _QUALITY_ARR[quality]


def language_name_from_code(code: str | None) -> str:
    if not code:
        return "English"
    normalized = code.strip()
    if not normalized:
        return "English"
    if len(normalized) > 3 or " " in normalized:
        return normalized
    return _LANGUAGE_NAMES.get(normalized.lower(), "English")


def list_tags() -> list[dict]:
    return []


def list_history() -> list[dict]:
    return []


def media_info_payload(
    *, video_codec: str = "", audio_codec: str = ""
) -> dict[str, str]:
    """Radarr-shaped mediaInfo with every parser-checked key present."""
    return {
        "videoCodec": video_codec,
        "videoCodecID": "",
        "videoCodecLibrary": "",
        "audioCodec": audio_codec,
        "audioCodecID": "",
        "audioProfile": "",
        "audioAdditionalFeatures": "",
        "audioLanguages": "",
    }
