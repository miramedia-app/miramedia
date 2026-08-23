"""Drift guard: service modules must not import or raise HTTPException."""

from __future__ import annotations

from pathlib import Path

import pytest

# Crude text scan — catches reintroduction of FastAPI HTTP types into the
# service layer without pulling in import-graph tooling.
_SERVICE_MODULES = (
    "miramedia/imports/service.py",
    "miramedia/imports/scan_resolve.py",
    "miramedia/playback/service.py",
    "miramedia/watchlists/service.py",
)


@pytest.mark.parametrize("relative_path", _SERVICE_MODULES)
def test_service_module_has_no_http_exception(relative_path: str) -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / relative_path).read_text(encoding="utf-8")
    assert "HTTPException" not in source
