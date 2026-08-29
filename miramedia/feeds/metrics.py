"""Prometheus counters for feed observation (design 385 §7)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from prometheus_client import Counter

_COUNTERS: dict[str, Counter] | None = None


def _get_counters() -> dict[str, Counter]:
    global _COUNTERS
    if _COUNTERS is not None:
        return _COUNTERS
    try:
        from prometheus_client import Counter
    except ImportError:
        _COUNTERS = {}
        return _COUNTERS
    _COUNTERS = {
        "feed_items_seen": Counter("feed_items_seen", "Parsed feed items"),
        "feed_items_unmatched": Counter("feed_items_unmatched", "Unmatched feed items"),
        "feed_would_grab": Counter("feed_would_grab", "Feed items that would grab"),
        "feed_poll_errors": Counter("feed_poll_errors", "Feed polls that held cursor"),
    }
    return _COUNTERS


def inc(name: str, amount: int = 1) -> None:
    counters = _get_counters()
    counter = counters.get(name)
    if counter is not None:
        counter.inc(amount)
