"""Regression tests for SubDL provider credential redaction in logs."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest
from babelfish import Language
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import Timeout
from subliminal.video import Movie

from miramedia.subtitles.providers.subdl import (
    API_URL,
    DOWNLOAD_BASE,
    SubDLProvider,
    SubDLSubtitle,
)

_SENTINEL_API_KEY = "SENTINEL_SUBDL_API_KEY_DO_NOT_LOG"


def _movie() -> Movie:
    return Movie(
        "Test.Movie.2020.mkv",
        title="Test Movie",
        year=2020,
        imdb_id="tt0111161",
    )


def _credentialized_url() -> str:
    return f"{API_URL}?api_key={_SENTINEL_API_KEY}&languages=english&type=movie"


def _provider() -> SubDLProvider:
    provider = SubDLProvider(api_key=_SENTINEL_API_KEY)
    provider.initialize()
    return provider


@pytest.mark.parametrize(
    "exc",
    [
        RequestsConnectionError(
            f"HTTPSConnectionPool(host='api.subdl.com', port=443): "
            f"Max retries exceeded with url: {_credentialized_url()}"
        ),
        Timeout(
            f"HTTPSConnectionPool(host='api.subdl.com', port=443): "
            f"Read timed out. (read timeout=30) url: {_credentialized_url()}"
        ),
    ],
    ids=["connection_error", "timeout"],
)
def test_query_transport_errors_redact_credential(
    caplog: pytest.LogCaptureFixture,
    exc: BaseException,
) -> None:
    provider = _provider()
    provider.session.get = MagicMock(side_effect=exc)

    with caplog.at_level(logging.WARNING, logger="miramedia.subtitles.providers.subdl"):
        assert provider.query({Language("eng")}, _movie()) == []

    combined = caplog.text
    assert _SENTINEL_API_KEY not in combined
    assert "api.subdl.com" in combined
    assert type(exc).__name__ in combined
    provider.terminate()


def test_query_unexpected_transport_error_redacts_credential(
    caplog: pytest.LogCaptureFixture,
) -> None:
    provider = _provider()
    provider.session.get = MagicMock(
        side_effect=RuntimeError(f"transport failed for {_credentialized_url()}")
    )

    with caplog.at_level(logging.WARNING, logger="miramedia.subtitles.providers.subdl"):
        assert provider.query({Language("eng")}, _movie()) == []

    combined = caplog.text
    assert _SENTINEL_API_KEY not in combined
    assert "api.subdl.com" in combined
    assert "RuntimeError" in combined
    provider.terminate()


def test_download_transport_errors_redact_credential(
    caplog: pytest.LogCaptureFixture,
) -> None:
    provider = _provider()
    download_path = "/subtitle/safe.zip"
    download_url = f"{DOWNLOAD_BASE}{download_path}"
    provider.session.get = MagicMock(
        side_effect=RequestsConnectionError(
            f"HTTPSConnectionPool(host='dl.subdl.com', port=443): "
            f"Max retries exceeded with url: {download_url}"
        )
    )
    subtitle = SubDLSubtitle(
        Language("eng"),
        "safe.srt",
        page_link="https://subdl.com/subtitle/safe",
        download_link=download_path,
        release_names=["Test.Release"],
    )

    with caplog.at_level(logging.WARNING, logger="miramedia.subtitles.providers.subdl"):
        provider.download_subtitle(subtitle)

    combined = caplog.text
    assert _SENTINEL_API_KEY not in combined
    assert "dl.subdl.com" in combined
    assert "ConnectionError" in combined
    provider.terminate()
