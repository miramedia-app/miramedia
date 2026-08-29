"""Prometheus counters for viewing-sync dry-run observation."""

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
        "viewing_sync_items_seen": Counter(
            "viewing_sync_items_seen", "Jellyfin items with play signal"
        ),
        "viewing_sync_unique_matches": Counter(
            "viewing_sync_unique_matches", "Uniquely matched viewing-sync items"
        ),
        "viewing_sync_quarantined": Counter(
            "viewing_sync_quarantined", "Quarantined viewing-sync items"
        ),
        "viewing_sync_poll_errors": Counter(
            "viewing_sync_poll_errors", "Viewing-sync dry-run poll failures"
        ),
        "viewing_sync_users_missing": Counter(
            "viewing_sync_users_missing",
            "Mapped Jellyfin users absent from /Users snapshot",
        ),
        "viewing_sync_user_fetch_errors": Counter(
            "viewing_sync_user_fetch_errors",
            "Per-user Jellyfin item fetch failures",
        ),
    }
    return _COUNTERS


def inc(name: str, amount: int = 1) -> None:
    counters = _get_counters()
    counter = counters.get(name)
    if counter is not None:
        counter.inc(amount)
