"""Characterization tests for miramedia/imports/files.py.

These tests pin the *existing* behaviour of the pure-filesystem import
helpers.  They never modify miramedia/ source — if a contract differs from
what's documented in the plan, that is noted as an observation, not fixed.
"""

# ruff: noqa: TRY003, EM101
from pathlib import Path
from unittest.mock import patch

import pytest

from miramedia.imports.files import (
    DiskSpaceError,
    ImportConflictError,
    delete_files_matching_stems,
    ensure_free_space,
    extract_archives,
    find_renamed_duplicate,
    import_file,
    link_video_into_slot,
    rename_media_slot,
)

# ---------------------------------------------------------------------------
# import_file
# ---------------------------------------------------------------------------


def test_import_file_fresh_creates_hardlink(tmp_path: Path) -> None:
    """A fresh import hardlinks target to source (same inode on same fs)."""
    src = tmp_path / "source.mkv"
    src.write_bytes(b"video content here")
    dst = tmp_path / "target.mkv"

    import_file(dst, src)

    assert dst.exists()
    assert dst.stat().st_ino == src.stat().st_ino


def test_import_file_reimport_same_pair_is_noop(tmp_path: Path) -> None:
    """Re-importing the same source/target pair is idempotent (same inode)."""
    src = tmp_path / "source.mkv"
    src.write_bytes(b"video content")
    dst = tmp_path / "target.mkv"

    import_file(dst, src)
    inode_after_first = dst.stat().st_ino

    # Second call must not raise and target must remain the same link.
    import_file(dst, src)
    assert dst.stat().st_ino == inode_after_first


def test_import_file_same_size_different_inode_is_noop(tmp_path: Path) -> None:
    """Pre-existing complete copy (same size, different inode) is skipped.

    The size-only heuristic treats any same-size target as already imported,
    even if the bytes differ — this pins the BLIND SPOT of that heuristic.
    """
    src = tmp_path / "source.mkv"
    original_bytes = b"AAA"  # 3 bytes
    src.write_bytes(original_bytes)

    dst = tmp_path / "target.mkv"
    # Write DIFFERENT bytes of the SAME length to the target first.
    different_bytes = b"BBB"  # still 3 bytes
    dst.write_bytes(different_bytes)

    # Ensure they are genuinely different inodes.
    assert dst.stat().st_ino != src.stat().st_ino

    import_file(dst, src)

    # The call must be a no-op: the differing content is NOT overwritten.
    assert dst.read_bytes() == different_bytes


def test_import_file_different_size_with_overwrite_replaces(tmp_path: Path) -> None:
    """A different-size pre-existing target is replaced when overwrite=True."""
    src = tmp_path / "source.mkv"
    src.write_bytes(b"new content abcdef")
    dst = tmp_path / "target.mkv"
    dst.write_bytes(b"old")  # shorter -> different size

    import_file(dst, src, overwrite=True)

    # After replacement the target must be linked (or copied) from source.
    assert dst.stat().st_size == src.stat().st_size


def test_import_file_overwrite_false_raises_conflict(tmp_path: Path) -> None:
    """overwrite=False raises ImportConflictError when target already exists."""
    src = tmp_path / "source.mkv"
    src.write_bytes(b"content")
    dst = tmp_path / "target.mkv"
    dst.write_bytes(b"something else entirely")  # different size, different content

    with pytest.raises(ImportConflictError):
        import_file(dst, src, overwrite=False)


def test_import_file_copy_fallback_on_oserror(tmp_path: Path) -> None:
    """When hardlink_to raises OSError the file is copied; no .mmpart remains."""
    src = tmp_path / "source.mkv"
    src.write_bytes(b"copy fallback content")
    dst = tmp_path / "target.mkv"

    def _failing_hardlink(self: Path, target: Path) -> None:  # noqa: ARG001
        raise OSError("simulated cross-device link")

    with patch.object(Path, "hardlink_to", _failing_hardlink):
        import_file(dst, src)

    assert dst.exists()
    assert dst.read_bytes() == b"copy fallback content"
    # .mmpart must be cleaned up after successful atomic publish.
    assert not (tmp_path / "target.mkv.mmpart").exists()


def test_import_file_copy_fallback_failure_raises_disk_space_error(
    tmp_path: Path,
) -> None:
    """When both hardlink and shutil.copy fail, DiskSpaceError is raised and .mmpart absent."""
    src = tmp_path / "source.mkv"
    src.write_bytes(b"data")
    dst = tmp_path / "target.mkv"

    def _failing_hardlink(self: Path, target: Path) -> None:  # noqa: ARG001
        raise OSError("simulated cross-device link")

    # files.py imports shutil at the top and calls shutil.copy directly;
    # patch it at the module level where it is looked up.
    with (
        patch.object(Path, "hardlink_to", _failing_hardlink),
        patch("miramedia.imports.files.shutil.copy", side_effect=OSError("disk full")),
    ):
        with pytest.raises(DiskSpaceError):
            import_file(dst, src)

    assert not dst.exists()
    assert not (tmp_path / "target.mkv.mmpart").exists()


def test_import_file_toctou_hardlink_raises_conflict(tmp_path: Path) -> None:
    """FileExistsError from hardlink_to surfaces as ImportConflictError (TOCTOU path)."""
    src = tmp_path / "source.mkv"
    src.write_bytes(b"content")
    dst = tmp_path / "target.mkv"
    # Do NOT pre-create dst: the TOCTOU path triggers from hardlink_to itself.

    def _toctou_hardlink(self: Path, target: Path) -> None:  # noqa: ARG001
        raise FileExistsError("simulated race")

    with patch.object(Path, "hardlink_to", _toctou_hardlink):
        with pytest.raises(ImportConflictError):
            import_file(dst, src)


def test_import_file_reappeared_full_copy_is_idempotent(tmp_path: Path) -> None:
    """A concurrent import that re-published an equal-content target is success.

    Reproduces the "ghost failure" race: a second importer unlinks then loses
    the hardlink to a peer that already published the same file. If the target
    now in place matches the source by size, treat it as done — not failed_io.
    """
    src = tmp_path / "source.mkv"
    src.write_bytes(b"content")
    dst = tmp_path / "target.mkv"

    def _race_hardlink(self: Path, target: Path) -> None:  # noqa: ARG001
        # Simulate the peer winning the race between our unlink and our link:
        # the equal-content file reappears, then our hardlink_to collides.
        dst.write_bytes(b"content")
        raise FileExistsError("simulated race")

    with patch.object(Path, "hardlink_to", _race_hardlink):
        import_file(dst, src)  # must NOT raise

    assert dst.read_bytes() == b"content"


def test_import_file_reappeared_different_content_raises(tmp_path: Path) -> None:
    """A reappeared target with mismatched content is a real conflict."""
    src = tmp_path / "source.mkv"
    src.write_bytes(b"the source content")
    dst = tmp_path / "target.mkv"

    def _race_hardlink(self: Path, target: Path) -> None:  # noqa: ARG001
        dst.write_bytes(b"different")  # different size -> genuine conflict
        raise FileExistsError("simulated race")

    with patch.object(Path, "hardlink_to", _race_hardlink):
        with pytest.raises(ImportConflictError):
            import_file(dst, src)


def test_import_file_replaced_target_is_linked_to_source(tmp_path: Path) -> None:
    """After replacing a different-size target the new file shares source inode (same fs)."""
    src = tmp_path / "source.mkv"
    src.write_bytes(b"new longer content here")
    dst = tmp_path / "target.mkv"
    dst.write_bytes(b"old")  # shorter -> triggers replacement path

    import_file(dst, src, overwrite=True)

    # On the same filesystem the replacement is done via hardlink,
    # so the resulting target shares the source's inode.
    assert dst.stat().st_ino == src.stat().st_ino


# ---------------------------------------------------------------------------
# extract_archives
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mime_type",
    [
        "application/x-zip-compressed",
        "application/x-compressed",
    ],
)
def test_extract_archives_recognizes_zip_mime_types(
    tmp_path: Path,
    mime_type: str,
) -> None:
    """ZIP archives guessed as Windows-style MIME types are routed to safe extraction."""
    archive = tmp_path / "release.zip"
    archive.write_bytes(b"fake zip content")

    def _guess_type(path: Path | str) -> tuple[str | None, None]:  # noqa: ARG001
        return (mime_type, None)

    with (
        patch("miramedia.imports.files.mimetypes.guess_type", _guess_type),
        patch("miramedia.imports.files.extract_archive_to_directory") as extract_mock,
    ):
        extract_archives([archive])

    extract_mock.assert_called_once_with(archive, archive.parent)


# ---------------------------------------------------------------------------
# ensure_free_space
# ---------------------------------------------------------------------------


def test_ensure_free_space_huge_request_raises(tmp_path: Path) -> None:
    """Requesting more bytes than available disk raises DiskSpaceError."""
    with pytest.raises(DiskSpaceError):
        ensure_free_space(tmp_path, 10**18)  # 1 exabyte -- always unavailable


def test_ensure_free_space_small_request_does_not_raise(tmp_path: Path) -> None:
    """A small, clearly-available byte request does not raise."""
    ensure_free_space(tmp_path, 1)  # must not raise


# ---------------------------------------------------------------------------
# rename_media_slot
# ---------------------------------------------------------------------------


def test_rename_media_slot_renames_all_matching_files(tmp_path: Path) -> None:
    """Files matching old_stem.* are renamed to new_stem.* with tails preserved."""
    (tmp_path / "old.mkv").write_bytes(b"video")
    (tmp_path / "old.en.srt").write_bytes(b"subtitles")
    (tmp_path / "old.nfo").write_bytes(b"nfo")

    rename_media_slot(tmp_path, "old", "new")

    assert (tmp_path / "new.mkv").exists()
    assert (tmp_path / "new.en.srt").exists()
    assert (tmp_path / "new.nfo").exists()
    assert not (tmp_path / "old.mkv").exists()
    assert not (tmp_path / "old.en.srt").exists()
    assert not (tmp_path / "old.nfo").exists()


def test_rename_media_slot_same_stem_is_noop(tmp_path: Path) -> None:
    """old_stem == new_stem: no files are touched."""
    f = tmp_path / "show.mkv"
    f.write_bytes(b"content")
    mtime_before = f.stat().st_mtime_ns

    rename_media_slot(tmp_path, "show", "show")

    assert f.exists()
    assert f.stat().st_mtime_ns == mtime_before


def test_rename_media_slot_unrelated_files_untouched(tmp_path: Path) -> None:
    """Files not starting with old_stem are left in place."""
    related = tmp_path / "ep01.mkv"
    related.write_bytes(b"episode")
    unrelated = tmp_path / "ep02.mkv"
    unrelated.write_bytes(b"other episode")

    rename_media_slot(tmp_path, "ep01", "episode01")

    assert (tmp_path / "episode01.mkv").exists()
    assert not related.exists()
    # Unrelated file must remain untouched.
    assert unrelated.exists()
    assert unrelated.read_bytes() == b"other episode"


# ---------------------------------------------------------------------------
# find_renamed_duplicate
# ---------------------------------------------------------------------------


def test_find_renamed_duplicate_inode_match(tmp_path: Path) -> None:
    """Inode-identical file (hardlinked) is detected regardless of path name."""
    src = tmp_path / "source.mkv"
    src.write_bytes(b"content")
    linked = tmp_path / "other_name.mkv"
    linked.hardlink_to(src)

    result = find_renamed_duplicate(src, {"key_a": linked})

    assert result == "key_a"


def test_find_renamed_duplicate_size_match_different_name(tmp_path: Path) -> None:
    """Same byte-size file with a different name and inode matches via size heuristic."""
    src = tmp_path / "source.mkv"
    src.write_bytes(b"12345")  # 5 bytes
    other = tmp_path / "existing.mkv"
    other.write_bytes(b"54321")  # also 5 bytes, different inode

    result = find_renamed_duplicate(src, {"key_b": other})

    assert result == "key_b"


def test_find_renamed_duplicate_no_match_returns_none(tmp_path: Path) -> None:
    """No size or inode match returns None."""
    src = tmp_path / "source.mkv"
    src.write_bytes(b"abc")
    other = tmp_path / "existing.mkv"
    other.write_bytes(b"abcdef")  # different size

    result = find_renamed_duplicate(src, {"key_c": other})

    assert result is None


def test_find_renamed_duplicate_missing_source_returns_none(tmp_path: Path) -> None:
    """Missing source file (OSError on stat) returns None rather than raising."""
    missing = tmp_path / "does_not_exist.mkv"
    other = tmp_path / "existing.mkv"
    other.write_bytes(b"content")

    result = find_renamed_duplicate(missing, {"key_d": other})

    assert result is None


def test_find_renamed_duplicate_self_key_returns_via_inode(tmp_path: Path) -> None:
    """Passing the source itself as an existing path matches via inode (not size guard)."""
    src = tmp_path / "source.mkv"
    src.write_bytes(b"same size content here")

    # Pass the source itself as the "existing" path under a key.
    result = find_renamed_duplicate(src, {"self_key": src})

    # The inode match fires first (inode IS equal) -- size guard `path != source`
    # would exclude the same path, but inode branch runs before size branch.
    assert result == "self_key"


def test_find_renamed_duplicate_size_guard_fires_for_different_path(
    tmp_path: Path,
) -> None:
    """Size-match branch fires when paths differ but byte counts are equal."""
    src = tmp_path / "source.mkv"
    src.write_bytes(b"five!")  # 5 bytes

    other = tmp_path / "other.mkv"
    other.write_bytes(b"FIVE!")  # also 5 bytes, different inode, different path

    # other path != src path, so size match fires.
    result = find_renamed_duplicate(src, {"key_e": other})
    assert result == "key_e"


# ---------------------------------------------------------------------------
# delete_files_matching_stems
# ---------------------------------------------------------------------------


def test_delete_files_matching_stems_removes_matching_files(tmp_path: Path) -> None:
    (tmp_path / "Show.S01E01.1080p.mkv").write_bytes(b"video")
    (tmp_path / "Show.S01E01.1080p.en.srt").write_bytes(b"sub")
    (tmp_path / "other.mkv").write_bytes(b"keep")

    delete_files_matching_stems(tmp_path, ["Show.S01E01.1080p"])

    assert not (tmp_path / "Show.S01E01.1080p.mkv").exists()
    assert not (tmp_path / "Show.S01E01.1080p.en.srt").exists()
    assert (tmp_path / "other.mkv").exists()


# ---------------------------------------------------------------------------
# link_video_into_slot
# ---------------------------------------------------------------------------


def test_link_video_into_slot_source_in_place_renames(tmp_path: Path) -> None:
    source = tmp_path / "old_stem.mkv"
    source.write_bytes(b"video")
    target = tmp_path / "new_stem.mkv"

    link_video_into_slot(
        tmp_path,
        source,
        "new_stem",
        target,
        source_in_place=True,
    )

    assert not source.exists()
    assert target.exists()
    assert target.read_bytes() == b"video"


def test_link_video_into_slot_cross_dir_hardlinks(tmp_path: Path) -> None:
    src_dir = tmp_path / "download"
    lib_dir = tmp_path / "library"
    src_dir.mkdir()
    lib_dir.mkdir()
    source = src_dir / "source.mkv"
    source.write_bytes(b"video content")
    target = lib_dir / "canonical.mkv"

    link_video_into_slot(
        lib_dir,
        source,
        "canonical",
        target,
        source_in_place=False,
    )

    assert target.exists()
    assert target.stat().st_ino == source.stat().st_ino
