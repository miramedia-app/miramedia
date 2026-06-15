"""Helpers for the quality + naming-component model.

A media file row carries the resolution bucket (``quality``) plus a set of
distinguishing components, each in its own column:

* ``codec`` (str, "" when unknown) — normalised video codec, e.g. ``"h265"``.
* ``hdr`` (bool) — whether the file is HDR.
* ``source`` (str, "" when unknown) — normalised source, e.g. ``"web"``.
* ``variant`` (str, "" when none) — free-text the *user* entered.
* ``extra`` (str, "" when none) — auto collision discriminator (``"2"``,
  ``"3"`` …) appended to keep same-quality filenames unique on disk.

This module centralises the small string helpers so callers (services, router,
filename builders) all agree on the rendering.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from miramedia.torrents.schemas import Quality

_QUALITY_LABEL = {
    Quality.uhd: "2160p",
    Quality.fullhd: "1080p",
    Quality.hd: "720p",
    Quality.sd: "480p",
    Quality.unknown: "",
}


@dataclass(frozen=True)
class NameParts:
    """The distinguishing components of a media file, used for filename render."""

    codec: str = ""
    hdr: bool = False
    source: str = ""
    variant: str = ""  # user-entered
    extra: str = ""  # auto collision discriminator

    @classmethod
    def from_row(cls, row: Any) -> NameParts:  # noqa: ANN401 — duck-typed file row
        return cls(
            codec=getattr(row, "codec", "") or "",
            hdr=bool(getattr(row, "hdr", False)),
            source=getattr(row, "source", "") or "",
            variant=getattr(row, "variant", "") or "",
            extra=getattr(row, "extra", "") or "",
        )


def quality_label(quality: Quality) -> str:
    """Render the user-facing label for a quality enum value."""
    return _QUALITY_LABEL.get(quality, "")


def file_suffix(quality: Quality, parts: NameParts) -> str:
    """Render the pre-formatted display suffix for a saved file.

    Combines the quality label with the bracketed codec + user variant + extra
    discriminator (HDR/source are stored but intentionally not shown here — add
    the ``{hdr}``/``{source}`` tokens to a template to include them). Produces
    ``"1080p"``, ``"1080p [h265]"``, ``"1080p [h265-director-cut-2]"``,
    ``"[director-cut]"``, or ``""``.
    """
    label = quality_label(quality)
    inner = "-".join(p for p in (parts.codec, parts.variant, parts.extra) if p)
    if label and inner:
        return f"{label} [{inner}]"
    if label:
        return label
    if inner:
        return f"[{inner}]"
    return ""
