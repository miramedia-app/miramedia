"""Unit tests for thread-safe indexer mirror preference ordering."""

from __future__ import annotations

import threading
from collections.abc import Iterable

import pytest

from miramedia.indexers.mirrors import MirrorPreference, is_allowed_mirror_origin

_MIRRORS = ("https://a.example", "https://b.example", "https://c.example")


def test_construction_preserves_first_occurrence_deduping() -> None:
    pref = MirrorPreference(
        [
            "https://a.example",
            "https://b.example",
            "https://a.example",
            "https://c.example",
            "https://b.example",
        ]
    )

    assert pref.ordered() == _MIRRORS


def test_construction_rejects_empty_mirror_list() -> None:
    with pytest.raises(ValueError, match="empty"):
        MirrorPreference([])


def test_ordered_returns_initial_order_before_success() -> None:
    pref = MirrorPreference(_MIRRORS)

    assert pref.ordered() == _MIRRORS


def test_mark_success_moves_known_mirror_to_front_preserving_others() -> None:
    pref = MirrorPreference(_MIRRORS)

    pref.mark_success("https://c.example")

    assert pref.ordered() == (
        "https://c.example",
        "https://a.example",
        "https://b.example",
    )


def test_mark_success_is_idempotent_for_current_preference() -> None:
    pref = MirrorPreference(_MIRRORS)

    pref.mark_success("https://b.example")
    first_snapshot = pref.ordered()
    pref.mark_success("https://b.example")

    assert (
        pref.ordered()
        == first_snapshot
        == (
            "https://b.example",
            "https://a.example",
            "https://c.example",
        )
    )


def test_mark_success_ignores_unknown_mirror() -> None:
    pref = MirrorPreference(_MIRRORS)

    pref.mark_success("https://evil.example")

    assert pref.ordered() == _MIRRORS


@pytest.mark.parametrize("mirrors", [_MIRRORS, ("solo.example",)])
def test_every_ordered_snapshot_contains_each_mirror_exactly_once(
    mirrors: Iterable[str],
) -> None:
    pref = MirrorPreference(mirrors)
    expected = tuple(dict.fromkeys(mirrors))

    for mirror in expected:
        pref.mark_success(mirror)
        snapshot = pref.ordered()
        assert len(snapshot) == len(expected)
        assert set(snapshot) == set(expected)
        assert len(set(snapshot)) == len(snapshot)


def test_concurrent_ordered_and_mark_success_never_corrupts_snapshots() -> None:
    pref = MirrorPreference(_MIRRORS)
    worker_count = 8
    iterations = 200
    start_barrier = threading.Barrier(worker_count)
    snapshots: list[tuple[str, ...]] = []
    errors: list[str] = []
    lock = threading.Lock()

    def worker(mirror: str) -> None:
        try:
            start_barrier.wait(timeout=5)
            for _ in range(iterations):
                with lock:
                    snapshots.append(pref.ordered())
                pref.mark_success(mirror)
        except Exception as exc:  # pragma: no cover - surfaced via errors list
            errors.append(f"{mirror}: {exc!r}")

    threads = [
        threading.Thread(
            target=worker,
            args=(mirror,),
            name=f"mirror-pref-{index}",
        )
        for index, mirror in enumerate(_MIRRORS * 2 + _MIRRORS[:2])  # 8 workers
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive(), f"{thread.name} did not finish"

    assert errors == []

    for snapshot in snapshots:
        assert len(snapshot) == len(_MIRRORS)
        assert set(snapshot) == set(_MIRRORS)
        assert snapshot[0] in _MIRRORS

    pref.mark_success("https://b.example")
    assert pref.ordered()[0] == "https://b.example"


@pytest.mark.parametrize(
    ("origin", "mirrors", "expected"),
    [
        ("https://a.example", _MIRRORS, True),
        ("http://a.example", _MIRRORS, False),
        ("https://evil.example", _MIRRORS, False),
        ("https://a.example/", ("https://a.example/",), True),
    ],
)
def test_is_allowed_mirror_origin(
    origin: str,
    mirrors: tuple[str, ...],
    expected: bool,
) -> None:
    assert is_allowed_mirror_origin(origin, mirrors) is expected
