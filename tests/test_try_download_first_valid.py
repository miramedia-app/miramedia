"""Auto-download must skip candidates whose titles cannot be used as paths."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from miramedia.exceptions import NoVideoFilesError, UnsafeTorrentTitleError
from miramedia.indexers.schemas import IndexerQueryResult
from tests.fakes import build_movie_service, build_show_service, run_async
from tests.fakes.repositories import make_movie, make_show


def _result(title: str) -> IndexerQueryResult:
    return IndexerQueryResult(
        title=title,
        download_url=f"magnet:?xt=urn:btih:{title}",
        seeders=10,
        flags=[],
        size=2_000_000_000,
        usenet=False,
        age=1,
        indexer="x",
    )


def test_try_download_first_valid_skips_unsafe_title_and_tries_next() -> None:
    movie = make_movie()
    svc, _, _ = build_movie_service()
    unsafe = _result("Spider-Man: Brand New Day 2026 1080p")
    safe = _result("Spider.Man.Brand.New.Day.2026.1080p")
    downloaded: list[str] = []

    async def _download(*, public_indexer_result_id, movie):  # noqa: ARG001
        if public_indexer_result_id == unsafe.id:
            msg = "colon"
            raise UnsafeTorrentTitleError(msg)
        downloaded.append(str(public_indexer_result_id))
        return MagicMock()

    svc.download_torrent = AsyncMock(side_effect=_download)
    picked = run_async(
        svc._try_download_first_valid(results=[unsafe, safe], movie=movie)
    )
    assert picked is safe
    assert downloaded == [str(safe.id)]


def test_try_download_first_valid_still_skips_no_video() -> None:
    movie = make_movie()
    svc, _, _ = build_movie_service()
    empty = _result("Empty.1080p")
    ok = _result("Real.1080p")

    async def _download(*, public_indexer_result_id, movie):  # noqa: ARG001
        if public_indexer_result_id == empty.id:
            msg = "no video"
            raise NoVideoFilesError(msg)
        return MagicMock()

    svc.download_torrent = AsyncMock(side_effect=_download)
    picked = run_async(svc._try_download_first_valid(results=[empty, ok], movie=movie))
    assert picked is ok


def test_auto_download_first_valid_skips_unsafe_title() -> None:
    show = make_show()
    svc, _, _ = build_show_service()
    unsafe = _result("Show: Title S01E01")
    safe = _result("Show.Title.S01E01")

    async def _download(
        *,
        public_indexer_result_id,
        show_id,
        episode_target=None,  # noqa: ARG001
    ):
        assert show_id == show.id
        if public_indexer_result_id == unsafe.id:
            msg = "colon"
            raise UnsafeTorrentTitleError(msg)
        return MagicMock()

    svc.download_torrent = AsyncMock(side_effect=_download)
    picked = run_async(svc._auto_download_first_valid([unsafe, safe], show, "S01E01"))
    assert picked is safe
