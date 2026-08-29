"""Table tests for semver helpers in miramedia.updates.service."""

from __future__ import annotations

import pytest

from miramedia.updates.service import compare_semver, is_semver

_IS_SEMVER_TRUE = [
    "1.2.3",
    "v1.2.3",
    "1.2.3-rc.1",
    "1.2.3+build5",
]

_IS_SEMVER_FALSE: list[str | None] = [
    None,
    "",
    "1.2",
    "latest",
    "1.2.3.4",
]

_COMPARE_SEMVER = [
    ("1.0.10", "1.0.9", 1),
    ("1.0.9", "1.0.10", -1),
    ("1.2.3", "1.2.3", 0),
    ("v1.2.3", "1.2.3", 0),
    ("1.2.3", "1.2.3-rc.1", 1),
    ("1.2.3-rc.1", "1.2.3-rc.2", -1),
    ("2.0.0", "1.99.99", 1),
    ("1.2.3+b1", "1.2.3+b2", 0),
    ("1.2.3-rc.9", "1.2.3-rc.10", -1),
    ("1.0.0-alpha", "1.0.0-alpha.1", -1),
    ("1.0.0-alpha.1", "1.0.0-alpha.beta", -1),
    ("1.0.0-alpha.beta", "1.0.0-beta", -1),
    ("1.0.0-beta", "1.0.0-beta.2", -1),
    ("1.0.0-beta.2", "1.0.0-beta.11", -1),
    ("1.0.0-beta.11", "1.0.0-rc.1", -1),
    ("1.0.0-rc.1", "1.0.0", -1),
    ("1.0.0-alpha", "1.0.0-alpha", 0),
    ("1.0.0-1", "1.0.0-alpha", -1),
    ("1.0.0-alpha", "1.0.0-1", 1),
]

_COMPARE_FALLBACK = [
    ("latest", "latest", 0),
    ("build-10", "build-9", 1),
    ("abc", "abd", -1),
]


@pytest.mark.parametrize("version", _IS_SEMVER_TRUE)
def test_is_semver_true(version: str) -> None:
    assert is_semver(version) is True


@pytest.mark.parametrize("version", _IS_SEMVER_FALSE)
def test_is_semver_false(version: str | None) -> None:
    assert is_semver(version) is False


@pytest.mark.parametrize(("a", "b", "expected"), _COMPARE_SEMVER)
def test_compare_semver(a: str, b: str, expected: int) -> None:
    assert compare_semver(a, b) == expected


@pytest.mark.parametrize(("a", "b", "expected"), _COMPARE_FALLBACK)
def test_compare_semver_fallback(a: str, b: str, expected: int) -> None:
    assert compare_semver(a, b) == expected


def test_compare_semver_fallback_mixed_digit_str_no_raise() -> None:
    """Non-semver inputs with mixed digit/str positions must not raise."""
    assert compare_semver("v1", "v2") == -1
    assert compare_semver("item2", "item10") == -1
