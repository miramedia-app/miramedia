"""Acceptance tests for webhook destination policy (D1-D11)."""

from __future__ import annotations

import socket

import pytest

from miramedia.notifications.destination_policy import (
    DestinationDenyReason,
    max_webhook_redirects,
    validate_redirect_chain,
    validate_resolved_address,
    validate_webhook_url,
)


def _dns(ip: str):
    def resolver(host: str, port: int | None) -> list:  # noqa: ARG001
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                0,
                "",
                (ip, port or 0),
            )
        ]

    return resolver


def test_d1_rejects_non_https_scheme_by_default() -> None:
    d = validate_webhook_url("ftp://example.invalid/hook", resolver=_dns("1.1.1.1"))
    assert not d.allowed
    assert d.reason == DestinationDenyReason.SCHEME


def test_d2_rejects_missing_host() -> None:
    d = validate_webhook_url("https:///path", resolver=_dns("1.1.1.1"))
    assert not d.allowed
    assert d.reason == DestinationDenyReason.HOST


def test_d3_rejects_userinfo() -> None:
    d = validate_webhook_url(
        "https://user:pass@example.invalid/hook", resolver=_dns("1.1.1.1")
    )
    assert not d.allowed
    assert d.reason == DestinationDenyReason.USERINFO


def test_d4_default_denies_rfc1918_resolution() -> None:
    d = validate_webhook_url(
        "https://ha.example.invalid/api/webhook/x",
        resolver=_dns("192.168.1.50"),
    )
    assert not d.allowed
    assert d.reason == DestinationDenyReason.PRIVATE


def test_d5_default_denies_loopback_literal() -> None:
    d = validate_webhook_url("https://127.0.0.1/hook")
    assert not d.allowed
    assert d.reason == DestinationDenyReason.LOOPBACK


def test_d6_always_denies_link_local() -> None:
    for allow in (False, True):
        d = validate_resolved_address("169.254.169.254", allow_private_network=allow)
        assert not d.allowed
        assert d.reason == DestinationDenyReason.LINK_LOCAL


def test_d7_opt_in_allows_rfc1918_and_loopback() -> None:
    private = validate_webhook_url(
        "https://ha.example.invalid/api/webhook/x",
        allow_private_network=True,
        resolver=_dns("192.168.1.50"),
    )
    assert private.allowed
    loop = validate_webhook_url(
        "https://127.0.0.1:8123/api/webhook/x",
        allow_private_network=True,
    )
    assert loop.allowed


def test_d8_opt_in_allows_http() -> None:
    d = validate_webhook_url(
        "http://ha.example.invalid:8123/api/webhook/x",
        allow_private_network=True,
        allow_insecure_transport=True,
        resolver=_dns("10.0.0.5"),
    )
    assert d.allowed


def test_d8b_private_network_does_not_waive_https() -> None:
    d = validate_webhook_url(
        "http://ha.example.invalid:8123/api/webhook/x",
        allow_private_network=True,
        resolver=_dns("192.168.1.50"),
    )
    assert not d.allowed
    assert d.reason == DestinationDenyReason.SCHEME


def test_d8c_insecure_transport_allows_http_private() -> None:
    d = validate_webhook_url(
        "http://ha.example.invalid/api/webhook/x",
        allow_private_network=True,
        allow_insecure_transport=True,
        resolver=_dns("192.168.1.50"),
    )
    assert d.allowed


def test_d8d_insecure_transport_allows_http_public() -> None:
    d = validate_webhook_url(
        "http://example.invalid/hook",
        allow_insecure_transport=True,
        resolver=_dns("1.1.1.1"),
    )
    assert d.allowed


def test_d9_default_denies_http() -> None:
    d = validate_webhook_url(
        "http://example.invalid/hook",
        resolver=_dns("1.1.1.1"),
    )
    assert not d.allowed
    assert d.reason == DestinationDenyReason.SCHEME


def test_d10_redirect_to_denied_class() -> None:
    d = validate_redirect_chain(
        [
            "https://example.invalid/hook",
            "https://169.254.169.254/",
        ],
        resolver=_dns("1.1.1.1"),
    )
    assert not d.allowed
    assert d.reason == DestinationDenyReason.LINK_LOCAL


def test_d11_redirect_limit() -> None:
    urls = ["https://example.invalid/h"] + [
        f"https://example.invalid/h{i}" for i in range(max_webhook_redirects() + 1)
    ]
    d = validate_redirect_chain(urls, resolver=_dns("8.8.8.8"))
    assert not d.allowed
    assert d.reason == DestinationDenyReason.REDIRECT_LIMIT


@pytest.mark.parametrize(
    ("ip", "allow", "ok"),
    [
        ("8.8.8.8", False, True),
        ("10.0.0.1", False, False),
        ("10.0.0.1", True, True),
        ("100.64.1.1", False, False),
        ("100.64.1.1", True, True),
        ("::1", False, False),
        ("::1", True, True),
        ("fe80::1", True, False),
        ("2002:0a00:0001::1", False, False),
        ("64:ff9b::7f00:1", False, False),
        ("2002:5db8:d822::1", False, True),
    ],
)
def test_address_matrix(ip: str, allow: bool, ok: bool) -> None:
    d = validate_resolved_address(ip, allow_private_network=allow)
    assert d.allowed is ok
