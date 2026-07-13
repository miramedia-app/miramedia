"""Path resolution characterization for integrity batching."""

from __future__ import annotations

from pathlib import Path

from miramedia.imports.files import files_matching_stem
from miramedia.torrents.integrity import (
    resolve_episode_file_path_in_memory,
    resolve_movie_file_path_in_memory,
    scan_directory_for_stem_prefixes,
)
from miramedia.torrents.schemas import Quality
from tests.fakes.repositories import make_movie, make_show


def test_resolve_episode_path_finds_video(tmp_path: Path) -> None:
    show = make_show(name="Severance", season_number=1, episode_number=7)
    season_dir = tmp_path / "Season 01"
    season_dir.mkdir()
    video = season_dir / "Severance S01E07 - 1080p.mkv"
    video.write_bytes(b"x")

    class _Row:
        quality = Quality.fullhd
        codec = ""
        hdr = False
        source = ""
        variant = ""
        extra = ""

    path = resolve_episode_file_path_in_memory(
        show=show,
        season_number=1,
        episode_number=7,
        episode_file=_Row(),
        season_dir=season_dir,
    )
    assert path == video


def test_resolve_episode_path_missing_directory() -> None:
    show = make_show(name="Missing", season_number=1, episode_number=1)

    class _Row:
        quality = Quality.hd
        codec = ""
        hdr = False
        source = ""
        variant = ""
        extra = ""

    assert (
        resolve_episode_file_path_in_memory(
            show=show,
            season_number=1,
            episode_number=1,
            episode_file=_Row(),
            season_dir=Path("/no/such/season"),
        )
        is None
    )


def test_resolve_movie_path_finds_video(tmp_path: Path) -> None:
    movie = make_movie(name="Dune")
    movie_root = tmp_path / "Dune (2020)"
    movie_root.mkdir()
    video = movie_root / "Dune (2020) - 2160p.mkv"
    video.write_bytes(b"x")

    class _Row:
        quality = Quality.uhd
        codec = ""
        hdr = False
        source = ""
        variant = ""
        extra = ""

    path = resolve_movie_file_path_in_memory(
        movie=movie,
        movie_file=_Row(),
        movie_root=movie_root,
    )
    assert path == video


def test_resolve_movie_path_missing_file(tmp_path: Path) -> None:
    movie = make_movie(name="Empty")
    movie_root = tmp_path / "Empty"
    movie_root.mkdir()

    class _Row:
        quality = Quality.hd
        codec = ""
        hdr = False
        source = ""
        variant = ""
        extra = ""

    assert (
        resolve_movie_file_path_in_memory(
            movie=movie,
            movie_file=_Row(),
            movie_root=movie_root,
        )
        is None
    )


def test_scan_directory_matches_files_matching_stem_order(tmp_path: Path) -> None:
    season_dir = tmp_path / "Season 01"
    season_dir.mkdir()
    stems = ["StemOrder.S01E01.1080p", "StemOrder.S01E01.720p"]
    (season_dir / f"{stems[0]}.mkv").write_bytes(b"a")
    (season_dir / f"{stems[1]}.mp4").write_bytes(b"b")

    prefixes = frozenset(f"{stem}." for stem in stems)
    scanned = scan_directory_for_stem_prefixes(season_dir, prefixes)
    legacy = [p for stem in stems for p in files_matching_stem(season_dir, stem)]
    assert {p.name for p in scanned.values()} == {p.name for p in legacy}


def test_scan_directory_does_not_retain_unrelated_files(tmp_path: Path) -> None:
    season_dir = tmp_path / "Season 01"
    season_dir.mkdir()
    target = season_dir / "Target.S01E01.1080p.mkv"
    target.write_bytes(b"match")
    for i in range(1000):
        (season_dir / f"noise-{i:04d}.mkv").write_bytes(b"x")

    matches = scan_directory_for_stem_prefixes(
        season_dir, frozenset({"Target.S01E01.1080p."})
    )

    assert matches == {"Target.S01E01.1080p.": target}
    assert len(matches) == 1


def test_scan_directory_retains_one_candidate_per_prefix_with_variant_noise(
    tmp_path: Path,
) -> None:
    season_dir = tmp_path / "Season 01"
    season_dir.mkdir()
    prefix = "Noise.S01E01.1080p."
    variants = [
        f"{prefix}remux.mkv",
        f"{prefix}web-dl.mkv",
        f"{prefix}proper.mkv",
    ]
    for name in reversed(variants):
        (season_dir / name).write_bytes(b"x")
    for i in range(500):
        (season_dir / f"other-{i:04d}.mkv").write_bytes(b"x")

    matches = scan_directory_for_stem_prefixes(season_dir, frozenset({prefix}))

    assert len(matches) == 1
    assert matches[prefix].name == sorted(variants)[0]


def test_scan_directory_overlapping_prefixes_each_get_lexicographic_best(
    tmp_path: Path,
) -> None:
    season_dir = tmp_path / "Season 01"
    season_dir.mkdir()
    broad = "Shared."
    narrow = "Shared.StemA."
    (season_dir / "Shared.proper.mkv").write_bytes(b"a")
    (season_dir / "Shared.StemA.web-dl.mkv").write_bytes(b"b")
    (season_dir / "Shared.StemA.remux.mkv").write_bytes(b"c")

    matches = scan_directory_for_stem_prefixes(season_dir, frozenset({broad, narrow}))

    assert matches[broad].name == "Shared.StemA.remux.mkv"
    assert matches[narrow].name == "Shared.StemA.remux.mkv"


def test_scan_directory_does_not_materialize_directory_listing(
    monkeypatch,
    tmp_path: Path,
) -> None:
    season_dir = tmp_path / "Season 01"
    season_dir.mkdir()
    prefix = "Stream.S01E01.1080p."
    (season_dir / f"{prefix}target.mkv").write_bytes(b"x")

    class _StreamingEntries:
        def __init__(self, entries: list[Path]) -> None:
            self._entries = entries
            self.max_live = 0
            self._live = 0

        def __iter__(self):
            self._live = 0
            for entry in self._entries:
                self._live += 1
                self.max_live = max(self.max_live, self._live)
                yield entry
                self._live -= 1

    noise = [season_dir / f"noise-{i:04d}.mkv" for i in range(500)]
    for path in noise:
        path.write_bytes(b"n")
    target = season_dir / f"{prefix}target.mkv"
    entries = [*noise, target]

    streaming = _StreamingEntries(entries)
    original_iterdir = Path.iterdir

    def _patched_iterdir(self: Path):
        if self == season_dir:
            return iter(streaming)
        return original_iterdir(self)

    def _fail_sorted(*_args, **_kwargs):
        msg = "directory listing must not be sorted or materialized"
        raise AssertionError(msg)

    monkeypatch.setattr(Path, "iterdir", _patched_iterdir)
    monkeypatch.setattr("builtins.sorted", _fail_sorted)

    matches = scan_directory_for_stem_prefixes(season_dir, frozenset({prefix}))

    assert matches == {prefix: target}
    assert streaming.max_live == 1
