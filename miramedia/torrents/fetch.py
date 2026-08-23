import ipaddress
import logging
import socket
from collections.abc import Iterator
from contextlib import contextmanager
from urllib.parse import urljoin, urlparse

import libtorrent
import requests

from miramedia.config import MiraMediaConfig

log = logging.getLogger(__name__)

_MAX_TORRENT_PAYLOAD_BYTES = 32 * 1024 * 1024
_MAX_TORRENT_PAYLOAD_REDIRECTS = 5
_TORRENT_URL_REDACTED = "<redacted>"
_MAGNET_URL_REDACTED = "magnet:<redacted>"


def _redact_torrent_url(url: str) -> str:
    """Return a log-safe view of a torrent or magnet URL.

    HTTP(S) URLs keep scheme, host, optional non-default port, and path only.
    Magnet URIs keep a validated info-hash fingerprint when parseable.
    Malformed input never echoes the original string.
    """
    if not url:
        return _TORRENT_URL_REDACTED
    if url.startswith("magnet:"):
        try:
            info_hash = str(libtorrent.parse_magnet_uri(url).info_hash).lower()
        except Exception:
            return _MAGNET_URL_REDACTED
        return f"magnet:?xt=urn:btih:{info_hash}"
    try:
        parsed = urlparse(url)
    except ValueError:
        return _TORRENT_URL_REDACTED
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return _TORRENT_URL_REDACTED
    host = parsed.hostname
    if parsed.port is not None and parsed.port not in (80, 443):
        host = f"{host}:{parsed.port}"
    path = parsed.path or "/"
    return f"{parsed.scheme}://{host}{path}"


def _is_blocked_ip(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        addr.is_loopback
        or addr.is_private
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_unspecified
        or addr.is_reserved
    )


def _resolve_and_validate_torrent_host(hostname: str) -> str:
    """Resolve *hostname* and return the first validated IP to pin for the fetch."""
    try:
        infos = socket.getaddrinfo(hostname, None)
    except OSError as exc:
        msg = f"Could not resolve torrent host {hostname!r}"
        raise ValueError(msg) from exc
    pinned_ip: str | None = None
    for info in infos:
        try:
            addr = ipaddress.ip_address(info[4][0])
        except ValueError as exc:
            msg = f"Invalid resolved address for torrent host {hostname!r}"
            raise ValueError(msg) from exc
        if _is_blocked_ip(addr):
            msg = f"Blocked resolved address for torrent host {hostname!r}"
            raise ValueError(msg)
        if pinned_ip is None:
            pinned_ip = str(info[4][0])
    if pinned_ip is None:
        msg = f"No addresses resolved for torrent host {hostname!r}"
        raise ValueError(msg)
    return pinned_ip


def _validate_torrent_http_url(url: str) -> tuple[str, str]:
    """Validate an HTTP(S) torrent URL and return ``(hostname, pinned_ip)``."""
    try:
        parsed = urlparse(url)
    except ValueError as exc:
        msg = "Malformed torrent URL"
        raise ValueError(msg) from exc
    if parsed.scheme not in ("http", "https"):
        msg = f"Unsupported torrent URL scheme: {parsed.scheme!r}"
        raise ValueError(msg)
    if parsed.username or parsed.password:
        msg = "Torrent URL userinfo is not allowed"
        raise ValueError(msg)
    hostname = parsed.hostname
    if not hostname:
        msg = "Torrent URL is missing a hostname"
        raise ValueError(msg)
    pinned_ip = _resolve_and_validate_torrent_host(hostname)
    return hostname, pinned_ip


@contextmanager
def _dns_pin(hostname: str, pinned_ip: str) -> Iterator[None]:
    """Pin DNS resolution for *hostname* to *pinned_ip* for the current request."""
    real_getaddrinfo = socket.getaddrinfo

    def pinned_getaddrinfo(
        host: str,
        port: object,
        *args: object,
        **kwargs: object,
    ) -> list[tuple]:
        if host == hostname:
            return real_getaddrinfo(pinned_ip, port, *args, **kwargs)
        return real_getaddrinfo(host, port, *args, **kwargs)

    socket.getaddrinfo = pinned_getaddrinfo  # ty: ignore[invalid-assignment]
    try:
        yield
    finally:
        socket.getaddrinfo = real_getaddrinfo


def _read_bounded_response_body(response: requests.Response) -> bytes:
    content_length = response.headers.get("Content-Length")
    if content_length is not None:
        try:
            declared = int(content_length)
        except ValueError:
            declared = None
        else:
            if declared > _MAX_TORRENT_PAYLOAD_BYTES:
                msg = (
                    f"Torrent payload Content-Length {declared} exceeds "
                    f"{_MAX_TORRENT_PAYLOAD_BYTES} bytes"
                )
                raise ValueError(msg)

    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_content(chunk_size=1 << 16):
        if not chunk:
            continue
        total += len(chunk)
        if total > _MAX_TORRENT_PAYLOAD_BYTES:
            msg = (
                f"Torrent payload streamed body exceeds "
                f"{_MAX_TORRENT_PAYLOAD_BYTES} bytes"
            )
            raise ValueError(msg)
        chunks.append(chunk)
    return b"".join(chunks)


def _guarded_fetch_torrent_bytes(url: str, *, timeout: float) -> bytes:
    """Fetch a ``.torrent`` payload over HTTP(S) with SSRF and size guards."""
    current_url = url
    for _ in range(_MAX_TORRENT_PAYLOAD_REDIRECTS + 1):
        hostname, pinned_ip = _validate_torrent_http_url(current_url)
        with _dns_pin(hostname, pinned_ip):
            response = requests.get(
                current_url,
                stream=True,
                timeout=timeout,
                allow_redirects=False,
            )
        try:
            if 300 <= response.status_code < 400:
                location = response.headers.get("Location")
                if not location:
                    msg = "Torrent payload redirect without Location header"
                    raise ValueError(msg)
                current_url = urljoin(current_url, location)
                if current_url.startswith("magnet:"):
                    msg = "Torrent payload redirect resolved to magnet URI"
                    raise ValueError(msg)
                continue
            response.raise_for_status()
            if not 200 <= response.status_code < 300:
                msg = f"Unexpected torrent payload status code: {response.status_code}"
                raise ValueError(msg)
            return _read_bounded_response_body(response)
        finally:
            response.close()
    msg = "Exceeded maximum number of torrent payload redirects"
    raise ValueError(msg)


def _fetch_torrent_payload(url: str, title: str) -> bytes | None:
    """Return raw bytes of the ``.torrent`` file or ``None`` if unreachable.

    Follows tracker redirects that eventually resolve to a magnet — in which
    case there's nothing to fetch and we surface ``None`` so the caller can
    fall back to the magnet path.
    """
    if url.startswith("magnet:"):
        return None

    timeout = MiraMediaConfig().indexers.timeout_seconds
    parsed = urlparse(url)
    if parsed.scheme in ("http", "https"):
        try:
            return _guarded_fetch_torrent_bytes(url, timeout=timeout)
        except Exception:
            log.debug(
                "Failed to fetch .torrent at %s for inspection",
                _redact_torrent_url(url),
                exc_info=True,
            )
            return None

    try:
        final_url = follow_redirects_to_final_torrent_url(
            initial_url=url,
            session=requests.Session(),
            timeout=timeout,
        )
    except Exception:
        log.debug(
            "Could not follow redirects to inspect torrent payload for %s",
            title,
            exc_info=True,
        )
        return None
    if final_url.startswith("magnet:"):
        return None
    try:
        return _guarded_fetch_torrent_bytes(final_url, timeout=timeout)
    except Exception:
        log.debug(
            "Failed to fetch .torrent at %s for inspection",
            _redact_torrent_url(final_url),
            exc_info=True,
        )
        return None


def follow_redirects_to_final_torrent_url(
    initial_url: str, session: requests.Session, timeout: float = 10
) -> str:
    """
    Follows redirects to get the final torrent URL.
    :param initial_url: The initial URL to follow.
    :param session: A requests session to use for the requests.
    :param timeout: Timeout in seconds for each redirect request.
    :return: The final torrent URL.
    :raises: RuntimeError if it fails.
    """
    current_url = initial_url
    try:
        for _ in range(_MAX_TORRENT_PAYLOAD_REDIRECTS + 1):
            parsed = urlparse(current_url)
            if parsed.scheme in ("http", "https"):
                hostname, pinned_ip = _validate_torrent_http_url(current_url)
                with _dns_pin(hostname, pinned_ip):
                    response = session.get(
                        current_url,
                        allow_redirects=False,
                        timeout=timeout,
                        stream=True,
                    )
            else:
                msg = f"Unsupported torrent URL scheme: {parsed.scheme!r}"
                raise ValueError(msg)

            try:
                if 300 <= response.status_code < 400:
                    redirect_url = response.headers.get("Location")
                    if not redirect_url:
                        msg = "Redirect response without Location header"
                        raise RuntimeError(msg)

                    current_url = urljoin(current_url, redirect_url)
                    log.debug(
                        "Following redirect to: %s",
                        _redact_torrent_url(current_url),
                    )

                    if current_url.startswith("magnet:"):
                        return current_url
                else:
                    response.raise_for_status()
                    return current_url
            finally:
                response.close()
        else:
            msg = "Exceeded maximum number of redirects"
            raise RuntimeError(msg)

    except requests.exceptions.RequestException as e:
        log.debug(
            "An error occurred during the request for %s",
            _redact_torrent_url(initial_url),
            exc_info=True,
        )
        msg = "An error occurred during the request"
        raise RuntimeError(msg) from e
