"""Unit tests for arr-shim on-disk path helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from miramedia.subtitles.arr_shim import shim_paths


def test_rootfolder_payloads_existing_directory(tmp_path: Path) -> None:
    root = tmp_path / "library"
    root.mkdir()

    payloads = shim_paths.rootfolder_payloads([root])

    assert len(payloads) == 1
    row = payloads[0]
    assert row["id"] == 1
    assert row["accessible"] is True
    assert row["freeSpace"] > 0
    assert row["path"] == str(root.resolve())


def test_rootfolder_payloads_missing_directory(tmp_path: Path) -> None:
    root = tmp_path / "not-created-yet"

    payloads = shim_paths.rootfolder_payloads([root])

    assert len(payloads) == 1
    row = payloads[0]
    assert row["accessible"] is True
    assert row["freeSpace"] == 0
    assert row["path"] == str(root)


def test_rootfolder_payloads_existing_file_not_directory(tmp_path: Path) -> None:
    root = tmp_path / "not-a-dir"
    root.write_text("oops", encoding="utf-8")

    payloads = shim_paths.rootfolder_payloads([root])

    assert len(payloads) == 1
    row = payloads[0]
    assert row["accessible"] is False
    assert row["freeSpace"] == 0
    assert row["path"] == str(root.resolve())


def test_rootfolder_payloads_probe_oserror(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "stalled-mount"

    def _raise_oserror(self: Path) -> bool:
        _ = self
        msg = "mount timed out"
        raise OSError(msg)

    monkeypatch.setattr(Path, "is_dir", _raise_oserror)

    payloads = shim_paths.rootfolder_payloads([root])

    assert len(payloads) == 1
    row = payloads[0]
    assert row["accessible"] is False
    assert row["freeSpace"] == 0
    assert row["path"] == str(root)
