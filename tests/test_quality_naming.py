"""Characterization tests for miramedia/torrents/quality_naming.py.

All expected values are frozen from observed runtime behaviour — do not adjust
to match a "should" output without re-running the probe.
"""

import types

import pytest

from miramedia.torrents.quality_naming import NameParts, file_suffix, quality_label
from miramedia.torrents.schemas import Quality

# ---------------------------------------------------------------------------
# quality_label
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("quality", "expected"),
    [
        (Quality.uhd, "2160p"),
        (Quality.fullhd, "1080p"),
        (Quality.hd, "720p"),
        (Quality.sd, "480p"),
        (Quality.unknown, ""),
    ],
)
def test_quality_label(quality: Quality, expected: str) -> None:
    assert quality_label(quality) == expected


# ---------------------------------------------------------------------------
# file_suffix — docstring-advertised cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("quality", "parts", "expected"),
    [
        (Quality.fullhd, NameParts(), "1080p"),
        (Quality.fullhd, NameParts(codec="h265"), "1080p [h265]"),
        (
            Quality.fullhd,
            NameParts(codec="h265", variant="director-cut", extra="2"),
            "1080p [h265-director-cut-2]",
        ),
        (Quality.unknown, NameParts(variant="director-cut"), "[director-cut]"),
        (Quality.unknown, NameParts(), ""),
    ],
)
def test_file_suffix(quality: Quality, parts: NameParts, expected: str) -> None:
    assert file_suffix(quality, parts) == expected


def test_file_suffix_uhd_no_parts() -> None:
    assert file_suffix(Quality.uhd, NameParts()) == "2160p"


def test_file_suffix_hd_with_source_only() -> None:
    # source is stored but intentionally NOT included in file_suffix inner join
    # (the docstring says source is "stored but intentionally not shown here")
    assert file_suffix(Quality.hd, NameParts(source="web")) == "720p"


def test_file_suffix_unknown_with_codec_and_variant() -> None:
    assert (
        file_suffix(Quality.unknown, NameParts(codec="h265", variant="alt"))
        == "[h265-alt]"
    )


# ---------------------------------------------------------------------------
# NameParts.from_row
# ---------------------------------------------------------------------------


def test_nameparts_from_row_all_none() -> None:
    row = types.SimpleNamespace(
        codec=None, hdr=None, source=None, variant=None, extra=None
    )
    parts = NameParts.from_row(row)
    assert parts == NameParts(codec="", hdr=False, source="", variant="", extra="")


def test_nameparts_from_row_missing_attrs() -> None:
    """from_row uses getattr with defaults, so missing attributes yield empty strings."""
    row = types.SimpleNamespace()
    parts = NameParts.from_row(row)
    assert parts == NameParts()


def test_nameparts_from_row_populated() -> None:
    row = types.SimpleNamespace(
        codec="h265", hdr=True, source="web", variant="director-cut", extra="2"
    )
    parts = NameParts.from_row(row)
    assert parts.codec == "h265"
    assert parts.hdr is True
    assert parts.source == "web"
    assert parts.variant == "director-cut"
    assert parts.extra == "2"
