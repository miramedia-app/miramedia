"""Unit tests for the pure mirror-state logic (seeded vs user rules)."""

from __future__ import annotations

import pytest

from miramedia.indexers.mirror_state import (
    MirrorRuleError,
    apply_user_update,
    derive_available_urls,
    load_entries,
    mirrors_from_urls,
    reconcile_seeded,
)
from miramedia.indexers.schemas import MirrorEntry


def _m(url: str, *, enabled: bool = True, source: str = "seeded") -> MirrorEntry:
    return MirrorEntry(url=url, enabled=enabled, source=source)  # type: ignore[arg-type]


def test_derive_available_urls_keeps_enabled_in_order_deduped() -> None:
    mirrors = [
        _m("https://a"),
        _m("https://b", enabled=False),
        _m("https://c"),
        _m("https://a"),
    ]
    assert derive_available_urls(mirrors) == ["https://a", "https://c"]


def test_mirrors_from_urls_puts_active_first_all_enabled() -> None:
    mirrors = mirrors_from_urls(["https://b", "https://a"], "https://a", source="user")
    assert [m.url for m in mirrors] == ["https://a", "https://b"]
    assert all(m.enabled and m.source == "user" for m in mirrors)


def test_load_entries_backfills_from_available_urls() -> None:
    entries = load_entries(None, ["https://a", "https://b"], "https://a")
    assert [(m.url, m.source) for m in entries] == [
        ("https://a", "user"),
        ("https://b", "user"),
    ]


def test_reconcile_appends_new_seeded_and_preserves_disable_and_order() -> None:
    existing = [
        _m("https://b", enabled=False),  # user disabled + reordered
        _m("https://a"),
    ]
    result = reconcile_seeded(existing, ["https://a", "https://b", "https://c"])

    # order + enabled preserved for existing; new seeded appended enabled
    assert [(m.url, m.enabled, m.source) for m in result] == [
        ("https://b", False, "seeded"),
        ("https://a", True, "seeded"),
        ("https://c", True, "seeded"),
    ]
    # a disabled seeded mirror stays out of the live list
    assert derive_available_urls(result) == ["https://a", "https://c"]


def test_reconcile_reclassifies_dropped_code_mirror_as_user() -> None:
    existing = [_m("https://a"), _m("https://gone", source="seeded")]
    result = reconcile_seeded(existing, ["https://a"])
    by_url = {m.url: m for m in result}
    # dropped from code seed -> becomes deletable user mirror
    assert by_url["https://gone"].source == "user"


def test_apply_update_reorders_and_disables_seeded() -> None:
    existing = [_m("https://a"), _m("https://b")]
    incoming = [_m("https://b", enabled=False), _m("https://a")]
    result = apply_user_update(existing, incoming, "https://a")
    assert [(m.url, m.enabled) for m in result] == [
        ("https://b", False),
        ("https://a", True),
    ]


def test_apply_update_rejects_deleting_seeded() -> None:
    existing = [_m("https://a"), _m("https://b")]
    incoming = [_m("https://a")]  # dropped seeded b
    with pytest.raises(MirrorRuleError, match="seeded"):
        apply_user_update(existing, incoming, "https://a")


def test_apply_update_allows_deleting_user_mirror() -> None:
    existing = [_m("https://a"), _m("https://custom", source="user")]
    incoming = [_m("https://a")]
    result = apply_user_update(existing, incoming, "https://a")
    assert [m.url for m in result] == ["https://a"]


def test_apply_update_cannot_relabel_seeded_as_user_to_delete() -> None:
    existing = [_m("https://a"), _m("https://b", source="seeded")]
    # client lies that b is a user mirror, then drops it next call — but source
    # is authoritative from storage, so b stays seeded and dropping it fails.
    relabelled = apply_user_update(
        existing, [_m("https://a"), _m("https://b", source="user")], "https://a"
    )
    assert {m.url: m.source for m in relabelled}["https://b"] == "seeded"


def test_apply_update_requires_active_present_and_enabled() -> None:
    existing = [_m("https://a"), _m("https://b")]
    with pytest.raises(MirrorRuleError, match="active"):
        apply_user_update(
            existing, [_m("https://a", enabled=False), _m("https://b")], "https://a"
        )
