"""Guard against order-sensitive circular imports at module load."""

from __future__ import annotations

import os
import subprocess
import sys


def _import_first(module: str) -> None:
    env = os.environ.copy()
    env.setdefault("MIRAMEDIA_LOG_FILE", "/dev/null")
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", f"import {module}"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"import {module} failed (exit {result.returncode}):\n"
        f"{result.stdout}\n{result.stderr}"
    )


def test_shows_service_imports_first() -> None:
    _import_first("miramedia.shows.service")


def test_movies_service_imports_first() -> None:
    _import_first("miramedia.movies.service")


def test_main_imports_first() -> None:
    _import_first("miramedia.main")
