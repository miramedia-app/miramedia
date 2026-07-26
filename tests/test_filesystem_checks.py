"""Tests for startup filesystem preflight cleanup (Plan 132)."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from miramedia.filesystem_checks import run_filesystem_checks


def _config(tmp_path: Path) -> SimpleNamespace:
    root = tmp_path / "media"
    misc = SimpleNamespace(
        show_directory=root / "shows",
        movie_directory=root / "movies",
        torrent_directory=root / "torrents",
        effective_completed_path=root / "completed",
        image_directory=root / "images",
    )
    return SimpleNamespace(misc=misc)


def _leftover_test_dirs(config: SimpleNamespace) -> list[Path]:
    roots = (
        config.misc.show_directory,
        config.misc.movie_directory,
        config.misc.effective_completed_path,
    )
    found: list[Path] = []
    for root in roots:
        if root.exists():
            found.extend(p for p in root.rglob(".miramedia_test_dir"))
    return found


def test_happy_path(tmp_path: Path) -> None:
    config = _config(tmp_path)
    log = logging.getLogger("test_filesystem_checks")

    run_filesystem_checks(config, log)

    assert _leftover_test_dirs(config) == []


def test_hardlink_falls_back_to_copy(tmp_path: Path) -> None:
    config = _config(tmp_path)
    log = logging.getLogger("test_filesystem_checks")
    hardlink_error = OSError("simulated cross-device link")

    with patch.object(Path, "hardlink_to", side_effect=hardlink_error):
        run_filesystem_checks(config, log)

    assert _leftover_test_dirs(config) == []


def test_copy_failure_propagates_permission_error(tmp_path: Path) -> None:
    config = _config(tmp_path)
    log = logging.getLogger("test_filesystem_checks")
    hardlink_error = OSError("simulated cross-device link")
    copy_denied = PermissionError("copy denied")

    with (
        patch.object(Path, "hardlink_to", side_effect=hardlink_error),
        patch.object(shutil, "copy", side_effect=copy_denied),
    ):
        with pytest.raises(PermissionError, match="copy denied"):
            run_filesystem_checks(config, log)

    assert _leftover_test_dirs(config) == []
