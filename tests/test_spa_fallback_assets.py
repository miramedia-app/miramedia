"""SPA fallback must never answer an asset request, and documents must revalidate.

Production symptom: after a deploy the browser still held the previous build's
HTML for one route. That HTML names hashed chunks that no longer exist, the
request for a missing chunk was answered with the SPA shell (200, text/html),
the browser failed to parse HTML as JavaScript, and Next's chunk-load recovery
reloaded the page — forever, on that route only.
"""

from __future__ import annotations

import asyncio

import pytest


@pytest.mark.parametrize(
    "path",
    [
        "/_next/static/chunks/1kk3cj7pt07fi.js",
        "/_next/static/chunks/main.css",
        "/dashboard/imports/index.txt",
        "/favicon.ico",
        "/logo.svg",
        "/site.webmanifest",
        "/sourcemap.js.map",
    ],
)
def test_asset_shaped_paths_are_not_spa_routes(path: str) -> None:
    from miramedia.main import _is_static_asset_request

    assert _is_static_asset_request(path) is True


@pytest.mark.parametrize(
    "path",
    [
        "/dashboard/imports/",
        "/dashboard/imports",
        "/dashboard/shows/0189a1f0-0000-7000-8000-000000000000/",
        "/login",
        "/",
    ],
)
def test_route_paths_still_reach_the_spa_shell(path: str) -> None:
    from miramedia.main import _is_static_asset_request

    assert _is_static_asset_request(path) is False


@pytest.mark.parametrize(
    ("path", "media_type", "expected"),
    [
        ("", None, True),
        ("dashboard/imports/", None, True),
        ("dashboard/imports/index.html", None, True),
        ("dashboard/imports/index.txt", None, True),
        ("some/route", "text/html; charset=utf-8", True),
        ("_next/static/chunks/abc.js", "text/javascript", False),
        ("logo.svg", "image/svg+xml", False),
    ],
)
def test_spa_document_detection(path: str, media_type: str | None, expected) -> None:
    from miramedia.main import _is_spa_document

    assert _is_spa_document(path, media_type) is expected


@pytest.fixture(autouse=True)
def frontend_dir(tmp_path, monkeypatch):
    from miramedia import main

    (tmp_path / "index.html").write_text("<!doctype html><html></html>")
    shell = tmp_path / "dashboard" / "shows" / "_shell"
    (shell / "_shell").mkdir(parents=True)
    (shell / "index.html").write_text("<!doctype html><html>shell</html>")
    (shell / "__next._tree.txt").write_text('1:"$Sreact.fragment"')
    (shell / "_shell" / "__next._tree.txt").write_text('1:"season"')
    monkeypatch.setattr(main, "FRONTEND_FILES_DIR", str(tmp_path))
    monkeypatch.setattr(main, "DISABLE_FRONTEND_MOUNT", False)
    return tmp_path


def _not_found(path: str):
    """Drive the 404 handler directly.

    The app's static mount is constructed at import time from an env var that
    is unset under test, so a TestClient request would die in the mount before
    ever reaching the handler under test.
    """
    from starlette.requests import Request

    from miramedia.main import not_found_handler

    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [],
        "scheme": "http",
        "server": ("testserver", 80),
        "root_path": "",
    }
    return asyncio.run(not_found_handler(Request(scope), Exception()))


def test_missing_chunk_404s_instead_of_returning_html() -> None:
    resp = _not_found("/_next/static/chunks/does-not-exist-abc123.js")

    assert resp.status_code == 404
    assert "text/html" not in resp.headers.get("content-type", "")


def test_missing_route_payload_404s() -> None:
    """Stale RSC payload requests must fail cleanly, not return the shell."""
    resp = _not_found("/dashboard/imports/index.txt")

    assert resp.status_code == 404


def test_unknown_route_still_gets_the_shell_and_revalidates() -> None:
    resp = _not_found("/dashboard/imports/deep/link")

    assert resp.status_code == 200
    assert resp.media_type == "text/html"
    # Without this the browser keeps a build-specific shell across deploys.
    assert resp.headers["cache-control"] == "no-cache"


def test_dynamic_route_payload_serves_the_shell_payload() -> None:
    """The reload loop: this must be the RSC payload, never HTML."""
    resp = _not_found(
        "/dashboard/shows/67f6ee6d-6225-48a7-8753-0b52e77404a3/__next._tree.txt"
    )

    assert resp.status_code == 200
    assert resp.media_type == "text/plain"
    assert resp.headers["cache-control"] == "no-cache"


def test_nested_dynamic_route_payload_uses_the_season_shell() -> None:
    resp = _not_found(
        "/dashboard/shows/67f6ee6d-6225-48a7-8753-0b52e77404a3"
        "/77f6ee6d-6225-48a7-8753-0b52e77404a3/__next._tree.txt"
    )

    assert resp.status_code == 200
    assert resp.media_type == "text/plain"


def test_dynamic_route_document_still_gets_the_shell_html() -> None:
    resp = _not_found("/dashboard/shows/67f6ee6d-6225-48a7-8753-0b52e77404a3/")

    assert resp.status_code == 200
    assert resp.media_type == "text/html"


def test_dynamic_payload_rewrite_refuses_traversal() -> None:
    from miramedia.main import _dynamic_shell_payload

    assert (
        _dynamic_shell_payload(
            "/dashboard/shows/67f6ee6d-6225-48a7-8753-0b52e77404a3/..%2Findex.html"
        )
        is None
    )


def test_unknown_dynamic_payload_falls_through_to_404() -> None:
    resp = _not_found(
        "/dashboard/movies/67f6ee6d-6225-48a7-8753-0b52e77404a3/__next._tree.txt"
    )

    assert resp.status_code == 404
