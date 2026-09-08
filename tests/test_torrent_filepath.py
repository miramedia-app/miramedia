"""Tests for fuzzy on-disk directory resolution in get_torrent_filepath.

Regression guard: the fuzzy word-overlap matcher used to resolve a torrent onto
a same-franchise / different-season directory because every title word
overlapped and only the season marker or year differed. ``_dir_discriminators``
now blocks those cross-identity matches.
"""

from types import SimpleNamespace

import pytest

from miramedia.torrents import paths
from miramedia.torrents.paths import _dir_discriminators, get_torrent_filepath


@pytest.fixture
def completed(tmp_path, monkeypatch):
    monkeypatch.setattr(
        paths,
        "MiraMediaConfig",
        lambda: SimpleNamespace(
            misc=SimpleNamespace(
                effective_completed_path=tmp_path,
                incomplete_torrent_path="",
            )
        ),
    )
    return tmp_path


def _torrent(title: str):
    return SimpleNamespace(title=title)


def test_discriminators_extracts_season_and_year():
    assert _dir_discriminators("The.Amazing.Digital.Circus.S01E04.1080p") == {"s01"}
    assert _dir_discriminators("The.Amazing.Digital.Circus.The.Last.Act.2026") == {
        "2026"
    }
    assert _dir_discriminators("Greys.Anatomy.S09.1080p.WEBRip.x265-RARBG") == {"s09"}
    assert _dir_discriminators("Plain.Title.No.Markers") == set()


def test_show_episode_does_not_resolve_to_same_franchise_movie(completed):
    """A show's S01E04 torrent must not fuzzy-match the franchise movie dir."""
    movie_dir = completed / (
        "The.Amazing.Digital.Circus.The.Last.Act.2026.1080p.WORKPRiNT.WEB-DL.x264-DK"
    )
    movie_dir.mkdir()
    torrent = _torrent("The.Amazing.Digital.Circus.S01E04.1080p.HEVC.x265-MeGusta")

    resolved = get_torrent_filepath(torrent)

    assert resolved != movie_dir
    assert resolved == completed / torrent.title  # deterministic (non-existent)


def test_season_pack_does_not_resolve_to_different_season(completed):
    """Greys S06 must not fuzzy-match an on-disk S09 pack (only season differs)."""
    (completed / "Greys.Anatomy.S09.1080p.WEBRip.x265-RARBG").mkdir()
    torrent = _torrent("Greys.Anatomy.S06.1080p.WEBRip.x265-RARBG")

    resolved = get_torrent_filepath(torrent)

    assert resolved == completed / torrent.title


def test_matching_season_still_resolves_when_named_differently(completed):
    """Positive path: same season, scene-renamed dir still fuzzy-matches."""
    on_disk = completed / "Greys Anatomy S06 1080p BluRay"
    on_disk.mkdir()
    torrent = _torrent("Greys.Anatomy.S06.1080p.WEBRip.x265-RARBG")

    assert get_torrent_filepath(torrent) == on_disk


@pytest.fixture
def split_roots(tmp_path, monkeypatch):
    """Completed + incomplete roots, as configured in a split-path setup."""
    completed = tmp_path / "completed"
    incomplete = tmp_path / "incomplete"
    completed.mkdir()
    incomplete.mkdir()
    monkeypatch.setattr(
        paths,
        "MiraMediaConfig",
        lambda: SimpleNamespace(
            misc=SimpleNamespace(
                effective_completed_path=completed,
                incomplete_torrent_path=str(incomplete),
            )
        ),
    )
    return completed, incomplete


def _populate(directory, name: str = "movie.mkv"):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_bytes(b"x")
    return directory


def test_payload_in_incomplete_is_never_imported(split_roots):
    """Only completed downloads are importable; incomplete is off limits."""
    completed, incomplete = split_roots
    title = "Turbo A Power Rangers Movie 1997 1080p BluRay HEVC x265 5 1 BONE"
    _populate(incomplete / title)

    # Deterministic completed path, even though it does not exist yet.
    assert get_torrent_filepath(_torrent(title)) == completed / title


def test_empty_leftover_dir_does_not_shadow_real_payload(split_roots):
    """A stale empty dir from a previous download must not win the fuzzy match."""
    completed, _incomplete = split_roots
    stale = completed / (
        "Turbo A Power Rangers Movie 1997 1080p BluRay HEVC x265 5.1 BONE"
    )
    stale.mkdir()
    title = "Turbo A Power Rangers Movie 1997 1080p BluRay HEVC x265 5 1 BONE"
    payload = _populate(completed / "Turbo.A.Power.Rangers.Movie.1997.1080p.BONE")

    assert get_torrent_filepath(_torrent(title)) == payload


def test_populated_dir_wins_over_empty_sibling(split_roots):
    completed, _incomplete = split_roots
    title = "Some Movie 2020 1080p BluRay x265"
    (completed / "Some.Movie.2020.1080p.WEB").mkdir()
    done = _populate(completed / title)

    assert get_torrent_filepath(_torrent(title)) == done


def test_empty_dir_still_resolved_when_nothing_populated(split_roots):
    """Fallback pass keeps the old behaviour for a genuinely empty download."""
    completed, _incomplete = split_roots
    title = "Some Movie 2020 1080p BluRay x265"
    empty = completed / title
    empty.mkdir()

    assert get_torrent_filepath(_torrent(title)) == empty
