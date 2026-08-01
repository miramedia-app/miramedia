"""TLS verification behavior for settings integration test handlers."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import requests

from miramedia.settings.integration_tests import (
    QbittorrentTestConfig,
    SabnzbdTestConfig,
    TransmissionTestConfig,
    test_qbittorrent,
    test_sabnzbd,
    test_transmission,
)


def test_qbittorrent_defaults_verify_tls(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[dict] = []

    def _post(*_args: object, **kwargs: object) -> MagicMock:
        captured.append(dict(kwargs))
        response = MagicMock()
        response.status_code = 200
        response.text = "Ok."
        return response

    def _get(*_args: object, **kwargs: object) -> MagicMock:
        captured.append(dict(kwargs))
        response = MagicMock()
        response.text = "v4.6.0"
        return response

    monkeypatch.setattr(
        "miramedia.settings.integration_tests.requests.Session.post", _post
    )
    monkeypatch.setattr(
        "miramedia.settings.integration_tests.requests.Session.get", _get
    )

    result = test_qbittorrent(QbittorrentTestConfig())
    assert result.ok is True
    assert all(call.get("verify", True) is True for call in captured)


def test_qbittorrent_allow_self_signed_disables_verify(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict] = []

    def _post(*_args: object, **kwargs: object) -> MagicMock:
        captured.append(dict(kwargs))
        response = MagicMock()
        response.status_code = 200
        response.text = "Ok."
        return response

    def _get(*_args: object, **kwargs: object) -> MagicMock:
        captured.append(dict(kwargs))
        response = MagicMock()
        response.text = "v4.6.0"
        return response

    monkeypatch.setattr(
        "miramedia.settings.integration_tests.requests.Session.post", _post
    )
    monkeypatch.setattr(
        "miramedia.settings.integration_tests.requests.Session.get", _get
    )

    cfg = QbittorrentTestConfig(allow_self_signed=True)
    result = test_qbittorrent(cfg)
    assert result.ok is True
    assert captured
    assert all(call.get("verify") is False for call in captured)


def test_qbittorrent_ssl_error_returns_actionable_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _post(*_args: object, **_kwargs: object) -> MagicMock:
        tls_error = "certificate verify failed"
        raise requests.exceptions.SSLError(tls_error)

    monkeypatch.setattr(
        "miramedia.settings.integration_tests.requests.Session.post", _post
    )

    result = test_qbittorrent(QbittorrentTestConfig())
    assert result.ok is False
    assert "Allow self-signed certificate" in result.message


def test_transmission_defaults_verify_tls(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[dict] = []

    def _post(*_args: object, **kwargs: object) -> MagicMock:
        captured.append(dict(kwargs))
        response = MagicMock()
        response.status_code = 200
        response.headers = {}
        response.json.return_value = {"arguments": {"version": "4.0.0"}}
        return response

    monkeypatch.setattr("miramedia.settings.integration_tests.requests.post", _post)

    result = test_transmission(TransmissionTestConfig())
    assert result.ok is True
    assert all(call.get("verify", True) is True for call in captured)


def test_sabnzbd_defaults_verify_tls(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[dict] = []

    def _get(*_args: object, **kwargs: object) -> MagicMock:
        captured.append(dict(kwargs))
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"version": "4.2.0"}
        return response

    monkeypatch.setattr("miramedia.settings.integration_tests.requests.get", _get)

    result = test_sabnzbd(SabnzbdTestConfig())
    assert result.ok is True
    assert captured[0].get("verify", True) is True
