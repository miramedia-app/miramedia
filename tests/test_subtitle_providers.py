"""Golden-fixture parse tests for in-repo subtitle provider adapters.

Fixtures are synthetic JSON/HTML shaped to match each adapter's parsers —
authored offline from the provider modules, never fetched live.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from babelfish import Language
from subliminal.exceptions import ConfigurationError
from subliminal.video import Movie

from miramedia.subtitles.providers.subdl import SubDLProvider
from miramedia.subtitles.providers.subsource import SubsourceProvider
from miramedia.subtitles.providers.yifysubtitles import YifySubtitlesProvider

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "subtitle_providers"


def _load_text(name: str) -> str:
    return (FIXTURES_DIR / name).read_text()


def _load_json(name: str) -> Any:
    return json.loads(_load_text(name))


def _movie() -> Movie:
    return Movie(
        "Test.Movie.2020.mkv",
        title="Test Movie",
        year=2020,
        imdb_id="tt0111161",
    )


def _json_response(payload: Any, *, status_code: int = 200) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = payload
    response.content = b""
    return response


# ---------------------------------------------------------------------------
# SubDL
# ---------------------------------------------------------------------------


class TestSubDLProvider:
    def test_query_success_parses_language_and_download_link(self) -> None:
        provider = SubDLProvider(api_key="test-key")
        provider.initialize()
        provider.session.get = MagicMock(
            return_value=_json_response(_load_json("subdl_success.json"))
        )

        results = provider.query({Language("eng")}, _movie())

        assert len(results) == 1
        sub = results[0]
        assert sub.language == Language("eng")
        assert sub.download_link == "/subtitle/abc123.zip"
        assert sub.release_names == ["Test.Movie.2020.1080p.BluRay"]
        assert sub.subtitle_id == "Test.Movie.2020.1080p.srt"
        provider.terminate()

    def test_query_empty_subtitles_returns_empty(self) -> None:
        provider = SubDLProvider(api_key="test-key")
        provider.initialize()
        provider.session.get = MagicMock(
            return_value=_json_response(_load_json("subdl_empty.json"))
        )

        assert provider.query({Language("eng")}, _movie()) == []
        provider.terminate()

    def test_query_not_found_payload_returns_empty(self) -> None:
        provider = SubDLProvider(api_key="test-key")
        provider.initialize()
        provider.session.get = MagicMock(
            return_value=_json_response(_load_json("subdl_not_found.json"))
        )

        assert provider.query({Language("eng")}, _movie()) == []
        provider.terminate()

    def test_query_non_json_body_returns_empty(self) -> None:
        provider = SubDLProvider(api_key="test-key")
        provider.initialize()
        response = MagicMock()
        response.status_code = 200
        response.json.side_effect = ValueError("not json")
        provider.session.get = MagicMock(return_value=response)

        assert provider.query({Language("eng")}, _movie()) == []
        provider.terminate()


# ---------------------------------------------------------------------------
# Subsource
# ---------------------------------------------------------------------------


class TestSubsourceProvider:
    def test_query_success_parses_release_and_id(self) -> None:
        provider = SubsourceProvider(api_key="test-key")
        provider.initialize()
        search = _load_json("subsource_search.json")
        subs = _load_json("subsource_subs.json")

        def _get_json(
            path: str,
            params: dict | None = None,  # noqa: ARG001
        ) -> dict | None:
            if path == "/movies/search":
                return search
            if path == "/subtitles":
                return subs
            return None

        provider._get_json = _get_json  # type: ignore[method-assign]

        results = provider.query(_movie(), {Language("eng")})

        assert len(results) == 1
        sub = results[0]
        assert sub.language == Language("eng")
        assert sub.subsource_id == 777
        assert sub.release_info == ["Test.Movie.2020.1080p.BluRay"]
        assert sub.page_link == "https://subsource.net/subtitle/777"
        provider.terminate()

    def test_query_empty_search_returns_empty(self) -> None:
        provider = SubsourceProvider(api_key="test-key")
        provider.initialize()
        provider._get_json = MagicMock(return_value={"success": True, "data": []})  # type: ignore[method-assign]

        assert provider.query(_movie(), {Language("eng")}) == []
        provider.terminate()

    def test_query_failed_http_returns_empty(self) -> None:
        provider = SubsourceProvider(api_key="test-key")
        provider.initialize()
        provider._get_json = MagicMock(return_value=None)  # type: ignore[method-assign]

        assert provider.query(_movie(), {Language("eng")}) == []
        provider.terminate()

    def test_query_malformed_title_missing_id_returns_empty(self) -> None:
        provider = SubsourceProvider(api_key="test-key")
        provider.initialize()

        def _get_json(
            path: str,
            params: dict | None = None,  # noqa: ARG001
        ) -> dict | None:
            if path == "/movies/search":
                return {
                    "success": True,
                    "data": [
                        {
                            "title": "Test Movie",
                            "type": "movie",
                            "releaseYear": 2020,
                            # missing movieId / id
                        }
                    ],
                }
            return {"success": True, "data": []}

        provider._get_json = _get_json  # type: ignore[method-assign]

        assert provider.query(_movie(), {Language("eng")}) == []
        provider.terminate()


# ---------------------------------------------------------------------------
# YIFY
# ---------------------------------------------------------------------------


class TestYifySubtitlesProvider:
    def test_parse_page_success(self) -> None:
        provider = YifySubtitlesProvider()
        html = _load_text("yify_success.html").encode()
        languages = {Language("eng"), Language("spa")}

        results = provider._parse_page(html, languages, "https://yifysubtitles.ch")

        assert len(results) == 2
        # Sorted by rating descending
        assert results[0].language == Language("eng")
        assert results[0].rating == 9
        assert "Test.Movie.2020.1080p.BluRay" in results[0].release
        assert results[0].page_link.endswith("/subtitles/test-movie-english-yify-1")
        assert results[1].language == Language("spa")
        assert results[1].hearing_impaired is True

    def test_parse_page_empty_html_returns_empty(self) -> None:
        provider = YifySubtitlesProvider()
        html = _load_text("yify_empty.html").encode()

        assert (
            provider._parse_page(html, {Language("eng")}, "https://yifysubtitles.ch")
            == []
        )

    def test_query_404_returns_empty(self) -> None:
        provider = YifySubtitlesProvider()
        provider.initialize()
        response = MagicMock()
        response.status_code = 404
        response.content = b""
        provider.session.get = MagicMock(return_value=response)

        assert provider.query({Language("eng")}, "tt0111161") == []
        provider.terminate()

    def test_list_subtitles_without_imdb_returns_empty(self) -> None:
        provider = YifySubtitlesProvider()
        movie = Movie("NoId.mkv", title="No Id", year=2020)

        assert provider.list_subtitles(movie, {Language("eng")}) == []


def test_api_key_providers_reject_empty_key() -> None:
    with pytest.raises(ConfigurationError):
        SubDLProvider(api_key="")
    with pytest.raises(ConfigurationError):
        SubsourceProvider(api_key="")
