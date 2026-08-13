from __future__ import annotations

import logging
import re
from typing import Any

from miramedia.config import MiraMediaConfig
from miramedia.naming_defaults import (
    DEFAULT_EPISODE_FILE_FORMAT,
    DEFAULT_MOVIE_FILE_FORMAT,
    DEFAULT_MOVIE_FOLDER_FORMAT,
    DEFAULT_SEASON_FOLDER_FORMAT,
    DEFAULT_SHOW_FOLDER_FORMAT,
)
from miramedia.torrents.quality_naming import NameParts, file_suffix, quality_label
from miramedia.torrents.schemas import Quality

log = logging.getLogger(__name__)


def sanitize_path_component(value: str) -> str:
    """Sanitize a rendered file/folder component while preserving readable text."""
    sanitized = remove_special_characters(value)
    sanitized = re.sub(r"\s+", " ", sanitized)
    return sanitized.strip(" .")


def _template_or_default(template: str | None, default: str) -> str:
    template = (template or "").strip()
    return template or default


def _render_template(
    *,
    template: str | None,
    default_template: str,
    context: dict[str, Any],
    label: str,
) -> str:
    effective_template = _template_or_default(template, default_template)
    try:
        rendered = effective_template.format(**context)
    except (KeyError, IndexError, ValueError) as exc:
        log.warning(
            "Invalid %s naming template %r; falling back to %r: %s",
            label,
            effective_template,
            default_template,
            exc,
        )
        rendered = default_template.format(**context)

    sanitized = sanitize_path_component(rendered)
    if sanitized:
        return sanitized

    log.warning(
        "%s naming template %r rendered an empty path component; falling back to %r",
        label,
        effective_template,
        default_template,
    )
    return sanitize_path_component(default_template.format(**context))


def _id_tag(media: Any) -> str:  # noqa: ANN401
    return build_folder_id_tag(
        getattr(media, "imdb_id", None),
        media.metadata_provider,
        media.external_id,
    )


def _old_provider_id_tag(media: Any) -> str:  # noqa: ANN401
    return f"[{media.metadata_provider}id-{media.external_id}]"


def _media_context(media: Any, *, title_key: str = "title") -> dict[str, Any]:  # noqa: ANN401
    title = media.name
    # Keys are the user-facing *template tokens* and are intentionally NOT the
    # same as the underlying model field names — they're a presentation-layer
    # alias (like an API serializer alias). Format is  token : source field:
    #   {provider_tag} : built from metadata_provider + external_id (+ imdb_id)
    #   {provider}     : media.metadata_provider
    #   {provider_id}  : media.external_id
    return {
        title_key: title,
        "title": title,
        "year": getattr(media, "year", ""),
        "provider_tag": _id_tag(media),
        "imdb_id": getattr(media, "imdb_id", "") or "",
        "provider": getattr(media, "metadata_provider", "") or "",
        "provider_id": getattr(media, "external_id", "") or "",
    }


def _suffix_context(quality: Quality, parts: NameParts) -> dict[str, str]:
    rendered_suffix = file_suffix(quality, parts)
    suffix = f" - {rendered_suffix}" if rendered_suffix else ""
    return {
        "quality": quality_label(quality),
        "variant": parts.variant,
        "suffix": suffix,
        "codec": parts.codec,
        "hdr": "hdr" if parts.hdr else "",
        "source": parts.source,
        "extra": parts.extra,
    }


def show_folder_name(show: Any) -> str:  # noqa: ANN401
    naming = MiraMediaConfig().misc.naming
    return _render_template(
        template=naming.show_folder_format,
        default_template=DEFAULT_SHOW_FOLDER_FORMAT,
        context=_media_context(show),
        label="show folder",
    )


def default_show_folder_name(show: Any) -> str:  # noqa: ANN401
    return _render_template(
        template=DEFAULT_SHOW_FOLDER_FORMAT,
        default_template=DEFAULT_SHOW_FOLDER_FORMAT,
        context=_media_context(show),
        label="default show folder",
    )


def old_show_folder_name(show: Any) -> str:  # noqa: ANN401
    context = _media_context(show)
    context["provider_tag"] = _old_provider_id_tag(show)
    return _render_template(
        template=DEFAULT_SHOW_FOLDER_FORMAT,
        default_template=DEFAULT_SHOW_FOLDER_FORMAT,
        context=context,
        label="legacy show folder",
    )


def movie_folder_name(movie: Any) -> str:  # noqa: ANN401
    naming = MiraMediaConfig().misc.naming
    return _render_template(
        template=naming.movie_folder_format,
        default_template=DEFAULT_MOVIE_FOLDER_FORMAT,
        context=_media_context(movie),
        label="movie folder",
    )


def default_movie_folder_name(movie: Any) -> str:  # noqa: ANN401
    return _render_template(
        template=DEFAULT_MOVIE_FOLDER_FORMAT,
        default_template=DEFAULT_MOVIE_FOLDER_FORMAT,
        context=_media_context(movie),
        label="default movie folder",
    )


def old_movie_folder_name(movie: Any) -> str:  # noqa: ANN401
    context = _media_context(movie)
    context["provider_tag"] = _old_provider_id_tag(movie)
    return _render_template(
        template=DEFAULT_MOVIE_FOLDER_FORMAT,
        default_template=DEFAULT_MOVIE_FOLDER_FORMAT,
        context=context,
        label="legacy movie folder",
    )


def season_folder_name(season_number: int) -> str:
    # Season 0 holds specials; use the media-server-standard "Specials" folder
    # (Plex/Jellyfin/Kodi) instead of "Season 0".
    if season_number == 0:
        return "Specials"
    naming = MiraMediaConfig().misc.naming
    context = {
        "season_number": season_number,
        "season_number_00": f"{season_number:02d}",
    }
    return _render_template(
        template=naming.season_folder_format,
        default_template=DEFAULT_SEASON_FOLDER_FORMAT,
        context=context,
        label="season folder",
    )


def default_season_folder_name(season_number: int) -> str:
    if season_number == 0:
        return "Specials"
    context = {
        "season_number": season_number,
        "season_number_00": f"{season_number:02d}",
    }
    return _render_template(
        template=DEFAULT_SEASON_FOLDER_FORMAT,
        default_template=DEFAULT_SEASON_FOLDER_FORMAT,
        context=context,
        label="default season folder",
    )


def movie_file_stem(
    movie: Any,  # noqa: ANN401
    quality: Quality,
    parts: NameParts,
) -> str:
    naming = MiraMediaConfig().misc.naming
    context = {
        **_media_context(movie, title_key="movie_title"),
        **_suffix_context(quality, parts),
    }
    return _render_template(
        template=naming.movie_file_format,
        default_template=DEFAULT_MOVIE_FILE_FORMAT,
        context=context,
        label="movie file",
    )


def default_movie_file_stem(
    movie: Any,  # noqa: ANN401 — duck-typed media object
    quality: Quality,
    parts: NameParts,
) -> str:
    context = {
        **_media_context(movie, title_key="movie_title"),
        **_suffix_context(quality, parts),
    }
    return _render_template(
        template=DEFAULT_MOVIE_FILE_FORMAT,
        default_template=DEFAULT_MOVIE_FILE_FORMAT,
        context=context,
        label="default movie file",
    )


def movie_file_stem_candidates(
    movie: Any,  # noqa: ANN401 — duck-typed media object
    quality: Quality,
    parts: NameParts,
) -> list[str]:
    # Custom template + built-in default, in case the user's template renders an
    # unusable stem (the default is the fallback the file was written with).
    return _unique(
        [
            movie_file_stem(movie, quality, parts),
            default_movie_file_stem(movie, quality, parts),
        ]
    )


def episode_file_stem(
    show: Any,  # noqa: ANN401
    *,
    season_number: int,
    episode_number: int,
    quality: Quality,
    parts: NameParts,
) -> str:
    naming = MiraMediaConfig().misc.naming
    context = {
        **_media_context(show, title_key="show_title"),
        **_suffix_context(quality, parts),
        "season_number": season_number,
        "season_number_00": f"{season_number:02d}",
        "episode_number": episode_number,
        "episode_number_00": f"{episode_number:02d}",
    }
    return _render_template(
        template=naming.episode_file_format,
        default_template=DEFAULT_EPISODE_FILE_FORMAT,
        context=context,
        label="episode file",
    )


def default_episode_file_stem(
    show: Any,  # noqa: ANN401
    *,
    season_number: int,
    episode_number: int,
    quality: Quality,
    parts: NameParts,
) -> str:
    context = {
        **_media_context(show, title_key="show_title"),
        **_suffix_context(quality, parts),
        "season_number": season_number,
        "season_number_00": f"{season_number:02d}",
        "episode_number": episode_number,
        "episode_number_00": f"{episode_number:02d}",
    }
    return _render_template(
        template=DEFAULT_EPISODE_FILE_FORMAT,
        default_template=DEFAULT_EPISODE_FILE_FORMAT,
        context=context,
        label="default episode file",
    )


def episode_file_stem_candidates(
    show: Any,  # noqa: ANN401
    *,
    season_number: int,
    episode_number: int,
    quality: Quality,
    parts: NameParts,
) -> list[str]:
    return _unique(
        [
            episode_file_stem(
                show,
                season_number=season_number,
                episode_number=episode_number,
                quality=quality,
                parts=parts,
            ),
            default_episode_file_stem(
                show,
                season_number=season_number,
                episode_number=episode_number,
                quality=quality,
                parts=parts,
            ),
        ]
    )


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def remove_special_characters(filename: str) -> str:
    """
    Removes special characters from the filename to ensure it works with Jellyfin.

    :param filename: The original filename.
    :return: A sanitized version of the filename.
    """
    # Remove invalid characters
    sanitized = re.sub(r"([<>:\"/\\|?*])", "", filename)

    # Remove leading and trailing dots or spaces
    return sanitized.strip(" .")


def build_folder_id_tag(
    imdb_id: str | None, metadata_provider: str, external_id: str
) -> str:
    """Build the bracket ID tag for a media folder name.

    Prefers the IMDb ID when available (e.g. ``[imdb-tt1234567]``),
    falling back to the provider-specific format (e.g. ``[tmdbid-12345]``).
    """
    if imdb_id and imdb_id.startswith("tt"):
        return f"[imdb-{imdb_id}]"
    # The native provider stores the IMDb ID directly in external_id; treat any
    # tt-prefixed external_id as an IMDb ID even when the imdb_id column is unset
    # (e.g. legacy rows added before imdb_id backfill).
    if external_id and external_id.startswith("tt"):
        return f"[imdb-{external_id}]"
    return f"[{metadata_provider}id-{external_id}]"


def extract_external_id_from_string(input_string: str) -> tuple[str | None, str | None]:
    """
    Extracts a metadata provider ID (imdb/native/tmdb/tvdb ID) from the given string.

    :param input_string: The string to extract the ID from.
    :return: The extracted Metadata Provider and ID or None if not found.
    """
    # Match standardized IMDb format (e.g. imdb-tt1234567)
    match = re.search(r"\b(imdb)[-_]?(tt[0-9]+)\b", input_string, re.IGNORECASE)
    if match:
        return match.group(1).lower(), match.group(2)

    # Match native provider with IMDb-style IDs (e.g. nativeid-tt1234567)
    match = re.search(
        r"\b(native)(?:id)?[-_]?(tt[0-9]+)\b", input_string, re.IGNORECASE
    )
    if match:
        return match.group(1).lower(), match.group(2)

    # Match tmdb/tvdb provider with numeric IDs
    match = re.search(
        r"\b(tmdb|tvdb)(?:id)?[-_]?([0-9]+)\b", input_string, re.IGNORECASE
    )
    if match:
        return match.group(1).lower(), match.group(2)

    return None, None


def format_episode_label(
    show_name: str,
    season_number: int,
    episode_number: int,
    episode_title: str | None = None,
    *,
    separator: str = " - ",
) -> str:
    """Human-facing episode label: ``Show - S01E02 - Title`` (title omitted when blank/None).

    On-disk filename templates live in ``miramedia.naming_defaults`` — this is for
    display labels only.
    """
    label = f"{show_name}{separator}S{season_number:02d}E{episode_number:02d}"
    title = (episode_title or "").strip()
    return f"{label}{separator}{title}" if title else label
