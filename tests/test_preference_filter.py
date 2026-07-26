"""DB-free unit tests for shared tri-state preference list filtering."""

from __future__ import annotations

import pytest

from miramedia.media_preferences import filter_enabled_preferences

ENABLED = {"1080p", "720p", "x265"}


@pytest.mark.parametrize(
    ("current", "expected"),
    [
        (None, None),
        ([], []),
        (["1080p", "720p"], ["1080p", "720p"]),
        (["1080p", "4K"], ["1080p"]),
        (["4K", "2160p"], None),
        (["1080p", "1080p", "4K"], ["1080p", "1080p"]),
        (["unknown", "4K"], None),
    ],
    ids=[
        "none_passthrough",
        "empty_list_passthrough",
        "all_enabled_order_preserved",
        "some_disabled_filtered",
        "none_survive_fallback_to_none",
        "duplicates_and_partial_filter",
        "unknown_values_filtered_out",
    ],
)
def test_filter_enabled_preferences(
    current: list[str] | None, expected: list[str] | None
) -> None:
    assert filter_enabled_preferences(current, ENABLED) == expected
