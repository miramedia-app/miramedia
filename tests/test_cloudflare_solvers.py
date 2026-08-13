"""Tests for Cloudflare external solver backends and the selection factory.

Solver HTTP boundary map (stub at module-level ``httpx.post``):

FlareSolverrSolver (``miramedia.cloudflare.solvers.proxy``):
  - POST ``{endpoint}/v1`` with ``cmd``, ``url``, ``maxTimeout`` (ms), optional ``proxy``.
  - Success: ``{"status": "ok", "solution": {"response": html, "cookies": [{name,value}],
    "userAgent": ua}}`` → ``SolveResult`` with mapped html/cookies/user_agent.
  - Failures: any ``httpx``/parse exception → None; non-2xx (``raise_for_status``) → None;
    ``status != "ok"`` → None; HTTP 200 ``status: ok`` with missing/empty ``solution`` or
    with no html and no cookies → None (see ``test_flare_solver_malformed_success_returns_none``).

BrowserRunSolver (``miramedia.cloudflare.solvers.browser_run``):
  - POST Cloudflare ``/browser-rendering/content`` with Bearer token, ``{"url": ...}``.
  - Success: ``{"success": true, "result": "<html>"}`` → ``SolveResult(html=...)``, cookies empty.
  - Failures: missing account_id/api_token → None; any HTTP/parse exception → None;
    ``success: false`` → None; HTTP 200 ``success: true`` without a non-empty string
    ``result`` → None (see ``test_browser_run_malformed_success_returns_none``).

FirecrawlSolver (``miramedia.cloudflare.solvers.firecrawl``):
  - POST ``{base_url}/v1/scrape`` with Bearer token, ``{"url", "formats", "proxy"}``.
  - Success: ``{"success": true, "data": {"rawHtml": ...}}`` → ``SolveResult(html=...)``.
  - Failures: missing api_key → None; any HTTP/parse exception → None;
    ``success: false`` → None; HTTP 200 ``success: true`` without ``data``/html fields →
    None (see ``test_firecrawl_malformed_success_returns_none``).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from miramedia.cloudflare.config import (
    BrowserRunConfig,
    ByparrConfig,
    CloudflareConfig,
    FirecrawlConfig,
    FlareSolverrConfig,
)
from miramedia.cloudflare.solvers import CloudflareSolver, SolveResult, get_solver
from miramedia.cloudflare.solvers.browser_run import BrowserRunSolver
from miramedia.cloudflare.solvers.firecrawl import FirecrawlSolver
from miramedia.cloudflare.solvers.proxy import FlareSolverrSolver

_TEST_URL = "https://indexer.example/search"
_CUSTOM_TIMEOUT = 42.0


def _flare_success_payload(
    *,
    html: str = "<html>ok</html>",
    cookies: list[dict[str, str]] | None = None,
    user_agent: str = "TestAgent/1.0",
) -> dict[str, Any]:
    return {
        "status": "ok",
        "solution": {
            "response": html,
            "cookies": cookies
            if cookies is not None
            else [{"name": "cf_clearance", "value": "token123"}],
            "userAgent": user_agent,
        },
    }


def _httpx_json_response(
    data: dict[str, Any] | None = None,
    *,
    status_code: int = 200,
    json_side_effect: Exception | None = None,
    raise_status: bool = False,
) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    if json_side_effect is not None:
        resp.json.side_effect = json_side_effect
    else:
        resp.json.return_value = data or {}
    if raise_status:
        request = httpx.Request("POST", _TEST_URL)
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "server error",
            request=request,
            response=resp,
        )
    else:
        resp.raise_for_status.return_value = None
    return resp


def _cf_config(solver: str, **overrides: Any) -> CloudflareConfig:
    return CloudflareConfig(solver=solver, **overrides)


# ---------------------------------------------------------------------------
# Factory (get_solver)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("solver_name", "expected_url"),
    [
        ("byparr", "http://byparr-custom:9191"),
        ("flaresolverr", "http://flare-custom:8181"),
    ],
)
def test_get_solver_flare_family_returns_correct_endpoint_and_label(
    solver_name: str,
    expected_url: str,
) -> None:
    cfg = _cf_config(
        solver_name,
        byparr=ByparrConfig(url="http://byparr-custom:9191"),
        flaresolverr=FlareSolverrConfig(url="http://flare-custom:8181"),
    )
    solver = get_solver(cfg)
    assert isinstance(solver, FlareSolverrSolver)
    assert solver.label == solver_name
    assert solver.endpoint == expected_url
    assert isinstance(solver, CloudflareSolver)


def test_get_solver_browser_run() -> None:
    cfg = _cf_config(
        "browser_run",
        browser_run=BrowserRunConfig(account_id="acct", api_token="tok"),
    )
    solver = get_solver(cfg)
    assert isinstance(solver, BrowserRunSolver)
    assert isinstance(solver, CloudflareSolver)
    assert solver.account_id == "acct"
    assert solver.api_token == "tok"


def test_get_solver_firecrawl() -> None:
    cfg = _cf_config(
        "firecrawl",
        firecrawl=FirecrawlConfig(api_key="key", base_url="https://fc.example"),
    )
    solver = get_solver(cfg)
    assert isinstance(solver, FirecrawlSolver)
    assert isinstance(solver, CloudflareSolver)
    assert solver.api_key == "key"
    assert solver.base_url == "https://fc.example"


def test_get_solver_unknown_name_raises_value_error() -> None:
    with pytest.raises(ValueError, match="unknown external Cloudflare solver: 'bogus'"):
        get_solver(_cf_config("bogus"))


# ---------------------------------------------------------------------------
# FlareSolverr / Byparr success mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("label", ["byparr", "flaresolverr"])
def test_flare_solver_maps_success_response(
    monkeypatch: pytest.MonkeyPatch,
    label: str,
) -> None:
    captured: dict[str, Any] = {}

    def fake_post(url: str, **kwargs: Any) -> MagicMock:
        captured["url"] = url
        captured["kwargs"] = kwargs
        return _httpx_json_response(_flare_success_payload())

    monkeypatch.setattr("miramedia.cloudflare.solvers.proxy.httpx.post", fake_post)
    sub_url = "http://byparr:8191" if label == "byparr" else "http://flaresolverr:8191"
    solver = FlareSolverrSolver(endpoint=sub_url, timeout=_CUSTOM_TIMEOUT, label=label)
    result = solver.solve(_TEST_URL)

    assert result == SolveResult(
        html="<html>ok</html>",
        cookies={"cf_clearance": "token123"},
        user_agent="TestAgent/1.0",
    )
    assert captured["url"] == f"{sub_url}/v1"
    assert captured["kwargs"]["json"]["cmd"] == "request.get"
    assert captured["kwargs"]["json"]["url"] == _TEST_URL


@pytest.mark.parametrize("label", ["byparr", "flaresolverr"])
def test_flare_solver_cookieless_response_still_returns_html(
    monkeypatch: pytest.MonkeyPatch,
    label: str,
) -> None:
    def fake_post(*_args: Any, **_kwargs: Any) -> MagicMock:
        return _httpx_json_response(
            _flare_success_payload(html="<html>only</html>", cookies=[], user_agent="")
        )

    monkeypatch.setattr("miramedia.cloudflare.solvers.proxy.httpx.post", fake_post)
    solver = FlareSolverrSolver(
        endpoint="http://sidecar:8191", timeout=_CUSTOM_TIMEOUT, label=label
    )
    result = solver.solve(_TEST_URL)

    assert result is not None
    assert result.html == "<html>only</html>"
    assert result.cookies == {}
    assert result.user_agent == ""


# ---------------------------------------------------------------------------
# BrowserRun success mapping
# ---------------------------------------------------------------------------


def test_browser_run_maps_success_response(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_post(url: str, **kwargs: Any) -> MagicMock:
        captured["url"] = url
        captured["kwargs"] = kwargs
        return _httpx_json_response(
            {"success": True, "result": "<html>rendered</html>"}
        )

    monkeypatch.setattr(
        "miramedia.cloudflare.solvers.browser_run.httpx.post", fake_post
    )
    solver = BrowserRunSolver(
        BrowserRunConfig(account_id="acct-1", api_token="token-1"),
        timeout=_CUSTOM_TIMEOUT,
    )
    result = solver.solve(_TEST_URL)

    assert result == SolveResult(html="<html>rendered</html>")
    assert "acct-1" in captured["url"]
    assert captured["kwargs"]["headers"]["Authorization"] == "Bearer token-1"


def test_browser_run_html_only_has_empty_cookies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_post(*_args: Any, **_kwargs: Any) -> MagicMock:
        return _httpx_json_response({"success": True, "result": "<html>only</html>"})

    monkeypatch.setattr(
        "miramedia.cloudflare.solvers.browser_run.httpx.post", fake_post
    )
    solver = BrowserRunSolver(
        BrowserRunConfig(account_id="acct", api_token="tok"),
        timeout=_CUSTOM_TIMEOUT,
    )
    result = solver.solve(_TEST_URL)

    assert result is not None
    assert result.cookies == {}
    assert result.html == "<html>only</html>"


# ---------------------------------------------------------------------------
# Firecrawl success mapping
# ---------------------------------------------------------------------------


def test_firecrawl_maps_success_response(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_post(url: str, **kwargs: Any) -> MagicMock:
        captured["url"] = url
        captured["kwargs"] = kwargs
        return _httpx_json_response(
            {"success": True, "data": {"rawHtml": "<html>scraped</html>"}}
        )

    monkeypatch.setattr("miramedia.cloudflare.solvers.firecrawl.httpx.post", fake_post)
    solver = FirecrawlSolver(
        FirecrawlConfig(api_key="fc-key", base_url="https://api.firecrawl.dev"),
        timeout=_CUSTOM_TIMEOUT,
    )
    result = solver.solve(_TEST_URL)

    assert result == SolveResult(html="<html>scraped</html>")
    assert captured["url"] == "https://api.firecrawl.dev/v1/scrape"
    assert captured["kwargs"]["json"]["formats"] == ["rawHtml"]


def test_firecrawl_cookieless_response_still_returns_html(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_post(*_args: Any, **_kwargs: Any) -> MagicMock:
        return _httpx_json_response(
            {"success": True, "data": {"html": "<html>alt</html>"}}
        )

    monkeypatch.setattr("miramedia.cloudflare.solvers.firecrawl.httpx.post", fake_post)
    solver = FirecrawlSolver(
        FirecrawlConfig(api_key="fc-key"),
        timeout=_CUSTOM_TIMEOUT,
    )
    result = solver.solve(_TEST_URL)

    assert result is not None
    assert result.html == "<html>alt</html>"
    assert result.cookies == {}


# ---------------------------------------------------------------------------
# Malformed-success shapes (HTTP 200, missing expected keys)
# ---------------------------------------------------------------------------

_FLARE_MALFORMED_SUCCESS = [
    pytest.param({"status": "ok"}, id="missing_solution"),
    pytest.param({"status": "ok", "solution": {}}, id="empty_solution"),
    pytest.param(
        {"status": "ok", "solution": {"cookies": [], "userAgent": ""}},
        id="missing_response",
    ),
]

_BROWSER_RUN_MALFORMED_SUCCESS = [
    pytest.param({"success": True}, id="missing_result"),
    pytest.param({"success": True, "result": None}, id="null_result"),
    pytest.param({"success": True, "result": {}}, id="dict_result"),
]

_FIRECRAWL_MALFORMED_SUCCESS = [
    pytest.param({"success": True}, id="missing_data"),
    pytest.param({"success": True, "data": {}}, id="empty_data"),
    pytest.param({"success": True, "data": {"metadata": {}}}, id="no_html_fields"),
]


@pytest.mark.parametrize("payload", _FLARE_MALFORMED_SUCCESS)
def test_flare_solver_malformed_success_returns_none(
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, Any],
) -> None:
    monkeypatch.setattr(
        "miramedia.cloudflare.solvers.proxy.httpx.post",
        lambda *_a, **_k: _httpx_json_response(payload),
    )
    solver = FlareSolverrSolver(endpoint="http://sidecar:8191", timeout=30.0)
    assert solver.solve(_TEST_URL) is None


@pytest.mark.parametrize("payload", _BROWSER_RUN_MALFORMED_SUCCESS)
def test_browser_run_malformed_success_returns_none(
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, Any],
) -> None:
    monkeypatch.setattr(
        "miramedia.cloudflare.solvers.browser_run.httpx.post",
        lambda *_a, **_k: _httpx_json_response(payload),
    )
    solver = BrowserRunSolver(
        BrowserRunConfig(account_id="acct", api_token="tok"),
        timeout=30.0,
    )
    assert solver.solve(_TEST_URL) is None


@pytest.mark.parametrize("payload", _FIRECRAWL_MALFORMED_SUCCESS)
def test_firecrawl_malformed_success_returns_none(
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, Any],
) -> None:
    monkeypatch.setattr(
        "miramedia.cloudflare.solvers.firecrawl.httpx.post",
        lambda *_a, **_k: _httpx_json_response(payload),
    )
    solver = FirecrawlSolver(FirecrawlConfig(api_key="key"), timeout=30.0)
    assert solver.solve(_TEST_URL) is None


# ---------------------------------------------------------------------------
# Never-raise contract
# ---------------------------------------------------------------------------

_FLARE_FAILURES = [
    pytest.param(
        httpx.ConnectTimeout("timed out"),
        id="connect_timeout",
    ),
    pytest.param(
        httpx.ConnectError("connection refused"),
        id="connection_error",
    ),
    pytest.param(
        _httpx_json_response(raise_status=True),
        id="http_500",
    ),
    pytest.param(
        _httpx_json_response(json_side_effect=ValueError("bad json")),
        id="malformed_json",
    ),
    pytest.param(
        _httpx_json_response({"status": "error", "message": "failed"}),
        id="status_not_ok",
    ),
]

_BROWSER_RUN_FAILURES = [
    pytest.param(
        httpx.ReadTimeout("timed out"),
        id="read_timeout",
    ),
    pytest.param(
        httpx.ConnectError("connection refused"),
        id="connection_error",
    ),
    pytest.param(
        _httpx_json_response(raise_status=True),
        id="http_500",
    ),
    pytest.param(
        _httpx_json_response(json_side_effect=ValueError("bad json")),
        id="malformed_json",
    ),
    pytest.param(
        _httpx_json_response({"success": False, "errors": [{"message": "nope"}]}),
        id="success_false",
    ),
]

_FIRECRAWL_FAILURES = [
    pytest.param(
        httpx.WriteTimeout("timed out"),
        id="write_timeout",
    ),
    pytest.param(
        httpx.ConnectError("connection refused"),
        id="connection_error",
    ),
    pytest.param(
        _httpx_json_response(raise_status=True),
        id="http_500",
    ),
    pytest.param(
        _httpx_json_response(json_side_effect=ValueError("bad json")),
        id="malformed_json",
    ),
    pytest.param(
        _httpx_json_response({"success": False}),
        id="success_false",
    ),
]


@pytest.mark.parametrize("post_result", _FLARE_FAILURES)
def test_flare_solver_never_raises_on_failure(
    monkeypatch: pytest.MonkeyPatch,
    post_result: Exception | MagicMock,
) -> None:
    if isinstance(post_result, Exception):
        monkeypatch.setattr(
            "miramedia.cloudflare.solvers.proxy.httpx.post",
            MagicMock(side_effect=post_result),
        )
    else:
        monkeypatch.setattr(
            "miramedia.cloudflare.solvers.proxy.httpx.post",
            MagicMock(return_value=post_result),
        )

    solver = FlareSolverrSolver(endpoint="http://sidecar:8191", timeout=30.0)
    assert solver.solve(_TEST_URL) is None
    assert solver.solve(_TEST_URL, progress=lambda _m: None) is None


@pytest.mark.parametrize("post_result", _BROWSER_RUN_FAILURES)
def test_browser_run_never_raises_on_failure(
    monkeypatch: pytest.MonkeyPatch,
    post_result: Exception | MagicMock,
) -> None:
    if isinstance(post_result, Exception):
        monkeypatch.setattr(
            "miramedia.cloudflare.solvers.browser_run.httpx.post",
            MagicMock(side_effect=post_result),
        )
    else:
        monkeypatch.setattr(
            "miramedia.cloudflare.solvers.browser_run.httpx.post",
            MagicMock(return_value=post_result),
        )

    solver = BrowserRunSolver(
        BrowserRunConfig(account_id="acct", api_token="tok"),
        timeout=30.0,
    )
    assert solver.solve(_TEST_URL) is None
    assert solver.solve(_TEST_URL, progress=lambda _m: None) is None


def test_browser_run_missing_credentials_returns_none_without_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    post = MagicMock()
    monkeypatch.setattr("miramedia.cloudflare.solvers.browser_run.httpx.post", post)
    solver = BrowserRunSolver(BrowserRunConfig(), timeout=30.0)

    assert solver.solve(_TEST_URL) is None
    post.assert_not_called()


@pytest.mark.parametrize("post_result", _FIRECRAWL_FAILURES)
def test_firecrawl_never_raises_on_failure(
    monkeypatch: pytest.MonkeyPatch,
    post_result: Exception | MagicMock,
) -> None:
    if isinstance(post_result, Exception):
        monkeypatch.setattr(
            "miramedia.cloudflare.solvers.firecrawl.httpx.post",
            MagicMock(side_effect=post_result),
        )
    else:
        monkeypatch.setattr(
            "miramedia.cloudflare.solvers.firecrawl.httpx.post",
            MagicMock(return_value=post_result),
        )

    solver = FirecrawlSolver(FirecrawlConfig(api_key="key"), timeout=30.0)
    assert solver.solve(_TEST_URL) is None
    assert solver.solve(_TEST_URL, progress=lambda _m: None) is None


def test_firecrawl_missing_api_key_returns_none_without_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    post = MagicMock()
    monkeypatch.setattr("miramedia.cloudflare.solvers.firecrawl.httpx.post", post)
    solver = FirecrawlSolver(FirecrawlConfig(api_key=""), timeout=30.0)

    assert solver.solve(_TEST_URL) is None
    post.assert_not_called()


# ---------------------------------------------------------------------------
# Timeout wiring
# ---------------------------------------------------------------------------


def test_flare_solver_passes_timeout_to_httpx(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_post(*_a: Any, **kwargs: Any) -> MagicMock:
        captured.update(kwargs)
        return _httpx_json_response(_flare_success_payload())

    monkeypatch.setattr("miramedia.cloudflare.solvers.proxy.httpx.post", fake_post)
    solver = FlareSolverrSolver(endpoint="http://sidecar:8191", timeout=_CUSTOM_TIMEOUT)
    solver.solve(_TEST_URL)

    assert captured["timeout"] == _CUSTOM_TIMEOUT + 30


def test_browser_run_passes_timeout_to_httpx(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_post(*_a: Any, **kwargs: Any) -> MagicMock:
        captured.update(kwargs)
        return _httpx_json_response({"success": True, "result": "ok"})

    monkeypatch.setattr(
        "miramedia.cloudflare.solvers.browser_run.httpx.post", fake_post
    )
    solver = BrowserRunSolver(
        BrowserRunConfig(account_id="acct", api_token="tok"),
        timeout=_CUSTOM_TIMEOUT,
    )
    solver.solve(_TEST_URL)

    assert captured["timeout"] == _CUSTOM_TIMEOUT


def test_firecrawl_passes_timeout_to_httpx(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_post(*_a: Any, **kwargs: Any) -> MagicMock:
        captured.update(kwargs)
        return _httpx_json_response({"success": True, "data": {"rawHtml": "ok"}})

    monkeypatch.setattr("miramedia.cloudflare.solvers.firecrawl.httpx.post", fake_post)
    solver = FirecrawlSolver(FirecrawlConfig(api_key="key"), timeout=_CUSTOM_TIMEOUT)
    solver.solve(_TEST_URL)

    assert captured["timeout"] == _CUSTOM_TIMEOUT
