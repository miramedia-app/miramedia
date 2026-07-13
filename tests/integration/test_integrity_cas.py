"""Real-DB integrity compare-and-set (Plan 079)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from sqlalchemy import select

from miramedia.exceptions import ConflictError
from miramedia.movies.repository import MovieRepository
from miramedia.movies.service import MovieService
from miramedia.shows.models import EpisodeFile
from miramedia.shows.repository import ShowRepository
from miramedia.shows.service import ShowService
from miramedia.torrents.repository import TorrentRepository
from miramedia.torrents.schemas import MediaType
from miramedia.torrents.service import TorrentService
from tests.integration.builders import insert_movie_file, insert_show_episode_file

pytestmark = pytest.mark.integration

_PRIOR = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
_MISMATCH = "sha1 mismatch (expected aaaaaaaa…, got bbbbbbbb…)"
_NEW = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


def _show_stack(db) -> tuple[ShowService, ShowRepository, TorrentService]:
    torrent_repo = TorrentRepository(db)
    show_repo = ShowRepository(db)
    torrent_svc = TorrentService(torrent_repository=torrent_repo)
    show_svc = ShowService(
        show_repository=show_repo,
        torrent_service=torrent_svc,
        indexer_service=MagicMock(),
        notification_service=MagicMock(),
    )
    return show_svc, show_repo, torrent_svc


def _movie_stack(db) -> tuple[MovieService, MovieRepository]:
    torrent_repo = TorrentRepository(db)
    movie_repo = MovieRepository(db)
    movie_svc = MovieService(
        movie_repository=movie_repo,
        torrent_service=TorrentService(torrent_repository=torrent_repo),
        indexer_service=MagicMock(),
        notification_service=MagicMock(),
    )
    return movie_svc, movie_repo


def test_audit_cas_requires_both_sha1_and_import_error(db, run_async) -> None:
    async def _run_test() -> None:
        _show, episode_file = await insert_show_episode_file(
            db, sha1=_PRIOR, import_error=None
        )
        show_repo = ShowRepository(db)

        stale = await show_repo.stamp_integrity_mismatch_if_current(
            episode_file.id,
            expected_sha1=_PRIOR,
            expected_import_error=_MISMATCH,
            import_error=_MISMATCH,
        )
        assert stale is False

        ok = await show_repo.stamp_integrity_mismatch_if_current(
            episode_file.id,
            expected_sha1=_PRIOR,
            expected_import_error=None,
            import_error=_MISMATCH,
        )
        assert ok is True
        await db.commit()

        row = (
            await db.execute(
                select(EpisodeFile).where(EpisodeFile.id == episode_file.id)
            )
        ).scalar_one()
        assert row.import_error == _MISMATCH

    run_async(_run_test())


def test_interleaved_update_defeats_stale_writer(db, make_session, run_async) -> None:
    async def _run_test() -> None:
        _show, episode_file = await insert_show_episode_file(
            db, sha1=None, import_error=None
        )
        file_id = episode_file.id

        reader = make_session()
        writer = make_session()
        reader_repo = ShowRepository(reader)
        writer_repo = ShowRepository(writer)

        snapshot = (
            await reader.execute(select(EpisodeFile).where(EpisodeFile.id == file_id))
        ).scalar_one()
        assert snapshot.sha1 is None
        assert snapshot.import_error is None

        applied = await writer_repo.apply_integrity_baseline_if_current(
            file_id,
            expected_sha1=None,
            expected_import_error=None,
            new_sha1=_NEW,
        )
        assert applied is True
        await writer.commit()

        stale = await reader_repo.apply_integrity_baseline_if_current(
            file_id,
            expected_sha1=None,
            expected_import_error=None,
            new_sha1=_PRIOR,
        )
        assert stale is False
        await reader.close()
        await writer.close()

        verify = make_session()
        row = (
            await verify.execute(select(EpisodeFile).where(EpisodeFile.id == file_id))
        ).scalar_one()
        await verify.close()
        assert row.sha1 == _NEW

    run_async(_run_test())


def test_dismiss_and_rebaseline_commit_visibility(db, make_session, run_async) -> None:
    async def _run_test() -> None:
        _show, episode_file = await insert_show_episode_file(
            db,
            sha1=_PRIOR,
            import_error=_MISMATCH,
        )
        show_svc, _, torrent_svc = _show_stack(db)
        movie_svc, _movie_repo = _movie_stack(db)

        await torrent_svc.dismiss_mismatch(
            media_type=MediaType.show,
            file_id=episode_file.id,
            show_service=show_svc,
            movie_service=movie_svc,
        )
        await db.commit()

        reader = make_session()
        dismissed = (
            await reader.execute(
                select(EpisodeFile).where(EpisodeFile.id == episode_file.id)
            )
        ).scalar_one()
        assert dismissed.import_error is None
        assert dismissed.sha1 == _PRIOR
        await reader.close()

        await db.execute(
            EpisodeFile.__table__.update()
            .where(EpisodeFile.id == episode_file.id)
            .values(import_error=_MISMATCH)
        )
        await db.commit()
        await db.close()

        writer = make_session()
        writer_show_svc, _, writer_torrent_svc = _show_stack(writer)
        writer_movie_svc, _ = _movie_stack(writer)
        await writer_torrent_svc.rebaseline_file(
            media_type=MediaType.show,
            file_id=episode_file.id,
            show_service=writer_show_svc,
            movie_service=writer_movie_svc,
        )
        await writer.commit()

        verify = make_session()
        row = (
            await verify.execute(
                select(EpisodeFile).where(EpisodeFile.id == episode_file.id)
            )
        ).scalar_one()
        assert row.import_error is None
        assert row.sha1 is None

        stale_repo = ShowRepository(verify)
        lost = await stale_repo.clear_file_integrity_state(
            episode_file.id,
            expected_sha1=_PRIOR,
            expected_import_error=_MISMATCH,
            reset_sha1=True,
        )
        assert lost is False
        await verify.close()

    run_async(_run_test())


def test_rebaseline_conflict_when_mismatch_cleared_between_read_and_write(
    db, make_session, run_async
) -> None:
    async def _run_test() -> None:
        _show, episode_file = await insert_show_episode_file(
            db,
            sha1=_PRIOR,
            import_error=_MISMATCH,
        )
        show_svc, _, torrent_svc = _show_stack(db)
        movie_svc, _ = _movie_stack(db)

        reader = make_session()
        observed = await ShowRepository(reader).get_episode_file_by_id(episode_file.id)
        assert observed is not None

        await torrent_svc.dismiss_mismatch(
            media_type=MediaType.show,
            file_id=episode_file.id,
            show_service=show_svc,
            movie_service=movie_svc,
        )
        await db.commit()
        await reader.close()

        with pytest.raises(ConflictError):
            await torrent_svc.rebaseline_file(
                media_type=MediaType.show,
                file_id=episode_file.id,
                show_service=show_svc,
                movie_service=movie_svc,
            )

    run_async(_run_test())


def test_movie_integrity_cas_matches_episode_semantics(db, run_async) -> None:
    async def _run_test() -> None:
        _movie, movie_file = await insert_movie_file(db, sha1=_PRIOR, import_error=None)
        movie_repo = MovieRepository(db)
        ok = await movie_repo.stamp_integrity_mismatch_if_current(
            movie_file.id,
            expected_sha1=_PRIOR,
            expected_import_error=None,
            import_error=_MISMATCH,
        )
        assert ok is True
        await db.commit()

        stale = await movie_repo.apply_integrity_baseline_if_current(
            movie_file.id,
            expected_sha1=None,
            expected_import_error=None,
            new_sha1=_NEW,
        )
        assert stale is False

    run_async(_run_test())
