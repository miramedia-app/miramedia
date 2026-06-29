"""Regression: marking a Season 0 special "wanted" must stick.

Specials used to be force-skipped in ``ShowService._show_to_public`` whenever
``download_specials`` was off — a derived override that ignored the persisted
``skipped`` flag. So when a user marked an individual special wanted
(``skipped=False``), the next read re-skipped it. The override is gone; the
persisted ``skipped`` flag is now the single source of truth.

Pure transform — no DB.
"""

from uuid import uuid4

from miramedia.media_status import MediaStatus
from miramedia.shows.models import Episode, Season, Show
from miramedia.shows.service import ShowService


def _show_with_special(*, special_skipped: list[bool]) -> Show:
    episodes = [
        Episode(
            id=uuid4(),
            number=i,
            title=f"Special {i}",
            skipped=skipped,
            downloaded=False,
            episode_files=[],
        )
        for i, skipped in enumerate(special_skipped, start=1)
    ]
    season = Season(
        id=uuid4(),
        number=0,
        skipped=all(special_skipped),
        episodes=episodes,
    )
    return Show(
        id=uuid4(),
        name="Test Show",
        overview="",
        year=2020,
        external_id="tt0000000",
        metadata_provider="native",
        library="/data",
        ended=False,
        skipped=False,
        wanted_episode_count=0,
        downloaded_episode_count=0,
        seasons=[season],
    )


def _public(show: Show):
    service = ShowService.__new__(ShowService)  # no deps needed for the transform
    return service._show_to_public(show, disk_by_season={})


def test_wanted_special_is_not_masked_to_skipped():
    # One special wanted (skipped=False), one skipped — mirrors a user who
    # un-skipped a single special on a show where specials default to skipped.
    show = _show_with_special(special_skipped=[False, True])
    public = _public(show)
    statuses = [ep.status for ep in public.seasons[0].episodes]
    assert statuses[0] == MediaStatus.wanted
    assert statuses[1] == MediaStatus.skipped
    # Season rolls up to wanted because one episode is wanted.
    assert public.seasons[0].status == MediaStatus.wanted


def test_all_specials_skipped_stay_skipped():
    show = _show_with_special(special_skipped=[True, True])
    public = _public(show)
    assert all(ep.status == MediaStatus.skipped for ep in public.seasons[0].episodes)
    assert public.seasons[0].status == MediaStatus.skipped
