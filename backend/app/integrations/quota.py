"""Per-day API quota accounting in ``api_quota_usage``.

YouTube Data API quota belongs to the Google Cloud *project* and resets at midnight Pacific Time. Since June 2026
it is split into buckets (https://developers.google.com/youtube/data/determine_quota_cost):

* ``youtube_uploads`` -- ``videos.insert``: its own bucket, 1 unit per call, default 100 calls/day;
* ``youtube_search`` -- ``search.list``: its own bucket, 1 unit per call, default 100 calls/day;
* ``youtube`` -- every other method (``videos.list``, ``channels.list``, ...): default 10,000 units/day.

Usage is recorded per connected account (``scope_key`` = account id) and checked against the project-wide total
of the bucket for the Pacific day. An upload is only checked against the uploads bucket, never against the
10,000-unit bucket. These counters are ClipFusion's own estimate: YouTube's answer wins -- a real quota error
marks the bucket as used up until the reset (see :func:`mark_exhausted`).
Instagram publishes are counted per account per UTC day (Meta's own rolling 24 h limit is checked separately
through ``content_publishing_limit``).
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import utcnow
from app.core.errors import QuotaExceeded
from app.models import ApiQuotaUsage

log = logging.getLogger(__name__)

YOUTUBE = "youtube"  # all other YouTube Data API methods (units)
YOUTUBE_UPLOADS = "youtube_uploads"  # videos.insert (own bucket)
YOUTUBE_SEARCH = "youtube_search"  # search.list (own bucket)
YOUTUBE_BUCKETS = (YOUTUBE_UPLOADS, YOUTUBE_SEARCH, YOUTUBE)
INSTAGRAM = "instagram"
PACIFIC = ZoneInfo("America/Los_Angeles")

# YouTube Data API v3 costs (units, each in its own bucket) of the calls this app makes.
COST_LIST = 1  # videos.list / channels.list / playlistItems.list -> YOUTUBE
COST_INSERT = 1  # videos.insert -> YOUTUBE_UPLOADS
COST_SEARCH = 1  # search.list -> YOUTUBE_SEARCH

_BUCKET_LABEL = {YOUTUBE_UPLOADS: "video upload", YOUTUBE_SEARCH: "search", YOUTUBE: "API"}


def _tz(provider: str) -> ZoneInfo | type[UTC]:
    return PACIFIC if provider.startswith(YOUTUBE) else UTC


def quota_day(provider: str, now: datetime | None = None) -> date:
    """The quota bucket (calendar day) ``now`` falls into for ``provider``."""
    now = now or utcnow()
    return now.astimezone(_tz(provider)).date()


def next_reset(provider: str, now: datetime | None = None) -> datetime:
    """UTC instant when the current quota day of ``provider`` ends."""
    day = quota_day(provider, now)
    tz = _tz(provider)
    local_midnight = datetime.combine(day + timedelta(days=1), time(0, 0), tzinfo=tz)
    return local_midnight.astimezone(UTC)


def get_usage(db: Session, provider: str, scope_key: str | None = None, day: date | None = None) -> int:
    """Units used on ``day``; ``scope_key=None`` sums every scope (project-wide)."""
    day = day or quota_day(provider)
    stmt = select(func.coalesce(func.sum(ApiQuotaUsage.units_used), 0)).where(
        ApiQuotaUsage.provider == provider, ApiQuotaUsage.day == day)
    if scope_key is not None:
        stmt = stmt.where(ApiQuotaUsage.scope_key == scope_key)
    return int(db.scalar(stmt) or 0)


def record_usage(db: Session, provider: str, scope_key: str, units: int, *, day: date | None = None) -> None:
    """Atomically add ``units`` to the bucket (flushes; the caller commits)."""
    if units <= 0:
        return
    day = day or quota_day(provider)
    where = (ApiQuotaUsage.provider == provider, ApiQuotaUsage.scope_key == scope_key, ApiQuotaUsage.day == day)
    for _ in range(2):
        res = db.execute(update(ApiQuotaUsage).where(*where)
                         .values(units_used=ApiQuotaUsage.units_used + units, updated_at=utcnow())
                         .execution_options(synchronize_session=False))
        if res.rowcount:
            return
        try:
            with db.begin_nested():
                db.add(ApiQuotaUsage(provider=provider, scope_key=scope_key, day=day, units_used=units))
            return
        except IntegrityError:  # concurrent insert of the same bucket: retry the update
            continue
    log.warning("Could not record %s quota usage for %s", provider, scope_key)


def youtube_limit(bucket: str) -> int:
    """Daily limit of a YouTube quota bucket (calls for uploads/search, units for everything else)."""
    s = get_settings()
    return {YOUTUBE_UPLOADS: s.youtube_daily_upload_limit, YOUTUBE_SEARCH: s.youtube_daily_search_limit}.get(
        bucket, s.youtube_daily_quota)


def ensure_bucket(db: Session, bucket: str, units: int = 1, *, now: datetime | None = None) -> None:
    """Raise :class:`QuotaExceeded` when today's project-wide usage of ``bucket`` + ``units`` exceeds its daily
    limit. ``details.retry_at`` is the next quota reset (UTC ISO)."""
    limit = youtube_limit(bucket)
    used = get_usage(db, bucket, None, quota_day(bucket, now))
    if used + units > limit:
        reset = next_reset(bucket, now)
        what = _BUCKET_LABEL.get(bucket, bucket)
        unit = "units" if bucket == YOUTUBE else ("uploads" if bucket == YOUTUBE_UPLOADS else "requests")
        raise QuotaExceeded(
            f"YouTube's daily {what} quota is used up ({used}/{limit} {unit} today). It resets at midnight "
            "Pacific Time; ask for more in Google Cloud Console (YouTube API Services quota extension) to "
            "do more per day.",
            details={"provider": bucket, "used": used, "requested": units, "limit": limit,
                     "retry_at": reset.isoformat()})


def ensure_youtube_upload_quota(db: Session, *, now: datetime | None = None) -> None:
    """One more ``videos.insert`` fits in today's upload bucket (else :class:`QuotaExceeded`)."""
    ensure_bucket(db, YOUTUBE_UPLOADS, COST_INSERT, now=now)


def ensure_youtube_search_quota(db: Session, *, now: datetime | None = None) -> None:
    """One more ``search.list`` fits in today's search bucket (else :class:`QuotaExceeded`)."""
    ensure_bucket(db, YOUTUBE_SEARCH, COST_SEARCH, now=now)


def ensure_youtube_quota(db: Session, units: int, *, now: datetime | None = None) -> None:
    """``units`` more fit in today's bucket for all other methods (10,000 units/day by default)."""
    ensure_bucket(db, YOUTUBE, units, now=now)


def mark_exhausted(db: Session, bucket: str, scope_key: str, *, now: datetime | None = None) -> None:
    """YouTube answered with a quota error: count the bucket as used up until the next reset, so nothing else is
    attempted (and the dashboard shows 0 left) until then. Flushes; the caller commits."""
    day = quota_day(bucket, now)
    missing = youtube_limit(bucket) - get_usage(db, bucket, None, day)
    if missing > 0:
        record_usage(db, bucket, scope_key, missing, day=day)


def youtube_status(db: Session, *, now: datetime | None = None) -> dict[str, Any]:
    """Today's usage of each YouTube bucket (project-wide) and when they reset."""
    out: dict[str, Any] = {"resets_at": next_reset(YOUTUBE, now)}
    for key, bucket in (("uploads", YOUTUBE_UPLOADS), ("search", YOUTUBE_SEARCH), ("units", YOUTUBE)):
        used = get_usage(db, bucket, None, quota_day(bucket, now))
        limit = youtube_limit(bucket)
        out[key] = {"used": used, "limit": limit, "remaining": max(limit - used, 0)}
    return out


def ensure_instagram_daily_limit(db: Session, account_id: str, *, now: datetime | None = None) -> None:
    """Enforce ``INSTAGRAM_DAILY_PUBLISH_LIMIT`` per account per UTC day."""
    limit = get_settings().instagram_daily_publish_limit
    used = get_usage(db, INSTAGRAM, account_id, quota_day(INSTAGRAM, now))
    if used >= limit:
        reset = next_reset(INSTAGRAM, now)
        raise QuotaExceeded(
            f"Instagram daily publishing limit reached for this account ({used}/{limit}).",
            details={"provider": INSTAGRAM, "used": used, "limit": limit, "retry_at": reset.isoformat()})
