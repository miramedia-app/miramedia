"""Unit tests for Sonarr shim JSON schema builders."""

from __future__ import annotations

import uuid
from datetime import date

from miramedia.file_status import ImportOutcome
from miramedia.shows.models import Episode, EpisodeFile, Season, Show
from miramedia.subtitles.arr_shim import sonarr_schemas
from miramedia.torrents.schemas import Quality


def _show(**overrides: object) -> Show:
    show = Show(
        id=uuid.uuid4(),
        external_id="12345",
        metadata_provider="tvdb",
        name="Test Show",
        overview="Overview",
        year=2020,
        ended=True,
        imdb_id="tt1234567",
        original_language="en",
        skipped=False,
    )
    for key, value in overrides.items():
        setattr(show, key, value)
    return show


def _episode(**overrides: object) -> Episode:
    episode = Episode(
        id=uuid.uuid4(),
        season_id=uuid.uuid4(),
        number=1,
        title="Pilot",
        skipped=False,
        air_date=date(2020, 1, 1),
    )
    for key, value in overrides.items():
        setattr(episode, key, value)
    return episode


def test_quality_to_sonarr_maps_all_values() -> None:
    assert sonarr_schemas.quality_to_sonarr(Quality.uhd) == ("WEBDL-2160p", 2160)
    assert sonarr_schemas.quality_to_sonarr(Quality.fullhd) == ("WEBDL-1080p", 1080)
    assert sonarr_schemas.quality_to_sonarr(Quality.hd) == ("WEBDL-720p", 720)
    assert sonarr_schemas.quality_to_sonarr(Quality.sd) == ("SDTV", 480)
    assert sonarr_schemas.quality_to_sonarr(Quality.unknown) == ("Unknown", 0)


def test_monitored_inverts_skipped() -> None:
    show = _show(skipped=True)
    payload = sonarr_schemas.series_json(show, arr_id=1, path="/shows/test")
    assert payload["monitored"] is False

    episode = Episode(
        id=uuid.uuid4(),
        season_id=uuid.uuid4(),
        number=1,
        title="Pilot",
        skipped=True,
    )
    payload = sonarr_schemas.episode_json(
        episode,
        arr_id=2,
        series_arr_id=1,
        season_number=1,
    )
    assert payload["monitored"] is False


def test_has_file_only_when_imported() -> None:
    imported = EpisodeFile(
        id=uuid.uuid4(),
        episode_id=uuid.uuid4(),
        quality=Quality.hd,
        import_status=ImportOutcome.imported,
    )
    pending = EpisodeFile(
        id=uuid.uuid4(),
        episode_id=uuid.uuid4(),
        quality=Quality.hd,
        import_status=ImportOutcome.pending,
    )
    episode = Episode(
        id=uuid.uuid4(),
        season_id=uuid.uuid4(),
        number=1,
        title="Pilot",
        episode_files=[pending],
    )
    payload = sonarr_schemas.episode_json(
        episode,
        arr_id=1,
        series_arr_id=1,
        season_number=1,
    )
    assert payload["hasFile"] is False
    assert "episodeFile" not in payload

    episode.episode_files = [pending, imported]
    payload = sonarr_schemas.episode_json(
        episode,
        arr_id=1,
        series_arr_id=1,
        season_number=1,
        include_episode_file=True,
        episode_file=imported,
        episode_file_arr_id=9,
        episode_file_path="/media/pilot.mkv",
        episode_file_size=50_000,
    )
    assert payload["hasFile"] is True
    assert payload["episodeFile"]["id"] == 9


def test_tvdb_id_provider_gating() -> None:
    show = _show(metadata_provider="tvdb", external_id="98765")
    assert sonarr_schemas.show_tvdb_id(show) == 98765

    show = _show(metadata_provider="tmdb", external_id="98765")
    assert sonarr_schemas.show_tvdb_id(show) == 0


def test_last_aired_from_episode_air_dates() -> None:
    show = _show()
    season = Season(id=uuid.uuid4(), show_id=show.id, number=1)
    season.episodes = [
        Episode(
            id=uuid.uuid4(),
            season_id=season.id,
            number=1,
            title="A",
            air_date=date(2020, 1, 1),
        ),
        Episode(
            id=uuid.uuid4(),
            season_id=season.id,
            number=2,
            title="B",
            air_date=date(2021, 6, 15),
        ),
    ]
    show.seasons = [season]
    payload = sonarr_schemas.series_json(show, arr_id=1, path="/shows/test")
    assert payload["lastAired"] == "2021-06-15"


def test_language_name_fallback() -> None:
    assert sonarr_schemas.language_name_from_code("en") == "English"
    assert sonarr_schemas.language_name_from_code("ja") == "Japanese"
    assert sonarr_schemas.language_name_from_code("xx") == "English"
    assert sonarr_schemas.language_name_from_code(None) == "English"
    assert sonarr_schemas.language_name_from_code("English") == "English"


def test_series_json_contains_parser_keys() -> None:
    show = _show()
    show.seasons = []
    payload = sonarr_schemas.series_json(show, arr_id=1, path="/shows/test")
    assert set(payload) == sonarr_schemas.SERIES_PARSER_KEYS


def test_episode_file_json_contains_parser_keys() -> None:
    episode_file = EpisodeFile(
        id=uuid.uuid4(),
        episode_id=uuid.uuid4(),
        quality=Quality.fullhd,
        codec="h264",
        import_status=ImportOutcome.imported,
    )
    payload = sonarr_schemas.episode_file_json(
        episode_file,
        arr_id=3,
        path="/shows/test/S01E01.mkv",
        size=123456,
    )
    assert set(payload) == sonarr_schemas.EPISODE_FILE_PARSER_KEYS
    assert payload["quality"]["quality"]["name"] == "WEBDL-1080p"
    assert payload["mediaInfo"]["videoCodec"] == "h264"


def test_episode_json_has_file_when_arr_id_path_and_positive_size() -> None:
    episode = _episode()
    payload = sonarr_schemas.episode_json(
        episode,
        arr_id=1,
        series_arr_id=1,
        season_number=1,
        episode_file_arr_id=9,
        episode_file_path="/media/pilot.mkv",
        episode_file_size=50_000,
    )
    assert payload["hasFile"] is True
    assert payload["episodeFileId"] == 9


def test_episode_json_no_has_file_when_size_zero() -> None:
    episode = _episode()
    payload = sonarr_schemas.episode_json(
        episode,
        arr_id=1,
        series_arr_id=1,
        season_number=1,
        episode_file_arr_id=9,
        episode_file_path="/media/pilot.mkv",
        episode_file_size=0,
    )
    assert payload["hasFile"] is False
    assert payload["episodeFileId"] == 0


def test_episode_json_no_has_file_without_arr_id() -> None:
    episode = _episode()
    payload = sonarr_schemas.episode_json(
        episode,
        arr_id=1,
        series_arr_id=1,
        season_number=1,
        episode_file_path="/media/pilot.mkv",
        episode_file_size=50_000,
    )
    assert payload["hasFile"] is False
    assert payload["episodeFileId"] == 0


def test_episode_json_no_has_file_without_path() -> None:
    episode = _episode()
    payload = sonarr_schemas.episode_json(
        episode,
        arr_id=1,
        series_arr_id=1,
        season_number=1,
        episode_file_arr_id=9,
        episode_file_size=50_000,
    )
    assert payload["hasFile"] is False
    assert payload["episodeFileId"] == 0
