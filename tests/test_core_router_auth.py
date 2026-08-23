"""Route-level auth tests for core router endpoints."""

from __future__ import annotations

import json
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient


class _FakeResult:
    def __init__(self, row: tuple[int, int]) -> None:
        self._row = row

    def one(self) -> tuple[int, int]:
        return self._row


def test_health_anonymous_returns_minimal_payload() -> None:
    from miramedia.database import get_session
    from miramedia.main import app

    async def _stub_session() -> Any:
        yield None

    app.dependency_overrides[get_session] = _stub_session
    try:
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/v1/health")
        assert response.status_code == 200, response.text
        body = response.json()
        assert set(body.keys()) == {"status", "db", "alembic", "cache"}
        assert body["status"] == "ok"
        for section in ("db", "alembic", "cache"):
            assert set(body[section].keys()) == {"ok"}
        raw = json.dumps(body)
        assert "error" not in raw
        assert "expected_head" not in raw
        assert "pools" not in raw
    finally:
        app.dependency_overrides.clear()


def test_health_anonymous_reports_db_degraded_without_exception_text() -> None:
    from miramedia.database import get_session
    from miramedia.main import app

    async def _stub_session() -> Any:
        yield None

    app.dependency_overrides[get_session] = _stub_session
    try:
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/v1/health")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["db"]["ok"] is False
        raw = json.dumps(body)
        assert "Traceback" not in raw
        assert "Exception" not in raw
        assert "connection" not in raw.lower()
    finally:
        app.dependency_overrides.clear()


def test_health_details_requires_superuser_anonymous() -> None:
    from miramedia.database import get_session
    from miramedia.main import app

    async def _stub_session() -> Any:
        yield None

    app.dependency_overrides[get_session] = _stub_session
    try:
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/v1/health/details")
        assert response.status_code == 401, (
            f"Expected 401 Unauthorized for anonymous /api/v1/health/details, "
            f"got {response.status_code}."
        )
    finally:
        app.dependency_overrides.clear()


def test_health_details_returns_full_payload_for_superuser() -> None:
    from miramedia.auth.users import current_superuser
    from miramedia.database import get_session
    from miramedia.main import app

    async def _stub_session() -> Any:
        yield None

    async def _superuser() -> Any:
        user = MagicMock()
        user.id = uuid.uuid4()
        user.is_superuser = True
        return user

    app.dependency_overrides[get_session] = _stub_session
    app.dependency_overrides[current_superuser] = _superuser
    try:
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/v1/health/details")
        assert response.status_code == 200, response.text
        body = response.json()
        assert "db" in body
        assert "alembic" in body
        assert "cache" in body
        assert "pools" in body["db"] or "error" in body["db"]
    finally:
        app.dependency_overrides.clear()


def test_features_reflects_config_flags() -> None:
    from miramedia.config import MiraMediaConfig
    from miramedia.database import get_session
    from miramedia.main import app

    config = MiraMediaConfig()

    async def _stub_session() -> Any:
        yield None

    app.dependency_overrides[get_session] = _stub_session
    try:
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/v1/features")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body == {
            "requests": config.requests.enabled,
            "subtitles": config.subtitles.enabled,
            "notifications": config.notifications.native.enabled,
            "watchlists": config.watchlists.enabled,
            "custom_lists": config.watchlists.custom_lists_enabled,
            "watch_next": config.watchlists.watch_next_enabled,
            "watch_next_include_specials": config.watchlists.native.watch_next_include_specials,
            "upcoming": config.watchlists.upcoming_enabled,
            "upcoming_default_past_days": config.watchlists.native.upcoming_default_past_days,
            "upcoming_default_future_days": config.watchlists.native.upcoming_default_future_days,
            "continue_watching": config.playback.continue_watching,
            "streaming": config.streams.enabled,
            "downloads": config.streams.downloads,
        }
    finally:
        app.dependency_overrides.clear()


def test_dashboard_summary_requires_auth_anonymous() -> None:
    from miramedia.database import get_session
    from miramedia.main import app

    async def _stub_session() -> Any:
        yield None

    app.dependency_overrides[get_session] = _stub_session
    try:
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/v1/dashboard/summary")
        assert response.status_code == 401, (
            f"Expected 401 Unauthorized for anonymous /api/v1/dashboard/summary, "
            f"got {response.status_code}."
        )
    finally:
        app.dependency_overrides.clear()


def test_dashboard_summary_returns_200_with_auth() -> None:
    from miramedia.auth.users import current_active_user
    from miramedia.config import MiraMediaConfig
    from miramedia.database import get_session
    from miramedia.main import app

    config = MiraMediaConfig()

    async def _stub_session() -> Any:
        db = MagicMock()
        db.scalar = AsyncMock(side_effect=[5, 3, 2, 1])
        db.execute = AsyncMock(
            side_effect=[
                _FakeResult((2, 1)),
                _FakeResult((1, 0)),
            ]
        )
        yield db

    async def _active_user() -> Any:
        user = MagicMock()
        user.id = uuid.uuid4()
        user.is_superuser = False
        return user

    app.dependency_overrides[get_session] = _stub_session
    app.dependency_overrides[current_active_user] = _active_user
    try:
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/v1/dashboard/summary")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body == {
            "shows": 5,
            "movies": 3,
            "torrents": 2,
            "requests_pending": 1 if config.requests.enabled else 0,
            "imports_failed": 3,
            "imports_ambiguous": 1,
        }
    finally:
        app.dependency_overrides.clear()
