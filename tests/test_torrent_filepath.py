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
            misc=SimpleNamespace(effective_completed_path=tmp_path)
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
