"""Regression tests for proxy-header trust and serve_image path containment."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from miramedia.config import DEFAULT_TRUSTED_PROXY_HOSTS, BasicConfig


def test_trusted_proxy_hosts_defaults_to_private_ranges() -> None:
    misc = BasicConfig()
    assert misc.trusted_proxy_hosts == DEFAULT_TRUSTED_PROXY_HOSTS


def test_trusted_proxy_hosts_accepts_star_wildcard() -> None:
    misc = BasicConfig(trusted_proxy_hosts="*")
    assert misc.trusted_proxy_hosts == "*"


def test_proxy_headers_middleware_accepts_default_trusted_hosts() -> None:
    misc = BasicConfig()
    ProxyHeadersMiddleware(app=MagicMock(), trusted_hosts=misc.trusted_proxy_hosts)


def test_proxy_headers_middleware_accepts_star_trusted_hosts() -> None:
    misc = BasicConfig(trusted_proxy_hosts="*")
    ProxyHeadersMiddleware(app=MagicMock(), trusted_hosts=misc.trusted_proxy_hosts)


def test_no_hardcoded_trusted_hosts_star_in_main() -> None:
    main_path = Path(__file__).resolve().parents[1] / "miramedia" / "main.py"
    source = main_path.read_text(encoding="utf-8")
    assert 'trusted_hosts="*"' not in source


def test_serve_image_rejects_path_traversal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("secret", encoding="utf-8")

    monkeypatch.setattr(
        "miramedia.core.router.config",
        SimpleNamespace(misc=SimpleNamespace(image_directory=image_dir)),
    )

    from miramedia.core.router import serve_image

    async def _run() -> None:
        with pytest.raises(HTTPException) as exc_info:
            await serve_image("../secret.txt")

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "Image not found"

    asyncio.run(_run())


def test_serve_image_serves_file_within_image_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    poster = image_dir / "poster.jpg"
    poster.write_bytes(b"fake-jpeg")

    monkeypatch.setattr(
        "miramedia.core.router.config",
        SimpleNamespace(misc=SimpleNamespace(image_directory=image_dir)),
    )

    from miramedia.core.router import serve_image

    async def _run() -> None:
        response = await serve_image("poster.jpg")
        assert response.path == poster.resolve()

    asyncio.run(_run())
