"""Pure slot computation: intervals, times, days, DST (America/New_York), limits, taken slots."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.modules.scheduling.slots import SlotParams, describe_local, next_slots

NY = ZoneInfo("America/New_York")


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


def test_interval_every_two_hours_anchored_at_midnight() -> None:
    p = SlotParams(frequency_type="interval", interval_minutes=120)
    slots = next_slots(p, utc(2026, 3, 2, 10, 30), 3)
    assert slots == [utc(2026, 3, 2, 12), utc(2026, 3, 2, 14), utc(2026, 3, 2, 16)]


def test_interval_with_daily_window() -> None:
    p = SlotParams(frequency_type="interval", interval_minutes=180, window_start="09:00", window_end="17:00")
    slots = next_slots(p, utc(2026, 3, 2, 16, 0), 4)
    assert slots == [utc(2026, 3, 3, 9), utc(2026, 3, 3, 12), utc(2026, 3, 3, 15), utc(2026, 3, 4, 9)]


def test_custom_interval_anchored_at_start_at_and_not_before_it() -> None:
    p = SlotParams(frequency_type="interval", interval_minutes=45, start_at=utc(2026, 3, 2, 10, 10))
    slots = next_slots(p, utc(2026, 3, 2, 9, 0), 3)
    assert slots == [utc(2026, 3, 2, 10, 10), utc(2026, 3, 2, 10, 55), utc(2026, 3, 2, 11, 40)]


def test_specific_times_and_days_of_week() -> None:
    p = SlotParams(frequency_type="times", times_of_day=("18:30", "09:00"), days_of_week=(0, 2, 4))
    # 2026-03-03 is a Tuesday -> Wednesday and Friday only
    slots = next_slots(p, utc(2026, 3, 3, 0, 0), 3)
    assert slots == [utc(2026, 3, 4, 9), utc(2026, 3, 4, 18, 30), utc(2026, 3, 6, 9)]
    assert all(s.astimezone(UTC).weekday() in (0, 2, 4) for s in slots)


def test_timezone_conversion() -> None:
    p = SlotParams(frequency_type="times", times_of_day=("09:00",), timezone="Asia/Kolkata")
    slots = next_slots(p, utc(2026, 3, 2, 0, 0), 1)
    assert slots == [utc(2026, 3, 2, 3, 30)]  # 09:00 IST = 03:30 UTC
    assert describe_local(slots[0], "Asia/Kolkata").endswith("09:00 IST")


def test_dst_spring_forward_skips_nonexistent_time() -> None:
    # 2026-03-08 02:00 EST -> 03:00 EDT; 02:30 does not exist that day.
    p = SlotParams(frequency_type="times", times_of_day=("02:30", "12:00"), timezone="America/New_York")
    slots = next_slots(p, datetime(2026, 3, 7, 0, 0, tzinfo=NY), 5)
    local = [s.astimezone(NY).strftime("%m-%d %H:%M %Z") for s in slots]
    assert local == ["03-07 02:30 EST", "03-07 12:00 EST", "03-08 12:00 EDT", "03-09 02:30 EDT", "03-09 12:00 EDT"]
    assert slots[2] == utc(2026, 3, 8, 16, 0)


def test_dst_fall_back_fires_ambiguous_time_once() -> None:
    # 2026-11-01 02:00 EDT -> 01:00 EST; 01:30 occurs twice locally.
    p = SlotParams(frequency_type="times", times_of_day=("01:30",), timezone="America/New_York")
    slots = next_slots(p, datetime(2026, 10, 31, 12, 0, tzinfo=NY), 2)
    assert slots[0] == utc(2026, 11, 1, 5, 30)  # first occurrence (EDT)
    assert slots[1].astimezone(NY).date().isoformat() == "2026-11-02"


def test_dst_fall_back_hourly_interval_has_no_double_fire() -> None:
    p = SlotParams(frequency_type="interval", interval_minutes=60, timezone="America/New_York")
    start = datetime(2026, 11, 1, 0, 0, tzinfo=NY) - timedelta(minutes=1)
    slots = next_slots(p, start, 30)
    day = [s for s in slots if s.astimezone(NY).date().isoformat() == "2026-11-01"]
    walls = [s.astimezone(NY).strftime("%H:%M") for s in day]
    assert len(walls) == 24 and len(set(walls)) == 24
    assert all(b - a >= timedelta(minutes=60) for a, b in zip(slots, slots[1:], strict=False))


def test_daily_limit_counts_taken_slots() -> None:
    p = SlotParams(frequency_type="interval", interval_minutes=60, daily_limit=3)
    taken = [utc(2026, 3, 2, 1), utc(2026, 3, 2, 2)]
    slots = next_slots(p, utc(2026, 3, 2, 5, 30), 3, taken)
    assert slots == [utc(2026, 3, 2, 6), utc(2026, 3, 3, 0), utc(2026, 3, 3, 1)]


def test_daily_limit_uses_local_days() -> None:
    p = SlotParams(frequency_type="interval", interval_minutes=360, daily_limit=1, timezone="America/New_York")
    slots = next_slots(p, datetime(2026, 6, 1, 0, 30, tzinfo=NY), 3)
    dates = [s.astimezone(NY).date().isoformat() for s in slots]
    assert dates == ["2026-06-01", "2026-06-02", "2026-06-03"]


def test_taken_slots_are_skipped_with_one_minute_spacing() -> None:
    p = SlotParams(frequency_type="interval", interval_minutes=60)
    taken = [utc(2026, 3, 2, 12, 0, 30), utc(2026, 3, 2, 13, 0)]
    slots = next_slots(p, utc(2026, 3, 2, 11, 30), 2, taken)
    assert slots == [utc(2026, 3, 2, 14), utc(2026, 3, 2, 15)]


def test_never_in_the_past() -> None:
    now = datetime.now(UTC)
    p = SlotParams(frequency_type="interval", interval_minutes=15)
    slots = next_slots(p, now, 5)
    assert len(slots) == 5 and all(s > now for s in slots)
    assert slots == sorted(slots)


def test_invalid_parameters() -> None:
    with pytest.raises(ValueError):
        next_slots(SlotParams(frequency_type="interval", interval_minutes=5), utc(2026, 1, 1), 1)
    with pytest.raises(ValueError):
        next_slots(SlotParams(frequency_type="times", times_of_day=("25:00",)), utc(2026, 1, 1), 1)
    with pytest.raises(ValueError):
        next_slots(SlotParams(timezone="Mars/Base"), utc(2026, 1, 1), 1)
