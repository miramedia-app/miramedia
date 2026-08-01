"""Characterization tests for TMDB error-path notification calls."""

from __future__ import annotations

import inspect

import pytest

from miramedia.metadata.backends import tmdb as tmdb_module
from miramedia.metadata.backends.tmdb import TmdbMetadataProvider
from miramedia.notifications.manager import NotificationManager

_TMDB_DOWN = "tmdb down"


def test_tmdb_backend_has_no_is_configured_guards() -> None:
    source = inspect.getsource(tmdb_module)
    assert "is_configured" not in source


def test_tmdb_api_error_calls_send_notification_without_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    send_calls: list[tuple[str, str]] = []

    def record_send(title: str, message: str) -> None:
        send_calls.append((title, message))

    def forbid_is_configured() -> bool:
        msg = "is_configured must not be called from TMDB error paths"
        raise AssertionError(msg)

    monkeypatch.setattr(
        "miramedia.metadata.backends.tmdb.notification_manager.send_notification",
        record_send,
    )
    monkeypatch.setattr(
        "miramedia.metadata.backends.tmdb.notification_manager.is_configured",
        forbid_is_configured,
    )
    monkeypatch.setattr(
        NotificationManager,
        "_build_providers_uncached",
        lambda _self: [],
    )

    provider = TmdbMetadataProvider.__new__(TmdbMetadataProvider)
    provider.primary_languages = []
    provider.default_language = "en"

    class _BoomTV:
        def __init__(self, **kwargs: object) -> None:
            pass

        def info(self, **_kwargs: object) -> dict:
            raise RuntimeError(_TMDB_DOWN)

    monkeypatch.setattr("miramedia.metadata.backends.tmdb.tmdbsimple.TV", _BoomTV)

    with pytest.raises(RuntimeError, match=_TMDB_DOWN):
        provider._TmdbMetadataProvider__get_show_metadata(123)

    assert send_calls == [
        (
            "TMDB API Error",
            "Failed to fetch show metadata for ID 123 from TMDB. Error: tmdb down",
        )
    ]
