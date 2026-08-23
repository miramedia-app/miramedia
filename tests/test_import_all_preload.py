"""Regression: import_all preloads IDs, then re-resolves media per fresh session."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

from miramedia.torrents.schemas import TorrentStatus
from tests.fakes import build_show_service, run_async
from tests.fakes.repositories import (
    FakeShowRepository,
    FakeTorrentRepository,
    make_show,
    make_torrent,
)


def _finished_torrent(title: str):
    return make_torrent(title=title).model_copy(
        update={"status": TorrentStatus.finished}
    )


def test_import_all_resolves_media_in_filter_and_re_resolves_in_loop() -> None:
    show = make_show()
    torrents = [_finished_torrent(f"ready-{i}") for i in range(3)]

    show_repo = FakeShowRepository()
    show_repo.add_show(show)
    torrent_repo = FakeTorrentRepository()
    for t in torrents:
        torrent_repo.torrents[t.id] = t
        torrent_repo.show_of_torrent[t.id] = show

    svc, _, _ = build_show_service(show_repo=show_repo, torrent_repo=torrent_repo)
    fresh_svc, _, fresh_repo = build_show_service(
        show_repo=show_repo, torrent_repo=torrent_repo
    )
    for t in torrents:
        fresh_repo.torrents[t.id] = t

    bg_sessions: list[object] = []
    call_count = 0

    @asynccontextmanager
    async def fake_bg():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            yield svc
        else:
            bg_sessions.append(fresh_svc)
            yield fresh_svc

    media_calls = 0
    real_get_media = svc._get_media_of_torrent

    async def counting_get_media(svc_arg, torrent):
        nonlocal media_calls
        media_calls += 1
        return await real_get_media(svc_arg, torrent)

    import_calls: list[str] = []

    async def track_import(_svc, torrent, _media):
        import_calls.append(torrent.title)

    with (
        patch("miramedia.background_services.bg_show_service", fake_bg),
        patch.object(
            svc, "reconcile_orphaned_failed_imports", AsyncMock(return_value=0)
        ),
        patch.object(
            svc.torrent_service,
            "get_all_torrents",
            AsyncMock(return_value=torrents),
        ),
        patch.object(
            svc.torrent_service,
            "bulk_check_torrents_imported",
            AsyncMock(return_value={t.id: False for t in torrents}),
        ),
        patch.object(
            svc.torrent_service, "is_due_for_retry", AsyncMock(return_value=True)
        ),
        patch.object(svc, "_get_media_of_torrent", counting_get_media),
        patch.object(svc, "_import_media_from_torrent", track_import),
    ):
        run_async(svc.import_all_torrents())

    # 3 torrents x (preload filter + per-item re-resolve in loop)
    assert media_calls == 6
    assert len(bg_sessions) == 3
    assert import_calls == [t.title for t in torrents]


def test_import_all_skips_vanished_torrent_without_aborting_batch() -> None:
    show = make_show()
    keep = _finished_torrent("keep")
    vanish = _finished_torrent("vanish")

    show_repo = FakeShowRepository()
    show_repo.add_show(show)
    torrent_repo = FakeTorrentRepository()
    for t in (keep, vanish):
        torrent_repo.torrents[t.id] = t
        torrent_repo.show_of_torrent[t.id] = show

    svc, _, _ = build_show_service(show_repo=show_repo, torrent_repo=torrent_repo)
    fresh_torrent_repo = FakeTorrentRepository()
    fresh_torrent_repo.torrents[keep.id] = keep
    fresh_torrent_repo.show_of_torrent[keep.id] = show
    fresh_svc, _, _ = build_show_service(
        show_repo=show_repo, torrent_repo=fresh_torrent_repo
    )

    call_count = 0

    @asynccontextmanager
    async def fake_bg():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            yield svc
        else:
            yield fresh_svc

    import_calls: list[str] = []

    async def track_import(_svc, torrent, _media):
        import_calls.append(torrent.title)

    with (
        patch("miramedia.background_services.bg_show_service", fake_bg),
        patch.object(
            svc, "reconcile_orphaned_failed_imports", AsyncMock(return_value=0)
        ),
        patch.object(
            svc.torrent_service,
            "get_all_torrents",
            AsyncMock(return_value=[keep, vanish]),
        ),
        patch.object(
            svc.torrent_service,
            "bulk_check_torrents_imported",
            AsyncMock(return_value={keep.id: False, vanish.id: False}),
        ),
        patch.object(
            svc.torrent_service, "is_due_for_retry", AsyncMock(return_value=True)
        ),
        patch.object(svc, "_import_media_from_torrent", track_import),
    ):
        run_async(svc.import_all_torrents())

    assert import_calls == [keep.title]


def test_import_all_failure_isolation_continues_batch() -> None:
    show = make_show()
    good = _finished_torrent("good")
    bad = _finished_torrent("bad")

    show_repo = FakeShowRepository()
    show_repo.add_show(show)
    torrent_repo = FakeTorrentRepository()
    for t in (good, bad):
        torrent_repo.torrents[t.id] = t
        torrent_repo.show_of_torrent[t.id] = show

    svc, _, _ = build_show_service(show_repo=show_repo, torrent_repo=torrent_repo)
    fresh_svc, _, fresh_repo = build_show_service(
        show_repo=show_repo, torrent_repo=torrent_repo
    )
    for t in (good, bad):
        fresh_repo.torrents[t.id] = t

    call_count = 0

    @asynccontextmanager
    async def fake_bg():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            yield svc
        else:
            yield fresh_svc

    import_calls: list[str] = []

    async def maybe_fail(_svc, torrent, _media):
        import_calls.append(torrent.title)
        if torrent.title == bad.title:
            boom = "boom"
            raise RuntimeError(boom)

    with (
        patch("miramedia.background_services.bg_show_service", fake_bg),
        patch.object(
            svc, "reconcile_orphaned_failed_imports", AsyncMock(return_value=0)
        ),
        patch.object(
            svc.torrent_service,
            "get_all_torrents",
            AsyncMock(return_value=[bad, good]),
        ),
        patch.object(
            svc.torrent_service,
            "bulk_check_torrents_imported",
            AsyncMock(return_value={good.id: False, bad.id: False}),
        ),
        patch.object(
            svc.torrent_service, "is_due_for_retry", AsyncMock(return_value=True)
        ),
        patch.object(svc, "_import_media_from_torrent", maybe_fail),
        patch.object(svc, "_mark_torrent_import_failed", AsyncMock()) as mark_failed,
    ):
        run_async(svc.import_all_torrents())

    assert import_calls == [bad.title, good.title]
    mark_failed.assert_awaited_once_with(bad.id, "Import raised; see logs.")


def test_import_all_re_resolves_media_after_prior_import_mutates_show() -> None:
    show = make_show(name="Before")
    first = _finished_torrent("first")
    second = _finished_torrent("second")

    show_repo = FakeShowRepository()
    show_repo.add_show(show)
    torrent_repo = FakeTorrentRepository()
    for t in (first, second):
        torrent_repo.torrents[t.id] = t
        torrent_repo.show_of_torrent[t.id] = show

    svc, _, _ = build_show_service(show_repo=show_repo, torrent_repo=torrent_repo)
    fresh_svc, _, fresh_repo = build_show_service(
        show_repo=show_repo, torrent_repo=torrent_repo
    )
    for t in (first, second):
        fresh_repo.torrents[t.id] = t

    call_count = 0

    @asynccontextmanager
    async def fake_bg():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            yield svc
        else:
            yield fresh_svc

    seen_names: list[str] = []

    async def mutate_on_first(_svc, torrent, media):
        seen_names.append(media.name)
        if torrent.title == first.title:
            updated = show.model_copy(update={"name": "After"})
            show_repo.shows[show.id] = updated
            for tid in (first.id, second.id):
                torrent_repo.show_of_torrent[tid] = updated

    with (
        patch("miramedia.background_services.bg_show_service", fake_bg),
        patch.object(
            svc, "reconcile_orphaned_failed_imports", AsyncMock(return_value=0)
        ),
        patch.object(
            svc.torrent_service,
            "get_all_torrents",
            AsyncMock(return_value=[first, second]),
        ),
        patch.object(
            svc.torrent_service,
            "bulk_check_torrents_imported",
            AsyncMock(return_value={first.id: False, second.id: False}),
        ),
        patch.object(
            svc.torrent_service, "is_due_for_retry", AsyncMock(return_value=True)
        ),
        patch.object(svc, "_import_media_from_torrent", mutate_on_first),
    ):
        run_async(svc.import_all_torrents())

    assert seen_names == ["Before", "After"]


def test_import_all_skips_when_media_vanishes_before_loop() -> None:
    show = make_show()
    keep = _finished_torrent("keep")
    vanish = _finished_torrent("vanish")

    show_repo = FakeShowRepository()
    show_repo.add_show(show)
    torrent_repo = FakeTorrentRepository()
    for t in (keep, vanish):
        torrent_repo.torrents[t.id] = t
        torrent_repo.show_of_torrent[t.id] = show

    svc, _, _ = build_show_service(show_repo=show_repo, torrent_repo=torrent_repo)
    fresh_svc, _, fresh_repo = build_show_service(
        show_repo=show_repo, torrent_repo=torrent_repo
    )
    for t in (keep, vanish):
        fresh_repo.torrents[t.id] = t

    call_count = 0

    @asynccontextmanager
    async def fake_bg():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            yield svc
        else:
            yield fresh_svc

    async def unlink_vanish(_db: object) -> None:
        torrent_repo.show_of_torrent.pop(vanish.id, None)

    import_calls: list[str] = []

    async def track_import(_svc, torrent, _media):
        import_calls.append(torrent.title)

    with (
        patch("miramedia.background_services.bg_show_service", fake_bg),
        patch(
            "miramedia.database.release_session_before_external_io",
            unlink_vanish,
        ),
        patch.object(
            svc, "reconcile_orphaned_failed_imports", AsyncMock(return_value=0)
        ),
        patch.object(
            svc.torrent_service,
            "get_all_torrents",
            AsyncMock(return_value=[keep, vanish]),
        ),
        patch.object(
            svc.torrent_service,
            "bulk_check_torrents_imported",
            AsyncMock(return_value={keep.id: False, vanish.id: False}),
        ),
        patch.object(
            svc.torrent_service, "is_due_for_retry", AsyncMock(return_value=True)
        ),
        patch.object(svc, "_import_media_from_torrent", track_import),
    ):
        run_async(svc.import_all_torrents())

    assert import_calls == [keep.title]
