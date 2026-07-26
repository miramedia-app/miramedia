"""Auth and status-route tests for the Sonarr/Radarr compatibility shim."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock
from urllib.parse import urlparse

import pytest
from fastapi.testclient import TestClient

from miramedia.subtitles.config import BazarrConfig, SubtitleConfig


def _bazarr_config(**overrides: Any) -> MagicMock:
    bazarr = BazarrConfig(**overrides)
    subtitles = SubtitleConfig(bazarr=bazarr)
    config = MagicMock()
    config.subtitles = subtitles
    return config


@pytest.fixture
def shim_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    from miramedia.database import get_session
    from miramedia.main import app
    from miramedia.subtitles.arr_shim import auth as shim_auth

    async def _stub_session() -> Any:
        yield None

    app.dependency_overrides[get_session] = _stub_session
    monkeypatch.setattr(
        shim_auth,
        "MiraMediaConfig",
        lambda: _bazarr_config(shim_api_key="test-shim-key"),
    )
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def test_shim_rejects_when_key_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    from miramedia.database import get_session
    from miramedia.main import app
    from miramedia.subtitles.arr_shim import auth as shim_auth

    async def _stub_session() -> Any:
        yield None

    app.dependency_overrides[get_session] = _stub_session
    monkeypatch.setattr(
        shim_auth,
        "MiraMediaConfig",
        lambda: _bazarr_config(shim_api_key=""),
    )
    try:
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/sonarr/api/v3/system/status")
        assert response.status_code == 401, response.text
    finally:
        app.dependency_overrides.clear()


def test_shim_rejects_wrong_apikey_query(shim_client: TestClient) -> None:
    response = shim_client.get(
        "/sonarr/api/v3/system/status",
        params={"apikey": "wrong-key"},
    )
    assert response.status_code == 401, response.text


def test_sonarr_status_with_apikey_query(shim_client: TestClient) -> None:
    response = shim_client.get(
        "/sonarr/api/v3/system/status",
        params={"apikey": "test-shim-key"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["version"].startswith("4.")
    assert body["appName"] == "Sonarr"
    assert body["instanceName"] == "MiraMedia"


def test_radarr_status_with_apikey_query(shim_client: TestClient) -> None:
    response = shim_client.get(
        "/radarr/api/v3/system/status",
        params={"apikey": "test-shim-key"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["version"].startswith("5.")
    assert body["appName"] == "Radarr"


def test_shim_accepts_x_api_key_header(shim_client: TestClient) -> None:
    response = shim_client.get(
        "/sonarr/api/v3/system/status",
        headers={"X-Api-Key": "test-shim-key"},
    )
    assert response.status_code == 200, response.text
    assert "version" in response.json()


def test_shim_rejects_non_ascii_apikey_query(shim_client: TestClient) -> None:
    response = shim_client.get(
        "/sonarr/api/v3/system/status",
        params={"apikey": "kéy"},
    )
    assert response.status_code == 401, response.text


def test_shim_rejects_non_ascii_x_api_key_header(shim_client: TestClient) -> None:
    response = shim_client.get(
        "/sonarr/api/v3/system/status",
        headers={"X-Api-Key": "kéy".encode()},
    )
    assert response.status_code == 401, response.text


def test_shim_accepts_non_ascii_configured_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from miramedia.database import get_session
    from miramedia.main import app
    from miramedia.subtitles.arr_shim import auth as shim_auth

    async def _stub_session() -> Any:
        yield None

    app.dependency_overrides[get_session] = _stub_session
    monkeypatch.setattr(
        shim_auth,
        "MiraMediaConfig",
        lambda: _bazarr_config(shim_api_key="kéy"),
    )
    try:
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get(
            "/sonarr/api/v3/system/status",
            params={"apikey": "kéy"},
        )
        assert response.status_code == 200, response.text
        assert "version" in response.json()
    finally:
        app.dependency_overrides.clear()


def test_sonarr_legacy_system_status(shim_client: TestClient) -> None:
    response = shim_client.get(
        "/sonarr/api/system/status",
        params={"apikey": "test-shim-key"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["version"].startswith("4.")
    assert body["appName"] == "Sonarr"


# --- Routing fidelity (regressions found against a live Bazarr container) ---


def test_trailing_slash_redirects_to_canonical_path(shim_client: TestClient) -> None:
    """Bazarr requests `/series/`; the SPA mount used to swallow it as HTML."""
    response = shim_client.get(
        "/sonarr/api/v3/rootfolder/",
        params={"apikey": "test-shim-key"},
        follow_redirects=False,
    )
    assert response.status_code == 307, response.text
    location = urlparse(response.headers["location"])
    assert location.path == "/sonarr/api/v3/rootfolder"
    # The query (Bazarr's apikey) must survive the redirect.
    assert "apikey=test-shim-key" in location.query


def test_unimplemented_shim_path_is_json_404(shim_client: TestClient) -> None:
    """Never hand an arr client the SPA shell — it parses every body as JSON."""
    response = shim_client.get(
        "/sonarr/api/v3/nonexistent", params={"apikey": "test-shim-key"}
    )
    assert response.status_code == 404, response.text
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {"detail": "Not Found"}


def test_radarr_unimplemented_shim_path_is_json_404(shim_client: TestClient) -> None:
    response = shim_client.get(
        "/radarr/api/v3/nonexistent", params={"apikey": "test-shim-key"}
    )
    assert response.status_code == 404, response.text
    assert response.json() == {"detail": "Not Found"}


def test_quality_profile_returns_one_profile(shim_client: TestClient) -> None:
    """Bazarr calls qualityprofile during sync; an HTML 200 broke the parse."""
    for prefix in ("/sonarr/api/v3", "/radarr/api/v3"):
        response = shim_client.get(
            f"{prefix}/qualityprofile", params={"apikey": "test-shim-key"}
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body
        assert body[0]["id"] == 1
