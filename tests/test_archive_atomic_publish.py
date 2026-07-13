"""Tests for private-complete atomic archive publication."""

from __future__ import annotations

import errno
import os
import struct
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from miramedia.imports.archive_extraction import (
    ArchiveExtractionError,
    extract_archive_to_directory,
)
from miramedia.imports.archive_publication import (
    _READ_CHUNK_SIZE,
    PRIVATE_BUILD_PREFIX,
    QUARANTINE_PREFIX,
    bind_directory,
    canonical_tree_digest,
    container_name_for_digest,
)
from tests.archive_test_helpers import container_paths, payload_file


def _write_zip(path: Path, entries: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)


def _build_staging_tree(base: Path, layout: dict[str, bytes]) -> None:
    for rel, data in layout.items():
        target = base / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def test_canonical_digest_distinguishes_ambiguous_trees(tmp_path: Path) -> None:
    from miramedia.imports import archive_publication as publication

    left = tmp_path / "left"
    right = tmp_path / "right"
    left.mkdir()
    right.mkdir()
    _build_staging_tree(left, {"a": b"x", "b": b"y"})
    _build_staging_tree(right, {"a": b"xb\x00y"})

    left_fd = publication.bind_directory(left)
    right_fd = publication.bind_directory(right)
    try:
        left_digest = canonical_tree_digest(left_fd)
        right_digest = canonical_tree_digest(right_fd)
    finally:
        os.close(left_fd)
        os.close(right_fd)

    assert left_digest != right_digest


def test_canonical_digest_streams_without_whole_file_materialization(
    tmp_path: Path,
) -> None:
    from miramedia.imports import archive_publication as publication

    root = tmp_path / "tree"
    root.mkdir()
    payload = b"x" * (_READ_CHUNK_SIZE + 17)
    (root / "big.bin").write_bytes(payload)

    read_sizes: list[int] = []
    real_read = os.read

    def _track_read(fd: int, size: int = -1, /) -> bytes:
        data = real_read(fd, size)
        if data:
            read_sizes.append(len(data))
        return data

    fd = publication.bind_directory(root)
    try:
        with patch(
            "miramedia.imports.archive_publication.os.read", side_effect=_track_read
        ):
            digest = canonical_tree_digest(fd)
    finally:
        os.close(fd)

    assert digest
    assert read_sizes
    assert max(read_sizes) <= _READ_CHUNK_SIZE
    assert sum(read_sizes) == len(payload)


def test_mode_failure_leaves_no_final_container(tmp_path: Path) -> None:
    from miramedia.imports import archive_publication as publication

    archive = tmp_path / "release.zip"
    dest = tmp_path / "import"
    dest.mkdir()
    keeper = dest / "keeper.mkv"
    keeper.write_bytes(b"keeper")
    keeper_inode = keeper.stat().st_ino
    _write_zip(archive, {"clip.mkv": b"x"})

    with (
        patch.object(
            publication,
            "_apply_importable_modes_at",
            side_effect=OSError("simulated chmod failure"),
        ),
        pytest.raises(ArchiveExtractionError, match="chmod failure"),
    ):
        extract_archive_to_directory(archive, dest)

    assert keeper.stat().st_ino == keeper_inode
    assert container_paths(dest) == []


def test_publish_failure_preserves_existing_final_container(tmp_path: Path) -> None:
    from miramedia.imports import archive_publication as publication

    archive = tmp_path / "release.zip"
    dest = tmp_path / "import"
    dest.mkdir()
    _write_zip(archive, {"clip.mkv": b"original"})
    extract_archive_to_directory(archive, dest)
    existing = container_paths(dest)[0]
    marker = existing / "payload" / "marker.txt"
    marker.write_bytes(b"keep")

    _write_zip(archive, {"clip.mkv": b"replacement"})

    def _fail_atomic() -> None:
        msg = "simulated atomic publish failure"
        raise OSError(msg)

    with (
        patch.object(publication, "atomic_rename_noreplace", side_effect=_fail_atomic),
        pytest.raises(ArchiveExtractionError, match="publish"),
    ):
        extract_archive_to_directory(archive, dest)

    assert marker.read_bytes() == b"keep"
    assert len(container_paths(dest)) == 1


def test_staging_swap_before_install_is_rejected(tmp_path: Path) -> None:
    from miramedia.imports import archive_publication as publication

    archive = tmp_path / "release.zip"
    dest = tmp_path / "import"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "evil.mkv").write_bytes(b"evil")
    dest.mkdir()
    _write_zip(archive, {"clip.mkv": b"good"})

    real_assert = publication._assert_staging_identity

    def _swap_then_assert(
        parent_fd: int,
        staging_name: str,
        staging_stat: os.stat_result,
    ) -> None:
        staging_path = dest.parent / staging_name
        for child in staging_path.iterdir():
            child.unlink()
        staging_path.rmdir()
        staging_path.symlink_to(outside)
        real_assert(parent_fd, staging_name, staging_stat)

    with (
        patch.object(
            publication,
            "_assert_staging_identity",
            side_effect=_swap_then_assert,
        ),
        pytest.raises(ArchiveExtractionError, match="replaced"),
    ):
        extract_archive_to_directory(archive, dest)

    assert container_paths(dest) == []


def test_hard_link_entry_rejected_during_digest(tmp_path: Path) -> None:
    from miramedia.imports import archive_publication as publication

    root = tmp_path / "tree"
    root.mkdir()
    original = root / "a.txt"
    original.write_bytes(b"x")
    os.link(original, root / "b.txt")

    fd = publication.bind_directory(root)
    try:
        with pytest.raises(ArchiveExtractionError, match="hard link"):
            canonical_tree_digest(fd)
    finally:
        os.close(fd)


def test_file_swap_between_metadata_and_hash_rejected(tmp_path: Path) -> None:
    from miramedia.imports import archive_publication as publication

    root = tmp_path / "tree"
    root.mkdir()
    (root / "swap.txt").write_bytes(b"good")

    real_collect = publication._collect_canonical_entry_metadata

    def _collect_then_swap(fd: int) -> list[publication._CanonicalEntry]:
        entries = real_collect(fd)
        (root / "swap.txt").unlink()
        (root / "swap.txt").write_bytes(b"bad")
        return entries

    fd = publication.bind_directory(root)
    try:
        with (
            patch.object(
                publication,
                "_collect_canonical_entry_metadata",
                side_effect=_collect_then_swap,
            ),
            pytest.raises(ArchiveExtractionError, match="redirected"),
        ):
            canonical_tree_digest(fd)
    finally:
        os.close(fd)


def test_file_swap_between_stat_and_open_rejected(tmp_path: Path) -> None:
    from miramedia.imports import archive_publication as publication

    root = tmp_path / "tree"
    root.mkdir()
    (root / "swap.txt").write_bytes(b"good")

    real_open = publication._open_regular_file_verified

    def _swap_then_open(dir_fd: int, name: str) -> tuple[int, os.stat_result]:
        path = root / name
        path.unlink()
        path.write_bytes(b"evil")
        return real_open(dir_fd, name)

    fd = publication.bind_directory(root)
    try:
        with (
            patch.object(
                publication,
                "_open_regular_file_verified",
                side_effect=_swap_then_open,
            ),
            pytest.raises(ArchiveExtractionError, match=r"redirected|size changed"),
        ):
            canonical_tree_digest(fd)
    finally:
        os.close(fd)


def test_quarantine_cleanup_leaves_replaced_private(tmp_path: Path) -> None:
    from miramedia.imports import archive_publication as publication

    parent = tmp_path / "import"
    parent.mkdir()
    private_name = f"{PRIVATE_BUILD_PREFIX}abc"
    private_path = parent / private_name
    private_path.mkdir()
    private_stat = private_path.lstat()
    private_path.rmdir()
    replacement = parent / "replacement"
    replacement.mkdir()
    (replacement / "marker").write_bytes(b"safe")
    replacement.rename(private_path)

    parent_fd = publication.bind_directory(parent)
    try:
        publication.quarantine_owned_directory(
            parent_fd,
            private_name,
            private_stat,
            allow_recursive_cleanup=True,
        )
        assert (private_path / "marker").read_bytes() == b"safe"
        assert list(parent.glob(f"{QUARANTINE_PREFIX}*")) == []
    finally:
        os.close(parent_fd)


def test_quarantine_swap_between_stat_and_rename_leaves_marker(tmp_path: Path) -> None:
    from miramedia.imports import archive_publication as publication

    parent = tmp_path / "import"
    parent.mkdir()
    owned = parent / f"{PRIVATE_BUILD_PREFIX}owned"
    owned.mkdir()
    owned_stat = owned.lstat()
    parent_fd = publication.bind_directory(parent)
    real_stat = os.stat
    try:

        def _swap_after_first_stat(
            name: str,
            *,
            dir_fd: int | None = None,
            follow_symlinks: bool = True,
        ) -> os.stat_result:
            result = real_stat(name, dir_fd=dir_fd, follow_symlinks=follow_symlinks)
            if (
                dir_fd == parent_fd
                and name == owned.name
                and result.st_ino == owned_stat.st_ino
            ):
                hidden = parent / "hidden-owned"
                owned.rename(hidden)
                replacement = parent / "replacement"
                replacement.mkdir()
                (replacement / "marker").write_bytes(b"safe")
                replacement.rename(parent / owned.name)
            return result

        with patch.object(os, "stat", side_effect=_swap_after_first_stat):
            publication.quarantine_owned_directory(
                parent_fd,
                owned.name,
                owned_stat,
                allow_recursive_cleanup=True,
            )
        assert (parent / owned.name / "marker").read_bytes() == b"safe"
    finally:
        os.close(parent_fd)


def test_quarantine_swap_before_rmdir_leaves_quarantine(tmp_path: Path) -> None:
    from miramedia.imports import archive_publication as publication

    parent = tmp_path / "import"
    parent.mkdir()
    owned = parent / f"{PRIVATE_BUILD_PREFIX}owned"
    owned.mkdir()
    owned_stat = owned.lstat()
    parent_fd = publication.bind_directory(parent)
    try:
        with patch.object(
            publication,
            "_directory_fd_is_empty",
            return_value=False,
        ):
            publication.quarantine_owned_directory(
                parent_fd,
                owned.name,
                owned_stat,
                allow_recursive_cleanup=True,
            )
        quarantines = list(parent.glob(f"{QUARANTINE_PREFIX}*"))
        assert len(quarantines) == 1
    finally:
        os.close(parent_fd)


def test_quarantine_without_recursive_cleanup_preserves_contents(
    tmp_path: Path,
) -> None:
    from miramedia.imports import archive_publication as publication

    parent = tmp_path / "import"
    parent.mkdir()
    owned = parent / f"{PRIVATE_BUILD_PREFIX}owned"
    owned.mkdir()
    (owned / "payload.txt").write_bytes(b"keep")
    owned_stat = owned.lstat()
    parent_fd = publication.bind_directory(parent)
    try:
        publication.quarantine_owned_directory(
            parent_fd,
            owned.name,
            owned_stat,
            allow_recursive_cleanup=False,
        )
        quarantines = list(parent.glob(f"{QUARANTINE_PREFIX}*"))
        assert len(quarantines) == 1
        assert (quarantines[0] / "payload.txt").read_bytes() == b"keep"
    finally:
        os.close(parent_fd)


def test_staging_directory_replacement_before_publish_rejected(
    tmp_path: Path,
) -> None:
    from miramedia.imports import archive_publication as publication

    archive = tmp_path / "release.zip"
    dest = tmp_path / "import"
    dest.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "evil.mkv").write_bytes(b"evil")
    _write_zip(archive, {"clip.mkv": b"good"})

    real_publish = publication.publish_staging_tree

    def _swap_directory_at_publish(
        staging: publication.BoundStagingDirectory,
        destination: publication.BoundImportDestination,
    ) -> Path:
        parent = destination.destination_path.parent
        staging_path = parent / staging.name
        replacement = parent / "replacement"
        replacement.mkdir()
        (replacement / "evil.mkv").write_bytes(b"evil")
        for child in staging_path.iterdir():
            child.unlink()
        staging_path.rmdir()
        replacement.rename(staging_path)
        return real_publish(staging, destination)

    with (
        patch.object(
            publication,
            "publish_staging_tree",
            side_effect=_swap_directory_at_publish,
        ),
        pytest.raises(ArchiveExtractionError, match="replaced"),
    ):
        extract_archive_to_directory(archive, dest)

    assert container_paths(dest) == []
    assert (outside / "evil.mkv").read_bytes() == b"evil"


def test_foreign_payload_preserved_when_verify_fails(tmp_path: Path) -> None:
    from miramedia.imports import archive_publication as publication

    archive = tmp_path / "release.zip"
    dest = tmp_path / "import"
    dest.mkdir()
    _write_zip(archive, {"clip.mkv": b"x"})

    with (
        patch.object(
            publication,
            "_verify_installed_payload_identity",
            side_effect=ArchiveExtractionError("identity mismatch"),
        ),
        pytest.raises(ArchiveExtractionError, match="identity mismatch"),
    ):
        extract_archive_to_directory(archive, dest)

    assert container_paths(dest) == []
    quarantines = list(dest.parent.glob(f"{QUARANTINE_PREFIX}*"))
    publishes = list(dest.parent.glob(f"{PRIVATE_BUILD_PREFIX}*"))
    assert quarantines or publishes
    survivors = quarantines or publishes
    assert any(p.rglob("clip.mkv") for p in survivors)


def test_inplace_file_mutation_during_hash_rejected(tmp_path: Path) -> None:
    from miramedia.imports import archive_publication as publication

    root = tmp_path / "tree"
    root.mkdir()
    target = root / "mutate.txt"
    target.write_bytes(b"aaaa")

    real_read = os.read

    def _mutate_on_second_read(fd: int, size: int = -1, /) -> bytes:
        data = real_read(fd, size)
        if data:
            target.write_bytes(b"bbbb")
        return data

    fd = publication.bind_directory(root)
    try:
        with (
            patch(
                "miramedia.imports.archive_publication.os.read",
                side_effect=_mutate_on_second_read,
            ),
            pytest.raises(ArchiveExtractionError, match="changed during hashing"),
        ):
            canonical_tree_digest(fd)
    finally:
        os.close(fd)


def test_staging_cleanup_leaves_replaced_directory(tmp_path: Path) -> None:
    from miramedia.imports import archive_extraction as mod

    archive = tmp_path / "bad.zip"
    dest = tmp_path / "import"
    dest.mkdir()
    _write_zip(archive, {"../escape.mkv": b"x"})

    captured: list[mod.BoundStagingDirectory] = []
    real_cleanup = mod._cleanup_staging

    def _swap_then_cleanup(
        staging: mod.BoundStagingDirectory,
        *,
        primary_error: BaseException | None = None,
    ) -> None:
        captured.append(staging)
        parent = dest.parent
        staging_path = parent / staging.name
        replacement = parent / "replacement"
        replacement.mkdir()
        (replacement / "marker").write_bytes(b"safe")
        if staging_path.exists():
            for child in staging_path.iterdir():
                child.unlink()
            staging_path.rmdir()
        replacement.rename(staging_path)
        real_cleanup(staging, primary_error=primary_error)

    with (
        patch.object(mod, "_cleanup_staging", side_effect=_swap_then_cleanup),
        pytest.raises(ArchiveExtractionError),
    ):
        extract_archive_to_directory(archive, dest)

    assert captured
    assert (dest.parent / captured[0].name / "marker").read_bytes() == b"safe"
    assert list(dest.parent.glob(f"{QUARANTINE_PREFIX}*")) == []


def test_missing_atomic_primitive_cleans_private_build(tmp_path: Path) -> None:
    from miramedia.imports import archive_publication as publication

    archive = tmp_path / "release.zip"
    dest = tmp_path / "import"
    dest.mkdir()
    _write_zip(archive, {"clip.mkv": b"x"})

    with (
        patch.object(
            publication,
            "atomic_rename_noreplace",
            side_effect=OSError(errno.ENOTSUP, "atomic rename unavailable"),
        ),
        pytest.raises(ArchiveExtractionError, match="publish"),
    ):
        extract_archive_to_directory(archive, dest)

    assert container_paths(dest) == []
    assert list(dest.parent.glob(f"{QUARANTINE_PREFIX}*")) == []
    assert len(list(dest.parent.glob(f"{PRIVATE_BUILD_PREFIX}*"))) == 1


def test_raced_identical_winner_returns_existing_container(tmp_path: Path) -> None:
    from miramedia.imports import archive_publication as publication

    archive = tmp_path / "release.zip"
    dest = tmp_path / "import"
    dest.mkdir()
    _write_zip(archive, {"clip.mkv": b"same"})
    first = extract_archive_to_directory(archive, dest)
    assert len(container_paths(dest)) == 1

    _write_zip(archive, {"clip.mkv": b"same"})

    real_try_open = publication._try_open_container
    open_calls = 0

    def _hide_existing_once(parent_fd: int, container_name: str) -> int | None:
        nonlocal open_calls
        open_calls += 1
        if open_calls == 1:
            return None
        return real_try_open(parent_fd, container_name)

    def _race_win(*_args: object, **_kwargs: object) -> None:
        race_msg = "simulated race"
        raise FileExistsError(race_msg)

    with (
        patch.object(
            publication, "_try_open_container", side_effect=_hide_existing_once
        ),
        patch.object(publication, "atomic_rename_noreplace", side_effect=_race_win),
    ):
        raced = extract_archive_to_directory(archive, dest)

    assert raced == first
    assert len(container_paths(dest)) == 1


def test_raced_non_identical_winner_raises_collision(tmp_path: Path) -> None:
    from miramedia.imports import archive_publication as publication

    archive = tmp_path / "release.zip"
    dest = tmp_path / "import"
    dest.mkdir()
    _write_zip(archive, {"clip.mkv": b"winner"})
    extract_archive_to_directory(archive, dest)
    existing = container_paths(dest)[0]

    _write_zip(archive, {"clip.mkv": b"loser"})

    real_try_open = publication._try_open_container
    open_calls = 0

    def _hide_existing_once(parent_fd: int, container_name: str) -> int | None:
        nonlocal open_calls
        open_calls += 1
        if open_calls == 1:
            return None
        return real_try_open(parent_fd, container_name)

    def _race_lose(*_args: object, **_kwargs: object) -> None:
        race_msg = "simulated race"
        raise FileExistsError(race_msg)

    def _force_same_container_name(_digest: str) -> str:
        return existing.name

    with (
        patch.object(
            publication, "_try_open_container", side_effect=_hide_existing_once
        ),
        patch.object(publication, "atomic_rename_noreplace", side_effect=_race_lose),
        patch.object(
            publication,
            "container_name_for_digest",
            side_effect=_force_same_container_name,
        ),
        pytest.raises(ArchiveExtractionError, match="already exists"),
    ):
        extract_archive_to_directory(archive, dest)

    assert (existing / "payload" / "clip.mkv").read_bytes() == b"winner"
    assert len(container_paths(dest)) == 1


def test_zip_sentinel_sizes_reject_without_zipfile(tmp_path: Path) -> None:
    archive = tmp_path / "zip64sentinel.zip"
    dest = tmp_path / "import"
    dest.mkdir()
    _write_zip(archive, {"clip.mkv": b"x"})
    data = bytearray(archive.read_bytes())
    eocd = data.rfind(b"PK\x05\x06")
    cd_offset = struct.unpack_from("<I", data, eocd + 16)[0]
    struct.pack_into("<I", data, cd_offset + 20, 0xFFFFFFFF)

    archive.write_bytes(data)

    with (
        patch("miramedia.imports.archive_parsers.zipfile.ZipFile") as zipfile_ctor,
        pytest.raises(ArchiveExtractionError, match="zip64"),
    ):
        extract_archive_to_directory(archive, dest)

    zipfile_ctor.assert_not_called()
    assert container_paths(dest) == []


def test_zip_invalid_filename_encoding_raises(tmp_path: Path) -> None:
    archive = tmp_path / "badname.zip"
    dest = tmp_path / "import"
    dest.mkdir()
    _write_zip(archive, {"clip.mkv": b"x"})
    data = bytearray(archive.read_bytes())
    eocd = data.rfind(b"PK\x05\x06")
    cd_offset = struct.unpack_from("<I", data, eocd + 16)[0]
    struct.pack_into("<H", data, cd_offset + 8, 0x0800)
    filename_offset = cd_offset + 46
    invalid_name = b"\xff\xfe\xfd\xfc\xfb\xfa\xf9"
    data[filename_offset : filename_offset + len(invalid_name)] = invalid_name
    struct.pack_into("<H", data, cd_offset + 28, len(invalid_name))
    archive.write_bytes(data)

    with (
        patch("miramedia.imports.archive_parsers.zipfile.ZipFile") as zipfile_ctor,
        pytest.raises(ArchiveExtractionError, match="zip filename"),
    ):
        extract_archive_to_directory(archive, dest)

    zipfile_ctor.assert_not_called()
    assert container_paths(dest) == []


def test_successful_publish_has_payload_not_empty_container(tmp_path: Path) -> None:
    archive = tmp_path / "release.zip"
    dest = tmp_path / "import"
    dest.mkdir()
    _write_zip(archive, {"clip.mkv": b"video"})

    container = extract_archive_to_directory(archive, dest)

    assert payload_file(dest, "clip.mkv").read_bytes() == b"video"
    assert container.name == container_name_for_digest(
        canonical_tree_digest_from_path(container / "payload")
    )


def canonical_tree_digest_from_path(payload_root: Path) -> str:
    from miramedia.imports import archive_publication as publication

    fd = publication.bind_directory(payload_root)
    try:
        return canonical_tree_digest(fd)
    finally:
        os.close(fd)


def test_staging_open_identity_mismatch_leaves_replacement(tmp_path: Path) -> None:
    from miramedia.imports import archive_extraction as extraction
    from miramedia.imports.archive_staging_io import STAGING_DIR_PREFIX

    parent = tmp_path / "parent"
    parent.mkdir()
    parent_fd = bind_directory(parent)
    replacement = parent / f"{STAGING_DIR_PREFIX}replacement"
    replacement.mkdir()
    (replacement / "marker").write_bytes(b"safe")
    real_open = os.open
    staging_names: list[str] = []

    def _swap_on_staging_open(path: str, flags: int, /, **kwargs: object) -> int:
        if (
            isinstance(path, str)
            and path.startswith(STAGING_DIR_PREFIX)
            and flags & os.O_DIRECTORY
            and kwargs.get("dir_fd") == parent_fd
        ):
            staging_names.append(path)
            replacement.rename(parent / path)
            msg = "simulated staging bind failure"
            raise OSError(errno.ENOENT, msg)
        return real_open(path, flags, **kwargs)

    try:
        with (
            patch.object(os, "open", side_effect=_swap_on_staging_open),
            pytest.raises(ArchiveExtractionError, match="bind staging directory"),
        ):
            extraction._create_staging_dir(parent_fd)
    finally:
        os.close(parent_fd)

    assert staging_names
    assert (parent / staging_names[0] / "marker").read_bytes() == b"safe"


def test_private_source_swap_before_rename_rejected(tmp_path: Path) -> None:
    from miramedia.imports import archive_publication as publication

    archive = tmp_path / "release.zip"
    dest = tmp_path / "import"
    dest.mkdir()
    _write_zip(archive, {"clip.mkv": b"good"})
    real_rename = publication.atomic_rename_noreplace

    def _swap_before_rename(
        src_parent_fd: int,
        src_name: str,
        dst_parent_fd: int,
        dst_name: str,
    ) -> None:
        real_rename(src_parent_fd, src_name, dst_parent_fd, dst_name)
        published = dest / dst_name
        hidden = dest / f"{dst_name}-hidden"
        published.rename(hidden)
        replacement = dest.parent / "replacement"
        replacement.mkdir()
        (replacement / "evil.mkv").write_bytes(b"evil")
        replacement.rename(published)

    with (
        patch.object(
            publication,
            "atomic_rename_noreplace",
            side_effect=_swap_before_rename,
        ),
        pytest.raises(ArchiveExtractionError, match="identity mismatch"),
    ):
        extract_archive_to_directory(archive, dest)

    assert container_paths(dest) == []
