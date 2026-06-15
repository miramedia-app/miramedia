"""Characterization tests for miramedia/naming.py.

All expected values are frozen from observed runtime behaviour.

Stubs use ``types.SimpleNamespace`` with the exact attributes consumed by
``_media_context``:
  - ``name``               (template token: ``{title}``)
  - ``year``               (template token: ``{year}``)
  - ``external_id``        (template token: ``{provider_id}``)
  - ``metadata_provider``  (template token: ``{provider}``)
  - ``imdb_id``            (used by ``_id_tag`` → ``build_folder_id_tag``)

NOTE: ``_media_context`` reads ``media.name``, NOT ``media.title``.
"""

import types

import pytest

from miramedia.naming import (
    episode_file_stem_candidates,
    extract_external_id_from_string,
    movie_file_stem_candidates,
    movie_folder_name,
    sanitize_path_component,
    season_folder_name,
    show_folder_name,
)
from miramedia.torrents.quality_naming import NameParts
from miramedia.torrents.schemas import Quality

# ---------------------------------------------------------------------------
# Shared stubs
# ---------------------------------------------------------------------------

_SHOW = types.SimpleNamespace(
    name="Breaking Bad",
    year=2008,
    external_id="tt0903747",
    metadata_provider="imdb",
    imdb_id="tt0903747",
)

_MOVIE = types.SimpleNamespace(
    name="The Dark Knight",
    year=2008,
    external_id="tt0468569",
    metadata_provider="imdb",
    imdb_id="tt0468569",
)

_MOVIE_TMDB = types.SimpleNamespace(
    name="Parasite",
    year=2019,
    external_id="496243",
    metadata_provider="tmdb",
    imdb_id=None,
)


# ---------------------------------------------------------------------------
# sanitize_path_component
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Title: Subtitle?", "Title Subtitle"),  # colon and ? stripped
        ("  a   b  ", "a b"),  # leading/trailing and interior spaces collapsed
        ("file...", "file"),  # trailing dots stripped
        ("", ""),  # empty string stays empty
        ("Hello/World", "HelloWorld"),  # slash stripped
        ("A<B>C", "ABC"),  # angle brackets stripped
    ],
)
def test_sanitize_path_component(value: str, expected: str) -> None:
    assert sanitize_path_component(value) == expected


# ---------------------------------------------------------------------------
# show_folder_name / movie_folder_name
# ---------------------------------------------------------------------------


def test_show_folder_name_imdb() -> None:
    assert show_folder_name(_SHOW) == "Breaking Bad (2008) [imdb-tt0903747]"


def test_movie_folder_name_imdb() -> None:
    assert movie_folder_name(_MOVIE) == "The Dark Knight (2008) [imdb-tt0468569]"


def test_movie_folder_name_tmdb_falls_back_to_provider_tag() -> None:
    assert movie_folder_name(_MOVIE_TMDB) == "Parasite (2019) [tmdbid-496243]"


def test_show_folder_name_contains_title() -> None:
    folder = show_folder_name(_SHOW)
    assert "Breaking Bad" in folder


def test_show_folder_name_contains_year() -> None:
    folder = show_folder_name(_SHOW)
    assert "2008" in folder


# ---------------------------------------------------------------------------
# season_folder_name
# ---------------------------------------------------------------------------


def test_season_folder_name_single_digit() -> None:
    assert season_folder_name(1) == "Season 1"


def test_season_folder_name_double_digit() -> None:
    assert season_folder_name(12) == "Season 12"


def test_season_folder_name_zero() -> None:
    # Season 0 holds specials → "Specials" folder (Plex/Jellyfin/Kodi standard)
    assert season_folder_name(0) == "Specials"


# ---------------------------------------------------------------------------
# extract_external_id_from_string
# ---------------------------------------------------------------------------


def test_extract_external_id_roundtrip_show_folder() -> None:
    folder = show_folder_name(_SHOW)
    provider, ext_id = extract_external_id_from_string(folder)
    assert provider == "imdb"
    assert ext_id == "tt0903747"


def test_extract_external_id_tmdb_tag() -> None:
    provider, ext_id = extract_external_id_from_string("Show (2020) [tmdbid-12345]")
    assert provider == "tmdb"
    assert ext_id == "12345"


def test_extract_external_id_tvdb_tag() -> None:
    provider, ext_id = extract_external_id_from_string("Show (2020) [tvdbid-67890]")
    assert provider == "tvdb"
    assert ext_id == "67890"


def test_extract_external_id_no_tag_returns_none_none() -> None:
    provider, ext_id = extract_external_id_from_string("Just A Show Name")
    assert provider is None
    assert ext_id is None


def test_extract_external_id_native_provider_with_imdb_id() -> None:
    provider, ext_id = extract_external_id_from_string("Show [nativeid-tt9876543]")
    assert provider == "native"
    assert ext_id == "tt9876543"


# ---------------------------------------------------------------------------
# episode_file_stem_candidates
# ---------------------------------------------------------------------------


def test_episode_file_stem_candidates_fullhd_no_parts() -> None:
    # Default template == default, so only one unique candidate
    candidates = episode_file_stem_candidates(
        _SHOW,
        season_number=1,
        episode_number=5,
        quality=Quality.fullhd,
        parts=NameParts(),
    )
    assert candidates == ["Breaking Bad S01E05 - 1080p"]


def test_episode_file_stem_candidates_uhd_with_codec() -> None:
    candidates = episode_file_stem_candidates(
        _SHOW,
        season_number=2,
        episode_number=10,
        quality=Quality.uhd,
        parts=NameParts(codec="h265"),
    )
    assert candidates == ["Breaking Bad S02E10 - 2160p [h265]"]


def test_episode_file_stem_candidates_zero_padded_season_episode() -> None:
    # S01E05 formatting (zero-padded two digits)
    candidates = episode_file_stem_candidates(
        _SHOW,
        season_number=1,
        episode_number=5,
        quality=Quality.unknown,
        parts=NameParts(),
    )
    assert candidates == ["Breaking Bad S01E05"]


def test_episode_file_stem_candidates_deduplicates() -> None:
    # When no custom template is set, custom == default → one entry
    candidates = episode_file_stem_candidates(
        _SHOW,
        season_number=1,
        episode_number=1,
        quality=Quality.fullhd,
        parts=NameParts(),
    )
    assert len(candidates) == 1


# ---------------------------------------------------------------------------
# movie_file_stem_candidates
# ---------------------------------------------------------------------------


def test_movie_file_stem_candidates_fullhd() -> None:
    candidates = movie_file_stem_candidates(_MOVIE, Quality.fullhd, NameParts())
    assert candidates == ["The Dark Knight (2008) - 1080p"]


def test_movie_file_stem_candidates_unknown_quality() -> None:
    # unknown quality → no suffix → deduplicates to single entry
    candidates = movie_file_stem_candidates(_MOVIE, Quality.unknown, NameParts())
    assert candidates == ["The Dark Knight (2008)"]


def test_movie_file_stem_candidates_deduplicates_when_no_custom_template() -> None:
    candidates = movie_file_stem_candidates(_MOVIE, Quality.hd, NameParts())
    assert len(candidates) == 1
