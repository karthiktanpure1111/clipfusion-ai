"""Per-day API quota accounting in ``api_quota_usage``.

YouTube Data API quota belongs to the Google Cloud *project* (default 10,000 units/day)
and resets at midnight Pacific Time, so usage is recorded per connected account
(``scope_key`` = account id) but checked against the project-wide total for the day.
Instagram publishes are counted per account per UTC day (Meta's own rolling 24 h limit is
checked separately through ``content_publishing_limit``).
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import utcnow
from app.core.errors import QuotaExceeded
from app.models import ApiQuotaUsage

log = logging.getLogger(__name__)

YOUTUBE = "youtube"
INSTAGRAM = "instagram"
PACIFIC = ZoneInfo("America/Los_Angeles")

# YouTube Data API v3 costs (units) of the calls this app makes.
COST_LIST = 1


def _tz(provider: str) -> ZoneInfo | type[UTC]:
    return PACIFIC if provider == YOUTUBE else UTC


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


def ensure_youtube_quota(db: Session, units: int, *, now: datetime | None = None) -> None:
    """Raise :class:`QuotaExceeded` when today's project-wide usage + ``units`` exceeds
    ``YOUTUBE_DAILY_QUOTA``. ``details.retry_at`` is the next quota reset (UTC ISO)."""
    limit = get_settings().youtube_daily_quota
    used = get_usage(db, YOUTUBE, None, quota_day(YOUTUBE, now))
    if used + units > limit:
        reset = next_reset(YOUTUBE, now)
        raise QuotaExceeded(
            f"YouTube API daily quota would be exceeded ({used} + {units} > {limit} units). "
            "The quota resets at midnight Pacific Time; request a quota extension in Google Cloud "
            "Console to publish more per day.",
            details={"provider": YOUTUBE, "used": used, "requested": units, "limit": limit,
                     "retry_at": reset.isoformat()})


def ensure_instagram_daily_limit(db: Session, account_id: str, *, now: datetime | None = None) -> None:
    """Enforce ``INSTAGRAM_DAILY_PUBLISH_LIMIT`` per account per UTC day."""
    limit = get_settings().instagram_daily_publish_limit
    used = get_usage(db, INSTAGRAM, account_id, quota_day(INSTAGRAM, now))
    if used >= limit:
        reset = next_reset(INSTAGRAM, now)
        raise QuotaExceeded(
            f"Instagram daily publishing limit reached for this account ({used}/{limit}).",
            details={"provider": INSTAGRAM, "used": used, "limit": limit, "retry_at": reset.isoformat()})
