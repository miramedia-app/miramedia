"""Characterization tests for Cloudflare bypass pure state logic.

Pins current behavior of the circuit breaker, challenge classifier,
cookie/domain matching, and cache refresh — no browser or network.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from miramedia.cloudflare.bypass import (
    CloudflareBypass,
    is_cloudflare_challenge,
)
from miramedia.cloudflare.config import CloudflareConfig


def _response(
    *,
    status_code: int = 200,
    headers: dict[str, str] | None = None,
    text: str = "",
) -> SimpleNamespace:
    return SimpleNamespace(
        status_code=status_code,
        headers=headers or {},
        text=text,
    )


@pytest.fixture
def bypass() -> CloudflareBypass:
    """Bypass instance with a small breaker threshold for fast tests."""
    with patch.dict(
        "os.environ",
        {
            "MIRAMEDIA_BYPASS_BREAKER_THRESHOLD": "3",
            "MIRAMEDIA_BYPASS_BREAKER_COOLDOWN": "600",
        },
    ):
        return CloudflareBypass(CloudflareConfig(cookie_ttl_seconds=1800))


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------


def test_breaker_closed_for_fresh_domain(bypass: CloudflareBypass) -> None:
    assert bypass._breaker_is_open("indexer.example") is False


def test_breaker_opens_after_threshold_failures(bypass: CloudflareBypass) -> None:
    domain = "fail.example"
    assert bypass._breaker_threshold == 3

    with patch("miramedia.cloudflare.bypass.time.monotonic", return_value=1000.0):
        bypass._breaker_record_failure(domain)
        assert bypass._breaker_is_open(domain) is False
        bypass._breaker_record_failure(domain)
        assert bypass._breaker_is_open(domain) is False
        bypass._breaker_record_failure(domain)
        assert bypass._breaker_is_open(domain) is True


def test_breaker_success_resets_failure_count_and_closes(
    bypass: CloudflareBypass,
) -> None:
    domain = "recover.example"

    with patch("miramedia.cloudflare.bypass.time.monotonic", return_value=500.0):
        for _ in range(bypass._breaker_threshold):
            bypass._breaker_record_failure(domain)
        assert bypass._breaker_is_open(domain) is True

        bypass._breaker_record_success(domain)
        assert bypass._breaker_is_open(domain) is False
        assert domain not in bypass._domain_fail_count
        assert domain not in bypass._domain_open_until


def test_breaker_half_open_after_cooldown_elapses(bypass: CloudflareBypass) -> None:
    domain = "cooldown.example"
    opened_at = 2000.0
    cooldown = bypass._breaker_cooldown

    with patch("miramedia.cloudflare.bypass.time.monotonic", return_value=opened_at):
        for _ in range(bypass._breaker_threshold):
            bypass._breaker_record_failure(domain)
        assert bypass._breaker_is_open(domain) is True

    # Still inside cooldown window.
    with patch(
        "miramedia.cloudflare.bypass.time.monotonic",
        return_value=opened_at + cooldown - 1,
    ):
        assert bypass._breaker_is_open(domain) is True

    # Cooldown elapsed → half-open probe allowed; open-until entry cleared.
    with patch(
        "miramedia.cloudflare.bypass.time.monotonic",
        return_value=opened_at + cooldown,
    ):
        assert bypass._breaker_is_open(domain) is False
        assert domain not in bypass._domain_open_until


# ---------------------------------------------------------------------------
# Challenge classification
# ---------------------------------------------------------------------------


def test_is_cloudflare_challenge_cf_mitigated_header() -> None:
    resp = _response(
        status_code=403,
        headers={"cf-mitigated": "challenge"},
        text="<html>blocked</html>",
    )
    assert is_cloudflare_challenge(resp) is True
    assert CloudflareBypass.is_cloudflare_challenge(resp) is True


def test_is_cloudflare_challenge_turnstile_body_markers() -> None:
    body = (
        "<html><head><title>Just a moment...</title></head>"
        "<body><div id='cf-browser-verification'>"
        "<script src='/challenge-platform/h/g/orchestrate/chl_page/v1'></script>"
        "</div></body></html>"
    )
    resp = _response(
        status_code=503,
        headers={"server": "cloudflare"},
        text=body,
    )
    assert is_cloudflare_challenge(resp) is True


def test_is_cloudflare_challenge_ordinary_search_results() -> None:
    body = (
        "<html><body><h1>Search results</h1>"
        "<table><tr><td>Torrent A</td><td>5.2 GB</td></tr></table>"
        "</body></html>"
    )
    assert is_cloudflare_challenge(_response(status_code=200, text=body)) is False
    assert (
        is_cloudflare_challenge(
            _response(
                status_code=403,
                headers={"server": "nginx"},
                text=body,
            )
        )
        is False
    )


def test_is_cloudflare_challenge_blank_or_markerless_cloudflare_body() -> None:
    # Cloudflare server but no challenge markers in an empty body → not a challenge.
    resp = _response(
        status_code=503,
        headers={"server": "cloudflare"},
        text="",
    )
    assert is_cloudflare_challenge(resp) is False


def test_is_blank_body_characterizes_empty_body() -> None:
    assert CloudflareBypass._is_blank_body("<html><body></body></html>") is True
    assert (
        CloudflareBypass._is_blank_body("<html><body>   <span></span>  </body></html>")
        is True
    )
    assert (
        CloudflareBypass._is_blank_body(
            "<html><body><p>Search results for ubuntu</p></body></html>"
        )
        is False
    )
    # No <body> tag → conservative False (characterizes current behavior).
    assert CloudflareBypass._is_blank_body("<html><head></head></html>") is False


# ---------------------------------------------------------------------------
# Cookie / domain matching
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("cookie_domain", "domain_suffix", "expected"),
    [
        ("example.com", "example.com", True),
        ("example.com", "sub.example.com", True),
        ("sub.example.com", "example.com", True),
        (".example.com", "sub.example.com", True),
        ("other.com", "example.com", False),
        ("", "example.com", True),  # host-only cookie matches page domain
    ],
)
def test_cookie_domain_matches(
    cookie_domain: str,
    domain_suffix: str,
    expected: bool,
) -> None:
    assert (
        CloudflareBypass._cookie_domain_matches(cookie_domain, domain_suffix)
        is expected
    )


def test_domain_of_normalizes_host_and_port() -> None:
    assert (
        CloudflareBypass._domain_of("https://Indexer.Example.com/search?q=linux")
        == "indexer.example.com"
    )
    assert (
        CloudflareBypass._domain_of("https://indexer.example.com:8443/rss")
        == "indexer.example.com:8443"
    )


# ---------------------------------------------------------------------------
# Cache refresh from cookies
# ---------------------------------------------------------------------------


def test_refresh_cache_from_cookies_ignores_non_cf_cookies(
    bypass: CloudflareBypass,
) -> None:
    domain = "cache.example"
    bypass.refresh_cache_from_cookies(
        domain,
        {"session_id": "abc", "uid": "42"},
        user_agent="Mozilla/5.0",
    )
    assert bypass.get_cached_session(domain) is None


def test_refresh_cache_from_cookies_creates_entry(bypass: CloudflareBypass) -> None:
    domain = "cache.example"
    now = 10_000.0

    with patch("miramedia.cloudflare.bypass.time.monotonic", return_value=now):
        bypass.refresh_cache_from_cookies(
            domain,
            {"cf_clearance": "token123", "ignored": "x"},
            user_agent="Mozilla/5.0 Test",
        )
        cached = bypass.get_cached_session(domain)
        assert cached is not None
        assert cached.cookies == {"cf_clearance": "token123"}
        assert cached.user_agent == "Mozilla/5.0 Test"
        assert cached.expires_at == now + bypass.config.cookie_ttl_seconds


def test_refresh_cache_from_cookies_merges_onto_existing(
    bypass: CloudflareBypass,
) -> None:
    domain = "merge.example"
    t0, t1 = 5000.0, 5001.0

    with patch("miramedia.cloudflare.bypass.time.monotonic", return_value=t0):
        bypass.refresh_cache_from_cookies(
            domain,
            {"cf_clearance": "old-clearance"},
            user_agent="UA/1.0",
        )

    with patch("miramedia.cloudflare.bypass.time.monotonic", return_value=t1):
        bypass.refresh_cache_from_cookies(
            domain,
            {"__cf_bm": "rotated-bm"},
            user_agent=None,
        )
        cached = bypass.get_cached_session(domain)
        assert cached is not None
        assert cached.cookies == {
            "cf_clearance": "old-clearance",
            "__cf_bm": "rotated-bm",
        }
        # characterizes current behavior: None user_agent preserves existing UA
        assert cached.user_agent == "UA/1.0"
        assert cached.expires_at == t1 + bypass.config.cookie_ttl_seconds
