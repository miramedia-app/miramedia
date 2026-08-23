"""Regression: season-wide disk scans run once per season directory."""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import patch

from miramedia.shows.schemas import (
    Episode,
    EpisodeFile,
    EpisodeId,
    EpisodeNumber,
    Season,
    SeasonId,
    SeasonNumber,
    Show,
    ShowId,
)
from miramedia.torrents.schemas import Quality
from tests.fakes import build_show_service, run_async
from tests.fakes.repositories import FakeShowRepository


def _season_with_episodes(
    *,
    episode_count: int = 3,
    on_disk_episode: int = 1,
) -> tuple[Show, Season]:
    show_id = ShowId(uuid.uuid4())
    season_id = SeasonId(uuid.uuid4())
    episodes: list[Episode] = []
    for number in range(1, episode_count + 1):
        episode_id = EpisodeId(uuid.uuid4())
        episodes.append(
            Episode(
                id=episode_id,
                number=EpisodeNumber(number),
                title=f"Episode {number}",
                episode_files=[
                    EpisodeFile(
                        episode_id=episode_id,
                        quality=Quality.fullhd,
                        torrent_id=None,
                    )
                ],
            )
        )
    season = Season(
        id=season_id,
        show_id=show_id,
        number=SeasonNumber(1),
        episodes=episodes,
    )
    show = Show(
        id=show_id,
        name="Test Show",
        overview="",
        year=2020,
        external_id="ext-1",
        metadata_provider="native",
        seasons=[season],
    )
    return show, season, on_disk_episode


def test_is_season_downloaded_scans_once_for_three_episode_season(
    tmp_path: Path,
) -> None:
    show, season, on_disk_episode = _season_with_episodes()
    repo = FakeShowRepository()
    repo.add_show(show)
    svc, _, _ = build_show_service(show_repo=repo)

    season_dir = tmp_path / "season"
    season_dir.mkdir()
    (season_dir / f"Test.Show.S01E0{on_disk_episode}.1080p.mkv").write_bytes(b"x")

    scan_calls = 0

    def counting_scan(season_path: Path) -> set[str]:
        nonlocal scan_calls
        scan_calls += 1
        return {path.name.lower() for path in season_path.iterdir() if path.is_file()}

    with (
        patch.object(svc, "get_root_season_directory", return_value=season_dir),
        patch.object(svc, "_scan_season_video_files", side_effect=counting_scan),
    ):
        downloaded = run_async(svc.is_season_downloaded(season=season, show=show))

    assert scan_calls == 1
    assert downloaded is False


def test_set_season_skipped_scans_once_and_preserves_downloaded_episode(
    tmp_path: Path,
) -> None:
    show, season, on_disk_episode = _season_with_episodes()
    repo = FakeShowRepository()
    repo.add_show(show)
    svc, _, _ = build_show_service(show_repo=repo)

    season_dir = tmp_path / "season"
    season_dir.mkdir()
    (season_dir / f"Test.Show.S01E0{on_disk_episode}.1080p.mkv").write_bytes(b"x")

    scan_calls = 0

    def counting_scan(season_path: Path) -> set[str]:
        nonlocal scan_calls
        scan_calls += 1
        return {path.name.lower() for path in season_path.iterdir() if path.is_file()}

    with (
        patch.object(svc, "get_root_season_directory", return_value=season_dir),
        patch.object(svc, "_scan_season_video_files", side_effect=counting_scan),
    ):
        run_async(svc.set_season_skipped(season_id=season.id, skipped=True))

    assert scan_calls == 1
    assert repo.seasons[season.id].skipped is True
    assert repo.episodes[season.episodes[0].id].skipped is False
    assert repo.episodes[season.episodes[1].id].skipped is True
    assert repo.episodes[season.episodes[2].id].skipped is True


def test_set_season_skipped_empty_season_does_not_crash(tmp_path: Path) -> None:
    show_id = ShowId(uuid.uuid4())
    season_id = SeasonId(uuid.uuid4())
    season = Season(
        id=season_id,
        show_id=show_id,
        number=SeasonNumber(1),
        episodes=[],
    )
    show = Show(
        id=show_id,
        name="Empty Season Show",
        overview="",
        year=2020,
        external_id="ext-2",
        metadata_provider="native",
        seasons=[season],
    )
    repo = FakeShowRepository()
    repo.add_show(show)
    svc, _, _ = build_show_service(show_repo=repo)

    with patch.object(svc, "get_root_season_directory", return_value=tmp_path):
        run_async(svc.set_season_skipped(season_id=season.id, skipped=True))

    assert repo.seasons[season.id].skipped is True
