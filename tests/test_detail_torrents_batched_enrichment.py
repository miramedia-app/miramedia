"""Detail-page torrent bundles use batched enrichment, not per-torrent N+1."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from miramedia.file_status import ImportOutcome
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
from miramedia.torrents.schemas import Quality, TorrentId
from tests.fakes.repositories import (
    FakeShowRepository,
    FakeTorrentRepository,
    make_torrent,
)
from tests.fakes.services import build_show_service, run_async


def _episode(*, number: int) -> Episode:
    return Episode(
        id=EpisodeId(uuid.uuid4()),
        number=EpisodeNumber(number),
        title=f"E{number}",
    )


def _show_with_two_seasons() -> Show:
    show_id = ShowId(uuid.uuid4())
    season1_id = SeasonId(uuid.uuid4())
    season2_id = SeasonId(uuid.uuid4())
    s1e1 = _episode(number=1)
    s1e2 = _episode(number=2)
    s2e1 = _episode(number=1)
    season1 = Season(
        id=season1_id,
        show_id=show_id,
        number=SeasonNumber(1),
        episodes=[s1e1, s1e2],
    )
    season2 = Season(
        id=season2_id,
        show_id=show_id,
        number=SeasonNumber(2),
        episodes=[s2e1],
    )
    return Show(
        id=show_id,
        name="Batch Show",
        overview="",
        year=2024,
        external_id="ext-batch",
        metadata_provider="native",
        seasons=[season1, season2],
    )


def _episode_file(
    *,
    episode_id: EpisodeId,
    torrent_id: TorrentId,
    variant: str,
    import_status: ImportOutcome = ImportOutcome.imported,
) -> EpisodeFile:
    return EpisodeFile(
        episode_id=episode_id,
        torrent_id=torrent_id,
        quality=Quality.fullhd,
        variant=variant,
        import_status=import_status,
        last_attempt_at=datetime(2024, 6, 1, tzinfo=UTC),
    )


def test_get_torrents_for_show_batches_context_and_gates_episodes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    show = _show_with_two_seasons()
    season1, season2 = show.seasons
    s1e1, s1e2 = season1.episodes
    s2e1 = season2.episodes[0]

    t_single = make_torrent(title="Show.S01E01")
    t_multi = make_torrent(title="Show.S01-S02")
    t_single_multi_ep = make_torrent(title="Show.S01E01-E02")

    show_repo = FakeShowRepository()
    show_repo.add_show(show)
    show_repo.torrents_by_show[show.id] = [t_single, t_multi, t_single_multi_ep]

    torrent_repo = FakeTorrentRepository(show_repo=show_repo)
    torrent_repo.torrents[t_single.id] = t_single
    torrent_repo.torrents[t_multi.id] = t_multi
    torrent_repo.torrents[t_single_multi_ep.id] = t_single_multi_ep
    torrent_repo.show_of_torrent[t_single.id] = show
    torrent_repo.show_of_torrent[t_multi.id] = show
    torrent_repo.show_of_torrent[t_single_multi_ep.id] = show
    torrent_repo.episode_files[t_single.id] = [
        _episode_file(
            episode_id=s1e1.id,
            torrent_id=t_single.id,
            variant="web-dl",
            import_status=ImportOutcome.imported,
        )
    ]
    torrent_repo.episode_files[t_multi.id] = [
        _episode_file(
            episode_id=s1e1.id,
            torrent_id=t_multi.id,
            variant="bluray",
            import_status=ImportOutcome.pending,
        ),
        _episode_file(
            episode_id=s2e1.id,
            torrent_id=t_multi.id,
            variant="bluray",
            import_status=ImportOutcome.pending,
        ),
    ]
    torrent_repo.episode_files[t_single_multi_ep.id] = [
        _episode_file(
            episode_id=s1e1.id,
            torrent_id=t_single_multi_ep.id,
            variant="hdtv",
            import_status=ImportOutcome.imported,
        ),
        _episode_file(
            episode_id=s1e2.id,
            torrent_id=t_single_multi_ep.id,
            variant="hdtv",
            import_status=ImportOutcome.imported,
        ),
    ]

    show_repo.get_seasons_by_torrent_id = AsyncMock()
    show_repo.get_episodes_by_torrent_id = AsyncMock()

    svc, _, _ = build_show_service(show_repo=show_repo, torrent_repo=torrent_repo)

    async def _noop_release(_db: object) -> None:
        return None

    monkeypatch.setattr(
        "miramedia.database.release_session_before_external_io",
        _noop_release,
    )

    async def _identity_status(t, *, persist=False):  # noqa: ARG001
        return t

    monkeypatch.setattr(
        svc.torrent_service,
        "get_torrent_status",
        _identity_status,
    )

    rich = run_async(svc.get_torrents_for_show(show))

    assert len(rich) == 3
    by_id = {rt.id: rt for rt in rich}

    single = by_id[t_single.id]
    assert single.variant == "web-dl"
    assert single.media is not None
    assert single.media.seasons == [1]
    assert single.media.episodes == [1]
    assert single.import_progress.total == 1
    assert single.import_progress.imported == 1

    multi = by_id[t_multi.id]
    assert multi.variant == "bluray"
    assert multi.media is not None
    assert multi.media.seasons == [1, 2]
    assert multi.media.episodes is None
    assert multi.import_progress.total == 2
    assert multi.import_progress.pending == 2

    single_multi_ep = by_id[t_single_multi_ep.id]
    assert single_multi_ep.variant == "hdtv"
    assert single_multi_ep.media is not None
    assert single_multi_ep.media.seasons == [1]
    assert single_multi_ep.media.episodes == [1, 2]
    assert single_multi_ep.import_progress.total == 2
    assert single_multi_ep.import_progress.imported == 2

    assert torrent_repo.show_context_batch_calls == 1
    assert torrent_repo.movie_context_batch_calls == 1
    assert torrent_repo.import_status_batch_calls == 1
    show_repo.get_seasons_by_torrent_id.assert_not_called()
    show_repo.get_episodes_by_torrent_id.assert_not_called()
