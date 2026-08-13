"""Constrained outbound webhook HTTP delivery client (Slice B; no provider wiring)."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from urllib.parse import urljoin, urlparse

import httpx

from miramedia.notifications.destination_policy import (
    AddrInfoResolver,
    max_webhook_redirects,
    resolve_webhook_host,
    validate_redirect_chain,
    validate_webhook_url,
)
from miramedia.notifications.envelope_signing import (
    CONTENT_TYPE,
    WEBHOOK_USER_AGENT,
    WebhookEnvelope,
    build_envelope,
    canonical_body_bytes,
    signing_headers,
)

logger = logging.getLogger(__name__)

CONNECT_READ_TIMEOUT_S = 10.0
MAX_RESPONSE_BYTES = 65_536
MAX_DELIVERY_ATTEMPTS = 3
_RETRY_BACKOFF_S = (1.0, 2.0)
_RETRYABLE_STATUS_CODES = frozenset({408, 429})

_PINNED_HOST_EXTENSION = "miramedia_pinned"  # (hostname, pinned_ip)


class _DeliveryOutcome(StrEnum):
    SUCCESS = "success"
    RETRY = "retry"
    FAILURE = "failure"


class _HttpPoster(Protocol):
    def post_stream(
        self,
        url: str,
        *,
        content: bytes,
        headers: dict[str, str],
        extensions: dict | None = None,
    ) -> httpx.Response: ...


@dataclass(frozen=True, slots=True)
class WebhookDestination:
    url: str
    allow_private_network: bool = False
    # https is required regardless of network class unless this is explicitly
    # set. Cleartext http exposes the signed envelope and HMAC on the wire.
    allow_insecure_transport: bool = False
    signing_secret: str = ""


class _HttpxStreamPoster:
    """Adapts ``httpx.Client`` to the streaming poster protocol."""

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def post_stream(
        self,
        url: str,
        *,
        content: bytes,
        headers: dict[str, str],
        extensions: dict | None = None,
    ) -> httpx.Response:
        request = self._client.build_request(
            "POST", url, content=content, headers=headers, extensions=extensions
        )
        return self._client.send(request, stream=True)


def _apply_pin(request: httpx.Request) -> None:
    pinned = request.extensions.get(_PINNED_HOST_EXTENSION)
    if pinned is not None:
        hostname, pinned_ip = pinned
        if request.url.host == hostname and hostname != pinned_ip:
            request.headers["Host"] = request.url.netloc.decode("ascii")
            request.extensions["sni_hostname"] = hostname
            request.url = request.url.copy_with(host=pinned_ip)


class _PinnedHostTransport(httpx.HTTPTransport):
    """Connect to a pre-validated IP while verifying TLS against the original hostname.

    Reads ``request.extensions[_PINNED_HOST_EXTENSION]`` = (hostname, pinned_ip).
    Rewrites the URL host to the IP, keeps the original hostname in the Host
    header, and sets the ``sni_hostname`` extension so certificate verification
    still checks the real name. No global state is touched.
    """

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        _apply_pin(request)
        return super().handle_request(request)


def _url_origin(url: str) -> tuple[str, str, int]:
    parsed = urlparse(url)
    hostname = parsed.hostname or ""
    port = parsed.port
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    return parsed.scheme, hostname, port


def _read_bounded_response_body(response: httpx.Response) -> None:
    try:
        content_length = response.headers.get("Content-Length")
        if content_length is not None:
            try:
                declared = int(content_length)
            except ValueError:
                declared = None
            else:
                if declared > MAX_RESPONSE_BYTES:
                    return

        total = 0
        for chunk in response.iter_bytes(chunk_size=1 << 16):
            total += len(chunk)
            if total > MAX_RESPONSE_BYTES:
                return
    finally:
        response.close()


def _is_retryable_status(status_code: int) -> bool:
    return status_code in _RETRYABLE_STATUS_CODES or status_code >= 500


def _build_request_headers(
    envelope: WebhookEnvelope,
    body: bytes,
    *,
    signing_secret: str,
    timestamp: int,
) -> dict[str, str]:
    headers = {
        "Content-Type": CONTENT_TYPE,
        "User-Agent": WEBHOOK_USER_AGENT,
    }
    if signing_secret:
        headers.update(
            signing_headers(
                signing_secret,
                envelope,
                timestamp=timestamp,
                body=body,
            )
        )
    return headers


def _attempt_delivery(
    url: str,
    *,
    body: bytes,
    headers: dict[str, str],
    allow_private_network: bool,
    allow_insecure_transport: bool,
    resolver: AddrInfoResolver | None,
    post: _HttpPoster,
) -> _DeliveryOutcome:
    current_url = url
    redirect_chain = [current_url]
    initial_origin = _url_origin(url)

    for _ in range(max_webhook_redirects() + 1):
        chain_decision = validate_redirect_chain(
            redirect_chain,
            allow_private_network=allow_private_network,
            allow_insecure_transport=allow_insecure_transport,
            resolver=resolver,
        )
        if not chain_decision.allowed:
            logger.warning(
                "Webhook redirect chain denied: %s",
                chain_decision.reason,
            )
            return _DeliveryOutcome.FAILURE

        try:
            hostname, pinned_ip = resolve_webhook_host(
                current_url,
                allow_private_network=allow_private_network,
                allow_insecure_transport=allow_insecure_transport,
                resolver=resolver,
            )
        except ValueError:
            logger.warning("Webhook destination resolution failed for redirect hop")
            return _DeliveryOutcome.FAILURE

        try:
            response = post.post_stream(
                current_url,
                content=body,
                headers=headers,
                extensions={_PINNED_HOST_EXTENSION: (hostname, pinned_ip)},
            )
        except (
            httpx.ConnectError,
            httpx.ConnectTimeout,
            httpx.ReadTimeout,
            httpx.WriteTimeout,
            httpx.PoolTimeout,
            httpx.NetworkError,
        ):
            return _DeliveryOutcome.RETRY
        except httpx.TransportError:
            return _DeliveryOutcome.RETRY

        _read_bounded_response_body(response)

        if 300 <= response.status_code < 400:
            location = response.headers.get("Location")
            if response.status_code not in (307, 308):
                logger.warning(
                    "Webhook redirect status %s does not preserve POST; "
                    "refusing to re-deliver (Location: %s)",
                    response.status_code,
                    location or "<missing>",
                )
                return _DeliveryOutcome.FAILURE
            if not location:
                logger.warning(
                    "Webhook redirect without Location header (status %s)",
                    response.status_code,
                )
                return _DeliveryOutcome.FAILURE
            next_url = urljoin(current_url, location)
            if _url_origin(next_url) != initial_origin:
                logger.warning("Webhook redirect changed origin; refusing")
                return _DeliveryOutcome.FAILURE
            redirect_chain.append(next_url)
            if len(redirect_chain) > max_webhook_redirects() + 1:
                logger.warning("Webhook redirect limit exceeded")
                return _DeliveryOutcome.FAILURE
            current_url = next_url
            continue

        if 200 <= response.status_code < 300:
            return _DeliveryOutcome.SUCCESS

        if _is_retryable_status(response.status_code):
            logger.warning(
                "Webhook delivery received retryable status %s",
                response.status_code,
            )
            return _DeliveryOutcome.RETRY

        logger.warning(
            "Webhook delivery received non-retryable status %s",
            response.status_code,
        )
        return _DeliveryOutcome.FAILURE

    logger.warning("Webhook redirect loop exhausted without terminal response")
    return _DeliveryOutcome.FAILURE


def deliver_webhook(
    title: str,
    message: str,
    *,
    destination: WebhookDestination,
    resolver: AddrInfoResolver | None = None,
    sleep: Callable[[float], None] | None = None,
    client: httpx.Client | _HttpPoster | None = None,
) -> bool:
    """Deliver a signed notification envelope to a constrained webhook destination.

    Blocks up to ``MAX_DELIVERY_ATTEMPTS * CONNECT_READ_TIMEOUT_S`` plus backoff
    (~35s worst case). Must not be called from the event loop — async callers
    should use ``anyio.to_thread.run_sync``.
    """
    initial_decision = validate_webhook_url(
        destination.url,
        allow_private_network=destination.allow_private_network,
        allow_insecure_transport=destination.allow_insecure_transport,
        resolver=resolver,
    )
    if not initial_decision.allowed:
        logger.warning(
            "Webhook destination denied before connect: %s",
            initial_decision.reason,
        )
        return False

    envelope = build_envelope(title, message)
    body = canonical_body_bytes(envelope)
    pause = sleep or time.sleep

    owns_client = client is None
    raw_client = client or httpx.Client(
        timeout=CONNECT_READ_TIMEOUT_S,
        verify=True,
        trust_env=False,
        follow_redirects=False,
        transport=_PinnedHostTransport(verify=True),
    )
    if isinstance(raw_client, httpx.Client):
        poster: _HttpPoster = _HttpxStreamPoster(raw_client)
        http_client: httpx.Client | None = raw_client
    else:
        poster = raw_client
        http_client = None

    try:
        for attempt in range(MAX_DELIVERY_ATTEMPTS):
            if attempt > 0:
                pause(_RETRY_BACKOFF_S[attempt - 1])
            timestamp = int(time.time())
            headers = _build_request_headers(
                envelope,
                body,
                signing_secret=destination.signing_secret,
                timestamp=timestamp,
            )
            outcome = _attempt_delivery(
                destination.url,
                body=body,
                headers=headers,
                allow_private_network=destination.allow_private_network,
                allow_insecure_transport=destination.allow_insecure_transport,
                resolver=resolver,
                post=poster,
            )
            if outcome == _DeliveryOutcome.SUCCESS:
                return True
            if outcome == _DeliveryOutcome.FAILURE:
                return False
        return False
    finally:
        if owns_client and http_client is not None:
            http_client.close()
