from pathlib import Path

from miramedia.torrents.paths import resolve_within


def test_plain_relative_path_ok(tmp_path: Path) -> None:
    (tmp_path / "dir").mkdir()
    (tmp_path / "dir" / "file.mkv").touch()
    assert (
        resolve_within(tmp_path, "dir/file.mkv")
        == (tmp_path / "dir" / "file.mkv").resolve()
    )


def test_dotdot_traversal_rejected(tmp_path: Path) -> None:
    assert resolve_within(tmp_path, "../outside.txt") is None


def test_absolute_path_rejected(tmp_path: Path) -> None:
    assert resolve_within(tmp_path, "/etc/passwd") is None


def test_symlink_escape_rejected(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-target"
    outside.touch()
    (tmp_path / "link").symlink_to(outside)
    assert resolve_within(tmp_path, "link") is None


def test_nested_dotdot_still_inside_ok(tmp_path: Path) -> None:
    (tmp_path / "a" / "b").mkdir(parents=True)
    (tmp_path / "a" / "f.mkv").touch()
    assert (
        resolve_within(tmp_path, "a/b/../f.mkv") == (tmp_path / "a" / "f.mkv").resolve()
    )
