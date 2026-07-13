"""Shared helpers for archive extraction tests."""

from __future__ import annotations

from pathlib import Path

from miramedia.imports.archive_publication import (
    ARCHIVE_CONTAINER_PREFIX,
    PAYLOAD_DIR_NAME,
)


def payload_root(destination: Path) -> Path:
    containers = sorted(destination.glob(f"{ARCHIVE_CONTAINER_PREFIX}*"))
    assert len(containers) == 1
    return containers[0] / PAYLOAD_DIR_NAME


def payload_file(destination: Path, *parts: str) -> Path:
    for container in container_paths(destination):
        candidate = container / PAYLOAD_DIR_NAME / Path(*parts)
        if candidate.exists():
            return candidate
    msg = f"payload file not found under {destination}: {'/'.join(parts)}"
    raise AssertionError(msg)


def container_paths(destination: Path) -> list[Path]:
    return sorted(destination.glob(f"{ARCHIVE_CONTAINER_PREFIX}*"))
