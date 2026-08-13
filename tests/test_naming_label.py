"""Unit tests for shared episode display-label formatting."""

from __future__ import annotations

from miramedia.naming import format_episode_label


def test_format_episode_label_happy_path_zero_padding() -> None:
    assert (
        format_episode_label("Severance", 2, 3, "Who Is Alive?")
        == "Severance - S02E03 - Who Is Alive?"
    )


def test_format_episode_label_missing_title() -> None:
    assert format_episode_label("Show", 1, 1, None) == "Show - S01E01"


def test_format_episode_label_blank_title() -> None:
    assert format_episode_label("Show", 1, 1, "  ") == "Show - S01E01"


def test_format_episode_label_specials_season_zero() -> None:
    assert format_episode_label("Show", 0, 5, "Special") == "Show - S00E05 - Special"


def test_format_episode_label_double_digit_rollover() -> None:
    assert format_episode_label("Show", 12, 104, "Finale") == "Show - S12E104 - Finale"


def test_format_episode_label_custom_separator() -> None:
    assert (
        format_episode_label("Show", 1, 1, "Pilot", separator=" · ")
        == "Show · S01E01 · Pilot"
    )
