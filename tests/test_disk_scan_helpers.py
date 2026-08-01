"""Unit tests for shared disk-scan and subtitle-link helpers."""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import patch

from miramedia.disk_scan import (
    invalidate_disk_scan_cache,
    scan_cache,
    scan_rows_for_files,
)
from miramedia.imports.files import link_subtitles
from miramedia.shows.service import invalidate_disk_scan_cache as show_invalidate
from miramedia.torrents.parsing import SubtitleInfo


def test_show_invalidate_clears_movie_cached_entry() -> None:
    cache_key = ("movie-root", frozenset({"stem-a"}))
    scan_cache[cache_key] = True
    assert scan_cache.get(cache_key) is True

    show_invalidate()

    assert scan_cache.get(cache_key) is None


def test_link_subtitles_skips_unmatched_files(tmp_path: Path) -> None:
    sub = tmp_path / "notes.txt"
    sub.write_text("not a subtitle")

    with patch("miramedia.imports.files.import_file") as import_file:
        link_subtitles(
            [sub],
            match=lambda _path: None,
            target_for=lambda _info, _n: tmp_path / "unused.srt",
        )

    import_file.assert_not_called()


def test_link_subtitles_skips_movie_style_missing_language(tmp_path: Path) -> None:
    from miramedia.torrents.parsing import parse_subtitle_filename

    matched = tmp_path / "Test.Movie.2020.en.srt"
    skipped = tmp_path / "Test.Movie.2020.forced.srt"
    matched.write_bytes(b"matched")
    skipped.write_bytes(b"forced")

    def _movie_match(path: Path) -> SubtitleInfo | None:
        sub_info = parse_subtitle_filename(path.name)
        if sub_info is None or sub_info.language is None:
            return None
        return sub_info

    linked: list[Path] = []

    def _target_for(sub_info: SubtitleInfo, n: int) -> Path:
        ordinal = "" if n == 1 else f".{n}"
        return tmp_path / f"video.{sub_info.language}{ordinal}.srt"

    with patch("miramedia.imports.files.import_file") as import_file:
        import_file.side_effect = lambda *, target_file, **_kwargs: linked.append(
            target_file
        )
        link_subtitles(
            [matched, skipped],
            match=_movie_match,
            target_for=_target_for,
        )

    assert linked == [tmp_path / "video.en.srt"]


def test_link_subtitles_disambiguates_same_language_collisions(tmp_path: Path) -> None:
    sub1 = tmp_path / "one.en.srt"
    sub2 = tmp_path / "two.en.srt"
    sub1.write_bytes(b"1")
    sub2.write_bytes(b"2")
    info = SubtitleInfo(
        language="en", container="srt", forced=False, sdh=False, cc=False
    )
    linked: list[Path] = []

    def _target_for(_info: SubtitleInfo, n: int) -> Path:
        ordinal = "" if n == 1 else f".{n}"
        return tmp_path / f"video.en{ordinal}.srt"

    with patch("miramedia.imports.files.import_file") as import_file:
        import_file.side_effect = lambda *, target_file, **_kwargs: linked.append(
            target_file
        )
        link_subtitles(
            [sub1, sub2],
            match=lambda _path: info,
            target_for=_target_for,
        )

    assert linked == [tmp_path / "video.en.srt", tmp_path / "video.en.2.srt"]


def test_link_subtitles_disambiguates_three_same_language_collisions(
    tmp_path: Path,
) -> None:
    subs = [tmp_path / f"{idx}.en.srt" for idx in ("one", "two", "three")]
    for idx, sub in enumerate(subs, start=1):
        sub.write_bytes(str(idx).encode())
    info = SubtitleInfo(
        language="en", container="srt", forced=False, sdh=False, cc=False
    )
    linked: list[Path] = []

    def _target_for(_info: SubtitleInfo, n: int) -> Path:
        ordinal = "" if n == 1 else f".{n}"
        return tmp_path / f"video.en{ordinal}.srt"

    with patch("miramedia.imports.files.import_file") as import_file:
        import_file.side_effect = lambda *, target_file, **_kwargs: linked.append(
            target_file
        )
        link_subtitles(
            subs,
            match=lambda _path: info,
            target_for=_target_for,
        )

    assert linked == [
        tmp_path / "video.en.srt",
        tmp_path / "video.en.2.srt",
        tmp_path / "video.en.3.srt",
    ]


def test_link_subtitles_show_style_empty_language_target(tmp_path: Path) -> None:
    sub = tmp_path / "Show.S01E01.forced.srt"
    sub.write_bytes(b"forced")
    info = SubtitleInfo(
        language=None, container="srt", forced=True, sdh=False, cc=False
    )
    linked: list[Path] = []

    def _target_for(sub_info: SubtitleInfo, n: int) -> Path:
        lang_part = f".{sub_info.language}" if sub_info.language else ""
        flag_part = ".forced" if sub_info.forced else (".sdh" if sub_info.sdh else "")
        ordinal = "" if n == 1 else f".{n}"
        return tmp_path / f"video{lang_part}{flag_part}{ordinal}.srt"

    with patch("miramedia.imports.files.import_file") as import_file:
        import_file.side_effect = lambda *, target_file, **_kwargs: linked.append(
            target_file
        )
        link_subtitles(
            [sub],
            match=lambda _path: info,
            target_for=_target_for,
        )

    assert linked == [tmp_path / "video.forced.srt"]


def test_scan_rows_for_files_matches_prefix_and_extension(tmp_path: Path) -> None:
    directory = tmp_path / "season"
    directory.mkdir()
    (directory / "Show S01E01 - 1080p.mkv").write_bytes(b"v")
    (directory / "Show S01E01 - 1080p.nfo").write_bytes(b"n")

    rows = [{"id": uuid.uuid4(), "stem": "Show S01E01 - 1080p"}]
    found = scan_rows_for_files(
        directory,
        rows,
        key=lambda row: row["id"],
        stems=lambda row: [row["stem"]],
        video_exts=frozenset({".mkv"}),
    )

    assert found[rows[0]["id"]] == "Show S01E01 - 1080p.mkv"


def test_scan_rows_for_files_first_hit_wins_deterministically(
    tmp_path: Path, monkeypatch
) -> None:
    directory = tmp_path / "season"
    directory.mkdir()
    first = directory / "Show S01E01 - 1080p.a.mkv"
    second = directory / "Show S01E01 - 1080p.b.mkv"
    first.write_bytes(b"a")
    second.write_bytes(b"b")
    real_iterdir = Path.iterdir

    def _iterdir(self: Path):
        if self == directory:
            return iter([first, second])
        return real_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", _iterdir)

    rows = [{"id": "row-1", "stem": "Show S01E01 - 1080p"}]
    found = scan_rows_for_files(
        directory,
        rows,
        key=lambda row: row["id"],
        stems=lambda row: [row["stem"]],
        video_exts=frozenset({".mkv"}),
    )

    assert found[rows[0]["id"]] == "Show S01E01 - 1080p.a.mkv"


def test_scan_rows_for_files_oserror_on_iterdir_returns_empty(
    tmp_path: Path, monkeypatch
) -> None:
    directory = tmp_path / "season"
    directory.mkdir()
    (directory / "Show S01E01 - 1080p.mkv").write_bytes(b"v")
    rows = [{"id": "row-1", "stem": "Show S01E01 - 1080p"}]
    real_iterdir = Path.iterdir

    def _iterdir(self: Path):
        if self == directory:
            msg = "permission denied"
            raise OSError(msg)
        return real_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", _iterdir)
    found = scan_rows_for_files(
        directory,
        rows,
        key=lambda row: row["id"],
        stems=lambda row: [row["stem"]],
        video_exts=frozenset({".mkv"}),
    )

    assert found == {}


def test_scan_rows_for_files_missing_directory_returns_empty(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    rows = [{"id": "row-1", "stem": "anything"}]

    found = scan_rows_for_files(
        missing,
        rows,
        key=lambda row: row["id"],
        stems=lambda row: [row["stem"]],
        video_exts=frozenset({".mkv"}),
    )

    assert found == {}


def test_invalidate_disk_scan_cache_clears_shared_cache() -> None:
    scan_cache["shared-key"] = {"name": "file.mkv"}
    invalidate_disk_scan_cache()
    assert scan_cache.get("shared-key") is None
