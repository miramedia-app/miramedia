"""Baseline security response header coverage for the API app."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware

from miramedia.core.security_headers import SecurityHeadersMiddleware
from miramedia.settings.service import apply_live_config_from_overrides

_BASELINE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}
_HSTS_HEADER = "Strict-Transport-Security"
_HSTS_VALUE = "max-age=31536000; includeSubDomains"
_CSP_REPORT_ONLY_HEADER = "Content-Security-Policy-Report-Only"
_CSP_HEADER = "Content-Security-Policy"
_CSP_PREFIX = "default-src 'self'"


def _health_client() -> TestClient:
    from miramedia.database import get_session
    from miramedia.main import app

    async def _stub_session() -> Any:
        yield None

    app.dependency_overrides[get_session] = _stub_session
    return TestClient(app, raise_server_exceptions=False)


def _clear_overrides() -> None:
    from miramedia.main import app

    app.dependency_overrides.clear()


def _patch_cors_allow_origins(client: TestClient, origins: list[str]) -> None:
    from miramedia.main import app

    if app.middleware_stack is None:
        client.get("/api/v1/health")
    stack: Any = app.middleware_stack
    while stack is not None:
        if type(stack).__name__ == "CORSMiddleware":
            stack.allow_origins = origins
            return
        stack = getattr(stack, "app", None)
    pytest.fail("CORSMiddleware not found in middleware stack")


def test_baseline_security_headers_on_json_response() -> None:
    client = _health_client()
    try:
        response = client.get("/api/v1/health")
        assert response.status_code == 200, response.text
        for name, value in _BASELINE_HEADERS.items():
            assert response.headers.get(name) == value
        assert _HSTS_HEADER not in response.headers
    finally:
        _clear_overrides()


@pytest.mark.parametrize(
    ("overrides", "expect_hsts"),
    [
        ({"misc": {"frontend_url": "http://localhost:8000/"}}, False),
        ({"misc": {"frontend_url": "https://media.example.com/"}}, True),
        (
            {
                "misc": {"frontend_url": "http://localhost:8000/"},
                "auth": {"cookie_secure": True},
            },
            True,
        ),
    ],
)
def test_hsts_gated_on_secure_cookie_logic(
    overrides: dict[str, Any],
    expect_hsts: bool,
) -> None:
    apply_live_config_from_overrides(overrides)
    client = _health_client()
    try:
        response = client.get("/api/v1/health")
        assert response.status_code == 200, response.text
        for name, value in _BASELINE_HEADERS.items():
            assert response.headers.get(name) == value
        if expect_hsts:
            assert response.headers.get(_HSTS_HEADER) == _HSTS_VALUE
        else:
            assert _HSTS_HEADER not in response.headers
    finally:
        _clear_overrides()


def test_correlation_id_still_present_with_security_headers() -> None:
    client = _health_client()
    try:
        response = client.get("/api/v1/health")
        assert response.status_code == 200, response.text
        assert response.headers.get("X-Correlation-ID")
        for name, value in _BASELINE_HEADERS.items():
            assert response.headers.get(name) == value
    finally:
        _clear_overrides()


def test_cors_still_exposes_total_count_header() -> None:
    from miramedia.main import app

    for middleware in app.user_middleware:
        if middleware.cls is CORSMiddleware:
            expose_headers = middleware.kwargs.get("expose_headers", [])
            assert "X-Total-Count" in expose_headers
            return
    pytest.fail("CORSMiddleware not registered")


def test_csp_report_only_by_default() -> None:
    client = _health_client()
    try:
        response = client.get("/api/v1/health")
        assert response.status_code == 200, response.text
        csp_report_only = response.headers.get(_CSP_REPORT_ONLY_HEADER)
        assert csp_report_only is not None
        assert csp_report_only.startswith(_CSP_PREFIX)
        assert _CSP_HEADER not in response.headers
    finally:
        _clear_overrides()


def test_csp_enforce_toggle() -> None:
    apply_live_config_from_overrides({"misc": {"csp_enforce": True}})
    client = _health_client()
    try:
        response = client.get("/api/v1/health")
        assert response.status_code == 200, response.text
        csp = response.headers.get(_CSP_HEADER)
        assert csp is not None
        assert csp.startswith(_CSP_PREFIX)
        assert _CSP_REPORT_ONLY_HEADER not in response.headers
    finally:
        _clear_overrides()


def test_csp_disabled_toggle() -> None:
    apply_live_config_from_overrides({"misc": {"csp_enabled": False}})
    client = _health_client()
    try:
        response = client.get("/api/v1/health")
        assert response.status_code == 200, response.text
        assert _CSP_HEADER not in response.headers
        assert _CSP_REPORT_ONLY_HEADER not in response.headers
    finally:
        _clear_overrides()


def test_new_baseline_headers_present() -> None:
    client = _health_client()
    try:
        response = client.get("/api/v1/health")
        assert response.status_code == 200, response.text
        assert response.headers.get("Permissions-Policy") == (
            "camera=(), microphone=(), geolocation=()"
        )
        assert response.headers.get("Cross-Origin-Opener-Policy") == "same-origin"
    finally:
        _clear_overrides()


def test_preflight_gets_security_headers() -> None:
    origin = "http://cors-test.example"
    client = _health_client()
    try:
        _patch_cors_allow_origins(client, [origin])
        response = client.options(
            "/api/v1/health",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "GET",
            },
        )
        assert response.status_code == 200, response.text
        assert response.headers.get("X-Content-Type-Options") == "nosniff"
    finally:
        _clear_overrides()


def test_security_headers_middleware_is_pure_asgi() -> None:
    assert not issubclass(SecurityHeadersMiddleware, BaseHTTPMiddleware)
