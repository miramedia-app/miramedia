"""Regression tests for torrent-title path containment (Plan 072).

The display title stored in the database must remain unchanged; only
filesystem joins go through ``torrent_title_path_component``.
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from miramedia.exceptions import (
    UNSAFE_TORRENT_TITLE_API_DETAIL,
    UnsafeTorrentTitleError,
    unsafe_torrent_title_error_handler,
)
from miramedia.indexers.schemas import IndexerQueryResult
from miramedia.torrents import utils
from miramedia.torrents.backends.native import NativeDownloadClient
from miramedia.torrents.backends.qbittorrent import QbittorrentDownloadClient
from miramedia.torrents.backends.transmission import TransmissionDownloadClient
from miramedia.torrents.models import Quality
from miramedia.torrents.schemas import MediaType, Torrent, TorrentStatus
from miramedia.torrents.utils import (
    _is_safe_deletion_target,
    exact_save_dirs_for_title,
    get_torrent_filepath,
    get_torrent_hash,
    resolve_within,
    torrent_deletion_dir_under_root,
    torrent_dir_under_root,
    torrent_sidecar_under_root,
    torrent_title_path_component,
)

DOWNLOAD_ROUTE = "/api/v1/torrents/download"


@pytest.mark.parametrize(
    "title",
    [
        "My.Show.S01E01.1080p.WEB-DL",
        "映画.2020.1080p",
        "My Show With Spaces",
        "Release-With.Dots_and-dashes",
    ],
)
def test_safe_titles_accepted_unchanged(title: str) -> None:
    assert torrent_title_path_component(title) == title


@pytest.mark.parametrize(
    "title",
    [
        "",
        "   ",
        ".",
        "..",
        "../outside",
        "..\\outside",
        "foo/bar",
        "foo\\bar",
        "/etc/passwd",
        "\\etc\\passwd",
        "C:\\Windows",
        "C:foo",
        "C:",
        "\\\\server\\share",
        "\\\\?\\C:\\secret",
        "\\\\.\\PHYSICALDRIVE0",
        "foo\x00bar",
        "foo\nbar",
        "foo/bar/baz",
        "CON",
        "NUL.txt",
        "COM1.log",
        "name<bad>",
        "trail...",
        "bad name ",
        "bad name.",
        ".resume_data",
        "file:name",
        "bad\x85name",
        "bad\x9bname",
        "CONIN$",
        "CONOUT$",
        "COM¹",
        "COM²",
        "LPT³",
    ],
)
def test_unsafe_titles_rejected(title: str) -> None:
    with pytest.raises(UnsafeTorrentTitleError):
        torrent_title_path_component(title)


def test_torrent_dir_under_root_stays_inside(tmp_path: Path) -> None:
    root = tmp_path / "torrents"
    root.mkdir()
    resolved = torrent_dir_under_root(root, "Safe.Title.1080p")
    assert resolved == (root / "Safe.Title.1080p").resolve()
    assert resolved.is_relative_to(root.resolve())
    assert resolved != root.resolve()


def test_torrent_dir_under_root_rejects_escape(tmp_path: Path) -> None:
    root = tmp_path / "torrents"
    root.mkdir()
    with pytest.raises(UnsafeTorrentTitleError):
        torrent_dir_under_root(root, "../escape")


def test_torrent_dir_under_root_rejects_leaf_symlink_to_root(tmp_path: Path) -> None:
    root = tmp_path / "completed"
    root.mkdir()
    (root / "link-to-root").symlink_to(root)
    with pytest.raises(UnsafeTorrentTitleError):
        torrent_dir_under_root(root, "link-to-root")


def test_torrent_dir_under_root_rejects_leaf_symlink_target(tmp_path: Path) -> None:
    root = tmp_path / "completed"
    outside = tmp_path / "outside"
    outside.mkdir()
    root.mkdir()
    (root / "link").symlink_to(outside)
    with pytest.raises(UnsafeTorrentTitleError):
        torrent_dir_under_root(root, "link")


def test_torrent_sidecar_under_root_rejects_leaf_symlink(tmp_path: Path) -> None:
    root = tmp_path / "completed"
    outside = tmp_path / "outside"
    outside.mkdir()
    root.mkdir()
    (root / "Safe.torrent").symlink_to(outside / "evil.torrent")
    with pytest.raises(UnsafeTorrentTitleError):
        torrent_sidecar_under_root(root, "Safe")


def test_torrent_deletion_dir_under_root_rejects_leaf_symlink(tmp_path: Path) -> None:
    root = tmp_path / "completed"
    target = tmp_path / "victim"
    target.mkdir()
    root.mkdir()
    (root / "link").symlink_to(target)
    with pytest.raises(UnsafeTorrentTitleError):
        torrent_deletion_dir_under_root(root, "link")


@pytest.mark.parametrize("root_name", ["completed", "incomplete"])
def test_native_cleanup_never_rmtrees_payload_dirs(
    tmp_path: Path, root_name: str, monkeypatch
) -> None:
    completed = tmp_path / "completed"
    incomplete = tmp_path / "incomplete"
    completed.mkdir()
    incomplete.mkdir()
    root = completed if root_name == "completed" else incomplete
    payload = root / "Safe.Release"
    payload.mkdir()
    (payload / "file.mkv").touch()

    def config():
        return SimpleNamespace(
            misc=SimpleNamespace(
                effective_completed_path=completed,
                incomplete_torrent_path=str(incomplete),
                torrent_directory=str(completed),
            ),
            indexers=SimpleNamespace(timeout_seconds=5),
            torrents=SimpleNamespace(
                native=SimpleNamespace(
                    listen_port_start=6881,
                    max_download_rate=0,
                    max_upload_rate=0,
                ),
            ),
        )

    import miramedia.torrents.backends.native as native_backend

    monkeypatch.setattr(native_backend, "MiraMediaConfig", config)
    monkeypatch.setattr(utils, "MiraMediaConfig", config)

    rmdirs: list[Path] = []

    def track_rmdir(self: Path) -> None:
        rmdirs.append(self)

    monkeypatch.setattr(Path, "rmdir", track_rmdir)

    client = NativeDownloadClient()
    torrent = Torrent(
        status=TorrentStatus.finished,
        title="Safe.Release",
        quality=Quality.unknown,
        hash="a" * 40,
        usenet=False,
    )
    client._try_rmdir_empty_save_dirs(torrent)

    assert (payload / "file.mkv").exists()
    assert payload not in rmdirs


@pytest.fixture
def torrent_roots(tmp_path, monkeypatch):
    completed = tmp_path / "completed"
    incomplete = tmp_path / "incomplete"
    completed.mkdir()
    incomplete.mkdir()

    def config():
        return SimpleNamespace(
            misc=SimpleNamespace(
                effective_completed_path=completed,
                incomplete_torrent_path=str(incomplete),
                torrent_directory=str(completed),
            ),
            indexers=SimpleNamespace(timeout_seconds=5),
            torrents=SimpleNamespace(
                native=SimpleNamespace(
                    listen_port_start=6881,
                    max_download_rate=0,
                    max_upload_rate=0,
                ),
                transmission=SimpleNamespace(
                    host="localhost",
                    port=9091,
                    username="",
                    password="",
                    https_enabled=False,
                    path="/transmission/rpc",
                ),
                qbittorrent=SimpleNamespace(
                    host="localhost",
                    port=8080,
                    username="admin",
                    password="admin",
                    category_name="MiraMedia",
                    category_save_path=str(completed),
                ),
            ),
        )

    import miramedia.torrents.backends.native as native_backend
    import miramedia.torrents.backends.qbittorrent as qbittorrent_backend
    import miramedia.torrents.backends.transmission as transmission_backend

    for module in (
        utils,
        native_backend,
        transmission_backend,
        qbittorrent_backend,
    ):
        monkeypatch.setattr(module, "MiraMediaConfig", config)
    return SimpleNamespace(completed=completed, incomplete=incomplete, root=tmp_path)


def test_is_safe_deletion_target_nested_roots_matrix(tmp_path: Path) -> None:
    downloads = tmp_path / "downloads"
    incomplete = downloads / "incomplete"
    incomplete.mkdir(parents=True)
    owned = incomplete / "Owned.Release"
    owned.mkdir()
    other = downloads / "other"
    other.mkdir()

    roots = [downloads, incomplete]

    assert _is_safe_deletion_target(downloads, roots) is False
    assert _is_safe_deletion_target(incomplete, roots) is False
    assert _is_safe_deletion_target(owned, roots) is True
    assert _is_safe_deletion_target(other, roots) is True


def test_get_torrent_filepath_rejects_symlink_primary_sanitized_and_fuzzy(
    tmp_path, monkeypatch
) -> None:
    completed = tmp_path / "completed"
    completed.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.mkv").touch()
    monkeypatch.setattr(
        utils,
        "MiraMediaConfig",
        lambda: SimpleNamespace(
            misc=SimpleNamespace(effective_completed_path=completed)
        ),
    )
    title = "Greys.Anatomy.S06.1080p.WEBRip.x265-RARBG"
    torrent = SimpleNamespace(title=title)
    (completed / title).symlink_to(outside)
    sanitized = "Greys.Anatomy.S09.1080p.WEBRip.x265-RARBG"
    (completed / sanitized).mkdir()
    fuzzy = completed / "Greys Anatomy S06 1080p BluRay"
    fuzzy.symlink_to(outside)

    with pytest.raises(UnsafeTorrentTitleError):
        get_torrent_filepath(torrent)


def test_get_torrent_filepath_uses_safe_component(torrent_roots) -> None:
    torrent = SimpleNamespace(title="Valid.Release.1080p")
    assert get_torrent_filepath(torrent) == torrent_roots.completed / torrent.title


def test_lookup_consumer_resolve_within_stays_inside_owned_root(torrent_roots) -> None:
    title = "Owned.Release"
    owned = torrent_roots.completed / title
    owned.mkdir()
    (owned / "episode.mkv").touch()
    torrent = SimpleNamespace(title=title)
    root = get_torrent_filepath(torrent)
    assert resolve_within(root, "episode.mkv") == (owned / "episode.mkv").resolve()
    assert resolve_within(root, "../outside.mkv") is None


def test_get_torrent_filepath_rejects_unsafe_title(torrent_roots) -> None:
    _ = torrent_roots
    torrent = SimpleNamespace(title="../outside")
    with pytest.raises(UnsafeTorrentTitleError):
        get_torrent_filepath(torrent)


def test_native_resolve_paths_contained(torrent_roots) -> None:
    client = NativeDownloadClient()
    initial, completed = client._resolve_paths("My.Release.1080p")
    assert initial == torrent_roots.incomplete / "My.Release.1080p"
    assert completed == torrent_roots.completed / "My.Release.1080p"
    assert completed.is_relative_to(torrent_roots.completed.resolve())
    assert completed != torrent_roots.completed.resolve()


def test_native_resolve_paths_rejects_traversal(torrent_roots) -> None:
    _ = torrent_roots
    client = NativeDownloadClient()
    with pytest.raises(UnsafeTorrentTitleError):
        client._resolve_paths("../outside")


def _indexer(title: str, *, download_url: str | None = None) -> IndexerQueryResult:
    return IndexerQueryResult(
        title=title,
        download_url=download_url
        or "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567",
        flags=[],
        size=0,
        usenet=False,
        age=0,
        indexer="test",
    )


def test_native_download_rejects_malicious_title_without_fs_mutation(
    torrent_roots,
) -> None:
    victim = torrent_roots.root / "victim"
    victim.mkdir()
    client = NativeDownloadClient()

    with patch(
        "miramedia.torrents.backends.native.get_torrent_hash",
        return_value="deadbeef",
    ) as get_hash:
        with pytest.raises(UnsafeTorrentTitleError):
            client.download_torrent(_indexer("../victim"))
    get_hash.assert_not_called()

    assert victim.exists()
    assert not (torrent_roots.completed / "victim").exists()
    assert not (torrent_roots.incomplete / "victim").exists()


def test_transmission_download_rejects_malicious_title(
    torrent_roots, monkeypatch
) -> None:
    victim = torrent_roots.root / "victim"
    victim.mkdir()
    mock_client = MagicMock()

    def fake_client(*_args, **_kwargs):
        return mock_client

    def fake_hash(torrent, **_kwargs):
        _ = torrent
        return "deadbeef"

    monkeypatch.setattr(
        "miramedia.torrents.backends.transmission.transmission_rpc.Client",
        fake_client,
    )
    monkeypatch.setattr(
        "miramedia.torrents.backends.transmission.get_torrent_hash",
        fake_hash,
    )
    client = TransmissionDownloadClient()

    with pytest.raises(UnsafeTorrentTitleError):
        client.download_torrent(_indexer("../victim"))

    mock_client.add_torrent.assert_not_called()
    assert victim.exists()


def test_transmission_download_safe_title_passes_contained_download_dir(
    torrent_roots, monkeypatch
) -> None:
    mock_client = MagicMock()

    def fake_client(*_args, **_kwargs):
        return mock_client

    def fake_hash(torrent, **_kwargs):
        _ = torrent
        return "deadbeef"

    monkeypatch.setattr(
        "miramedia.torrents.backends.transmission.transmission_rpc.Client",
        fake_client,
    )
    monkeypatch.setattr(
        "miramedia.torrents.backends.transmission.get_torrent_hash",
        fake_hash,
    )
    client = TransmissionDownloadClient()
    title = "Safe.Release.1080p"

    client.download_torrent(_indexer(title))

    mock_client.add_torrent.assert_called_once_with(
        torrent="magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567",
        download_dir=str((torrent_roots.completed / title).resolve()),
    )


def test_qbittorrent_download_rejects_malicious_title(
    torrent_roots, monkeypatch
) -> None:
    victim = torrent_roots.root / "victim"
    victim.mkdir()
    api = MagicMock()
    api.torrents_add.return_value = "Ok."

    def fake_client(*_args, **_kwargs):
        return api

    def fake_hash(torrent, **_kwargs):
        _ = torrent
        return "deadbeef"

    monkeypatch.setattr(
        "miramedia.torrents.backends.qbittorrent.qbittorrentapi.Client",
        fake_client,
    )
    monkeypatch.setattr(
        "miramedia.torrents.backends.qbittorrent.get_torrent_hash",
        fake_hash,
    )
    client = QbittorrentDownloadClient()

    with pytest.raises(UnsafeTorrentTitleError):
        client.download_torrent(_indexer("..\\victim"))

    api.torrents_add.assert_not_called()
    assert victim.exists()


def test_qbittorrent_download_safe_title_passes_leaf_save_path(
    torrent_roots, monkeypatch
) -> None:
    _ = torrent_roots
    api = MagicMock()
    api.torrents_add.return_value = "Ok."

    def fake_client(*_args, **_kwargs):
        return api

    def fake_hash(torrent, **_kwargs):
        _ = torrent
        return "deadbeef"

    monkeypatch.setattr(
        "miramedia.torrents.backends.qbittorrent.qbittorrentapi.Client",
        fake_client,
    )
    monkeypatch.setattr(
        "miramedia.torrents.backends.qbittorrent.get_torrent_hash",
        fake_hash,
    )
    client = QbittorrentDownloadClient()
    title = "Safe.Release.1080p"
    magnet = "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567"

    client.download_torrent(_indexer(title, download_url=magnet))

    api.torrents_add.assert_called_once_with(
        category="MiraMedia",
        urls=magnet,
        save_path=title,
    )


def test_get_torrent_hash_rejects_malicious_title_without_sidecar_write(
    torrent_roots,
) -> None:
    victim = torrent_roots.root / "victim"
    victim.mkdir()
    with pytest.raises(UnsafeTorrentTitleError):
        get_torrent_hash(_indexer("../victim"))
    assert list(torrent_roots.completed.iterdir()) == []
    assert victim.exists()


def test_get_torrent_hash_writes_sidecar_under_completed(
    torrent_roots, monkeypatch
) -> None:
    title = "Safe.Release.1080p"
    payload = (
        b"d8:announce0e4:infod6:lengthi1e4:name4:test12:piece lengthi16384e"
        b"6:pieces20:aaaaaaaaaaaaaaaaaaaae5:filesld6:lengthi1e4:pathl4:teste"
        b"eee"
    )

    class FakeResponse:
        def __init__(self) -> None:
            self.status_code = 200
            self.headers: dict[str, str] = {}
            self.closed = False

        def raise_for_status(self) -> None:
            return None

        def iter_content(self, chunk_size: int = 0):
            del chunk_size
            yield payload

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(utils.requests, "get", lambda *_a, **_k: FakeResponse())
    monkeypatch.setattr(
        utils,
        "_parse_torrent_bytes",
        lambda _content: ("a" * 40, title, []),
    )

    torrent_hash = get_torrent_hash(
        _indexer(title, download_url="http://example.com/safe.torrent")
    )

    sidecar = torrent_sidecar_under_root(torrent_roots.completed, title)
    assert sidecar.exists()
    assert sidecar.read_bytes() == payload
    assert sidecar.is_relative_to(torrent_roots.completed.resolve())
    assert torrent_hash == "a" * 40


def test_remove_torrent_without_handle_preserves_preexisting_same_title_payload(
    torrent_roots,
) -> None:
    title = "Victim.Release"
    victim_dir = torrent_roots.completed / title
    victim_dir.mkdir()
    precious = victim_dir / "precious.mkv"
    precious.touch()
    client = NativeDownloadClient()
    torrent = Torrent(
        status=TorrentStatus.finished,
        title=title,
        quality=Quality.unknown,
        hash="a" * 40,
        usenet=False,
    )

    with patch("shutil.rmtree") as rmtree:
        client.remove_torrent(torrent, delete_data=True)

    rmtree.assert_not_called()
    assert precious.exists()


def test_duplicate_title_different_hash_neither_deletes_payload(
    torrent_roots,
) -> None:
    title = "Shared.Title"
    shared = torrent_roots.completed / title
    shared.mkdir()
    (shared / "content.mkv").touch()
    client = NativeDownloadClient()
    torrent_a = Torrent(
        status=TorrentStatus.finished,
        title=title,
        quality=Quality.unknown,
        hash="a" * 40,
        usenet=False,
    )
    torrent_b = Torrent(
        status=TorrentStatus.finished,
        title=title,
        quality=Quality.unknown,
        hash="b" * 40,
        usenet=False,
    )

    with patch("shutil.rmtree") as rmtree:
        client.remove_torrent(torrent_a, delete_data=True)
        client.remove_torrent(torrent_b, delete_data=True)

    rmtree.assert_not_called()
    assert (shared / "content.mkv").exists()


def test_remove_torrent_with_handle_rmdirs_only_empty_exact_dirs(
    torrent_roots, monkeypatch
) -> None:
    title = "Mine.Release"
    completed_dir = torrent_roots.completed / title
    completed_dir.mkdir()
    incomplete_dir = torrent_roots.incomplete / title
    incomplete_dir.mkdir()
    client = NativeDownloadClient()
    torrent = Torrent(
        status=TorrentStatus.finished,
        title=title,
        quality=Quality.unknown,
        hash="c" * 40,
        usenet=False,
    )
    handle = MagicMock()
    handle.is_valid.return_value = True
    monkeypatch.setattr(client, "_get_handle_by_hash", lambda _h: handle)
    monkeypatch.setattr(
        client._session,
        "remove_torrent",
        lambda *_a, **_k: None,
    )

    rmdirs: list[Path] = []

    def track_rmdir(self: Path) -> None:
        rmdirs.append(self)

    monkeypatch.setattr(Path, "rmdir", track_rmdir)

    with patch("shutil.rmtree") as rmtree:
        client.remove_torrent(torrent, delete_data=True)

    rmtree.assert_not_called()
    assert {p.resolve() for p in rmdirs} == {
        completed_dir.resolve(),
        incomplete_dir.resolve(),
    }


def test_remove_torrent_with_handle_skips_nonempty_exact_dirs(
    torrent_roots, monkeypatch
) -> None:
    title = "Mine.Release"
    completed_dir = torrent_roots.completed / title
    completed_dir.mkdir()
    (completed_dir / "leftover.mkv").touch()
    client = NativeDownloadClient()
    torrent = Torrent(
        status=TorrentStatus.finished,
        title=title,
        quality=Quality.unknown,
        hash="d" * 40,
        usenet=False,
    )
    handle = MagicMock()
    monkeypatch.setattr(client, "_get_handle_by_hash", lambda _h: handle)
    monkeypatch.setattr(client._session, "remove_torrent", lambda *_a, **_k: None)

    rmdirs: list[Path] = []

    def track_rmdir(self: Path) -> None:
        rmdirs.append(self)

    monkeypatch.setattr(Path, "rmdir", track_rmdir)

    with patch("shutil.rmtree") as rmtree:
        client.remove_torrent(torrent, delete_data=True)

    rmtree.assert_not_called()
    assert rmdirs == []
    assert (completed_dir / "leftover.mkv").exists()


def test_exact_save_dirs_for_title_returns_creation_paths(torrent_roots) -> None:
    title = "Owned.Release"
    torrent = Torrent(
        status=TorrentStatus.finished,
        title=title,
        quality=Quality.unknown,
        hash="abc",
        usenet=False,
    )
    assert {p.resolve() for p in exact_save_dirs_for_title(torrent.title)} == {
        (torrent_roots.completed / title).resolve(),
        (torrent_roots.incomplete / title).resolve(),
    }


def test_unsafe_torrent_title_handler_returns_stable_422() -> None:
    async def _run() -> None:
        response = await unsafe_torrent_title_error_handler(
            None,
            UnsafeTorrentTitleError(
                "Torrent title ../secret cannot be contained under /data/torrents"
            ),
        )
        assert response.status_code == 422
        assert response.body == (
            b'{"detail":"Torrent title is not a safe filesystem path component"}'
        )

    asyncio.run(_run())


@contextmanager
def download_api_client(*, indexer_result: IndexerQueryResult):
    from miramedia.auth.users import current_active_user, current_superuser
    from miramedia.database import get_session
    from miramedia.indexers.dependencies import get_indexer_service
    from miramedia.main import app
    from miramedia.movies.dependencies import get_movie_repository, get_movie_service
    from miramedia.shows.dependencies import get_show_repository, get_show_service
    from miramedia.torrents.dependencies import get_torrent_service

    movie_id = uuid.uuid4()
    result_id = indexer_result.id

    async def _session():
        yield None

    async def _active_user():
        user = MagicMock()
        user.id = uuid.uuid4()
        user.is_superuser = True
        user.is_active = True
        user.is_verified = True
        return user

    async def _superuser():
        return await _active_user()

    indexer_service = MagicMock()
    indexer_service.get_result = AsyncMock(return_value=indexer_result)

    show_repo = MagicMock()
    movie_repo = MagicMock()
    show_service = MagicMock()
    movie_service = MagicMock()

    torrent_service = MagicMock()
    torrent_service.download_and_link = AsyncMock(
        side_effect=UnsafeTorrentTitleError(
            "Torrent title ../secret cannot be contained under /data/torrents"
        )
    )

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[current_active_user] = _active_user
    app.dependency_overrides[current_superuser] = _superuser
    app.dependency_overrides[get_indexer_service] = lambda: indexer_service
    app.dependency_overrides[get_show_repository] = lambda: show_repo
    app.dependency_overrides[get_movie_repository] = lambda: movie_repo
    app.dependency_overrides[get_show_service] = lambda: show_service
    app.dependency_overrides[get_movie_service] = lambda: movie_service
    app.dependency_overrides[get_torrent_service] = lambda: torrent_service
    try:
        client = TestClient(app, raise_server_exceptions=False)
        yield client, result_id, movie_id
    finally:
        app.dependency_overrides.clear()


def test_download_api_rejects_unsafe_title_with_stable_response(tmp_path) -> None:
    _ = tmp_path
    malicious = _indexer("../victim")

    with download_api_client(indexer_result=malicious) as (client, result_id, movie_id):
        response = client.post(
            DOWNLOAD_ROUTE,
            json={
                "indexer_result_id": str(result_id),
                "media_type": MediaType.movie.value,
                "media_id": str(movie_id),
            },
        )

    assert response.status_code == 422
    assert response.json() == {"detail": UNSAFE_TORRENT_TITLE_API_DETAIL}
    assert "/data" not in response.text
    assert "victim" not in response.text
