"""Pure publishing-slot computation (no database).

Slots are generated on the schedule's local wall clock (IANA timezone via ``zoneinfo``):

* ``interval``: every ``interval_minutes`` (60/120/180/360 or custom >= 15) on a daily
  pattern anchored at ``window_start`` (or the local time of ``start_at``, else midnight),
  optionally restricted to the daily window ``[window_start, window_end]``.
* ``times``: the listed ``times_of_day`` ("HH:MM").

Rules: only ``days_of_week`` (Mon=0); DST-safe (local times that do not exist on a
spring-forward day are skipped; ambiguous fall-back times fire once, on their first
occurrence); ``daily_limit`` per local day counts already-taken slots; never at or before
``after`` / ``start_at``; at least 1 minute away from any taken slot.
"""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MIN_INTERVAL_MINUTES = 15
MIN_SPACING = timedelta(minutes=1)
MAX_HORIZON_DAYS = 800
ALL_DAYS = (0, 1, 2, 3, 4, 5, 6)


@dataclass(frozen=True)
class SlotParams:
    frequency_type: str = "interval"
    timezone: str = "UTC"
    interval_minutes: int | None = None
    times_of_day: tuple[str, ...] = ()
    days_of_week: tuple[int, ...] = ALL_DAYS
    daily_limit: int = 0  # 0 = unlimited
    window_start: str | None = None
    window_end: str | None = None
    start_at: datetime | None = None

    @classmethod
    def from_schedule(cls, schedule: object) -> SlotParams:
        """Build params from a ``PublishingSchedule``-like object."""
        return cls(frequency_type=getattr(schedule, "frequency_type", "interval"),
                   timezone=getattr(schedule, "timezone", "UTC") or "UTC",
                   interval_minutes=getattr(schedule, "interval_minutes", None),
                   times_of_day=tuple(getattr(schedule, "times_of_day", None) or ()),
                   days_of_week=tuple(getattr(schedule, "days_of_week", None) or ALL_DAYS),
                   daily_limit=int(getattr(schedule, "daily_limit", 0) or 0),
                   window_start=getattr(schedule, "window_start", None),
                   window_end=getattr(schedule, "window_end", None),
                   start_at=ensure_utc(getattr(schedule, "start_at", None)))


def ensure_utc(value: datetime | None) -> datetime | None:
    """Treat naive datetimes as UTC and convert aware ones to UTC."""
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def parse_hhmm(value: str) -> int:
    """'HH:MM' -> minutes since midnight. Raises ValueError when malformed."""
    hh, mm = value.strip().split(":")
    h, m = int(hh), int(mm)
    if not (0 <= h <= 23 and 0 <= m <= 59) or len(mm) != 2:
        raise ValueError(f"Invalid time of day: {value!r}")
    return h * 60 + m


def get_zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"Unknown timezone: {name!r}") from exc


def to_utc_if_exists(day: date, minute: int, tz: ZoneInfo) -> datetime | None:
    """Local wall time -> UTC, or None when it does not exist (DST gap). Ambiguous -> fold=0."""
    local = datetime.combine(day, time(minute // 60, minute % 60), tzinfo=tz)
    utc = local.astimezone(UTC)
    back = utc.astimezone(tz)
    if (back.hour, back.minute) != (local.hour, local.minute):
        return None
    return utc


def _day_minutes(params: SlotParams) -> list[int]:
    """Minutes-of-day at which slots occur (before day/limit filtering)."""
    if params.frequency_type == "times":
        return sorted({parse_hhmm(t) for t in params.times_of_day})
    interval = int(params.interval_minutes or 60)
    if interval < MIN_INTERVAL_MINUTES:
        raise ValueError(f"interval_minutes must be >= {MIN_INTERVAL_MINUTES}")
    lo = parse_hhmm(params.window_start) if params.window_start else 0
    hi = parse_hhmm(params.window_end) if params.window_end else 24 * 60 - 1
    if params.window_start:
        anchor = lo
    elif params.start_at is not None:
        local = params.start_at.astimezone(get_zone(params.timezone))
        anchor = local.hour * 60 + local.minute
    else:
        anchor = 0
    first = anchor % interval
    while first < lo:
        first += interval
    return list(range(first, hi + 1, interval))


def _too_close(slot: datetime, taken_sorted: list[datetime]) -> bool:
    i = bisect_left(taken_sorted, slot)
    return any(abs(slot - taken_sorted[j]) < MIN_SPACING for j in (i - 1, i) if 0 <= j < len(taken_sorted))


def next_slots(params: SlotParams, after: datetime, count: int, taken: Iterable[datetime] = ()) -> list[datetime]:
    """Return up to ``count`` future slot instants (UTC, ascending) strictly after ``after``.

    ``taken`` are instants already used by the same account (any schedule); they count
    toward ``daily_limit`` of their local day and block slots within 1 minute."""
    if count <= 0:
        return []
    tz = get_zone(params.timezone)
    minutes = _day_minutes(params)
    if not minutes:
        return []
    days = set(params.days_of_week or ALL_DAYS)
    if after.tzinfo is None:
        after = after.replace(tzinfo=UTC)
    lower = after
    if params.start_at is not None and params.start_at > lower:
        lower = params.start_at - timedelta(microseconds=1)
    taken_sorted = sorted(t if t.tzinfo else t.replace(tzinfo=UTC) for t in taken)
    per_day: dict[date, int] = {}
    for t in taken_sorted:
        d = t.astimezone(tz).date()
        per_day[d] = per_day.get(d, 0) + 1

    out: list[datetime] = []
    day = lower.astimezone(tz).date()
    for _ in range(MAX_HORIZON_DAYS):
        if day.weekday() in days:
            for minute in minutes:
                if params.daily_limit and per_day.get(day, 0) >= params.daily_limit:
                    break
                slot = to_utc_if_exists(day, minute, tz)
                if slot is None or slot <= lower:
                    continue
                if _too_close(slot, taken_sorted):
                    continue
                out.append(slot)
                per_day[day] = per_day.get(day, 0) + 1
                if len(out) >= count:
                    return out
        day += timedelta(days=1)
    return out


def describe_local(slot: datetime, timezone: str) -> str:
    """Human-readable local time, e.g. 'Mon 2026-03-09 09:00 EDT'."""
    local = slot.astimezone(get_zone(timezone))
    return local.strftime("%a %Y-%m-%d %H:%M %Z")
