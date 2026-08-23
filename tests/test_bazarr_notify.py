"""Tests for Bazarr import-webhook notify service + import hooks."""

from __future__ import annotations

import uuid
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from miramedia.file_status import ImportOutcome
from miramedia.shows.schemas import EpisodeId
from miramedia.subtitles.config import BazarrConfig, SubtitleConfig
from miramedia.subtitles.service import SubtitleService
from miramedia.torrents.mediainfo import MediaFileInfo
from miramedia.torrents.schemas import Quality
from tests.fakes import build_show_service, run_async
from tests.fakes.config import fake_config
from tests.fakes.repositories import FakeShowRepository, make_show

_RELEASE_PATCH = "miramedia.database.release_session_before_external_io"


def _bazarr_enabled_config() -> MagicMock:
    cfg = fake_config(
        show_directory=Path("/shows"),
        movie_directory=Path("/movies"),
        completed_directory=Path("/completed"),
    )
    cfg.subtitles = SubtitleConfig(
        bazarr=BazarrConfig(
            enabled=True,
            url="http://bazarr:6767",
            api_key="bazarr-key",
            shim_api_key="shim-key",
        )
    )
    return cfg


def _subtitle_service(db: object | None = None) -> SubtitleService:
    repo = MagicMock()
    repo.db = db or MagicMock()
    return SubtitleService(subtitle_repository=repo)


@pytest.mark.anyio
async def test_notify_episode_disabled_makes_no_http() -> None:
    service = _subtitle_service()
    cfg = fake_config(
        show_directory=Path("/shows"),
        movie_directory=Path("/movies"),
        completed_directory=Path("/completed"),
    )
    cfg.subtitles = SubtitleConfig(bazarr=BazarrConfig(enabled=False))

    with (
        patch("miramedia.subtitles.service.MiraMediaConfig", return_value=cfg),
        patch(
            "miramedia.subtitles.service.BazarrClient",
        ) as client_cls,
        patch(
            "miramedia.subtitles.arr_ids.get_or_create_arr_ids",
            new_callable=AsyncMock,
        ) as resolve,
    ):
        await service.notify_bazarr_episode_imported(
            service.subtitle_repository.db,
            uuid.uuid4(),
            EpisodeId(uuid.uuid4()),
        )

    client_cls.assert_not_called()
    resolve.assert_not_awaited()


@pytest.mark.anyio
async def test_notify_episode_releases_session_before_http() -> None:
    service = _subtitle_service()
    episode_file_id = uuid.uuid4()
    episode_id = EpisodeId(uuid.uuid4())
    call_order: list[str] = []

    async def _release(_db: object) -> None:
        call_order.append("release")

    async def _resolve(
        _db: object, entity_type: str, _uuids: list[uuid.UUID]
    ) -> dict[uuid.UUID, int]:
        if entity_type == "episode_file":
            return {episode_file_id: 10}
        return {episode_id: 20}

    def _notify(*_args: object, **_kwargs: object) -> bool:
        call_order.append("http")
        return True

    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.notify_episode_files_imported.side_effect = _notify

    with (
        patch(
            "miramedia.subtitles.service.MiraMediaConfig",
            return_value=_bazarr_enabled_config(),
        ),
        patch(_RELEASE_PATCH, side_effect=_release),
        patch(
            "miramedia.subtitles.arr_ids.get_or_create_arr_ids",
            side_effect=_resolve,
        ),
        patch("miramedia.subtitles.service.BazarrClient", return_value=mock_client),
        patch(
            "miramedia.subtitles.service.asyncio.to_thread", new_callable=AsyncMock
        ) as to_thread,
    ):
        to_thread.side_effect = lambda fn, *args: fn(*args)
        await service.notify_bazarr_episode_imported(
            service.subtitle_repository.db, episode_file_id, episode_id
        )

    assert call_order == ["release", "http"]
    mock_client.notify_episode_files_imported.assert_called_once_with([10], [20])


@pytest.mark.anyio
async def test_notify_episode_http_failure_does_not_raise() -> None:
    service = _subtitle_service()
    episode_file_id = uuid.uuid4()
    episode_id = EpisodeId(uuid.uuid4())

    with (
        patch(
            "miramedia.subtitles.service.MiraMediaConfig",
            return_value=_bazarr_enabled_config(),
        ),
        patch(_RELEASE_PATCH, new_callable=AsyncMock),
        patch(
            "miramedia.subtitles.arr_ids.get_or_create_arr_ids",
            new_callable=AsyncMock,
            side_effect=[
                {episode_file_id: 1},
                {episode_id: 2},
            ],
        ),
        patch(
            "miramedia.subtitles.service.BazarrClient",
            return_value=MagicMock(
                __enter__=MagicMock(
                    return_value=MagicMock(
                        notify_episode_files_imported=MagicMock(return_value=False)
                    )
                ),
            ),
        ),
        patch(
            "miramedia.subtitles.service.asyncio.to_thread", new_callable=AsyncMock
        ) as to_thread,
    ):
        to_thread.side_effect = lambda fn, *args: fn(*args)
        await service.notify_bazarr_episode_imported(
            service.subtitle_repository.db, episode_file_id, episode_id
        )


@pytest.mark.anyio
async def test_notify_movie_resolves_arr_ids() -> None:
    from miramedia.movies.schemas import MovieId

    service = _subtitle_service()
    movie_file_id = uuid.uuid4()
    movie_id = MovieId(uuid.uuid4())
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.notify_movie_file_imported.return_value = True

    with (
        patch(
            "miramedia.subtitles.service.MiraMediaConfig",
            return_value=_bazarr_enabled_config(),
        ),
        patch(_RELEASE_PATCH, new_callable=AsyncMock),
        patch(
            "miramedia.subtitles.arr_ids.get_or_create_arr_ids",
            new_callable=AsyncMock,
            side_effect=[
                {movie_file_id: 55},
                {movie_id: 66},
            ],
        ),
        patch("miramedia.subtitles.service.BazarrClient", return_value=mock_client),
        patch(
            "miramedia.subtitles.service.asyncio.to_thread", new_callable=AsyncMock
        ) as to_thread,
    ):
        to_thread.side_effect = lambda fn, *args: fn(*args)
        await service.notify_bazarr_movie_imported(
            service.subtitle_repository.db, movie_file_id, movie_id
        )

    mock_client.notify_movie_file_imported.assert_called_once_with(55, 66)


class TestImportEpisodeBazarrHook:
    def test_import_episode_fires_bazarr_notify_after_commit(
        self, tmp_path: Path
    ) -> None:
        show = make_show(name="Test Show", year=2020)
        season = show.seasons[0]
        episode = season.episodes[0]
        repo = FakeShowRepository()
        repo.add_show(show)

        source = tmp_path / "incoming" / "Test.Show.S01E01.1080p.mkv"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"video-bytes")

        svc, _, _ = build_show_service(show_repo=repo)
        notify = AsyncMock()

        config_patches = [
            patch(
                target,
                return_value=fake_config(
                    show_directory=tmp_path / "shows",
                    movie_directory=tmp_path / "movies",
                    completed_directory=tmp_path / "completed",
                ),
            )
            for target in (
                "miramedia.config.MiraMediaConfig",
                "miramedia.naming.MiraMediaConfig",
                "miramedia.shows.service.MiraMediaConfig",
                "miramedia.movies.service.MiraMediaConfig",
                "miramedia.torrents.paths.MiraMediaConfig",
            )
        ]

        with ExitStack() as stack:
            for p in config_patches:
                stack.enter_context(p)
            stack.enter_context(
                patch(
                    "miramedia.database.release_session_before_external_io",
                    new_callable=AsyncMock,
                )
            )
            stack.enter_context(
                patch(
                    "miramedia.media_state.refresh_media_state", new_callable=AsyncMock
                )
            )
            stack.enter_context(
                patch(
                    "miramedia.shows.service.analyze_async",
                    new_callable=AsyncMock,
                    return_value=MediaFileInfo(
                        quality=Quality.fullhd, video_codec="h264"
                    ),
                )
            )
            stack.enter_context(
                patch("miramedia.shows.service.invalidate_disk_scan_cache")
            )
            stack.enter_context(
                patch.object(
                    svc, "_trigger_subtitle_search_for_episode", new_callable=AsyncMock
                )
            )
            stack.enter_context(
                patch.object(svc, "_trigger_bazarr_notify_for_episode", notify)
            )
            outcome, err = run_async(
                svc.import_episode_from_file(
                    show=show,
                    season=season,
                    episode=episode,
                    source_file=source,
                    torrent_id=None,
                )
            )

        assert outcome == ImportOutcome.imported
        assert err is None
        notify.assert_awaited_once()
        called_file_id, called_episode_id = notify.await_args.args
        assert called_episode_id == episode.id
        assert called_file_id in repo.episode_files


@pytest.mark.anyio
async def test_notify_episodes_batch_sends_one_webhook() -> None:
    """A season pack is one POST carrying every file/episode id, not one each."""
    service = _subtitle_service()
    pairs = [(uuid.uuid4(), EpisodeId(uuid.uuid4())) for _ in range(3)]
    file_ids = {file_uuid: 100 + i for i, (file_uuid, _) in enumerate(pairs)}
    episode_ids = {episode_uuid: 200 + i for i, (_, episode_uuid) in enumerate(pairs)}

    async def _resolve(
        _db: object, entity_type: str, uuids: list[uuid.UUID]
    ) -> dict[uuid.UUID, int]:
        source = file_ids if entity_type == "episode_file" else episode_ids
        return {u: source[u] for u in uuids}

    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.notify_episode_files_imported.return_value = True

    with (
        patch(
            "miramedia.subtitles.service.MiraMediaConfig",
            return_value=_bazarr_enabled_config(),
        ),
        patch(_RELEASE_PATCH, new_callable=AsyncMock),
        patch(
            "miramedia.subtitles.arr_ids.get_or_create_arr_ids",
            side_effect=_resolve,
        ),
        patch("miramedia.subtitles.service.BazarrClient", return_value=mock_client),
        patch(
            "miramedia.subtitles.service.asyncio.to_thread", new_callable=AsyncMock
        ) as to_thread,
    ):
        to_thread.side_effect = lambda fn, *args: fn(*args)
        await service.notify_bazarr_episodes_imported(
            service.subtitle_repository.db, pairs
        )

    mock_client.notify_episode_files_imported.assert_called_once_with(
        [100, 101, 102], [200, 201, 202]
    )


@pytest.mark.anyio
async def test_notify_episodes_empty_batch_makes_no_http() -> None:
    service = _subtitle_service()

    with (
        patch(
            "miramedia.subtitles.service.MiraMediaConfig",
            return_value=_bazarr_enabled_config(),
        ),
        patch("miramedia.subtitles.service.BazarrClient") as client_cls,
    ):
        await service.notify_bazarr_episodes_imported(
            service.subtitle_repository.db, []
        )

    client_cls.assert_not_called()


@pytest.mark.anyio
async def test_notify_episode_closes_bazarr_session() -> None:
    """BazarrClient must close its requests.Session after notify."""
    service = _subtitle_service()
    episode_file_id = uuid.uuid4()
    episode_id = EpisodeId(uuid.uuid4())
    session = MagicMock()
    session.close = MagicMock()

    with (
        patch(
            "miramedia.subtitles.service.MiraMediaConfig",
            return_value=_bazarr_enabled_config(),
        ),
        patch(_RELEASE_PATCH, new_callable=AsyncMock),
        patch(
            "miramedia.subtitles.arr_ids.get_or_create_arr_ids",
            new_callable=AsyncMock,
            side_effect=[
                {episode_file_id: 10},
                {episode_id: 20},
            ],
        ),
        patch(
            "miramedia.subtitles.bazarr_client.requests.Session", return_value=session
        ),
        patch(
            "miramedia.subtitles.service.asyncio.to_thread", new_callable=AsyncMock
        ) as to_thread,
    ):
        to_thread.side_effect = lambda fn, *args: fn(*args)
        await service.notify_bazarr_episode_imported(
            service.subtitle_repository.db, episode_file_id, episode_id
        )

    session.close.assert_called_once()
