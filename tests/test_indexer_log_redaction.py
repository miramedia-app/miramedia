"""Regression tests for indexer and torrent-fetch credential redaction in logs."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import requests

from miramedia.indexers.backends.generic import GenericIndexer
from miramedia.indexers.config import TorznabSiteConfig
from miramedia.indexers.schemas import IndexerQueryResult
from miramedia.indexers.service import IndexerService
from miramedia.indexers.sites.torznab_site import TorznabSite
from miramedia.torrents.inspection import get_torrent_hash

_FAKE_KEY = "FAKEKEY123"
_TORZNAB_ORIGIN = "http://torznab.example:9117"
_TORZNAB_PATH = "/api/v2.0/indexers/test/results/torznab/api"
_SITE_NAME = "test-torznab"


def _credentialized_torznab_url() -> str:
    return f"{_TORZNAB_ORIGIN}{_TORZNAB_PATH}?apikey={_FAKE_KEY}&t=movie&q=test"


def _connection_error_with_credential_url() -> requests.exceptions.ConnectionError:
    return requests.exceptions.ConnectionError(
        f"HTTPConnectionPool(host='torznab.example', port=9117): "
        f"Max retries exceeded with url: {_credentialized_torznab_url()}"
    )


def _assert_logs_safe(combined: str) -> None:
    assert _FAKE_KEY not in combined
    assert f"apikey={_FAKE_KEY}" not in combined
    assert "apikey=" not in combined


def _indexer_result(download_url: str) -> IndexerQueryResult:
    return IndexerQueryResult(
        title="Safe.Title",
        download_url=download_url,
        flags=[],
        size=1,
        usenet=False,
        age=0,
        indexer="test",
    )


def _make_torznab_site() -> TorznabSite:
    return TorznabSite(
        config=TorznabSiteConfig(
            name=_SITE_NAME,
            url=f"{_TORZNAB_ORIGIN}{_TORZNAB_PATH}",
            api_key=_FAKE_KEY,
        ),
    )


def test_torznab_site_search_failure_logs_redact_credential(
    caplog: pytest.LogCaptureFixture,
) -> None:
    site = _make_torznab_site()
    site._fetch = MagicMock(side_effect=_connection_error_with_credential_url())  # type: ignore[method-assign]

    with caplog.at_level(logging.ERROR, logger="miramedia.indexers.sites.torznab_site"):
        results = site.search("test query", category="movie")

    assert results == []
    _assert_logs_safe(caplog.text)
    assert _SITE_NAME in caplog.text
    assert "ConnectionError" in caplog.text


def test_cloudflare_fetch_runtime_error_redacts_query_string() -> None:
    site = TorznabSite(
        config=TorznabSiteConfig(
            name=_SITE_NAME,
            url=f"{_TORZNAB_ORIGIN}{_TORZNAB_PATH}",
            api_key=_FAKE_KEY,
            cloudflare_protected=True,
        ),
    )
    site._fetch_via_cloudflare_session = MagicMock(return_value=None)  # type: ignore[method-assign]
    site.bypass = None

    with pytest.raises(
        RuntimeError, match="Cloudflare fetch returned no usable HTML"
    ) as exc_info:
        site._fetch(
            f"{_TORZNAB_ORIGIN}{_TORZNAB_PATH}",
            params={"apikey": _FAKE_KEY, "t": "movie", "q": "test"},
        )

    message = str(exc_info.value)
    _assert_logs_safe(message)
    assert f"{_TORZNAB_ORIGIN}{_TORZNAB_PATH}" in message


class _FailingIndexer(GenericIndexer):
    def __init__(self) -> None:
        super().__init__(name="FailingIndexer")

    def search(
        self,
        query: str,  # noqa: ARG002
        is_tv: bool = False,  # noqa: ARG002
        on_site_result: object = None,  # noqa: ARG002
    ) -> list[IndexerQueryResult]:
        raise _connection_error_with_credential_url()

    def search_season(
        self,
        query: str,  # noqa: ARG002
        show: object,  # noqa: ARG002
        season_number: int,  # noqa: ARG002
    ) -> list[IndexerQueryResult]:
        raise _connection_error_with_credential_url()

    def search_movie(
        self,
        query: str,  # noqa: ARG002
        movie: object,  # noqa: ARG002
    ) -> list[IndexerQueryResult]:
        raise _connection_error_with_credential_url()


@pytest.mark.anyio
async def test_indexer_service_search_failure_logs_redact_credential(
    caplog: pytest.LogCaptureFixture,
) -> None:
    service = IndexerService(indexer_repository=MagicMock())
    service.indexers = [_FailingIndexer()]
    service._initialized = True

    with caplog.at_level(logging.ERROR, logger="miramedia.indexers.service"):
        results = await service.search("test query", is_tv=False)

    assert results == []
    _assert_logs_safe(caplog.text)
    assert "FailingIndexer" in caplog.text
    assert "ConnectionError" in caplog.text


def test_get_torrent_hash_download_failure_logs_redact_credential(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    completed = tmp_path / "completed"
    completed.mkdir()
    download_url = (
        f"http://tracker.example/file.torrent?passkey={_FAKE_KEY}&apikey={_FAKE_KEY}"
    )

    monkeypatch.setattr(
        "miramedia.torrents.inspection.MiraMediaConfig",
        lambda: SimpleNamespace(
            misc=SimpleNamespace(
                effective_completed_path=completed,
                incomplete_torrent_path="",
                torrent_directory=str(completed),
            ),
            indexers=SimpleNamespace(timeout_seconds=5),
        ),
    )
    monkeypatch.setattr(
        "miramedia.torrents.inspection._guarded_fetch_torrent_bytes",
        lambda *_a, **_k: (_ for _ in ()).throw(
            requests.exceptions.HTTPError(f"404 Client Error for url: {download_url}")
        ),
    )

    with caplog.at_level(logging.ERROR, logger="miramedia.torrents.inspection"):
        with pytest.raises(requests.exceptions.HTTPError):
            get_torrent_hash(_indexer_result(download_url))

    _assert_logs_safe(caplog.text)
    assert "http://tracker.example/file.torrent" in caplog.text
    assert "HTTPError" in caplog.text
