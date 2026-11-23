"""Publishing schedules and queue placement.

Nothing in this module commits: callers (routers, workers, other services) own the
transaction. Contract hooks used by other modules:
``on_clips_generated``, ``on_clip_render_finished``, ``next_publish_time`` (approve/reject was removed in
API v2: clips are uploaded explicitly through sequential upload runs or queued by automatic schedules).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.database import utcnow
from app.core.errors import ConflictError, NotFoundError, ValidationFailed
from app.models import (
    ConnectedAccount,
    GeneratedClip,
    PublishingHistory,
    PublishingQueueItem,
    PublishingSchedule,
    User,
)
from app.models.enums import (
    AccountStatus,
    FrequencyType,
    NotificationType,
    PublishMode,
    QueueStatus,
)
from app.modules.publishing import history
from app.modules.publishing.payloads import build_payload
from app.modules.scheduling.slots import SlotParams, ensure_utc, get_zone, next_slots, parse_hhmm

log = logging.getLogger(__name__)

DEFAULT_DELAY = timedelta(minutes=5)
QUEUED_STATUSES = (QueueStatus.SCHEDULED, QueueStatus.RENDERING)
SLOT_STATUSES = (QueueStatus.SCHEDULED, QueueStatus.RENDERING, QueueStatus.PUBLISHING,
                 QueueStatus.PROCESSING_REMOTE, QueueStatus.PUBLISHED)
REOPENABLE = (QueueStatus.FAILED, QueueStatus.CANCELLED)
SUCCESS_ACTIONS = ("publish",)
_SCHEDULE_FIELDS = ("name", "mode", "frequency_type", "interval_minutes", "times_of_day", "days_of_week",
                    "timezone", "daily_limit", "window_start", "window_end", "start_at", "auto_min_score",
                    "default_privacy")


# ------------------------------------------------------------- validation
def validate_schedule(s: PublishingSchedule) -> None:
    """Raise ValidationFailed describing the first invalid field."""
    def bad(msg: str) -> ValidationFailed:
        return ValidationFailed(msg, code="invalid_schedule")

    try:
        get_zone(s.timezone)
    except ValueError as exc:
        raise bad(f"Unknown timezone '{s.timezone}' (use an IANA name such as 'Europe/Berlin')") from exc
    if s.mode not in (PublishMode.REVIEW, PublishMode.AUTOMATIC):
        raise bad("mode must be 'review' or 'automatic'")
    if s.frequency_type == FrequencyType.INTERVAL:
        if not s.interval_minutes or s.interval_minutes < 15 or s.interval_minutes > 7 * 24 * 60:
            raise bad("interval_minutes must be between 15 and 10080 (presets: 60, 120, 180, 360)")
    elif s.frequency_type == FrequencyType.TIMES:
        if not s.times_of_day or len(s.times_of_day) > 48:
            raise bad("times_of_day needs between 1 and 48 'HH:MM' entries")
    else:
        raise bad("frequency_type must be 'interval' or 'times'")
    try:
        for t in s.times_of_day or []:
            parse_hhmm(t)
        lo = parse_hhmm(s.window_start) if s.window_start else None
        hi = parse_hhmm(s.window_end) if s.window_end else None
    except (ValueError, AttributeError) as exc:
        raise bad(str(exc)) from exc
    if lo is not None and hi is not None and hi <= lo:
        raise bad("window_end must be later than window_start")
    days = s.days_of_week or []
    if not days or any(not isinstance(d, int) or d < 0 or d > 6 for d in days):
        raise bad("days_of_week must contain values 0 (Monday) .. 6 (Sunday)")
    if not 1 <= int(s.daily_limit or 0) <= 100:
        raise bad("daily_limit must be between 1 and 100")
    if not 0 <= float(s.auto_min_score) <= 100:
        raise bad("auto_min_score must be between 0 and 100")
    if s.default_privacy not in ("public", "unlisted", "private"):
        raise bad("default_privacy must be public, unlisted or private")
    if s.mode == PublishMode.AUTOMATIC and s.rights_confirmed_at is None:
        raise bad("Automatic mode publishes without review: confirm that you own or are authorised to "
                  "publish the content (rights_confirmed=true).")


def _apply(s: PublishingSchedule, values: dict[str, Any]) -> None:
    for key in _SCHEDULE_FIELDS:
        if key in values:
            val = values[key]
            if key in ("times_of_day", "days_of_week") and val is not None:
                val = sorted(set(val)) if key == "days_of_week" else list(val)
            if key == "start_at":
                val = ensure_utc(val)
            setattr(s, key, val)
    if values.get("rights_confirmed"):
        s.rights_confirmed_at = s.rights_confirmed_at or utcnow()


# ------------------------------------------------------------------- CRUD
def list_schedules(db: Session, user: User) -> list[PublishingSchedule]:
    return list(db.scalars(select(PublishingSchedule).where(PublishingSchedule.user_id == user.id)
                           .order_by(PublishingSchedule.created_at)))


def create_schedule(db: Session, user: User, values: dict[str, Any]) -> PublishingSchedule:
    account = db.get(ConnectedAccount, values.get("connected_account_id"))
    if account is None or account.user_id != user.id:
        raise NotFoundError("Account not found")
    s = PublishingSchedule(id=uuid.uuid4(), user_id=user.id, connected_account_id=account.id,
                           platform=account.platform, name="", is_active=True, mode=PublishMode.REVIEW,
                           frequency_type=FrequencyType.INTERVAL, interval_minutes=None, times_of_day=[],
                           days_of_week=[0, 1, 2, 3, 4, 5, 6], timezone=user.timezone or "UTC", daily_limit=6,
                           auto_min_score=0.0, default_privacy="public")
    _apply(s, values)
    if s.frequency_type == FrequencyType.INTERVAL and not s.interval_minutes:
        s.interval_minutes = 180
    s.name = s.name or f"{account.platform.capitalize()} schedule"
    validate_schedule(s)
    db.add(s)
    db.flush()
    return s


def update_schedule(db: Session, schedule: PublishingSchedule, values: dict[str, Any]) -> PublishingSchedule:
    _apply(schedule, values)
    validate_schedule(schedule)
    db.flush()
    refresh_next_run(db, schedule)
    return schedule


def delete_schedule(db: Session, schedule: PublishingSchedule) -> None:
    """Cancel publications this schedule placed but that have not started, then delete it."""
    items = db.scalars(select(PublishingQueueItem).where(
        PublishingQueueItem.schedule_id == schedule.id,
        PublishingQueueItem.status.in_((QueueStatus.PENDING_APPROVAL, *QUEUED_STATUSES))))
    for item in items:
        item.status = QueueStatus.CANCELLED
        history.record(db, item, "cancel", QueueStatus.CANCELLED, "Schedule deleted")
    db.flush()
    db.delete(schedule)
    db.flush()


def set_schedule_active(db: Session, schedule: PublishingSchedule, active: bool) -> PublishingSchedule:
    """Pause/resume. Resuming moves overdue items onto the next free slots."""
    schedule.is_active = active
    db.flush()
    if active:
        reslot_overdue(db, schedule)
    refresh_next_run(db, schedule)
    return schedule


# ------------------------------------------------------------------ slots
def taken_slots(db: Session, account_id: uuid.UUID, *, since: datetime,
                exclude_ids: tuple[uuid.UUID, ...] = ()) -> list[datetime]:
    """Instants already used by ``account_id`` (any schedule) from ``since`` on."""
    stmt = select(PublishingQueueItem.scheduled_at).where(
        PublishingQueueItem.connected_account_id == account_id, PublishingQueueItem.scheduled_at.is_not(None),
        PublishingQueueItem.scheduled_at >= since, PublishingQueueItem.status.in_(SLOT_STATUSES))
    if exclude_ids:
        stmt = stmt.where(PublishingQueueItem.id.not_in(exclude_ids))
    return [t for t in db.scalars(stmt) if t is not None]


def preview_slots(db: Session, schedule: PublishingSchedule, count: int, *,
                  after: datetime | None = None) -> list[datetime]:
    """The next ``count`` free slots of ``schedule`` (taking the account's queue into account)."""
    after = after or utcnow()
    taken = taken_slots(db, schedule.connected_account_id, since=after - timedelta(days=2))
    return next_slots(SlotParams.from_schedule(schedule), after, count, taken)


def assign_next_slot(db: Session, schedule: PublishingSchedule, *, after: datetime | None = None) -> datetime | None:
    """The next free slot for one more publication on ``schedule`` (None if none in the horizon)."""
    slots = preview_slots(db, schedule, 1, after=after)
    return slots[0] if slots else None


def reslot_overdue(db: Session, schedule: PublishingSchedule) -> int:
    """Move this schedule's overdue scheduled items onto upcoming free slots."""
    now = utcnow()
    overdue = list(db.scalars(select(PublishingQueueItem).where(
        PublishingQueueItem.schedule_id == schedule.id, PublishingQueueItem.status == QueueStatus.SCHEDULED,
        PublishingQueueItem.scheduled_at < now).order_by(PublishingQueueItem.scheduled_at)))
    if not overdue:
        return 0
    taken = taken_slots(db, schedule.connected_account_id, since=now - timedelta(days=2),
                        exclude_ids=tuple(i.id for i in overdue))
    slots = next_slots(SlotParams.from_schedule(schedule), now, len(overdue), taken)
    for item, slot in zip(overdue, slots, strict=False):
        item.scheduled_at = slot
    db.flush()
    return min(len(overdue), len(slots))


def refresh_next_run(db: Session, schedule: PublishingSchedule) -> None:
    """``next_run_at`` = earliest pending publication placed by this schedule (None when idle/paused)."""
    if not schedule.is_active:
        schedule.next_run_at = None
        return
    schedule.next_run_at = db.scalar(select(func.min(PublishingQueueItem.scheduled_at)).where(
        PublishingQueueItem.schedule_id == schedule.id, PublishingQueueItem.status.in_(QUEUED_STATUSES)))


def refresh_item_schedule(db: Session, item: PublishingQueueItem) -> None:
    """Keep ``next_run_at`` of the item's schedule in sync after the item changed state."""
    if item.schedule_id is None:
        return
    schedule = db.get(PublishingSchedule, item.schedule_id)
    if schedule is not None:
        db.flush()
        refresh_next_run(db, schedule)


# ---------------------------------------------------------------- enqueue
def already_published(db: Session, clip_id: uuid.UUID, account_id: uuid.UUID) -> bool:
    return db.scalar(select(func.count(PublishingHistory.id)).where(
        PublishingHistory.clip_id == clip_id, PublishingHistory.connected_account_id == account_id,
        PublishingHistory.action.in_(SUCCESS_ACTIONS), PublishingHistory.status == QueueStatus.PUBLISHED)) > 0


def enqueue_clip(db: Session, clip: GeneratedClip, account: ConnectedAccount, *,
                 schedule: PublishingSchedule | None = None, scheduled_at: datetime | None = None,
                 payload_overrides: dict[str, Any] | None = None, rights_confirmed: bool) -> PublishingQueueItem:
    """Queue ``clip`` for ``account`` with duplicate prevention. Never commits."""
    if not rights_confirmed:
        raise ValidationFailed("Confirm that you own the content or are authorised to publish it.",
                               code="rights_not_confirmed")
    if account.user_id != clip.user_id:
        raise NotFoundError("Account not found")
    if account.status != AccountStatus.ACTIVE:
        raise ConflictError(f"The {account.platform} account '{account.display_name}' must be reconnected "
                            "before publishing.", code="account_inactive")
    if clip.removed_at is not None:
        raise ConflictError("Removed clips cannot be published.", code="clip_removed")
    if already_published(db, clip.id, account.id):
        raise ConflictError("This clip was already published to this account.", code="already_published")
    key = f"{clip.id}:{account.id}"
    item = db.scalar(select(PublishingQueueItem).where(PublishingQueueItem.idempotency_key == key))
    if item is not None and item.status not in REOPENABLE:
        raise ConflictError("This clip is already queued for this account.", code="already_queued",
                            details={"queue_item_id": str(item.id), "status": item.status})
    now = utcnow()
    when = ensure_utc(scheduled_at)
    if when is None and schedule is not None:
        when = assign_next_slot(db, schedule, after=now)
        if when is None:
            raise ConflictError("The schedule has no free publishing slot.", code="no_free_slot")
    when = max(when, now) if when is not None else now + DEFAULT_DELAY
    payload = build_payload(clip, account.platform, schedule=schedule, overrides=payload_overrides)
    if item is None:
        item = PublishingQueueItem(id=uuid.uuid4(), user_id=clip.user_id, clip_id=clip.id,
                                   connected_account_id=account.id, platform=account.platform,
                                   idempotency_key=key, status=QueueStatus.SCHEDULED)
        db.add(item)
    _reset_item(item, when=when, payload=payload, schedule=schedule)
    history.record(db, item, "scheduled", QueueStatus.SCHEDULED, f"Scheduled for {when.isoformat()}")
    db.flush()
    if schedule is not None:
        refresh_next_run(db, schedule)
    return item


def _reset_item(item: PublishingQueueItem, *, when: datetime, payload: dict[str, Any],
                schedule: PublishingSchedule | None) -> None:
    item.status = QueueStatus.SCHEDULED
    item.scheduled_at = when
    item.payload = payload
    item.schedule_id = schedule.id if schedule else None
    item.rights_confirmed = True
    item.attempts = 0
    item.next_attempt_at = None
    item.last_error = None
    item.external_id = item.external_container_id = item.upload_session_url = None
    item.external_url = item.remote_status = None
    item.locked_at = item.locked_by = None
    item.published_at = None
    item.origin = None  # set again by timeline scheduling


# ------------------------------------------------------------------ hooks
def _active_schedules(db: Session, user_id: uuid.UUID, mode: str | None = None) -> list[PublishingSchedule]:
    stmt = (select(PublishingSchedule).join(ConnectedAccount,
                                             ConnectedAccount.id == PublishingSchedule.connected_account_id)
            .where(PublishingSchedule.user_id == user_id, PublishingSchedule.is_active.is_(True),
                   ConnectedAccount.status == AccountStatus.ACTIVE)
            .order_by(PublishingSchedule.created_at))
    if mode:
        stmt = stmt.where(PublishingSchedule.mode == mode)
    return list(db.scalars(stmt))


def _queue_on_schedules(db: Session, clip: GeneratedClip,
                        schedules: list[PublishingSchedule]) -> list[PublishingQueueItem]:
    items = []
    for s in schedules:
        if s.rights_confirmed_at is None:
            log.info("Schedule %s has no rights confirmation; not auto-queueing clip %s", s.id, clip.id)
            continue
        account = db.get(ConnectedAccount, s.connected_account_id)
        try:
            items.append(enqueue_clip(db, clip, account, schedule=s, rights_confirmed=True))
        except (ConflictError, ValidationFailed) as exc:
            log.info("Clip %s not queued on schedule %s: %s", clip.id, s.id, exc.message)
    return items


def on_clips_generated(db: Session, user: User, clips: list[GeneratedClip]) -> list[PublishingQueueItem]:
    """Contract: automatic-mode schedules queue clips scoring >= auto_min_score (no approval step)."""
    autos = [s for s in _active_schedules(db, user.id, PublishMode.AUTOMATIC) if s.rights_confirmed_at]
    if not autos:
        return []
    items: list[PublishingQueueItem] = []
    for clip in sorted(clips, key=lambda c: c.highlight_score or 0.0, reverse=True):
        if clip.removed_at is not None:
            continue
        matching = [s for s in autos if (clip.highlight_score or 0.0) >= s.auto_min_score]
        if not matching:
            continue
        items.extend(_queue_on_schedules(db, clip, matching))
    return items


def on_clip_render_finished(db: Session, clip: GeneratedClip, success: bool) -> None:
    """Contract: release (or fail) queue items that were waiting for this clip's render."""
    from app.modules.notifications.service import notify

    items = db.scalars(select(PublishingQueueItem).where(PublishingQueueItem.clip_id == clip.id,
                                                         PublishingQueueItem.status == QueueStatus.RENDERING))
    items = list(items)
    for item in items:
        if success:
            item.status = QueueStatus.SCHEDULED
            item.last_error = None
            history.record(db, item, "render", QueueStatus.SCHEDULED, "Render finished; ready to publish")
        else:
            item.status = QueueStatus.FAILED
            item.last_error = f"Render failed: {clip.render_error or 'unknown error'}"
            history.record(db, item, "failed", QueueStatus.FAILED, item.last_error)
            notify(db, item.user_id, NotificationType.PUBLISH_FAILED, "Publishing failed: render error",
                   item.last_error, link=f"/app/clips/{clip.id}",
                   data={"queue_item_id": str(item.id), "clip_id": str(clip.id)})
    db.flush()


# ----------------------------------------------------------- next / pause
def is_user_paused(user: User) -> bool:
    return bool((user.settings or {}).get("publishing_paused"))


def _pending_query(user_id: uuid.UUID):  # noqa: ANN202
    return (select(PublishingQueueItem.connected_account_id, func.min(PublishingQueueItem.scheduled_at))
            .outerjoin(PublishingSchedule, PublishingSchedule.id == PublishingQueueItem.schedule_id)
            .where(PublishingQueueItem.user_id == user_id, PublishingQueueItem.status.in_(QUEUED_STATUSES),
                   or_(PublishingQueueItem.schedule_id.is_(None), PublishingSchedule.is_active.is_(True))))


def next_publish_times(db: Session, user: User) -> dict[uuid.UUID, datetime]:
    """Earliest pending publication per connected account (empty while publishing is paused)."""
    if is_user_paused(user):
        return {}
    rows = db.execute(_pending_query(user.id).group_by(PublishingQueueItem.connected_account_id))
    return {acc: when for acc, when in rows if when is not None}


def next_publish_time(db: Session, user: User) -> datetime | None:
    """Contract: the user's next publication time overall (None when nothing/paused)."""
    times = next_publish_times(db, user)
    return min(times.values()) if times else None


def set_user_paused(db: Session, user: User, paused: bool) -> None:
    """User-level pause/resume (``user.settings['publishing_paused']``)."""
    user.settings = {**(user.settings or {}), "publishing_paused": paused}
    db.flush()
    if not paused:
        for s in _active_schedules(db, user.id):
            reslot_overdue(db, s)
            refresh_next_run(db, s)
