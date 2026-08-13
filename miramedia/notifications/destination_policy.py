"""Outbound webhook destination policy (pure validation; no network I/O)."""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlparse

# getaddrinfo-compatible callable for tests.
AddrInfoResolver = Callable[..., list]


class DestinationDenyReason(StrEnum):
    SCHEME = "scheme"
    HOST = "host"
    USERINFO = "userinfo"
    PRIVATE = "private"
    LOOPBACK = "loopback"
    LINK_LOCAL = "link_local"
    MULTICAST = "multicast"
    UNSPECIFIED = "unspecified"
    RESOLVE = "resolve"
    REDIRECT_LIMIT = "redirect_limit"


@dataclass(frozen=True, slots=True)
class DestinationDecision:
    allowed: bool
    reason: DestinationDenyReason | None = None
    detail: str = ""


_MAX_REDIRECTS = 3


def max_webhook_redirects() -> int:
    return _MAX_REDIRECTS


def _is_cgnat(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if not isinstance(addr, ipaddress.IPv4Address):
        return False
    return addr in ipaddress.ip_network("100.64.0.0/10")


def _classify_ip(
    addr: ipaddress.IPv4Address | ipaddress.IPv6Address,
    *,
    allow_private_network: bool,
) -> DestinationDenyReason | None:
    if addr.is_unspecified:
        return DestinationDenyReason.UNSPECIFIED
    if addr.is_multicast:
        return DestinationDenyReason.MULTICAST
    if addr.is_link_local:
        return DestinationDenyReason.LINK_LOCAL
    if addr.is_loopback:
        return None if allow_private_network else DestinationDenyReason.LOOPBACK
    # is_private covers RFC1918 + IPv6 ULA; also treat CGNAT as private.
    if addr.is_private or _is_cgnat(addr):
        return None if allow_private_network else DestinationDenyReason.PRIVATE
    if not addr.is_global:
        return DestinationDenyReason.PRIVATE
    return None


def validate_resolved_address(
    ip_text: str, *, allow_private_network: bool
) -> DestinationDecision:
    try:
        addr = ipaddress.ip_address(ip_text)
    except ValueError:
        return DestinationDecision(False, DestinationDenyReason.RESOLVE, "invalid_ip")
    # Unwrap IPv6 transition forms so classification sees embedded IPv4.
    if isinstance(addr, ipaddress.IPv6Address):
        if addr.ipv4_mapped is not None:
            addr = addr.ipv4_mapped
        elif addr.sixtofour is not None:
            addr = addr.sixtofour
        elif addr in ipaddress.ip_network("64:ff9b::/96"):
            addr = ipaddress.IPv4Address(int(addr) & 0xFFFFFFFF)
    reason = _classify_ip(addr, allow_private_network=allow_private_network)
    if reason is not None:
        return DestinationDecision(False, reason, str(addr))
    return DestinationDecision(True)


def validate_webhook_url(
    url: str,
    *,
    allow_private_network: bool = False,
    allow_insecure_transport: bool = False,
    resolver: AddrInfoResolver | None = None,
) -> DestinationDecision:
    """Validate scheme/host/userinfo and every resolved address.

    ``resolver`` defaults to ``socket.getaddrinfo``. Inject a stub in tests.
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return DestinationDecision(False, DestinationDenyReason.HOST, "parse_error")

    allowed_schemes = {"http", "https"} if allow_insecure_transport else {"https"}
    if parsed.scheme not in allowed_schemes:
        return DestinationDecision(False, DestinationDenyReason.SCHEME, parsed.scheme)
    if parsed.username is not None or parsed.password is not None:
        return DestinationDecision(False, DestinationDenyReason.USERINFO)
    hostname = parsed.hostname
    if not hostname:
        return DestinationDecision(False, DestinationDenyReason.HOST, "missing")

    # Literal IP in the URL — classify directly (still deny link-local always).
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        literal_ip = False
    else:
        literal_ip = True
    if literal_ip:
        return validate_resolved_address(
            hostname, allow_private_network=allow_private_network
        )

    resolve = resolver or socket.getaddrinfo
    try:
        infos = resolve(hostname, parsed.port or None)
    except OSError as exc:
        return DestinationDecision(False, DestinationDenyReason.RESOLVE, str(exc))
    if not infos:
        return DestinationDecision(False, DestinationDenyReason.RESOLVE, "empty")

    for info in infos:
        ip_text = str(info[4][0])
        decision = validate_resolved_address(
            ip_text, allow_private_network=allow_private_network
        )
        if not decision.allowed:
            return decision
    return DestinationDecision(True)


def resolve_webhook_host(
    url: str,
    *,
    allow_private_network: bool = False,
    allow_insecure_transport: bool = False,
    resolver: AddrInfoResolver | None = None,
) -> tuple[str, str]:
    """Validate *url* and return ``(hostname, pinned_ip)`` for DNS pinning."""
    decision = validate_webhook_url(
        url,
        allow_private_network=allow_private_network,
        allow_insecure_transport=allow_insecure_transport,
        resolver=resolver,
    )
    if not decision.allowed:
        msg = f"Webhook destination denied: {decision.reason}"
        raise ValueError(msg)

    parsed = urlparse(url)
    hostname = parsed.hostname
    if not hostname:
        msg = "Webhook URL is missing a hostname"
        raise ValueError(msg)

    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        return hostname, hostname

    resolve = resolver or socket.getaddrinfo
    try:
        infos = resolve(hostname, parsed.port or None)
    except OSError as exc:
        msg = f"Could not resolve webhook host {hostname!r}"
        raise ValueError(msg) from exc

    pinned_ip: str | None = None
    for info in infos:
        ip_text = str(info[4][0])
        addr_decision = validate_resolved_address(
            ip_text, allow_private_network=allow_private_network
        )
        if not addr_decision.allowed:
            msg = (
                f"Blocked resolved address for webhook host {hostname!r}: "
                f"{addr_decision.reason}"
            )
            raise ValueError(msg)
        if pinned_ip is None:
            pinned_ip = ip_text

    if pinned_ip is None:
        msg = f"No addresses resolved for webhook host {hostname!r}"
        raise ValueError(msg)
    return hostname, pinned_ip


def validate_redirect_chain(
    urls: list[str],
    *,
    allow_private_network: bool = False,
    allow_insecure_transport: bool = False,
    resolver: AddrInfoResolver | None = None,
) -> DestinationDecision:
    """Validate an ordered redirect chain including the initial URL.

    Length must be ≤ max_webhook_redirects()+1 (initial + redirects).
    """
    if len(urls) == 0:
        return DestinationDecision(False, DestinationDenyReason.HOST, "empty_chain")
    if len(urls) > _MAX_REDIRECTS + 1:
        return DestinationDecision(
            False, DestinationDenyReason.REDIRECT_LIMIT, str(len(urls))
        )
    for url in urls:
        decision = validate_webhook_url(
            url,
            allow_private_network=allow_private_network,
            allow_insecure_transport=allow_insecure_transport,
            resolver=resolver,
        )
        if not decision.allowed:
            return decision
    return DestinationDecision(True)
