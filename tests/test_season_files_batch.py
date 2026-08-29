"""Batch season-files endpoint and service characterization tests."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from miramedia.exceptions import NotFoundError
from miramedia.file_status import ImportOutcome
from miramedia.shows.schemas import (
    Episode,
    EpisodeFile,
    EpisodeId,
    EpisodeNumber,
    PublicEpisodeFile,
    Season,
    SeasonId,
    SeasonNumber,
    Show,
    ShowId,
)
from miramedia.shows.service import ShowService
from miramedia.torrents.schemas import Quality, TorrentId
from tests.fakes import build_show_service, run_async
from tests.fakes.repositories import FakeShowRepository, make_show


def _show_with_seasons(season_numbers: list[int]) -> Show:
    show_id = ShowId(uuid.uuid4())
    seasons: list[Season] = []
    for number in season_numbers:
        episode_id = EpisodeId(uuid.uuid4())
        seasons.append(
            Season(
                id=SeasonId(uuid.uuid4()),
                show_id=show_id,
                number=SeasonNumber(number),
                episodes=[
                    Episode(
                        id=episode_id,
                        number=EpisodeNumber(1),
                        title=f"S{number:02d}E01",
                    )
                ],
            )
        )
    return Show(
        id=show_id,
        name="Batch Show",
        overview="",
        year=2020,
        external_id="ext-batch",
        metadata_provider="native",
        seasons=seasons,
    )


def _add_episode_file(
    repo: FakeShowRepository,
    *,
    episode_id: EpisodeId,
    torrent_id: TorrentId | None = None,
) -> EpisodeFile:
    episode_file = EpisodeFile(
        id=uuid.uuid4(),
        episode_id=episode_id,
        quality=Quality.fullhd,
        torrent_id=torrent_id,
        import_status=ImportOutcome.pending,
    )
    repo.episode_files[episode_file.id] = episode_file
    return episode_file


@pytest.mark.anyio
async def test_single_season_files_match_batch_characterization() -> None:
    show = _show_with_seasons([1])
    season = show.seasons[0]
    episode = season.episodes[0]
    repo = FakeShowRepository()
    repo.add_show(show)
    file_row = _add_episode_file(repo, episode_id=episode.id)

    svc, _, _ = build_show_service(show_repo=repo)
    with (
        patch(
            "miramedia.database.release_session_before_external_io",
            new_callable=AsyncMock,
        ),
        patch(
            "miramedia.shows.service.scan_rows_for_files",
            return_value={file_row.id: "episode.mkv"},
        ),
    ):
        single = await svc.get_public_episode_files_by_season_id(season)
        batch_results, batch_errors = await svc.get_public_episode_files_by_season_ids(
            [season.id]
        )

    assert batch_errors == {}
    assert len(single) == 1
    assert single[0].id == file_row.id
    assert single[0].file_name == "episode.mkv"
    assert batch_results[season.id][0].file_name == "episode.mkv"


@pytest.mark.anyio
async def test_batch_uses_set_oriented_repository_calls() -> None:
    show = _show_with_seasons([1, 2, 3])
    repo = FakeShowRepository()
    repo.add_show(show)
    for season in show.seasons:
        _add_episode_file(repo, episode_id=season.episodes[0].id)

    svc, _, _ = build_show_service(show_repo=repo)
    season_ids = [season.id for season in show.seasons]

    with (
        patch(
            "miramedia.database.release_session_before_external_io",
            new_callable=AsyncMock,
        ),
        patch("miramedia.shows.service.scan_rows_for_files", return_value={}),
    ):
        await svc.get_public_episode_files_by_season_ids(season_ids)

    assert repo.get_seasons_by_ids_calls == 1
    assert repo.last_season_ids_batch == season_ids
    assert repo.get_episode_files_by_season_ids_calls == 1
    assert repo.get_shows_by_ids_calls == 1


@pytest.mark.anyio
async def test_batch_deduplicates_season_ids() -> None:
    show = _show_with_seasons([1])
    season = show.seasons[0]
    repo = FakeShowRepository()
    repo.add_show(show)
    _add_episode_file(repo, episode_id=season.episodes[0].id)

    svc, _, _ = build_show_service(show_repo=repo)
    with (
        patch(
            "miramedia.database.release_session_before_external_io",
            new_callable=AsyncMock,
        ),
        patch("miramedia.shows.service.scan_rows_for_files", return_value={}),
    ):
        results, errors = await svc.get_public_episode_files_by_season_ids(
            [season.id, season.id]
        )

    assert errors == {}
    assert list(results.keys()) == [season.id]
    assert repo.last_season_ids_batch == [season.id]


@pytest.mark.anyio
async def test_batch_missing_season_returns_partial_error() -> None:
    show = _show_with_seasons([1])
    season = show.seasons[0]
    missing_id = SeasonId(uuid.uuid4())
    repo = FakeShowRepository()
    repo.add_show(show)

    svc, _, _ = build_show_service(show_repo=repo)
    with (
        patch(
            "miramedia.database.release_session_before_external_io",
            new_callable=AsyncMock,
        ),
        patch("miramedia.shows.service.scan_rows_for_files", return_value={}),
    ):
        results, errors = await svc.get_public_episode_files_by_season_ids(
            [season.id, missing_id]
        )

    assert season.id in results
    assert missing_id in errors
    assert "not found" in errors[missing_id].lower()


@pytest.mark.anyio
async def test_batch_show_id_mismatch_is_error() -> None:
    show = _show_with_seasons([1])
    other_show = make_show(season_number=2)
    repo = FakeShowRepository()
    repo.add_show(show)
    repo.add_show(other_show)
    other_season = other_show.seasons[0]

    svc, _, _ = build_show_service(show_repo=repo)
    with (
        patch(
            "miramedia.database.release_session_before_external_io",
            new_callable=AsyncMock,
        ),
        patch("miramedia.shows.service.scan_rows_for_files", return_value={}),
    ):
        results, errors = await svc.get_public_episode_files_by_season_ids(
            [other_season.id],
            show_id=show.id,
        )

    assert results == {}
    assert other_season.id in errors
    assert "does not belong" in errors[other_season.id]


@pytest.mark.anyio
async def test_batch_releases_session_before_filesystem_scan() -> None:
    show = _show_with_seasons([1])
    season = show.seasons[0]
    repo = FakeShowRepository()
    repo.add_show(show)
    _add_episode_file(repo, episode_id=season.episodes[0].id)

    svc, _, _ = build_show_service(show_repo=repo)
    release = AsyncMock()
    scan = patch("miramedia.shows.service.scan_rows_for_files", return_value={})

    with (
        patch(
            "miramedia.database.release_session_before_external_io",
            release,
        ),
        scan,
    ):
        await svc.get_public_episode_files_by_season_ids([season.id])

    release.assert_awaited_once()
    assert release.await_args is not None


def test_single_season_not_found_raises() -> None:
    show = _show_with_seasons([1])
    season = show.seasons[0]
    repo = FakeShowRepository()
    svc, _, _ = build_show_service(show_repo=repo)

    with pytest.raises(NotFoundError):
        run_async(svc.get_public_episode_files_by_season_id(season))


def test_enrich_public_episode_files_maps_disk_and_import_state() -> None:
    torrent_id = TorrentId(uuid.uuid4())
    file_id = uuid.uuid4()
    episode_file = PublicEpisodeFile(
        id=file_id,
        episode_id=EpisodeId(uuid.uuid4()),
        quality=Quality.fullhd,
        torrent_id=torrent_id,
        import_status=ImportOutcome.pending,
    )
    enriched = ShowService._enrich_public_episode_files(
        public_episode_files=[episode_file],
        imported_by_torrent={torrent_id: True},
        disk_names={file_id: "on-disk.mkv"},
    )
    assert enriched[0].downloaded is True
    assert enriched[0].file_name == "on-disk.mkv"
    assert enriched[0].file_status.value == "imported"
