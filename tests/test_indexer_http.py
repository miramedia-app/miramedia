"""Deterministic tests for native indexer HTTP retry policy."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

from miramedia.indexers.http_retry import (
    MAX_RETRY_AFTER,
    IndexerDeadlineError,
    backoff_delay,
    indexer_get,
    parse_retry_after,
    should_fast_fail_response,
)
from miramedia.indexers.sites.x1337 import X1337Site

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "indexer_sites"


def _load_fixture(name: str) -> str:
    return (FIXTURES_DIR / name).read_text()


def _request(url: str = "https://example.test/path") -> httpx.Request:
    return httpx.Request("GET", url)


def _response(
    status_code: int,
    *,
    text: str = "",
    headers: dict[str, str] | None = None,
    url: str = "https://example.test/path",
) -> httpx.Response:
    return httpx.Response(
        status_code,
        text=text,
        headers=headers or {},
        request=_request(url),
    )


class TestParseRetryAfter:
    def test_delta_seconds(self) -> None:
        assert parse_retry_after("30") == 30.0

    def test_http_date(self) -> None:
        now = datetime(2026, 8, 9, 12, 0, 0, tzinfo=UTC)
        assert parse_retry_after("Wed, 09 Aug 2026 12:00:30 GMT", now=now) == 30.0

    def test_malformed_returns_none(self) -> None:
        assert parse_retry_after("not-a-date") is None
        assert parse_retry_after("") is None
        assert parse_retry_after(None) is None


class TestBackoffDelay:
    def test_full_jitter_bounded_by_cap(self) -> None:
        assert backoff_delay(10, 0.5, 2.0, rand=lambda: 1.0) == 2.0

    def test_retry_after_is_floor(self) -> None:
        assert backoff_delay(0, 0.5, 2.0, retry_after=5.0, rand=lambda: 0.0) == 5.0

    def test_retry_after_is_capped(self) -> None:
        delay = backoff_delay(0, 0.5, 2.0, retry_after=3600.0, rand=lambda: 0.5)
        assert delay == MAX_RETRY_AFTER


class TestIndexerGet:
    def test_success_without_retry(self) -> None:
        client = MagicMock()
        client.get.return_value = _response(200, text="ok")

        response = indexer_get(client, "https://example.test/path")

        assert response.status_code == 200
        client.get.assert_called_once()

    def test_retries_retryable_status_then_succeeds(self) -> None:
        client = MagicMock()
        client.get.side_effect = [
            _response(503, text="temporary"),
            _response(200, text="ok"),
        ]
        sleeps: list[float] = []

        response = indexer_get(
            client,
            "https://example.test/path",
            deadline=100.0,
            monotonic=lambda: 0.0,
            sleep=lambda seconds: sleeps.append(seconds),
            rand=lambda: 0.5,
        )

        assert response.status_code == 200
        assert client.get.call_count == 2
        assert len(sleeps) == 1
        assert sleeps[0] == pytest.approx(0.25)

    def test_does_not_retry_ordinary_4xx(self) -> None:
        client = MagicMock()
        client.get.return_value = _response(404, text="missing")

        response = indexer_get(client, "https://example.test/path")

        assert response.status_code == 404
        client.get.assert_called_once()

    def test_retries_transport_failure_once(self) -> None:
        client = MagicMock()
        client.get.side_effect = [
            httpx.ConnectError("connection reset"),
            _response(200, text="ok"),
        ]
        sleeps: list[float] = []

        response = indexer_get(
            client,
            "https://example.test/path",
            deadline=100.0,
            monotonic=lambda: 0.0,
            sleep=lambda seconds: sleeps.append(seconds),
            rand=lambda: 0.5,
        )

        assert response.status_code == 200
        assert client.get.call_count == 2
        assert len(sleeps) == 1

    def test_honors_retry_after_seconds(self) -> None:
        client = MagicMock()
        client.get.side_effect = [
            _response(429, headers={"retry-after": "12"}),
            _response(200, text="ok"),
        ]
        sleeps: list[float] = []

        indexer_get(
            client,
            "https://example.test/path",
            deadline=100.0,
            monotonic=lambda: 0.0,
            sleep=lambda seconds: sleeps.append(seconds),
            rand=lambda: 0.0,
        )

        assert sleeps == [12.0]

    def test_clamps_retry_after_to_remaining_deadline(self) -> None:
        client = MagicMock()
        sleeps: list[float] = []
        clock = {"t": 0.0}
        responses = iter(
            [
                _response(429, headers={"retry-after": "30"}),
                _response(200, text="ok"),
            ]
        )

        def fake_get(*_args: Any, **_kwargs: Any) -> httpx.Response:
            clock["t"] = 25.0
            return next(responses)

        client.get.side_effect = fake_get

        def monotonic() -> float:
            return clock["t"]

        def sleep(seconds: float) -> None:
            sleeps.append(seconds)

        indexer_get(
            client,
            "https://example.test/path",
            deadline=30.0,
            monotonic=monotonic,
            sleep=sleep,
            rand=lambda: 0.0,
        )

        assert sleeps == [5.0]

    def test_malformed_retry_after_uses_jitter(self) -> None:
        client = MagicMock()
        client.get.side_effect = [
            _response(502, headers={"retry-after": "soon"}),
            _response(200, text="ok"),
        ]
        sleeps: list[float] = []

        indexer_get(
            client,
            "https://example.test/path",
            deadline=100.0,
            monotonic=lambda: 0.0,
            sleep=lambda seconds: sleeps.append(seconds),
            rand=lambda: 0.5,
        )

        assert sleeps == [pytest.approx(0.25)]

    def test_cloudflare_challenge_fast_fail_without_retry(self) -> None:
        client = MagicMock()
        challenge = _load_fixture("x1337_challenge.html")
        client.get.return_value = _response(
            403,
            text=challenge,
            headers={"server": "cloudflare"},
        )
        sleeps: list[float] = []

        response = indexer_get(
            client,
            "https://example.test/path",
            deadline=100.0,
            monotonic=lambda: 0.0,
            sleep=lambda seconds: sleeps.append(seconds),
        )

        assert should_fast_fail_response(response)
        client.get.assert_called_once()
        assert sleeps == []

    def test_cloudflare_503_server_fast_fail_without_retry(self) -> None:
        client = MagicMock()
        client.get.return_value = _response(
            503,
            text="blocked",
            headers={"server": "cloudflare"},
        )
        sleeps: list[float] = []

        response = indexer_get(
            client,
            "https://example.test/path",
            deadline=100.0,
            monotonic=lambda: 0.0,
            sleep=lambda seconds: sleeps.append(seconds),
        )

        assert should_fast_fail_response(response)
        client.get.assert_called_once()
        assert sleeps == []

    def test_deadline_exhausted_before_retry_wait(self) -> None:
        client = MagicMock()
        client.get.return_value = _response(503, text="temporary")
        times = iter([0.0, 0.0, 1.0])

        with pytest.raises(IndexerDeadlineError):
            indexer_get(
                client,
                "https://example.test/path",
                deadline=1.0,
                monotonic=lambda: next(times),
                sleep=lambda _seconds: None,
                rand=lambda: 0.0,
            )

        client.get.assert_called_once()

    def test_deadline_exhausted_before_second_request(self) -> None:
        client = MagicMock()
        client.get.return_value = _response(503, text="temporary")
        times = iter([0.0, 0.0, 2.0])

        with pytest.raises(IndexerDeadlineError):
            indexer_get(
                client,
                "https://example.test/path",
                deadline=1.0,
                monotonic=lambda: next(times),
                sleep=lambda _seconds: None,
                rand=lambda: 0.0,
            )

        client.get.assert_called_once()

    def test_at_most_one_retry_per_path(self) -> None:
        client = MagicMock()
        client.get.return_value = _response(504, text="still failing")

        response = indexer_get(
            client,
            "https://example.test/path",
            deadline=100.0,
            monotonic=lambda: 0.0,
            sleep=lambda _seconds: None,
            rand=lambda: 0.0,
        )

        assert response.status_code == 504
        assert client.get.call_count == 2


class TestX1337RetryMirrorIntegration:
    @staticmethod
    def _fake_response(**kwargs: Any) -> httpx.Response:
        return _response(**kwargs)

    def test_503_retry_then_mirror_failover_stays_bounded(self) -> None:
        site = X1337Site(bypass=None, timeout=5)
        site.url = "https://mirror-a.example"
        site.available_urls = [
            "https://mirror-a.example",
            "https://mirror-b.example",
        ]
        site._mirror_pref = None
        html = _load_fixture("x1337.html")
        calls: list[str] = []

        def fake_get(
            _client: MagicMock,
            url: str,
            **_kwargs: Any,
        ) -> httpx.Response:
            calls.append(url)
            if url.startswith("https://mirror-a.example"):
                if calls.count(url) == 1:
                    return self._fake_response(
                        status_code=503,
                        text="temporary",
                        url=url,
                    )
                return self._fake_response(status_code=404, text="gone", url=url)
            return self._fake_response(status_code=200, text=html, url=url)

        client = MagicMock()
        with (
            patch(
                "miramedia.indexers.sites.x1337._get_http_client",
                return_value=client,
            ),
            patch(
                "miramedia.indexers.sites.x1337.indexer_get",
                side_effect=fake_get,
            ),
            patch.object(site, "_fetch_magnet", return_value=(None, 36_500)),
        ):
            results, hard_error = site._search("show", "TV")

        assert hard_error is False
        assert results == []
        mirror_a_calls = [url for url in calls if url.startswith("https://mirror-a")]
        mirror_b_calls = [url for url in calls if url.startswith("https://mirror-b")]
        assert len(mirror_a_calls) <= 4
        assert len(mirror_b_calls) <= 2
        assert mirror_b_calls

    def test_challenge_skips_retry_and_moves_to_next_mirror(self) -> None:
        site = X1337Site(bypass=None, timeout=5)
        site.url = "https://mirror-a.example"
        site.available_urls = [
            "https://mirror-a.example",
            "https://mirror-b.example",
        ]
        site._mirror_pref = None
        challenge = _load_fixture("x1337_challenge.html")
        html = _load_fixture("x1337.html")
        calls: list[str] = []

        def fake_get(
            _client: MagicMock,
            url: str,
            **_kwargs: Any,
        ) -> httpx.Response:
            calls.append(url)
            if url.startswith("https://mirror-a.example"):
                return self._fake_response(
                    status_code=403,
                    text=challenge,
                    headers={"server": "cloudflare"},
                    url=url,
                )
            return self._fake_response(status_code=200, text=html, url=url)

        client = MagicMock()
        with (
            patch(
                "miramedia.indexers.sites.x1337._get_http_client",
                return_value=client,
            ),
            patch(
                "miramedia.indexers.sites.x1337.indexer_get",
                side_effect=fake_get,
            ),
            patch.object(site, "_fetch_magnet", return_value=(None, 36_500)),
            patch.object(site, "_fetch_with_bypass") as bypass_fetch,
        ):
            results, hard_error = site._search("show", "TV")

        bypass_fetch.assert_not_called()
        assert hard_error is False
        assert results == []
        mirror_a_calls = [url for url in calls if url.startswith("https://mirror-a")]
        assert len(mirror_a_calls) == 1
        assert any(url.startswith("https://mirror-b") for url in calls)
