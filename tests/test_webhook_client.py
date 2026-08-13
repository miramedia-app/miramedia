"""HTTP delivery tests for the constrained webhook client."""

from __future__ import annotations

import socket
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from miramedia.notifications.destination_policy import (
    DestinationDenyReason,
    max_webhook_redirects,
)
from miramedia.notifications.webhook_client import (
    _PINNED_HOST_EXTENSION,
    MAX_RESPONSE_BYTES,
    WebhookDestination,
    _apply_pin,
    _build_request_headers,
    deliver_webhook,
)


def _dns(ip: str) -> Callable[..., list]:
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


def _mock_client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(
        transport=httpx.MockTransport(handler),
        timeout=10.0,
        verify=True,
        trust_env=False,
        follow_redirects=False,
    )


def test_deliver_success_posts_signed_envelope() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        seen["body"] = request.content
        return httpx.Response(204)

    destination = WebhookDestination(
        url="https://hooks.example.invalid/miramedia",
        signing_secret="test-signing-secret-not-real",
    )
    ok = deliver_webhook(
        "[MiraMedia] Test",
        "hello",
        destination=destination,
        resolver=_dns("93.184.216.34"),
        sleep=lambda _s: None,
        client=_mock_client(handler),
    )
    assert ok is True
    assert seen["url"] == destination.url
    assert seen["headers"]["user-agent"] == "MiraMedia-Webhook/1"
    assert seen["headers"]["content-type"].startswith("application/json")
    assert "x-miramedia-webhook-signature" in seen["headers"]
    assert b'"type":"notification.message"' in seen["body"]


def test_deliver_denies_http_without_insecure_transport_flag() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(204)

    ok = deliver_webhook(
        "t",
        "m",
        destination=WebhookDestination(
            url="http://hooks.example.invalid/x",
            allow_private_network=True,
        ),
        resolver=_dns("93.184.216.34"),
        sleep=lambda _s: None,
        client=_mock_client(handler),
    )
    assert ok is False
    assert calls == 0


def test_deliver_allows_http_with_insecure_transport_flag() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(204)

    ok = deliver_webhook(
        "t",
        "m",
        destination=WebhookDestination(
            url="http://hooks.example.invalid/x",
            allow_insecure_transport=True,
        ),
        resolver=_dns("93.184.216.34"),
        sleep=lambda _s: None,
        client=_mock_client(handler),
    )
    assert ok is True
    assert calls == 1


def test_deliver_denies_private_destination_before_http() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200)

    ok = deliver_webhook(
        "t",
        "m",
        destination=WebhookDestination(
            url="https://ha.example.invalid/hook",
            allow_private_network=False,
        ),
        resolver=_dns("192.168.1.50"),
        sleep=lambda _s: None,
        client=_mock_client(handler),
    )
    assert ok is False
    assert calls == 0


def test_apply_pin_rewrites_url_host_and_sni() -> None:
    request = httpx.Request(
        "POST",
        "https://hooks.example.invalid/x",
        extensions={_PINNED_HOST_EXTENSION: ("hooks.example.invalid", "93.184.216.34")},
    )
    _apply_pin(request)
    assert request.url.host == "93.184.216.34"
    assert request.extensions["sni_hostname"] == "hooks.example.invalid"
    assert request.headers["Host"] == "hooks.example.invalid"


def test_deliver_webhook_does_not_mutate_getaddrinfo() -> None:
    before = socket.getaddrinfo

    def handler(request: httpx.Request) -> httpx.Response:
        assert socket.getaddrinfo is before
        _ = request
        return httpx.Response(204)

    destination = WebhookDestination(
        url="https://hooks.example.invalid/miramedia",
        signing_secret="test-signing-secret-not-real",
    )
    ok = deliver_webhook(
        "[MiraMedia] Test",
        "hello",
        destination=destination,
        resolver=_dns("93.184.216.34"),
        sleep=lambda _s: None,
        client=_mock_client(handler),
    )
    assert ok is True
    assert socket.getaddrinfo is before


def test_redirect_hop_revalidated_and_followed() -> None:
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        if len(urls) == 1:
            return httpx.Response(
                307,
                headers={"Location": "https://hooks.example.invalid/final"},
            )
        return httpx.Response(200)

    ok = deliver_webhook(
        "t",
        "m",
        destination=WebhookDestination(url="https://hooks.example.invalid/start"),
        resolver=_dns("93.184.216.34"),
        sleep=lambda _s: None,
        client=_mock_client(handler),
    )
    assert ok is True
    assert urls == [
        "https://hooks.example.invalid/start",
        "https://hooks.example.invalid/final",
    ]


def test_redirect_to_link_local_denied() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                307,
                headers={"Location": "https://169.254.169.254/latest/meta-data"},
            )
        return httpx.Response(200)

    ok = deliver_webhook(
        "t",
        "m",
        destination=WebhookDestination(url="https://hooks.example.invalid/start"),
        resolver=_dns("93.184.216.34"),
        sleep=lambda _s: None,
        client=_mock_client(handler),
    )
    assert ok is False
    assert calls == 1


@pytest.mark.parametrize("status_code", [301, 302, 303])
def test_redirect_status_does_not_repost_body(status_code: int) -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            status_code,
            headers={"Location": "https://hooks.example.invalid/other"},
        )

    ok = deliver_webhook(
        "t",
        "m",
        destination=WebhookDestination(url="https://hooks.example.invalid/start"),
        resolver=_dns("93.184.216.34"),
        sleep=lambda _s: None,
        client=_mock_client(handler),
    )
    assert ok is False
    assert calls == 1


def test_307_followed_with_post_body() -> None:
    first_body: bytes | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal first_body
        if first_body is None:
            first_body = request.content
            return httpx.Response(
                307,
                headers={"Location": "https://hooks.example.invalid/final"},
            )
        assert request.method == "POST"
        assert request.content == first_body
        return httpx.Response(200)

    ok = deliver_webhook(
        "t",
        "m",
        destination=WebhookDestination(url="https://hooks.example.invalid/start"),
        resolver=_dns("93.184.216.34"),
        sleep=lambda _s: None,
        client=_mock_client(handler),
    )
    assert ok is True


def test_extensions_plumbed_through_poster() -> None:
    recorded: dict[str, object] = {}

    class RecordingPoster:
        def post_stream(
            self,
            _url: str,
            *,
            content: bytes,
            headers: dict[str, str],
            extensions: dict | None = None,
        ) -> httpx.Response:
            recorded["content"] = content
            recorded["headers"] = headers
            recorded["extensions"] = extensions
            return httpx.Response(204)

    ok = deliver_webhook(
        "t",
        "m",
        destination=WebhookDestination(url="https://hooks.example.invalid/hook"),
        resolver=_dns("93.184.216.34"),
        sleep=lambda _s: None,
        client=RecordingPoster(),  # type: ignore[arg-type]
    )
    assert ok is True
    assert recorded["extensions"] == {
        _PINNED_HOST_EXTENSION: ("hooks.example.invalid", "93.184.216.34")
    }


def test_retryable_5xx_then_success() -> None:
    attempts = 0
    sleeps: list[float] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return httpx.Response(503)
        return httpx.Response(200)

    ok = deliver_webhook(
        "t",
        "m",
        destination=WebhookDestination(url="https://hooks.example.invalid/hook"),
        resolver=_dns("93.184.216.34"),
        sleep=sleeps.append,
        client=_mock_client(handler),
    )
    assert ok is True
    assert attempts == 3
    assert sleeps == [1.0, 2.0]


def test_non_retryable_4xx_fails_without_retry() -> None:
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(404)

    ok = deliver_webhook(
        "t",
        "m",
        destination=WebhookDestination(url="https://hooks.example.invalid/hook"),
        resolver=_dns("93.184.216.34"),
        sleep=lambda _s: None,
        client=_mock_client(handler),
    )
    assert ok is False
    assert attempts == 1


@pytest.mark.parametrize("status_code", [408, 429, 500])
def test_retryable_status_codes(status_code: int) -> None:
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(status_code)
        return httpx.Response(200)

    ok = deliver_webhook(
        "t",
        "m",
        destination=WebhookDestination(url="https://hooks.example.invalid/hook"),
        resolver=_dns("93.184.216.34"),
        sleep=lambda _s: None,
        client=_mock_client(handler),
    )
    assert ok is True
    assert attempts == 2


def test_connect_error_retries_until_exhausted() -> None:
    attempts = 0

    class FailingClient:
        def post_stream(
            self,
            _url: str,
            *,
            content: bytes,
            headers: dict[str, str],
            extensions: dict | None = None,
        ) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            _ = content, headers, extensions
            msg = "connection refused"
            raise httpx.ConnectError(msg)

    ok = deliver_webhook(
        "t",
        "m",
        destination=WebhookDestination(url="https://hooks.example.invalid/hook"),
        resolver=_dns("93.184.216.34"),
        sleep=lambda _s: None,
        client=FailingClient(),  # type: ignore[arg-type]
    )
    assert ok is False
    assert attempts == 3


def test_timeout_error_retries() -> None:
    attempts = 0

    class TimeoutClient:
        def post_stream(
            self,
            _url: str,
            *,
            content: bytes,
            headers: dict[str, str],
            extensions: dict | None = None,
        ) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            _ = content, headers, extensions
            if attempts == 1:
                msg = "read timed out"
                raise httpx.ReadTimeout(msg)
            return httpx.Response(200)

    ok = deliver_webhook(
        "t",
        "m",
        destination=WebhookDestination(url="https://hooks.example.invalid/hook"),
        resolver=_dns("93.184.216.34"),
        sleep=lambda _s: None,
        client=TimeoutClient(),  # type: ignore[arg-type]
    )
    assert ok is True
    assert attempts == 2


def test_redirect_limit_exceeded() -> None:
    hops = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal hops
        hops += 1
        return httpx.Response(
            307,
            headers={"Location": f"{request.url}/next{hops}"},
        )

    ok = deliver_webhook(
        "t",
        "m",
        destination=WebhookDestination(url="https://hooks.example.invalid/start"),
        resolver=_dns("93.184.216.34"),
        sleep=lambda _s: None,
        client=_mock_client(handler),
    )
    assert ok is False
    assert hops == max_webhook_redirects() + 1


def test_private_opt_in_allows_http_delivery() -> None:
    seen = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal seen
        seen = True
        return httpx.Response(200)

    ok = deliver_webhook(
        "t",
        "m",
        destination=WebhookDestination(
            url="http://ha.example.invalid:8123/api/webhook/x",
            allow_private_network=True,
            allow_insecure_transport=True,
        ),
        resolver=_dns("10.0.0.5"),
        sleep=lambda _s: None,
        client=_mock_client(handler),
    )
    assert ok is True
    assert seen is True


def test_default_deny_reason_matches_policy() -> None:
    from miramedia.notifications.destination_policy import validate_webhook_url

    decision = validate_webhook_url(
        "http://example.invalid/hook",
        resolver=_dns("1.1.1.1"),
    )
    assert not decision.allowed
    assert decision.reason == DestinationDenyReason.SCHEME


def test_streamed_over_cap_body_stops_reading_and_completes() -> None:
    from httpx._content import IteratorByteStream

    chunk_reads = 0
    chunk_size = MAX_RESPONSE_BYTES // 2 + 1

    def chunks() -> Any:
        nonlocal chunk_reads
        chunk_reads += 1
        yield b"x" * chunk_size
        chunk_reads += 1
        yield b"x" * chunk_size

    class StreamingPoster:
        def post_stream(
            self,
            _url: str,
            *,
            content: bytes,
            headers: dict[str, str],
            extensions: dict | None = None,
        ) -> httpx.Response:
            _ = content, headers, extensions
            return httpx.Response(200, stream=IteratorByteStream(chunks()))

    ok = deliver_webhook(
        "t",
        "m",
        destination=WebhookDestination(url="https://hooks.example.invalid/hook"),
        resolver=_dns("93.184.216.34"),
        sleep=lambda _s: None,
        client=StreamingPoster(),  # type: ignore[arg-type]
    )
    assert ok is True
    assert chunk_reads == 2


def test_cross_host_redirect_refused_without_foreign_request() -> None:
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        return httpx.Response(
            307,
            headers={"Location": "https://evil.example.invalid/steal"},
        )

    ok = deliver_webhook(
        "t",
        "m",
        destination=WebhookDestination(url="https://hooks.example.invalid/start"),
        resolver=_dns("93.184.216.34"),
        sleep=lambda _s: None,
        client=_mock_client(handler),
    )
    assert ok is False
    assert urls == ["https://hooks.example.invalid/start"]


def test_headers_rebuilt_per_retry_with_stable_webhook_id() -> None:
    attempts = 0
    build_calls = 0
    captured_ids: list[str] = []
    real_build = _build_request_headers

    def capture_build(*args: Any, **kwargs: Any) -> dict[str, str]:
        nonlocal build_calls
        build_calls += 1
        headers = real_build(*args, **kwargs)
        captured_ids.append(headers.get("X-MiraMedia-Webhook-Id", ""))
        return headers

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(503)
        return httpx.Response(200)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "miramedia.notifications.webhook_client._build_request_headers",
            capture_build,
        )
        ok = deliver_webhook(
            "t",
            "m",
            destination=WebhookDestination(
                url="https://hooks.example.invalid/hook",
                signing_secret="test-signing-secret-not-real",
            ),
            resolver=_dns("93.184.216.34"),
            sleep=lambda _s: None,
            client=_mock_client(handler),
        )

    assert ok is True
    assert attempts == 2
    assert build_calls == 2
    assert captured_ids[0] == captured_ids[1]
    assert captured_ids[0]
