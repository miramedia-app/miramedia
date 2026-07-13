"""Characterization tests for miramedia/torrents/parsing.py.

All expected values are frozen from observed runtime behaviour.
"""

from pathlib import Path

import pytest

from miramedia.torrents.parsing import (
    is_sample_or_extra,
    is_subtitle_file,
    is_video_file,
    match_episode_file,
    match_special_file,
    normalize_codec,
    normalize_source,
    parse_release,
    parse_subtitle_filename,
)
from miramedia.torrents.schemas import Quality

# ---------------------------------------------------------------------------
# parse_release — basic cases
# ---------------------------------------------------------------------------


def test_parse_release_show_episode() -> None:
    r = parse_release("Show.Name.S01E01.1080p.WEB-DL.x265-GROUP.mkv")
    assert r.title == "Show Name"
    assert r.type == "episode"
    assert r.seasons == [1]
    assert r.episodes == [1]
    assert r.quality == Quality.fullhd


def test_parse_release_movie() -> None:
    r = parse_release("Movie.Name.2023.2160p.BluRay.mkv")
    assert r.title == "Movie Name"
    assert r.type == "movie"
    assert r.year == 2023
    assert r.quality == Quality.uhd


def test_parse_release_multi_episode() -> None:
    r = parse_release("Show.Name.S01E01E02E03.720p.mkv")
    assert r.seasons == [1]
    assert r.episodes == [1, 2, 3]


def test_parse_release_season_pack() -> None:
    r = parse_release("Show.S02.Complete.720p.mkv")
    assert r.seasons == [2]
    assert r.episodes == []


def test_parse_release_unknown_quality() -> None:
    r = parse_release("Show.S01E01.mkv")
    assert r.quality == Quality.unknown


def test_parse_release_hd_quality() -> None:
    r = parse_release("Show.S01E01.720p.mkv")
    assert r.quality == Quality.hd


def test_parse_release_sd_quality() -> None:
    r = parse_release("Show.S01E01.480p.mkv")
    assert r.quality == Quality.sd


# ---------------------------------------------------------------------------
# match_episode_file
# ---------------------------------------------------------------------------


def test_match_episode_file_sxxexx() -> None:
    assert match_episode_file("Show.S01E01.mkv", 1, 1) is True


def test_match_episode_file_1x01_style() -> None:
    assert match_episode_file("Show.1x01.mkv", 1, 1) is True


def test_match_episode_file_wrong_episode() -> None:
    assert match_episode_file("Show.S01E02.mkv", 1, 1) is False


def test_match_episode_file_wrong_season() -> None:
    assert match_episode_file("Show.S02E01.mkv", 1, 1) is False


def test_match_episode_file_bare_anime_number_without_marker() -> None:
    # Without SxxExx / NxNN markers, absolute-only anime filenames do not match.
    assert match_episode_file("[Group] Show - 13 [1080p].mkv", 1, 13) is False


# ---------------------------------------------------------------------------
# match_special_file — Season 0 title-based matching
# ---------------------------------------------------------------------------


def test_match_special_file_title_overlap() -> None:
    # Title words present in the filename → match (show words carry no signal).
    assert (
        match_special_file(
            "The.Bear.Christmas.Special.1080p.WEB.mkv",
            episode_title="Christmas Special",
            show_name="The Bear",
        )
        is True
    )


def test_match_special_file_distinguishes_specials() -> None:
    # A different special's file must not claim this special (only "special"
    # overlaps → 1/2 < 0.6).
    assert (
        match_special_file(
            "The.Bear.Holiday.Special.1080p.mkv",
            episode_title="Christmas Special",
            show_name="The Bear",
        )
        is False
    )


def test_match_special_file_no_title_needs_lone_file() -> None:
    # Special with no distinguishing title: accept only as the lone candidate.
    assert (
        match_special_file(
            "The.Bear.Special.mkv", episode_title="", show_name="The Bear"
        )
        is False
    )
    assert (
        match_special_file(
            "The.Bear.Special.mkv",
            episode_title="",
            show_name="The Bear",
            accept_lone_file=True,
        )
        is True
    )


# ---------------------------------------------------------------------------
# is_video_file / is_subtitle_file
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("movie.mkv", True),
        ("episode.mp4", True),
        ("clip.avi", True),
        ("subtitle.srt", False),
        ("readme.txt", False),
        ("archive.zip", False),
    ],
)
def test_is_video_file(filename: str, expected: bool) -> None:
    assert is_video_file(Path(filename)) is expected


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("sub.srt", True),
        ("sub.ass", True),
        ("sub.vtt", True),
        ("movie.mkv", False),
        ("doc.txt", False),
    ],
)
def test_is_subtitle_file(filename: str, expected: bool) -> None:
    assert is_subtitle_file(Path(filename)) is expected


# ---------------------------------------------------------------------------
# is_sample_or_extra
# ---------------------------------------------------------------------------


def test_is_sample_or_extra_sample_filename() -> None:
    assert is_sample_or_extra(Path("sample.mkv")) is True


def test_is_sample_or_extra_trailer_filename() -> None:
    assert is_sample_or_extra(Path("trailer.mkv")) is True


def test_is_sample_or_extra_normal_filename() -> None:
    assert is_sample_or_extra(Path("Show.S01E01.mkv")) is False


def test_is_sample_or_extra_extras_subdir() -> None:
    # Files under an "extras" subdirectory are also flagged
    assert is_sample_or_extra(Path("Show/extras/behind-the-scenes.mkv")) is True


# ---------------------------------------------------------------------------
# parse_subtitle_filename
# ---------------------------------------------------------------------------


def test_parse_subtitle_filename_two_char_lang() -> None:
    result = parse_subtitle_filename("Show.S01E01.en.srt")
    assert result is not None
    assert result.language == "en"
    assert result.container == "srt"
    assert result.forced is False
    assert result.sdh is False


def test_parse_subtitle_filename_three_char_lang() -> None:
    result = parse_subtitle_filename("Movie.eng.srt")
    assert result is not None
    assert result.language == "en"


def test_parse_subtitle_filename_forced_flag() -> None:
    result = parse_subtitle_filename("Show.S01E01.forced.srt")
    assert result is not None
    assert result.forced is True


def test_parse_subtitle_filename_not_subtitle_returns_none() -> None:
    assert parse_subtitle_filename("Show.S01E01.mkv") is None


def test_parse_subtitle_filename_txt_returns_none() -> None:
    assert parse_subtitle_filename("notes.txt") is None


def test_parse_subtitle_filename_ass_container() -> None:
    result = parse_subtitle_filename("Show.S01E01.ja.ass")
    assert result is not None
    assert result.container == "ass"
    assert result.language == "ja"


# ---------------------------------------------------------------------------
# normalize_source / normalize_codec — guessit may return a list when a name
# carries several tokens for one property ("WORKPRINT WEB-DL"). Regression for
# the AttributeError that crashed every import of such a release.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("WEB-DL", "web"),
        (["Workprint", "Web"], "web"),  # list shape from multi-source names
        (["Workprint"], ""),  # unknown-only list → ""
        (None, ""),
        ([], ""),
    ],
)
def test_normalize_source_accepts_str_or_list(value, expected) -> None:
    assert normalize_source(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [("x265", "h265"), (["x264", "H.265"], "h264"), (None, "")],
)
def test_normalize_codec_accepts_str_or_list(value, expected) -> None:
    assert normalize_codec(value) == expected


def test_parse_workprint_webdl_does_not_crash_normalizers() -> None:
    """The exact production release that crashed import: source is a list."""
    r = parse_release(
        "The Amazing Digital Circus The Last Act 2026 1080p WORKPRiNT WEB-DL x264-DK"
    )
    assert isinstance(r.source, list)  # guessit shape we must tolerate
    assert normalize_source(r.source) == "web"
    assert normalize_codec(r.video_codec) == "h264"
