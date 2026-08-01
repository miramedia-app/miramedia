"""Tests for the shared in-process sliding-window rate limiter."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.datastructures import FormData


def test_limiter_allows_up_to_max_then_raises_429() -> None:
    from miramedia.rate_limit import SlidingWindowLimiter

    limiter = SlidingWindowLimiter(max_requests=3, window_seconds=60.0)
    for _ in range(3):
        limiter.check("client-a")
    with pytest.raises(HTTPException) as exc_info:
        limiter.check("client-a")
    assert exc_info.value.status_code == 429
    assert exc_info.value.headers is not None
    assert "Retry-After" in exc_info.value.headers


def test_limiter_window_expiry_allows_again(monkeypatch: pytest.MonkeyPatch) -> None:
    from miramedia.rate_limit import SlidingWindowLimiter

    limiter = SlidingWindowLimiter(max_requests=2, window_seconds=10.0)
    current = 1000.0
    monkeypatch.setattr(time, "monotonic", lambda: current)

    limiter.check("client-a")
    limiter.check("client-a")
    with pytest.raises(HTTPException):
        limiter.check("client-a")

    current += 11.0
    limiter.check("client-a")


def test_limiter_keys_are_independent() -> None:
    from miramedia.rate_limit import SlidingWindowLimiter

    limiter = SlidingWindowLimiter(max_requests=1, window_seconds=60.0)
    limiter.check("client-a")
    with pytest.raises(HTTPException):
        limiter.check("client-a")
    limiter.check("client-b")


def test_login_limiter_only_applies_to_login_path() -> None:
    from miramedia.main import _limit_login, _login_limiter

    _login_limiter._buckets.clear()

    login_request = MagicMock()
    login_request.client = SimpleNamespace(host="203.0.113.1")
    login_request.url.path = "/api/v1/auth/cookie/login"

    logout_request = MagicMock()
    logout_request.client = SimpleNamespace(host="203.0.113.1")
    logout_request.url.path = "/api/v1/auth/cookie/logout"

    async def _run() -> None:
        for _ in range(10):
            await _limit_login(login_request)
        with pytest.raises(HTTPException) as exc_info:
            await _limit_login(login_request)
        assert exc_info.value.status_code == 429

        for _ in range(20):
            await _limit_login(logout_request)

    asyncio.run(_run())


def test_shim_failed_auth_is_throttled_correct_key_unlimited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from miramedia.subtitles.arr_shim import auth as shim_auth
    from miramedia.subtitles.config import BazarrConfig, SubtitleConfig

    shim_auth._failed_auth_limiter._buckets.clear()

    config = MagicMock()
    config.subtitles = SubtitleConfig(bazarr=BazarrConfig(shim_api_key="good-key"))
    monkeypatch.setattr(shim_auth, "MiraMediaConfig", lambda: config)

    request = MagicMock()
    request.client = SimpleNamespace(host="198.51.100.9")
    request.query_params = {"apikey": "bad-key"}
    request.headers = {}

    async def _run() -> None:
        for _ in range(30):
            with pytest.raises(HTTPException) as exc_info:
                await shim_auth.require_shim_api_key(request)
            assert exc_info.value.status_code == 401

        with pytest.raises(HTTPException) as exc_info:
            await shim_auth.require_shim_api_key(request)
        assert exc_info.value.status_code == 429

        request.query_params = {"apikey": "good-key"}
        for _ in range(50):
            await shim_auth.require_shim_api_key(request)

    asyncio.run(_run())


def test_lru_eviction_preserves_at_limit_bucket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from miramedia.rate_limit import SlidingWindowLimiter

    monkeypatch.setattr("miramedia.rate_limit._MAX_KEYS", 2)
    limiter = SlidingWindowLimiter(max_requests=2, window_seconds=60.0)

    limiter.check("hot")
    limiter.check("hot")
    limiter.check("cold-a")
    limiter.check("cold-b")

    assert "hot" in limiter._buckets
    assert "cold-a" not in limiter._buckets
    assert "cold-b" in limiter._buckets


def test_lru_recency_protects_key_from_eviction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from miramedia.rate_limit import SlidingWindowLimiter

    monkeypatch.setattr("miramedia.rate_limit._MAX_KEYS", 3)
    limiter = SlidingWindowLimiter(max_requests=2, window_seconds=60.0)

    limiter.check("old")
    limiter.check("middle")
    limiter.check("new")
    limiter.check("old")

    limiter.check("overflow-a")
    limiter.check("overflow-b")

    assert "old" in limiter._buckets
    assert "middle" not in limiter._buckets


def test_login_account_bucket_limits_across_client_hosts() -> None:
    from miramedia.main import _limit_login, _login_limiter

    _login_limiter._buckets.clear()

    async def _run() -> None:
        for index in range(10):
            request = MagicMock()
            request.client = SimpleNamespace(host=f"203.0.113.{index + 1}")
            request.url.path = "/api/v1/auth/cookie/login"
            request.form = AsyncMock(
                return_value=FormData(
                    {"username": "victim@example.com", "password": "wrong"}
                )
            )
            await _limit_login(request)

        blocked = MagicMock()
        blocked.client = SimpleNamespace(host="203.0.113.99")
        blocked.url.path = "/api/v1/auth/cookie/login"
        blocked.form = AsyncMock(
            return_value=FormData(
                {"username": "victim@example.com", "password": "wrong"}
            )
        )
        with pytest.raises(HTTPException) as exc_info:
            await _limit_login(blocked)
        assert exc_info.value.status_code == 429

    asyncio.run(_run())


@asynccontextmanager
async def _auth_route_client(
    *,
    allow_registration: bool = True,
) -> AsyncGenerator[TestClient]:
    from miramedia.auth.users import get_user_manager
    from miramedia.database import get_session
    from miramedia.main import app

    user_manager = MagicMock()
    user_manager.create = AsyncMock(
        side_effect=HTTPException(
            status_code=400, detail="REGISTER_USER_ALREADY_EXISTS"
        )
    )
    user_manager.request_verify = AsyncMock(return_value=None)

    async def _stub_session() -> Any:
        yield None

    prior = dict(app.dependency_overrides)
    app.dependency_overrides[get_session] = _stub_session
    app.dependency_overrides[get_user_manager] = lambda: user_manager
    try:
        with patch(
            "miramedia.main.MiraMediaConfig",
            lambda: MagicMock(auth=MagicMock(allow_registration=allow_registration)),
        ):
            client = TestClient(app, raise_server_exceptions=False)
            try:
                yield client
            finally:
                client.close()
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(prior)


def test_register_returns_429_after_budget_exhausted() -> None:
    from miramedia.main import _register_limiter

    _register_limiter._buckets.clear()

    async def _run() -> None:
        async with _auth_route_client() as client:
            for _ in range(5):
                response = client.post(
                    "/api/v1/auth/register",
                    json={
                        "email": "new@example.com",
                        "password": "long-enough-password",
                    },
                )
                assert response.status_code != 429, response.text

            response = client.post(
                "/api/v1/auth/register",
                json={
                    "email": "new@example.com",
                    "password": "long-enough-password",
                },
            )
            assert response.status_code == 429

    asyncio.run(_run())


def test_request_verify_token_returns_429_after_budget_exhausted() -> None:
    from miramedia.main import _verify_limiter

    _verify_limiter._buckets.clear()

    async def _run() -> None:
        async with _auth_route_client() as client:
            for _ in range(5):
                response = client.post(
                    "/api/v1/auth/request-verify-token",
                    json={"email": "verify@example.com"},
                )
                assert response.status_code != 429, response.text

            response = client.post(
                "/api/v1/auth/request-verify-token",
                json={"email": "verify@example.com"},
            )
            assert response.status_code == 429

    asyncio.run(_run())


def test_login_form_still_readable_after_limiter_dependency() -> None:
    from miramedia.auth.users import get_user_manager
    from miramedia.database import get_session
    from miramedia.main import _login_limiter, app

    _login_limiter._buckets.clear()

    user_manager = MagicMock()
    user_manager.authenticate = AsyncMock(return_value=None)

    async def _stub_session() -> Any:
        yield None

    prior = dict(app.dependency_overrides)
    app.dependency_overrides[get_session] = _stub_session
    app.dependency_overrides[get_user_manager] = lambda: user_manager
    try:
        client = TestClient(app, raise_server_exceptions=False)
        response = client.post(
            "/api/v1/auth/cookie/login",
            data={"username": "reader@example.com", "password": "wrong-password"},
        )
        assert response.status_code == 400, response.text
        user_manager.authenticate.assert_awaited_once()
        credentials = user_manager.authenticate.await_args.args[0]
        assert credentials.username == "reader@example.com"
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(prior)


def test_effective_budget_divides_by_worker_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from miramedia.rate_limit import effective_budget

    monkeypatch.setenv("MIRAMEDIA_WEB_WORKERS", "4")
    assert effective_budget(10) == 2


def test_effective_budget_single_worker_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from miramedia.rate_limit import effective_budget

    monkeypatch.setenv("MIRAMEDIA_WEB_WORKERS", "1")
    assert effective_budget(10) == 10


def test_effective_budget_floors_to_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from miramedia.rate_limit import effective_budget

    monkeypatch.setenv("MIRAMEDIA_WEB_WORKERS", "100")
    assert effective_budget(10) == 1


def test_effective_budget_invalid_env_falls_back(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from miramedia.rate_limit import effective_budget

    monkeypatch.setenv("MIRAMEDIA_WEB_WORKERS", "not-a-number")
    with caplog.at_level(logging.WARNING, logger="miramedia.rate_limit"):
        assert effective_budget(10) == 10
    assert any(
        "Invalid MIRAMEDIA_WEB_WORKERS" in record.getMessage()
        for record in caplog.records
    )


def test_configured_workers_invalid_env_falls_back(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from miramedia.rate_limit import configured_workers

    monkeypatch.setenv("MIRAMEDIA_WEB_WORKERS", "not-a-number")
    with caplog.at_level(logging.WARNING, logger="miramedia.rate_limit"):
        assert configured_workers() == 1
    assert any(
        "Invalid MIRAMEDIA_WEB_WORKERS" in record.getMessage()
        for record in caplog.records
    )


def test_login_budget_shared_across_jwt_and_cookie_paths() -> None:
    from miramedia.main import _limit_login, _login_limiter

    _login_limiter._buckets.clear()

    jwt_path = "/api/v1/auth/jwt/login"
    cookie_path = "/api/v1/auth/cookie/login"
    client_host = "203.0.113.50"
    email = "shared@example.com"

    async def _run() -> None:
        for _ in range(10):
            request = MagicMock()
            request.client = SimpleNamespace(host=client_host)
            request.url.path = jwt_path
            request.form = AsyncMock(
                return_value=FormData({"username": email, "password": "wrong"})
            )
            await _limit_login(request)

        blocked = MagicMock()
        blocked.client = SimpleNamespace(host=client_host)
        blocked.url.path = cookie_path
        blocked.form = AsyncMock(
            return_value=FormData({"username": email, "password": "wrong"})
        )
        with pytest.raises(HTTPException) as exc_info:
            await _limit_login(blocked)
        assert exc_info.value.status_code == 429

    asyncio.run(_run())
