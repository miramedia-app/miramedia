"""Regression tests for Jackett search credential redaction in logs."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import requests
from requests import Response

from miramedia.indexers.backends.jackett import Jackett

_SENTINEL_API_KEY = "SENTINEL_JACKETT_API_KEY_DO_NOT_LOG"
_JACKETT_ORIGIN = "http://jackett.example:9117"
_INDEXER = "test-indexer"
_REQUEST_PATH = f"/api/v2.0/indexers/{_INDEXER}/results/torznab/api"

_MINIMAL_TORZNAB_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
     xmlns:atom="http://www.w3.org/2005/Atom"
     xmlns:torznab="http://torznab.com/schemas/2015/feed">
  <channel>
    <item>
      <title>Test Release</title>
      <size>1048576</size>
      <enclosure url="http://example.com/test.torrent"
                 type="application/x-bittorrent"
                 length="1048576"/>
      <torznab:attr name="seeders" value="10"/>
    </item>
  </channel>
</rss>
"""


def _credentialized_response_url() -> str:
    return f"{_JACKETT_ORIGIN}{_REQUEST_PATH}?apikey={_SENTINEL_API_KEY}&t=movie&q=test"


def _stub_query_params(
    _backend: Jackett, _indexer: str, _session: object, _params: dict
) -> dict[str, str]:
    return {"apikey": _SENTINEL_API_KEY, "t": "movie", "q": "test"}


def _make_backend(monkeypatch: pytest.MonkeyPatch) -> Jackett:
    monkeypatch.setattr(
        "miramedia.indexers.backends.jackett.MiraMediaConfig",
        lambda: SimpleNamespace(
            indexers=SimpleNamespace(
                timeout_seconds=30,
                jackett=SimpleNamespace(
                    api_key=_SENTINEL_API_KEY,
                    url=_JACKETT_ORIGIN,
                    indexers=[_INDEXER],
                ),
            )
        ),
    )
    backend = Jackett()
    monkeypatch.setattr(
        backend,
        "_Jackett__get_optimal_query_parameters",
        lambda indexer, session, params: _stub_query_params(
            backend, indexer, session, params
        ),
    )
    return backend


def _response(*, status_code: int) -> Response:
    response = Response()
    response.status_code = status_code
    response.url = _credentialized_response_url()
    response._content = _MINIMAL_TORZNAB_XML.encode()
    return response


def _assert_logs_safe(combined: str) -> None:
    assert _SENTINEL_API_KEY not in combined
    assert f"apikey={_SENTINEL_API_KEY}" not in combined
    assert "apikey=" not in combined
    assert _INDEXER in combined
    assert "jackett.example" in combined
    assert _REQUEST_PATH in combined


def test_jackett_success_logs_redact_credential(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _make_backend(monkeypatch)
    session = MagicMock()
    session.get.return_value = _response(status_code=200)

    with caplog.at_level(logging.DEBUG, logger="miramedia.indexers.backends.jackett"):
        results = backend.get_torrents_by_indexer(
            _INDEXER, {"t": "movie", "q": "test"}, session
        )

    assert len(results) == 1
    _assert_logs_safe(caplog.text)


def test_jackett_non_200_logs_redact_credential(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _make_backend(monkeypatch)
    session = MagicMock()
    session.get.return_value = _response(status_code=503)

    with caplog.at_level(logging.DEBUG, logger="miramedia.indexers.backends.jackett"):
        results = backend.get_torrents_by_indexer(
            _INDEXER, {"t": "movie", "q": "test"}, session
        )

    assert results == []
    _assert_logs_safe(caplog.text)
    assert "503" in caplog.text
    assert _INDEXER in caplog.text


def test_jackett_search_exception_logs_redact_credential(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _make_backend(monkeypatch)
    exc = requests.exceptions.ConnectionError(
        f"HTTPConnectionPool(host='jackett.example', port=9117): "
        f"Max retries exceeded with url: {_credentialized_response_url()}"
    )

    def _raise(*_args: object, **_kwargs: object) -> None:
        raise exc

    backend.get_torrents_by_indexer = MagicMock(side_effect=_raise)  # type: ignore[method-assign]

    with caplog.at_level(logging.ERROR, logger="miramedia.indexers.backends.jackett"):
        results = backend.search("test query", is_tv=False)

    assert results == []
    assert _SENTINEL_API_KEY not in caplog.text
    assert f"apikey={_SENTINEL_API_KEY}" not in caplog.text
    assert "apikey=" not in caplog.text
    assert "ConnectionError" in caplog.text
