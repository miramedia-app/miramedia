"""Air-date parsing: Cinemeta UTC datetimes must land on the local calendar day.

Regression for the calendar off-by-one where an HBO episode airing Aug 9 (US
evening) was stored/shown as Aug 10 — Cinemeta's ``released`` is a UTC datetime
(``2026-08-10T01:00:00.000Z``) and truncating it to the UTC date rolled the day
forward. ``parse_iso_date`` now converts datetimes to the server-local zone first.
"""

import time
from contextlib import contextmanager
from datetime import date, datetime
from datetime import time as dt_time
from zoneinfo import ZoneInfo

import pytest

from miramedia.config import MiraMediaConfig
from miramedia.metadata.utils import parse_iso_date, parse_iso_time

# US-evening HBO air time expressed as Cinemeta does: UTC, one day ahead of ET.
CINEMETA_RELEASED = "2026-08-10T01:00:00.000Z"


@contextmanager
def local_timezone(tz: str):
    """Force process-local timezone for the duration of the block (Unix only)."""
    if not hasattr(time, "tzset"):  # pragma: no cover - Windows lacks tzset
        pytest.skip("time.tzset unavailable on this platform")
    import os

    previous = os.environ.get("TZ")
    os.environ["TZ"] = tz
    time.tzset()
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous
        time.tzset()


@contextmanager
def configured_timezone_setting(tz: str):
    """Override the DB-backed misc.timezone setting on the config singleton."""
    misc = MiraMediaConfig().misc
    previous = misc.timezone
    misc.timezone = tz
    try:
        yield
    finally:
        misc.timezone = previous


def test_cinemeta_utc_datetime_uses_local_calendar_day_behind_utc():
    with local_timezone("America/Denver"):  # UTC-6/-7: still Aug 9 locally
        assert parse_iso_date(CINEMETA_RELEASED) == date(2026, 8, 9)


def test_timezone_setting_overrides_process_zone():
    # Process zone is UTC+10 (would yield Aug 10), but the misc.timezone setting
    # pins New York (UTC-4/-5) — the setting wins, so the air date is Aug 9.
    with (
        local_timezone("Australia/Sydney"),
        configured_timezone_setting("America/New_York"),
    ):
        assert parse_iso_date(CINEMETA_RELEASED) == date(2026, 8, 9)


def test_blank_timezone_setting_falls_back_to_process_zone():
    with local_timezone("America/Denver"), configured_timezone_setting(""):
        assert parse_iso_date(CINEMETA_RELEASED) == date(2026, 8, 9)


def test_invalid_timezone_setting_falls_back_without_raising():
    with local_timezone("America/Denver"), configured_timezone_setting("bogus/zone"):
        assert parse_iso_date(CINEMETA_RELEASED) == date(2026, 8, 9)


def test_cinemeta_utc_datetime_uses_local_calendar_day_ahead_of_utc():
    with local_timezone("Australia/Sydney"):  # UTC+10: genuinely Aug 10 locally
        assert parse_iso_date(CINEMETA_RELEASED) == date(2026, 8, 10)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-08-09", date(2026, 8, 9)),  # TMDB/TVDB/TVMaze date-only, unchanged
        ("2026-08-09T12:00:00+00:00", date(2026, 8, 9)),  # explicit offset form
        (None, None),
        ("", None),
        ("not-a-date", None),
    ],
)
def test_parse_iso_date_edge_cases(value, expected):
    assert parse_iso_date(value) == expected


def test_parse_iso_time_converts_to_configured_zone():
    # 01:00 UTC → New York (UTC-4/-5) is the prior evening at 21:00.
    with (
        local_timezone("Australia/Sydney"),
        configured_timezone_setting("America/New_York"),
    ):
        assert parse_iso_time(CINEMETA_RELEASED) == dt_time(21, 0)


def test_parse_iso_time_result_depends_on_configured_timezone_at_parse_time():
    # Same UTC instant stored as different naive local times depending on
    # misc.timezone at ingest — the drift mechanism if the operator later
    # changes the setting without rewriting stored air_time values.
    utc_instant = datetime.fromisoformat(CINEMETA_RELEASED)
    expected_ny = utc_instant.astimezone(ZoneInfo("America/New_York")).time()
    expected_berlin = utc_instant.astimezone(ZoneInfo("Europe/Berlin")).time()
    assert expected_ny != expected_berlin

    with configured_timezone_setting("America/New_York"):
        ny = parse_iso_time(CINEMETA_RELEASED)
    with configured_timezone_setting("Europe/Berlin"):
        berlin = parse_iso_time(CINEMETA_RELEASED)

    assert ny == expected_ny
    assert berlin == expected_berlin
    assert ny != berlin


def test_parse_iso_time_dst_boundary_shifts_stored_time():
    # Same UTC clock time in January vs July under America/New_York: EST vs
    # EDT, so the stored naive local time differs by one hour. Documents that
    # per-airdate DST is applied at parse time — stored values are correct for
    # the air date. Drift is config-change-after-ingest, not DST itself.
    january_utc = "2026-01-10T01:00:00.000Z"
    july_utc = "2026-07-10T01:00:00.000Z"
    with configured_timezone_setting("America/New_York"):
        january_local = parse_iso_time(january_utc)
        july_local = parse_iso_time(july_utc)

    assert january_local is not None
    assert july_local is not None
    january_minutes = january_local.hour * 60 + january_local.minute
    july_minutes = july_local.hour * 60 + july_local.minute
    assert abs(july_minutes - january_minutes) == 60


@pytest.mark.parametrize(
    "value",
    ["2026-08-09", None, "", "not-a-date"],  # date-only / missing → no time to show
)
def test_parse_iso_time_returns_none_without_a_time(value):
    assert parse_iso_time(value) is None
