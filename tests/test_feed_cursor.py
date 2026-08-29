"""Tests for feed watermark advance/hold logic."""

from datetime import UTC, datetime, timedelta

import pytest

from miramedia.feeds.schemas import FeedEnvelope
from miramedia.feeds.service import FeedObserveService, _maxage_cutoffs
from miramedia.indexers.schemas import IndexerQueryResult

FIXED_NOW = datetime(2024, 6, 10, 12, 0, tzinfo=UTC)


def _envelope(
    pub_date: datetime | None,
    *,
    provider_guid: str | None = None,
) -> FeedEnvelope:
    return FeedEnvelope(
        result=IndexerQueryResult(
            title="item",
            download_url="magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567",
            seeders=1,
            flags=[],
            size=1,
            usenet=False,
            age=0,
            indexer="x",
        ),
        provider_guid=provider_guid,
        pub_date=pub_date,
    )


def _filter(
    envelopes: list[FeedEnvelope],
    watermark: datetime | None,
    maxage_days: int,
    *,
    monkeypatch: pytest.MonkeyPatch,
    now: datetime = FIXED_NOW,
) -> list[FeedEnvelope]:
    monkeypatch.setattr("miramedia.feeds.service._utc_now", lambda: now)
    return FeedObserveService._filter_by_maxage(envelopes, watermark, maxage_days)


def test_advance_watermark_on_newer_dates():
    current = datetime(2024, 1, 1, tzinfo=UTC)
    processed = [
        datetime(2024, 1, 2, tzinfo=UTC),
        datetime(2024, 1, 3, tzinfo=UTC),
    ]
    new = FeedObserveService._advance_watermark(current, processed, page_count=10)
    assert new == datetime(2024, 1, 3, tzinfo=UTC)


def test_hold_watermark_when_no_processed_dates():
    current = datetime(2024, 1, 1, tzinfo=UTC)
    new = FeedObserveService._advance_watermark(current, [], page_count=10)
    assert new == current


def test_maxage_filter_keeps_unseen_guid_within_window(monkeypatch):
    watermark = datetime(2024, 6, 1, tzinfo=UTC)
    old_date = watermark - timedelta(days=1)
    envelope = _envelope(old_date, provider_guid="late-guid")
    kept = _filter(
        [envelope],
        watermark,
        maxage_days=7,
        monkeypatch=monkeypatch,
        now=datetime(2024, 6, 7, tzinfo=UTC),
    )
    assert len(kept) == 1


@pytest.mark.parametrize(
    ("name", "watermark", "maxage_days", "pub_date", "provider_guid", "expected_kept"),
    [
        (
            "dated_inside_absolute_window_without_guid",
            None,
            7,
            FIXED_NOW - timedelta(days=3),
            None,
            True,
        ),
        (
            "dated_exactly_at_absolute_retention_cutoff",
            None,
            7,
            FIXED_NOW - timedelta(days=7),
            None,
            True,
        ),
        (
            "dated_outside_absolute_window_without_guid",
            None,
            7,
            FIXED_NOW - timedelta(days=7, seconds=1),
            None,
            False,
        ),
        (
            "dated_outside_absolute_window_with_guid",
            None,
            7,
            FIXED_NOW - timedelta(days=7, seconds=1),
            "late-guid",
            False,
        ),
        (
            "late_guid_inside_watermark_ordering_window",
            datetime(2024, 6, 8, tzinfo=UTC),
            7,
            datetime(2024, 6, 7, tzinfo=UTC),
            "late-guid",
            True,
        ),
        (
            "dated_inside_absolute_and_watermark_ordering_without_guid",
            datetime(2024, 6, 8, tzinfo=UTC),
            7,
            datetime(2024, 6, 5, tzinfo=UTC),
            None,
            True,
        ),
        (
            "dated_behind_watermark_ordering_without_guid",
            datetime(2024, 6, 8, tzinfo=UTC),
            7,
            datetime(2024, 5, 31, tzinfo=UTC),
            None,
            False,
        ),
        (
            "watermark_behind_now_guid_outside_absolute_rejected",
            datetime(2024, 6, 1, tzinfo=UTC),
            7,
            datetime(2024, 5, 31, tzinfo=UTC),
            "late-guid",
            False,
        ),
    ],
)
def test_maxage_filter_table(
    monkeypatch,
    name,
    watermark,
    maxage_days,
    pub_date,
    provider_guid,
    expected_kept,
):
    envelope = _envelope(pub_date, provider_guid=provider_guid)
    now = (
        datetime(2026, 8, 25, tzinfo=UTC)
        if name == "watermark_behind_now_guid_outside_absolute_rejected"
        else FIXED_NOW
    )
    kept = _filter([envelope], watermark, maxage_days, monkeypatch=monkeypatch, now=now)
    assert (len(kept) == 1) is expected_kept, name


def test_maxage_filter_keeps_undated_envelopes(monkeypatch):
    envelope = _envelope(None, provider_guid="undated-guid")
    kept = _filter(
        [envelope], datetime(2024, 6, 8, tzinfo=UTC), 7, monkeypatch=monkeypatch
    )
    assert kept == [envelope]


def test_maxage_filter_disabled_when_maxage_non_positive(monkeypatch):
    old_date = FIXED_NOW - timedelta(days=365)
    envelope = _envelope(old_date, provider_guid="old-guid")
    kept = _filter([envelope], None, 0, monkeypatch=monkeypatch)
    assert kept == [envelope]


def test_maxage_cutoffs_separate_absolute_and_watermark_bounds():
    watermark = datetime(2024, 6, 8, tzinfo=UTC)
    absolute, ordering = _maxage_cutoffs(FIXED_NOW, watermark, maxage_days=7)
    assert absolute == datetime(2024, 6, 3, 12, 0, tzinfo=UTC)
    assert ordering == datetime(2024, 6, 1, tzinfo=UTC)
