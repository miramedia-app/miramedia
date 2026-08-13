"""Unit tests for playback completion helpers."""

from miramedia.playback.completion import (
    below_noise_floor,
    completion_threshold_ms,
    is_completed,
)


def test_completion_threshold_uses_ninety_percent_when_larger_than_tail() -> None:
    duration_ms = 100_000
    assert completion_threshold_ms(duration_ms) == 90_000


def test_completion_threshold_uses_tail_when_larger_than_ninety_percent() -> None:
    duration_ms = 400_000
    assert completion_threshold_ms(duration_ms) == 370_000


def test_is_completed_at_threshold() -> None:
    duration_ms = 100_000
    threshold = completion_threshold_ms(duration_ms)
    assert is_completed(threshold, duration_ms)
    assert not is_completed(threshold - 1, duration_ms)


def test_seek_back_clears_completed() -> None:
    duration_ms = 100_000
    assert is_completed(95_000, duration_ms)
    assert not is_completed(10_000, duration_ms)


def test_below_noise_floor_ignores_early_positions() -> None:
    assert below_noise_floor(4_999, completed=False)
    assert not below_noise_floor(5_000, completed=False)
    assert not below_noise_floor(1_000, completed=True)
