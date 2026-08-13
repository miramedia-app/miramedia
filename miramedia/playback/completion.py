"""Pure helpers for playback completion and noise-floor policy."""

_NOISE_FLOOR_MS = 5_000
_COMPLETION_TAIL_MS = 30_000


def completion_threshold_ms(duration_ms: int) -> int:
    """Return the position at or above which a title counts as completed."""
    ninety_percent = int(0.90 * duration_ms)
    return max(duration_ms - _COMPLETION_TAIL_MS, ninety_percent)


def is_completed(position_ms: int, duration_ms: int) -> bool:
    return position_ms >= completion_threshold_ms(duration_ms)


def below_noise_floor(position_ms: int, *, completed: bool) -> bool:
    return position_ms < _NOISE_FLOOR_MS and not completed
