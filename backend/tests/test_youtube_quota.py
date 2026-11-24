"""YouTube Data API quota buckets (https://developers.google.com/youtube/data/determine_quota_cost):
videos.insert and search.list have their own 100/day buckets (1 unit per call); all other methods share
10,000 units/day; everything resets at midnight Pacific Time."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.core.config import get_settings
from app.core.errors import QuotaExceeded
from app.integrations import quota

# a day no other test touches (the test database is shared)
NOW = datetime(2031, 1, 7, 18, 0, tzinfo=UTC)


@pytest.fixture()
def defaults(monkeypatch) -> None:
    s = get_settings()
    for key, value in (("youtube_daily_upload_limit", 100), ("youtube_daily_search_limit", 100),
                       ("youtube_daily_quota", 10_000)):
        monkeypatch.setattr(s, key, value)


def _use(db, bucket: str, units: int, scope: str = "acc-1") -> None:
    quota.record_usage(db, bucket, scope, units, day=quota.quota_day(bucket, NOW))
    db.commit()


def test_costs_and_limits_follow_the_current_documentation(defaults) -> None:
    assert quota.COST_INSERT == 1 and quota.COST_SEARCH == 1 and quota.COST_LIST == 1
    assert quota.youtube_limit(quota.YOUTUBE_UPLOADS) == 100
    assert quota.youtube_limit(quota.YOUTUBE_SEARCH) == 100
    assert quota.youtube_limit(quota.YOUTUBE) == 10_000
    assert not hasattr(get_settings(), "youtube_upload_quota_cost")  # the old 1,600-unit cost is gone


def test_reset_is_midnight_pacific_including_daylight_saving() -> None:
    # 23:59 PST on Jan 6 -> still the Jan 6 quota day, resets 08:00 UTC
    winter = datetime(2031, 1, 7, 7, 59, tzinfo=UTC)
    assert str(quota.quota_day(quota.YOUTUBE_UPLOADS, winter)) == "2031-01-06"
    assert quota.next_reset(quota.YOUTUBE_UPLOADS, winter) == datetime(2031, 1, 7, 8, 0, tzinfo=UTC)
    # summer (PDT, UTC-7): resets 07:00 UTC; every YouTube bucket shares the same day
    summer = datetime(2031, 7, 1, 6, 59, tzinfo=UTC)
    for bucket in quota.YOUTUBE_BUCKETS:
        assert str(quota.quota_day(bucket, summer)) == "2031-06-30"
        assert quota.next_reset(bucket, summer) == datetime(2031, 7, 1, 7, 0, tzinfo=UTC)
    assert str(quota.quota_day(quota.INSTAGRAM, summer)) == "2031-07-01"  # Instagram stays on UTC days


def test_buckets_are_independent(db, defaults) -> None:
    _use(db, quota.YOUTUBE, 10_000)  # the 10,000-unit bucket is completely used
    quota.ensure_youtube_upload_quota(db, now=NOW)  # uploads still allowed
    quota.ensure_youtube_search_quota(db, now=NOW)  # searches too
    with pytest.raises(QuotaExceeded):
        quota.ensure_youtube_quota(db, 1, now=NOW)


def test_upload_bucket_allows_exactly_100_uploads(db, defaults) -> None:
    _use(db, quota.YOUTUBE_UPLOADS, 60, "acc-1")
    _use(db, quota.YOUTUBE_UPLOADS, 39, "acc-2")  # project-wide: every connected account counts
    quota.ensure_youtube_upload_quota(db, now=NOW)  # the 100th upload fits
    _use(db, quota.YOUTUBE_UPLOADS, 1, "acc-2")
    with pytest.raises(QuotaExceeded) as exc:
        quota.ensure_youtube_upload_quota(db, now=NOW)
    details = exc.value.details
    assert details["used"] == 100 and details["limit"] == 100 and details["provider"] == quota.YOUTUBE_UPLOADS
    assert details["retry_at"] == quota.next_reset(quota.YOUTUBE_UPLOADS, NOW).isoformat()
    assert "100/100 uploads" in exc.value.message and "midnight Pacific" in exc.value.message
    status = quota.youtube_status(db, now=NOW)
    assert status["uploads"] == {"used": 100, "limit": 100, "remaining": 0}
    assert status["resets_at"] == quota.next_reset(quota.YOUTUBE, NOW)


def test_status_reports_each_bucket_and_mark_exhausted(db, defaults, monkeypatch) -> None:
    now = datetime(2031, 2, 3, 18, 0, tzinfo=UTC)
    quota.record_usage(db, quota.YOUTUBE_UPLOADS, "a", 3, day=quota.quota_day(quota.YOUTUBE_UPLOADS, now))
    quota.record_usage(db, quota.YOUTUBE, "a", 42, day=quota.quota_day(quota.YOUTUBE, now))
    db.commit()
    st = quota.youtube_status(db, now=now)
    assert st["uploads"] == {"used": 3, "limit": 100, "remaining": 97}
    assert st["search"] == {"used": 0, "limit": 100, "remaining": 100}
    assert st["units"] == {"used": 42, "limit": 10_000, "remaining": 9_958}
    quota.mark_exhausted(db, quota.YOUTUBE_UPLOADS, "a", now=now)  # YouTube said quotaExceeded
    db.commit()
    assert quota.youtube_status(db, now=now)["uploads"]["remaining"] == 0
    assert quota.youtube_status(db, now=now)["units"]["remaining"] == 9_958  # other buckets untouched
    # the next Pacific day starts from zero
    tomorrow = datetime(2031, 2, 4, 18, 0, tzinfo=UTC)
    assert quota.youtube_status(db, now=tomorrow)["uploads"]["remaining"] == 100


def test_dashboard_shows_the_upload_allowance(client, user) -> None:
    from tests.test_upload_runs_factories import headers

    data = client.get("/api/v1/dashboard/summary", headers=headers(user)).json()
    q = data["youtube_quota"]
    assert set(q) == {"uploads", "search", "units", "resets_at"}
    assert q["uploads"]["limit"] == get_settings().youtube_daily_upload_limit
    assert q["uploads"]["remaining"] == max(q["uploads"]["limit"] - q["uploads"]["used"], 0)


def test_dashboard_says_when_publishing_is_paused(client, db, user) -> None:
    from tests.test_upload_runs_factories import headers

    h = headers(user)
    assert client.get("/api/v1/dashboard/summary", headers=h).json()["publishing_paused"] is False
    assert client.post("/api/v1/publishing/pause", headers=h).json() == {"paused": True}
    data = client.get("/api/v1/dashboard/summary", headers=h).json()
    assert data["publishing_paused"] is True and data["next_publish_at"] is None
    assert client.post("/api/v1/publishing/resume", headers=h).json() == {"paused": False}
