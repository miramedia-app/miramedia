import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from miramedia.movies.schemas import MovieFile
from miramedia.movies.service import MovieService
from miramedia.shows.schemas import EpisodeFile
from miramedia.shows.service import ShowService
from miramedia.torrents.schemas import Quality

INFO_HASH = "a" * 40


def test_movie_file_can_retain_source_info_hash() -> None:
    row = MovieFile(
        movie_id=uuid4(), quality=Quality.fullhd, source_info_hash=INFO_HASH
    )

    assert row.source_info_hash == INFO_HASH


def test_episode_file_can_retain_source_info_hash() -> None:
    row = EpisodeFile(
        episode_id=uuid4(),
        quality=Quality.fullhd,
        torrent_id=None,
        source_info_hash=INFO_HASH,
    )

    assert row.source_info_hash == INFO_HASH


def test_deleting_movie_file_can_block_its_source_hash() -> None:
    file_id = uuid4()
    movie_id = uuid4()
    row = SimpleNamespace(
        id=file_id,
        movie_id=movie_id,
        torrent_id=None,
        source_info_hash=INFO_HASH,
        quality=Quality.fullhd,
    )
    repo = SimpleNamespace(
        db=object(),
        get_movie_file_by_id=AsyncMock(return_value=row),
        delete_movie_file=AsyncMock(),
    )
    torrent_repo = SimpleNamespace(add_blocked_hash=AsyncMock())
    torrent_service = SimpleNamespace(torrent_repository=torrent_repo)
    service = MovieService(repo, torrent_service, None, None)

    with patch("miramedia.media_state.refresh_media_state", new=AsyncMock()):
        asyncio.run(
            service.delete_movie_file(
                SimpleNamespace(id=movie_id),
                file_id,
                delete_from_disk=False,
                block_source=True,
            )
        )

    torrent_repo.add_blocked_hash.assert_awaited_once_with(
        INFO_HASH, reason="user_blocked"
    )
    repo.delete_movie_file.assert_awaited_once_with(file_id)


def test_deleting_episode_file_can_block_its_source_hash() -> None:
    file_id = uuid4()
    episode_id = uuid4()
    show_id = uuid4()
    row = SimpleNamespace(
        id=file_id,
        episode_id=episode_id,
        torrent_id=None,
        source_info_hash=INFO_HASH,
    )
    repo = SimpleNamespace(
        db=object(),
        get_episode_file_by_id=AsyncMock(return_value=row),
        get_episode=AsyncMock(return_value=SimpleNamespace(id=episode_id)),
        get_season_by_episode=AsyncMock(return_value=SimpleNamespace(show_id=show_id)),
        delete_episode_file=AsyncMock(),
    )
    torrent_repo = SimpleNamespace(add_blocked_hash=AsyncMock())
    torrent_service = SimpleNamespace(torrent_repository=torrent_repo)
    service = ShowService(repo, torrent_service, None, None)

    with patch("miramedia.media_state.refresh_media_state", new=AsyncMock()):
        asyncio.run(
            service.delete_episode_file(
                file_id,
                delete_from_disk=False,
                block_source=True,
            )
        )

    torrent_repo.add_blocked_hash.assert_awaited_once_with(
        INFO_HASH, reason="user_blocked"
    )
    repo.delete_episode_file.assert_awaited_once_with(file_id=file_id)


def test_deleting_a_missing_episode_file_is_idempotent() -> None:
    file_id = uuid4()
    repo = SimpleNamespace(
        db=object(),
        get_episode_file_by_id=AsyncMock(return_value=None),
        get_episode=AsyncMock(),
        get_season_by_episode=AsyncMock(),
        delete_episode_file=AsyncMock(),
    )
    service = ShowService(repo, SimpleNamespace(), None, None)

    asyncio.run(service.delete_episode_file(file_id, delete_from_disk=False))

    repo.delete_episode_file.assert_not_awaited()
    repo.get_episode.assert_not_awaited()


def test_deleting_a_missing_movie_file_is_idempotent() -> None:
    file_id = uuid4()
    movie_id = uuid4()
    repo = SimpleNamespace(
        db=object(),
        get_movie_file_by_id=AsyncMock(return_value=None),
        delete_movie_file=AsyncMock(),
    )
    service = MovieService(repo, SimpleNamespace(), None, None)

    asyncio.run(
        service.delete_movie_file(
            SimpleNamespace(id=movie_id),
            file_id,
            delete_from_disk=False,
        )
    )

    repo.delete_movie_file.assert_not_awaited()
