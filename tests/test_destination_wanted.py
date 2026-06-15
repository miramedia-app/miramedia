"""Unit tests for TorrentService._is_destination_wanted.

These tests drive the method with SimpleNamespace stubs so no DB or
async fixture machinery is needed. ``TorrentService.__new__`` bypasses
``__init__`` following the pattern established in test_auth_logging.py.
"""

import asyncio
import types
import uuid

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_service():
    """Return a TorrentService instance without calling __init__."""
    from miramedia.torrents.service import TorrentService

    return TorrentService.__new__(TorrentService)


def _make_indexer_result(seasons: list[int], episodes: list[int] | None = None):
    """Return a minimal stub that satisfies _is_destination_wanted."""
    return types.SimpleNamespace(
        title="Test Show S01E01",
        season=seasons,
        episode=episodes or [],
    )


def _make_show(skipped: bool = False, seasons: list | None = None):
    """Return a stub show with the given skipped flag and seasons."""
    return types.SimpleNamespace(
        skipped=skipped,
        seasons=seasons or [],
    )


def _make_season(number: int, skipped: bool = False, episodes: list | None = None):
    """Return a stub season."""
    return types.SimpleNamespace(
        number=number,
        skipped=skipped,
        episodes=episodes or [],
    )


def _make_episode(number: int, skipped: bool = False):
    """Return a stub episode."""
    return types.SimpleNamespace(number=number, skipped=skipped)


def _stub_show_repo(show):
    """Return a stub show repository whose get_show_by_id returns *show*."""

    async def get_show_by_id(show_id):  # noqa: ARG001
        return show

    return types.SimpleNamespace(get_show_by_id=get_show_by_id)


def _call(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAllSeasonsSkipped:
    """When every targeted season is skipped the destination is not wanted."""

    def test_single_season_skipped(self):
        svc = _make_service()
        from miramedia.torrents.schemas import MediaType

        skipped_season = _make_season(1, skipped=True)
        show = _make_show(seasons=[skipped_season])
        result = _call(
            svc._is_destination_wanted(
                indexer_result=_make_indexer_result(seasons=[1]),
                media_type=MediaType.show,
                media_id=uuid.uuid4(),
                show_repository=_stub_show_repo(show),
            )
        )
        wanted, sbn = result
        assert wanted is False
        assert 1 in sbn

    def test_multi_season_all_skipped(self):
        svc = _make_service()
        from miramedia.torrents.schemas import MediaType

        seasons = [_make_season(i, skipped=True) for i in range(1, 4)]
        show = _make_show(seasons=seasons)
        wanted, _ = _call(
            svc._is_destination_wanted(
                indexer_result=_make_indexer_result(seasons=[1, 2, 3]),
                media_type=MediaType.show,
                media_id=uuid.uuid4(),
                show_repository=_stub_show_repo(show),
            )
        )
        assert wanted is False


class TestOneSeasonWanted:
    """When at least one targeted season is not skipped the destination is wanted."""

    def test_one_wanted_among_skipped(self):
        svc = _make_service()
        from miramedia.torrents.schemas import MediaType

        seasons = [
            _make_season(1, skipped=True),
            _make_season(2, skipped=False),
        ]
        show = _make_show(seasons=seasons)
        wanted, sbn = _call(
            svc._is_destination_wanted(
                indexer_result=_make_indexer_result(seasons=[1, 2]),
                media_type=MediaType.show,
                media_id=uuid.uuid4(),
                show_repository=_stub_show_repo(show),
            )
        )
        assert wanted is True
        assert len(sbn) == 2

    def test_show_not_skipped_no_target_seasons(self):
        """No target seasons → wanted (season-pack fall-through)."""
        svc = _make_service()
        from miramedia.torrents.schemas import MediaType

        show = _make_show(seasons=[_make_season(1, skipped=True)])
        wanted, _ = _call(
            svc._is_destination_wanted(
                indexer_result=_make_indexer_result(seasons=[]),
                media_type=MediaType.show,
                media_id=uuid.uuid4(),
                show_repository=_stub_show_repo(show),
            )
        )
        assert wanted is True


class TestMissingSeasonPermissive:
    """A season not in the loaded show is treated as wanted (permissive)."""

    def test_missing_season_wanted(self):
        svc = _make_service()
        from miramedia.torrents.schemas import MediaType

        # Show has no seasons loaded (e.g. not yet scraped)
        show = _make_show(seasons=[])
        wanted, sbn = _call(
            svc._is_destination_wanted(
                indexer_result=_make_indexer_result(seasons=[5]),
                media_type=MediaType.show,
                media_id=uuid.uuid4(),
                show_repository=_stub_show_repo(show),
            )
        )
        assert wanted is True
        assert sbn == {}

    def test_missing_season_in_episode_check_permissive(self):
        """Episode-level check: missing season → any_episode_wanted = True."""
        svc = _make_service()
        from miramedia.torrents.schemas import MediaType

        show = _make_show(seasons=[])
        wanted, _ = _call(
            svc._is_destination_wanted(
                indexer_result=_make_indexer_result(seasons=[3], episodes=[1, 2]),
                media_type=MediaType.show,
                media_id=uuid.uuid4(),
                show_repository=_stub_show_repo(show),
            )
        )
        assert wanted is True


class TestEpisodeLevelSkip:
    """Episode-level skip filtering."""

    def test_all_targeted_episodes_skipped(self):
        svc = _make_service()
        from miramedia.torrents.schemas import MediaType

        eps = [_make_episode(1, skipped=True), _make_episode(2, skipped=True)]
        season = _make_season(1, episodes=eps)
        show = _make_show(seasons=[season])
        wanted, _ = _call(
            svc._is_destination_wanted(
                indexer_result=_make_indexer_result(seasons=[1], episodes=[1, 2]),
                media_type=MediaType.show,
                media_id=uuid.uuid4(),
                show_repository=_stub_show_repo(show),
            )
        )
        # Both targeted episodes are skipped → not wanted
        assert wanted is False

    def test_one_targeted_episode_wanted(self):
        svc = _make_service()
        from miramedia.torrents.schemas import MediaType

        eps = [_make_episode(1, skipped=True), _make_episode(2, skipped=False)]
        season = _make_season(1, episodes=eps)
        show = _make_show(seasons=[season])
        wanted, _ = _call(
            svc._is_destination_wanted(
                indexer_result=_make_indexer_result(seasons=[1], episodes=[1, 2]),
                media_type=MediaType.show,
                media_id=uuid.uuid4(),
                show_repository=_stub_show_repo(show),
            )
        )
        assert wanted is True

    def test_targeted_episode_not_in_season(self):
        """Episode not in season.episodes is skipped silently (continue)."""
        svc = _make_service()
        from miramedia.torrents.schemas import MediaType

        # Season exists but has NO episodes matching the requested ep numbers
        season = _make_season(1, episodes=[_make_episode(99)])
        show = _make_show(seasons=[season])
        wanted, _ = _call(
            svc._is_destination_wanted(
                indexer_result=_make_indexer_result(seasons=[1], episodes=[1, 2, 3]),
                media_type=MediaType.show,
                media_id=uuid.uuid4(),
                show_repository=_stub_show_repo(show),
            )
        )
        # None of the targeted episodes match → any_episode_wanted stays False
        assert wanted is False

    def test_no_targeted_episodes_returns_wanted(self):
        """Season-pack with episodes=[] → wanted after season check."""
        svc = _make_service()
        from miramedia.torrents.schemas import MediaType

        season = _make_season(1, skipped=False)
        show = _make_show(seasons=[season])
        wanted, _ = _call(
            svc._is_destination_wanted(
                indexer_result=_make_indexer_result(seasons=[1], episodes=[]),
                media_type=MediaType.show,
                media_id=uuid.uuid4(),
                show_repository=_stub_show_repo(show),
            )
        )
        assert wanted is True


class TestShowSkipped:
    """When the show itself is skipped the destination is not wanted."""

    def test_show_skipped(self):
        svc = _make_service()
        from miramedia.torrents.schemas import MediaType

        show = _make_show(skipped=True, seasons=[_make_season(1)])
        wanted, sbn = _call(
            svc._is_destination_wanted(
                indexer_result=_make_indexer_result(seasons=[1]),
                media_type=MediaType.show,
                media_id=uuid.uuid4(),
                show_repository=_stub_show_repo(show),
            )
        )
        assert wanted is False
        # seasons_by_number is None when we bail early on show-level skip
        assert sbn is None


class TestSeasonsMapReturnedOnSuccess:
    """seasons_by_number is populated and returned when the show is loaded."""

    def test_map_keys(self):
        svc = _make_service()
        from miramedia.torrents.schemas import MediaType

        seasons = [_make_season(i) for i in [1, 2, 3]]
        show = _make_show(seasons=seasons)
        _, sbn = _call(
            svc._is_destination_wanted(
                indexer_result=_make_indexer_result(seasons=[1]),
                media_type=MediaType.show,
                media_id=uuid.uuid4(),
                show_repository=_stub_show_repo(show),
            )
        )
        assert set(sbn.keys()) == {1, 2, 3}
