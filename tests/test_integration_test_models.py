"""Typed config validation for settings integration test handlers."""

from __future__ import annotations

import uuid
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest
import requests
from fastapi.testclient import TestClient
from pydantic import ValidationError

from miramedia.settings.integration_tests import (
    HANDLERS,
    IntegrationTestResult,
    QbittorrentTestConfig,
    SabnzbdTestConfig,
    TransmissionTestConfig,
    test_sabnzbd,
)
from miramedia.settings.validation import SECRET_MASK


@contextmanager
def integration_client() -> Generator[TestClient]:
    from miramedia.auth.users import current_superuser
    from miramedia.main import app

    async def _superuser() -> Any:
        user = MagicMock()
        user.id = uuid.uuid4()
        user.is_superuser = True
        return user

    prior = dict(app.dependency_overrides)
    app.dependency_overrides[current_superuser] = _superuser
    client = TestClient(app, raise_server_exceptions=False)
    try:
        yield client
    finally:
        client.close()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(prior)


def test_valid_qbittorrent_payload_reaches_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: list[QbittorrentTestConfig] = []

    def _handler(cfg: QbittorrentTestConfig) -> object:
        called.append(cfg)
        from miramedia.settings.integration_tests import IntegrationTestResult

        return IntegrationTestResult(ok=True, message="ok")

    monkeypatch.setitem(HANDLERS, "qbittorrent", (QbittorrentTestConfig, _handler))

    with integration_client() as client:
        response = client.post(
            "/api/v1/system/settings/integrations/qbittorrent/test",
            json={"config": {"host": "qb.local", "port": 8080}},
        )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert called
    assert called[0].host == "qb.local"
    assert called[0].port == 8080


def test_extra_section_keys_ignored_by_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: list[QbittorrentTestConfig] = []

    def _handler(cfg: QbittorrentTestConfig) -> object:
        called.append(cfg)
        from miramedia.settings.integration_tests import IntegrationTestResult

        return IntegrationTestResult(ok=True, message="ok")

    monkeypatch.setitem(HANDLERS, "qbittorrent", (QbittorrentTestConfig, _handler))

    with integration_client() as client:
        response = client.post(
            "/api/v1/system/settings/integrations/qbittorrent/test",
            json={
                "config": {
                    "host": "qb.local",
                    "port": 8080,
                    "enabled": True,
                    "category_name": "movies",
                    "category_save_path": "/downloads",
                    "bogus_field": True,
                }
            },
        )

    body = response.json()
    assert response.status_code == 200
    assert body["ok"] is True
    assert called
    assert called[0].host == "qb.local"
    assert "extra" not in body["message"].lower()
    assert "unexpected" not in body["message"].lower()


def test_full_smtp_section_payload_not_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from miramedia.settings.integration_tests import (
        IntegrationTestResult,
        SmtpTestConfig,
    )

    monkeypatch.setitem(
        HANDLERS,
        "smtp",
        (
            SmtpTestConfig,
            lambda _cfg: IntegrationTestResult(ok=False, message="smtp unreachable"),
        ),
    )

    with integration_client() as client:
        response = client.post(
            "/api/v1/system/settings/integrations/smtp/test",
            json={
                "config": {
                    "smtp_host": "mail.example.com",
                    "smtp_port": 587,
                    "smtp_user": "user",
                    "smtp_password": "secret",
                    "use_tls": True,
                    "enabled": False,
                }
            },
        )

    body = response.json()
    assert response.status_code == 200
    assert "extra" not in body["message"].lower()
    assert "unexpected" not in body["message"].lower()


def test_full_gotify_section_payload_not_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from miramedia.settings.integration_tests import (
        IntegrationTestResult,
        UrlTestConfig,
    )

    monkeypatch.setitem(
        HANDLERS,
        "gotify",
        (
            UrlTestConfig,
            lambda _cfg: IntegrationTestResult(ok=False, message="gotify unreachable"),
        ),
    )

    with integration_client() as client:
        response = client.post(
            "/api/v1/system/settings/integrations/gotify/test",
            json={
                "config": {
                    "url": "http://gotify.local",
                    "api_key": "key",
                    "enabled": True,
                    "priority": 5,
                }
            },
        )

    body = response.json()
    assert response.status_code == 200
    assert "extra" not in body["message"].lower()
    assert "unexpected" not in body["message"].lower()


def test_masked_secret_substituted_from_effective_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from miramedia.settings.integration_tests import IntegrationTestResult
    from miramedia.settings.validation import SECRET_MASK

    called: list[QbittorrentTestConfig] = []

    def _handler(cfg: QbittorrentTestConfig) -> object:
        called.append(cfg)
        return IntegrationTestResult(ok=True, message="ok")

    monkeypatch.setitem(HANDLERS, "qbittorrent", (QbittorrentTestConfig, _handler))
    monkeypatch.setattr(
        "miramedia.settings.router._integration_effective_section",
        lambda _integration: {
            "host": "qb.local",
            "password": "stored-qb-pass",
        },
    )

    with integration_client() as client:
        response = client.post(
            "/api/v1/system/settings/integrations/qbittorrent/test",
            json={"config": {"host": "qb.local", "password": SECRET_MASK}},
        )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert called
    assert called[0].password == "stored-qb-pass"


def test_masked_secret_without_stored_value_resolves_to_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from miramedia.settings.integration_tests import IntegrationTestResult
    from miramedia.settings.validation import SECRET_MASK

    called: list[QbittorrentTestConfig] = []

    def _handler(cfg: QbittorrentTestConfig) -> object:
        called.append(cfg)
        return IntegrationTestResult(ok=True, message="ok")

    monkeypatch.setitem(HANDLERS, "qbittorrent", (QbittorrentTestConfig, _handler))
    monkeypatch.setattr(
        "miramedia.settings.router._integration_effective_section",
        lambda _integration: {"host": "qb.local"},
    )

    with integration_client() as client:
        response = client.post(
            "/api/v1/system/settings/integrations/qbittorrent/test",
            json={"config": {"host": "qb.local", "password": SECRET_MASK}},
        )

    assert response.status_code == 200
    assert called
    assert called[0].password == ""


def test_invalid_host_with_slash_rejected() -> None:
    with pytest.raises(ValidationError):
        QbittorrentTestConfig(host="evil.com/path")


def test_port_out_of_range_rejected() -> None:
    with pytest.raises(ValidationError):
        QbittorrentTestConfig(port=70000)


@pytest.mark.parametrize("integration", sorted(HANDLERS))
def test_every_handler_has_validatable_model(integration: str) -> None:
    model_cls, _handler = HANDLERS[integration]
    model_cls.model_validate({})


def test_defaults_match_legacy_fallbacks() -> None:
    assert QbittorrentTestConfig().port == 8080
    assert TransmissionTestConfig().port == 9091
    assert TransmissionTestConfig().path == "/transmission/rpc"
    assert SabnzbdTestConfig().port == 8080


def test_masked_credential_changed_host_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request_mock = MagicMock()
    monkeypatch.setattr(
        "miramedia.settings.integration_tests.requests.get", request_mock
    )
    monkeypatch.setattr(
        "miramedia.settings.integration_tests.requests.post", request_mock
    )
    monkeypatch.setattr(
        "miramedia.settings.router._integration_effective_section",
        lambda _integration: {
            "host": "stored.host",
            "port": 8080,
            "password": "stored-qb-pass",
        },
    )

    with integration_client() as client:
        response = client.post(
            "/api/v1/system/settings/integrations/qbittorrent/test",
            json={
                "config": {
                    "host": "evil.host",
                    "port": 8080,
                    "password": SECRET_MASK,
                }
            },
        )

    body = response.json()
    assert response.status_code == 200
    assert body["ok"] is False
    assert "re-enter the credential" in body["message"]
    request_mock.assert_not_called()


def test_masked_credential_matching_target_resolves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: list[QbittorrentTestConfig] = []

    def _handler(cfg: QbittorrentTestConfig) -> object:
        called.append(cfg)
        return IntegrationTestResult(ok=True, message="ok")

    monkeypatch.setitem(HANDLERS, "qbittorrent", (QbittorrentTestConfig, _handler))
    monkeypatch.setattr(
        "miramedia.settings.router._integration_effective_section",
        lambda _integration: {
            "host": "qb.local",
            "port": 8080,
            "password": "stored-qb-pass",
        },
    )

    with integration_client() as client:
        response = client.post(
            "/api/v1/system/settings/integrations/qbittorrent/test",
            json={
                "config": {
                    "host": "qb.local",
                    "port": 8080,
                    "password": SECRET_MASK,
                }
            },
        )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert called
    assert called[0].password == "stored-qb-pass"


def test_sabnzbd_connection_error_does_not_echo_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "super-secret-api-key"
    exc = requests.exceptions.ConnectionError(
        f"HTTPConnectionPool(host='localhost'): Failed - apikey={secret}"
    )
    monkeypatch.setattr(
        "miramedia.settings.integration_tests.requests.get",
        MagicMock(side_effect=exc),
    )

    result = test_sabnzbd(
        SabnzbdTestConfig(host="localhost", port=8080, api_key=secret)
    )

    assert result.ok is False
    assert secret not in result.message


@contextmanager
def indexer_client(
    repo: object,
) -> Generator[TestClient]:
    from miramedia.auth.users import current_superuser
    from miramedia.database import get_session
    from miramedia.indexers.dependencies import get_indexer_repository
    from miramedia.main import app

    async def _stub_session() -> Any:
        yield None

    async def _superuser() -> Any:
        user = MagicMock()
        user.id = uuid.uuid4()
        user.is_superuser = True
        return user

    def _repo_dep() -> object:
        return repo

    prior = dict(app.dependency_overrides)
    app.dependency_overrides[get_session] = _stub_session
    app.dependency_overrides[current_superuser] = _superuser
    app.dependency_overrides[get_indexer_repository] = _repo_dep
    client = TestClient(app, raise_server_exceptions=False)
    try:
        yield client
    finally:
        client.close()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(prior)


def test_indexer_create_strips_mask_sentinel() -> None:
    from miramedia.indexers.schemas import (
        IndexerSiteCreate,
        IndexerSiteId,
        IndexerSiteRead,
    )

    captured: list[IndexerSiteCreate] = []
    now = datetime.now(UTC)

    class FakeIndexerRepository:
        async def create_site(self, data: IndexerSiteCreate) -> IndexerSiteRead:
            captured.append(data)
            return IndexerSiteRead(
                id=IndexerSiteId(uuid.uuid4()),
                name=data.name,
                site_type=data.site_type,
                url=data.url,
                available_urls=data.available_urls,
                api_key=data.api_key,
                supports_tv=data.supports_tv,
                supports_movies=data.supports_movies,
                categories_tv=data.categories_tv,
                categories_movies=data.categories_movies,
                cloudflare_protected=data.cloudflare_protected,
                enabled=data.enabled,
                is_preloaded=False,
                priority=data.priority,
                created_at=now,
                updated_at=now,
            )

    with indexer_client(FakeIndexerRepository()) as client:
        response = client.post(
            "/api/v1/indexers/sites",
            json={
                "name": "test-site",
                "url": "https://indexer.example/torznab",
                "api_key": SECRET_MASK,
            },
        )

    assert response.status_code == 201
    assert captured
    assert captured[0].api_key == ""
    assert captured[0].api_key != SECRET_MASK
