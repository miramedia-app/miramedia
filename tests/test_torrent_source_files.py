"""Unit tests for torrent source-file enumeration off the event loop."""

from __future__ import annotations

import uuid
from unittest.mock import patch

from miramedia.shows.schemas import (
    Episode,
    EpisodeId,
    EpisodeNumber,
    Season,
    SeasonId,
    SeasonNumber,
    Show,
    ShowId,
)
from tests.fakes.repositories import FakeTorrentRepository, make_torrent
from tests.fakes.services import build_torrent_service, run_async


def _show_with_episodes(*, episode_count: int = 2) -> tuple[Show, list[Episode]]:
    show_id = ShowId(uuid.uuid4())
    season_id = SeasonId(uuid.uuid4())
    episodes = [
        Episode(
            id=EpisodeId(uuid.uuid4()),
            number=EpisodeNumber(index + 1),
            title=f"Episode {index + 1}",
        )
        for index in range(episode_count)
    ]
    season = Season(
        id=season_id,
        show_id=show_id,
        number=SeasonNumber(1),
        episodes=episodes,
    )
    show = Show(
        id=show_id,
        name="Source File Show",
        overview="",
        year=2024,
        external_id="ext-source-files",
        metadata_provider="native",
        seasons=[season],
    )
    return show, episodes


def test_list_source_files_season_pack_suggestions(tmp_path) -> None:
    show, episodes = _show_with_episodes(episode_count=2)
    torrent_repo = FakeTorrentRepository()
    torrent_service, _ = build_torrent_service(torrent_repo=torrent_repo)
    torrent = make_torrent(title="Source.File.Show.S01")
    torrent_repo.torrents[torrent.id] = torrent
    torrent_repo.show_of_torrent[torrent.id] = show

    (tmp_path / "Show.S01E01.1080p.mkv").write_bytes(b"video-one")
    (tmp_path / "Show.S01E02.1080p.mkv").write_bytes(b"video-two")
    (tmp_path / "Show.S01E01.1080p.srt").write_text("subtitle", encoding="utf-8")
    (tmp_path / "readme.txt").write_text("junk", encoding="utf-8")

    with patch(
        "miramedia.torrents.paths.get_torrent_filepath",
        return_value=tmp_path,
    ):
        files = run_async(torrent_service.list_source_files(torrent))

    by_path = {entry.relative_path: entry for entry in files}

    ep1 = by_path["Show.S01E01.1080p.mkv"]
    assert ep1.is_video is True
    assert ep1.is_subtitle is False
    assert ep1.suggested_episode_id == episodes[0].id
    assert ep1.suggested_movie_id is None

    ep2 = by_path["Show.S01E02.1080p.mkv"]
    assert ep2.is_video is True
    assert ep2.suggested_episode_id == episodes[1].id

    subtitle = by_path["Show.S01E01.1080p.srt"]
    assert subtitle.is_video is False
    assert subtitle.is_subtitle is True
    assert subtitle.suggested_episode_id == episodes[0].id

    junk = by_path["readme.txt"]
    assert junk.is_video is False
    assert junk.is_subtitle is False
    assert junk.suggested_episode_id is None
    assert junk.suggested_movie_id is None
