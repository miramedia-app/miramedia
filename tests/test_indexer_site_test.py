"""Unit tests for the indexer-site connectivity test (``_run_site_test``).

DB-free: the repository and Cloudflare bypass are stubbed. The key behaviour
under test is mirror failover — a preloaded native site whose primary mirror is
Cloudflare-walled must still succeed via a plain-HTTP mirror even when the CF
bypass is disabled (regression: 1337x reported "bypass disabled" instead of
trying its .st/.ws/.to mirrors).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import miramedia.cloudflare as cf
import miramedia.indexers.router as router


class _FakeResponse:
    def __init__(
        self,
        *,
        status_code: int,
        text: str = "",
        headers: dict[str, str] | None = None,
        reason: str = "",
    ) -> None:
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}
        self.reason = reason

    @property
    def ok(self) -> bool:
        return self.status_code < 400


def _cf_challenge() -> _FakeResponse:
    return _FakeResponse(
        status_code=403,
        headers={"server": "cloudflare", "cf-mitigated": "challenge"},
        text="Just a moment...",
        reason="Forbidden",
    )


def _ok_page() -> _FakeResponse:
    body = "<table class='table-list'><tbody><tr><td>x</td></tr></tbody></table>"
    return _FakeResponse(status_code=200, text=body, reason="OK")


class _StubRepo:
    def __init__(self) -> None:
        self.updates: list = []

    async def update_site(self, site_id, update):
        self.updates.append((site_id, update))


def _site() -> SimpleNamespace:
    # 1337x is a preloaded native site with .to/.st/.ws/.to mirrors and
    # cloudflare_protected=True on the class, so no flag write is expected.
    return SimpleNamespace(
        name="1337x",
        site_type="html",
        url="https://1337x.to",
        api_key="",
        cloudflare_protected=True,
    )


def _run(monkeypatch, get_impl, *, bypass_enabled: bool):
    calls: list[str] = []

    def fake_get(url, **_kwargs):
        calls.append(url)
        return get_impl(url)

    monkeypatch.setattr(router.requests, "get", fake_get)
    monkeypatch.setattr(
        cf,
        "get_cloudflare_bypass",
        lambda: SimpleNamespace(
            config=SimpleNamespace(enabled=bypass_enabled),
            get_cached_session=lambda _domain: None,
            solve=lambda *_a, **_k: None,
        ),
    )

    result = asyncio.run(
        router._run_site_test(_site(), "site-1", _StubRepo(), progress=None)
    )
    return result, calls


def test_mirror_failover_succeeds_when_primary_cf_walled_and_bypass_disabled(
    monkeypatch,
):
    def get_impl(url: str) -> _FakeResponse:
        # Primary (1337x.to) is Cloudflare-gated; the first mirror serves plain.
        if "1337x.to" in url:
            return _cf_challenge()
        return _ok_page()

    result, calls = _run(monkeypatch, get_impl, bypass_enabled=False)

    assert result.success is True
    assert result.cloudflare_detected is True  # a mirror was challenged
    assert "mirror" in result.message
    # It must have probed past the walled primary to a working mirror.
    assert len(calls) >= 2


def test_all_mirrors_cf_walled_and_bypass_disabled_reports_enable_hint(monkeypatch):
    result, calls = _run(monkeypatch, lambda _u: _cf_challenge(), bypass_enabled=False)

    assert result.success is False
    assert result.cloudflare_detected is True
    assert "bypass is disabled" in result.message
    # Every mirror was probed before giving up.
    assert len(calls) >= 2


def test_primary_succeeds_without_touching_mirrors(monkeypatch):
    result, calls = _run(monkeypatch, lambda _u: _ok_page(), bypass_enabled=False)

    assert result.success is True
    assert result.cloudflare_detected is False
    assert len(calls) == 1  # short-circuits on first working mirror


def test_connection_error_log_never_contains_api_key(monkeypatch, caplog):
    import logging

    import requests as _requests

    secret = "sekrit-api-key-12345"
    site = SimpleNamespace(
        name="mytorznab",
        site_type="torznab",
        url="https://indexer.example.com/api",
        api_key=secret,
        cloudflare_protected=False,
    )

    def fake_get(url, **kwargs):
        # Reproduce the real leak shape: the message embeds the full URL
        # with the query string, like urllib3's MaxRetryError does.
        params = kwargs.get("params") or {}
        query = "&".join(f"{k}={v}" for k, v in params.items())
        err_msg = f"Max retries exceeded with url: {url}?{query}"
        raise _requests.ConnectionError(err_msg)

    monkeypatch.setattr(router.requests, "get", fake_get)
    monkeypatch.setattr(
        cf,
        "get_cloudflare_bypass",
        lambda: SimpleNamespace(
            config=SimpleNamespace(enabled=False),
            get_cached_session=lambda _domain: None,
            solve=lambda *_a, **_k: None,
        ),
    )

    with caplog.at_level(logging.DEBUG):
        result = asyncio.run(
            router._run_site_test(site, "site-1", _StubRepo(), progress=None)
        )

    assert result.success is False
    combined = "\n".join(
        record.getMessage() + str(record.exc_text or "") for record in caplog.records
    )
    assert secret not in combined
    assert "mirror connection error" in combined
