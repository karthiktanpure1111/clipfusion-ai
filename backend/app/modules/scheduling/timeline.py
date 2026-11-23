"""Timeline scheduling: a video's clips go to YouTube one by one, a fixed gap apart (default 1 hour).

* ``schedule_source`` (``POST /publishing/timeline``): every generated clip of a video that is not uploaded or
  scheduled yet gets a publishing-queue item, in timeline order, ``gap`` apart, starting right away when the
  channel is free (never within ``gap`` of another scheduled post; see ``plan_slots``) or at a chosen time.
* Auto-schedule (on by default, ``PUT /publishing/auto-schedule``): when a video's clips have finished
  rendering they are scheduled the same way -- clip 1 uploads right away, clip 2 one gap later, and so on.
  A clip rendered later (e.g. after a framing review) joins the end of the queue, in timeline order. The rights
  confirmed when the video was added cover these uploads.
* Missed slots (ClipFusion was not running, or publishing was paused) are shifted forward keeping the gaps,
  instead of uploading everything back to back when ClipFusion starts again.

Items carry ``origin = "timeline"``. The periodic dispatcher uploads them when due (one transfer at a time per
account) and a verified upload cleans up the clip's files exactly like a manual upload."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import utcnow
from app.core.deps import get_owned
from app.core.errors import ConflictError, ValidationFailed
from app.models import ConnectedAccount, GeneratedClip, PublishingQueueItem, User, VideoSource
from app.models.enums import AccountStatus, NotificationType, Platform, QueueStatus, UploadStatus
from app.modules.publishing import history
from app.modules.scheduling.slots import ensure_utc

log = logging.getLogger(__name__)

ORIGIN = "timeline"
LEAD = timedelta(minutes=1)  # posts sooner than this are "now" (never re-ordered)
MISSED_AFTER = timedelta(minutes=10)  # later than this past its time = the slot was missed
PAST_TOLERANCE = timedelta(minutes=1)
PENDING = (QueueStatus.SCHEDULED, QueueStatus.RENDERING)
ACTIVE = (QueueStatus.SCHEDULED, QueueStatus.RENDERING, QueueStatus.PUBLISHING)
DEFAULT_GAP_MINUTES = 60
SETTINGS_KEY = "auto_schedule"
RIGHTS_MESSAGE = "Confirm that you have the creator's permission, a licence, or other rights to publish this content."


def format_gap(gap: timedelta) -> str:
    minutes = int(gap.total_seconds() // 60)
    if minutes % 60 == 0:
        hours = minutes // 60
        return f"{hours} hour{'s' if hours != 1 else ''}"
    return f"{minutes} minutes"


# ------------------------------------------------------------------ slots
def plan_slots(db: Session, account_id: uuid.UUID, gap: timedelta, count: int, *,
               now: datetime | None = None) -> list[datetime]:
    """``count`` upload times for ``account_id``: the first as soon as possible (right now when the channel is
    free), each next one ``gap`` later, and none closer than ``gap`` to a post that is already scheduled or
    uploading -- free time before a later scheduled post is used instead of queueing behind it."""
    now = now or utcnow()
    Q = PublishingQueueItem
    taken = [ensure_utc(t) for t in db.scalars(select(Q.scheduled_at).where(
        Q.connected_account_id == account_id, Q.status.in_(ACTIVE), Q.scheduled_at.is_not(None)))]
    out: list[datetime] = []
    t = now
    for _ in range(max(count, 0)):
        moved = True
        while moved:  # step past every post that is too close (taken times are finite: this ends)
            moved = False
            for other in taken:
                if abs(other - t) < gap:
                    t, moved = other + gap, True
        out.append(t)
        taken.append(t)
        t = t + gap
    return out


def next_slot(db: Session, account_id: uuid.UUID, gap: timedelta, *, now: datetime | None = None) -> datetime:
    """When the next timeline post of ``account_id`` can go out (see :func:`plan_slots`)."""
    return plan_slots(db, account_id, gap, 1, now=now)[0]


def scheduled_clip_ids(db: Session, clip_ids: list[uuid.UUID], account_id: uuid.UUID | None = None, *,
                       statuses: tuple[str, ...] = PENDING) -> dict[uuid.UUID, PublishingQueueItem]:
    """``clip_id -> earliest queue item`` in ``statuses`` (default: scheduled or waiting for its render;
    ``ACTIVE`` also counts posts that are uploading)."""
    if not clip_ids:
        return {}
    Q = PublishingQueueItem
    stmt = select(Q).where(Q.clip_id.in_(clip_ids), Q.status.in_(statuses))
    if account_id is not None:
        stmt = stmt.where(Q.connected_account_id == account_id)
    out: dict[uuid.UUID, PublishingQueueItem] = {}
    for item in db.scalars(stmt.order_by(Q.scheduled_at)):
        out.setdefault(item.clip_id, item)
    return out


# ---------------------------------------------------------------- enqueue
def _overrides(db: Session, clip: GeneratedClip, privacy: str) -> dict[str, Any]:
    """Privacy + the source credit appended to the description (as for sequential uploads)."""
    from app.modules.publishing.payloads import build_payload

    description = build_payload(clip, Platform.YOUTUBE).get("description") or ""
    source = db.get(VideoSource, clip.video_source_id)
    credit = (source.attribution or "").strip() if source is not None else ""
    if credit and credit.lower() not in description.lower():
        description = (description.rstrip() + "\n\n" + credit).strip()[:5000]
    return {"privacy": privacy, "description": description}


def _enqueue(db: Session, clip: GeneratedClip, account: ConnectedAccount, when: datetime, privacy: str,
             note: str) -> PublishingQueueItem:
    from app.modules.scheduling.service import enqueue_clip

    item = enqueue_clip(db, clip, account, scheduled_at=when, payload_overrides=_overrides(db, clip, privacy),
                        rights_confirmed=True)
    item.origin = ORIGIN
    item.scheduled_at = when  # exact plan (enqueue_clip clamps to its own "now", a few ms later)
    history.record(db, item, "timeline", QueueStatus.SCHEDULED, note)
    db.flush()  # sessions do not autoflush: later queries must see the origin
    return item


def _youtube_account(db: Session, user: User, account_id: uuid.UUID) -> ConnectedAccount:
    from app.modules.upload_runs.service import _account

    return _account(db, user, account_id)


def schedule_source(db: Session, user: User, *, video_source_id: uuid.UUID, connected_account_id: uuid.UUID,
                    gap_minutes: int, start_at: datetime | None, privacy: str | None,
                    clip_ids: list[uuid.UUID] | None, rights_confirmed: bool) -> list[PublishingQueueItem]:
    """Put a video's generated, not yet uploaded/scheduled clips on the timeline. Commits."""
    from app.modules.upload_runs.service import _ordered_clips, uploadable
    from app.modules.users.service import read_settings

    if not rights_confirmed:
        raise ValidationFailed(RIGHTS_MESSAGE, code="rights_not_confirmed")
    source = get_owned(db, VideoSource, video_source_id, user, name="Video")
    account = _youtube_account(db, user, connected_account_id)
    gap = timedelta(minutes=gap_minutes)
    now = utcnow()
    if start_at is not None and ensure_utc(start_at) < now - PAST_TOLERANCE:
        raise ValidationFailed("Pick a start time in the future.", code="start_in_past")
    clips = [c for c in _ordered_clips(db, source.id) if uploadable(c)]
    if clip_ids is not None:
        wanted = set(clip_ids)
        clips = [c for c in clips if c.id in wanted]
    taken = scheduled_clip_ids(db, [c.id for c in clips], statuses=ACTIVE)
    clips = [c for c in clips if c.id not in taken]
    if not clips:
        raise ConflictError("There is no generated clip left to schedule for this video (clips that are uploaded, "
                            "uploading or already scheduled are skipped).", code="nothing_to_schedule")
    if start_at is not None:  # a time picked by the user: exactly every gap from there
        slots = [ensure_utc(start_at) + gap * i for i in range(len(clips))]
    else:
        slots = plan_slots(db, account.id, gap, len(clips), now=now)
    first = slots[0]
    privacy = privacy or read_settings(user).default_privacy
    items: list[PublishingQueueItem] = []
    for clip in clips:
        when = slots[len(items)]
        try:
            with db.begin_nested():
                items.append(_enqueue(db, clip, account, when, privacy,
                                      f"Timeline: {format_gap(gap)} apart, starting {first.isoformat()}"))
        except (ConflictError, ValidationFailed) as exc:
            log.info("Clip %s not put on the timeline: %s", clip.id, exc.message)
    if not items:
        db.rollback()
        raise ConflictError("None of the clips could be scheduled.", code="nothing_to_schedule")
    db.commit()
    log.info("Timeline: %d clips of source %s scheduled %s apart from %s on account %s", len(items), source.id,
             format_gap(gap), first.isoformat(), account.id)
    _start_due(db, items)
    return items


def _start_due(db: Session, items: list[PublishingQueueItem]) -> None:
    """Upload the posts that are due now immediately (instead of at the dispatcher's next pass)."""
    from app.modules.upload_runs.service import _dispatch

    now = utcnow()
    for item in items:
        if item.scheduled_at is not None and ensure_utc(item.scheduled_at) <= now:
            try:
                _dispatch(db, item)  # one transfer at a time per account; otherwise the dispatcher picks it up
            except Exception:  # noqa: BLE001 - the periodic dispatcher retries
                db.rollback()
                log.exception("Starting the upload of queue item %s failed", item.id)


def cancel_source(db: Session, user: User, video_source_id: uuid.UUID) -> int:
    """Cancel every scheduled (not yet uploading) publication of a video's clips. Commits."""
    source = get_owned(db, VideoSource, video_source_id, user, name="Video")
    Q = PublishingQueueItem
    items = list(db.scalars(select(Q).join(GeneratedClip, GeneratedClip.id == Q.clip_id).where(
        GeneratedClip.video_source_id == source.id, Q.user_id == user.id, Q.status.in_(PENDING))))
    for item in items:
        item.status = QueueStatus.CANCELLED
        item.locked_at = item.locked_by = None
        history.record(db, item, "cancel", QueueStatus.CANCELLED, "Schedule cleared from the video page")
    db.commit()
    return len(items)


def timeline_out(db: Session, items: list[PublishingQueueItem]) -> dict[str, Any]:
    from app.modules.publishing.service import serialize_items

    times = [ensure_utc(i.scheduled_at) for i in items if i.scheduled_at is not None]
    return {"scheduled": len(items), "first_at": min(times, default=None), "last_at": max(times, default=None),
            "items": serialize_items(db, items)}


# ----------------------------------------------------------- auto-schedule
def auto_config(user: User) -> dict[str, Any]:
    raw = (user.settings or {}).get(SETTINGS_KEY)
    return dict(raw) if isinstance(raw, dict) else {}


def _default_account(db: Session, user: User) -> ConnectedAccount | None:
    """The first active YouTube account that may upload."""
    from app.integrations.google_oauth import SCOPE_YOUTUBE_UPLOAD

    accounts = db.scalars(select(ConnectedAccount).where(
        ConnectedAccount.user_id == user.id, ConnectedAccount.platform == Platform.YOUTUBE,
        ConnectedAccount.status == AccountStatus.ACTIVE).order_by(ConnectedAccount.created_at))
    return next((a for a in accounts if SCOPE_YOUTUBE_UPLOAD in (a.scopes or [])), None)


def _resolved(db: Session, user: User) -> tuple[bool, ConnectedAccount | None, int, str | None, datetime | None]:
    """``(enabled, account, gap_minutes, privacy, since)``; auto-schedule is on unless switched off."""
    cfg = auto_config(user)
    from app.core.config import get_settings

    enabled = bool(cfg.get("enabled", get_settings().auto_schedule_default))
    account = None
    account_id = _uuid(cfg.get("connected_account_id"))
    if account_id is not None:
        account = db.get(ConnectedAccount, account_id)
        if account is not None and account.user_id != user.id:
            account = None
    if account is None:
        account = _default_account(db, user)
    since = None
    if cfg.get("since"):
        try:
            since = ensure_utc(datetime.fromisoformat(str(cfg["since"])))
        except ValueError:
            since = None
    return enabled, account, int(cfg.get("gap_minutes") or DEFAULT_GAP_MINUTES), cfg.get("privacy"), since


def auto_out(db: Session, user: User) -> dict[str, Any]:
    enabled, account, gap, privacy, since = _resolved(db, user)
    return {"enabled": enabled, "connected_account_id": account.id if account else None,
            "account_name": account.display_name if account else None, "gap_minutes": gap, "privacy": privacy,
            "since": since, "rights_confirmed_at": None,
            "next_slot_at": next_slot(db, account.id, timedelta(minutes=gap)) if account and enabled else None}


def set_auto(db: Session, user: User, *, enabled: bool, connected_account_id: uuid.UUID | None, gap_minutes: int,
             privacy: str | None, rights_confirmed: bool = False) -> dict[str, Any]:
    """Switch auto-schedule on/off (clips created from then on are scheduled). Commits."""
    cfg = auto_config(user)
    was_enabled = bool(cfg) and bool(cfg.get("enabled"))
    if enabled and connected_account_id is not None:
        _youtube_account(db, user, connected_account_id)
    if enabled and (not was_enabled or not cfg.get("since")):
        cfg["since"] = utcnow().isoformat()
    cfg.update({"enabled": enabled, "gap_minutes": gap_minutes, "privacy": privacy})
    if connected_account_id is not None:
        cfg["connected_account_id"] = str(connected_account_id)
    user.settings = {**(user.settings or {}), SETTINGS_KEY: cfg}
    db.commit()
    db.refresh(user)
    return auto_out(db, user)


def _uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value)) if value else None
    except ValueError:
        return None


def auto_schedule_source(db: Session, source_id: uuid.UUID) -> list[PublishingQueueItem]:
    """Auto-schedule a video's ready clips that were never scheduled: in timeline order, the first right away
    (or after the posts already waiting), then one per gap. Commits; never raises."""
    from app.integrations.google_oauth import SCOPE_YOUTUBE_UPLOAD
    from app.modules.upload_runs.service import _ordered_clips, uploadable

    try:
        source = db.get(VideoSource, source_id)
        user = db.get(User, source.user_id) if source is not None else None
        if source is None or user is None or not source.rights_confirmed:
            return []
        enabled, account, gap_minutes, privacy, since = _resolved(db, user)
        if not enabled or account is None or account.status != AccountStatus.ACTIVE \
                or SCOPE_YOUTUBE_UPLOAD not in (account.scopes or []):
            return []
        clips = [c for c in _ordered_clips(db, source.id)
                 if c.upload_status == UploadStatus.NONE and uploadable(c)
                 and (since is None or ensure_utc(c.created_at) >= since)]
        if not clips:
            return []
        keys = {f"{c.id}:{account.id}": c for c in clips}
        known = set(db.scalars(select(PublishingQueueItem.idempotency_key).where(
            PublishingQueueItem.idempotency_key.in_(list(keys)))))
        clips = [c for k, c in keys.items() if k not in known]  # scheduled, uploaded or unscheduled by the user
        if not clips:
            return []
        if not privacy:
            from app.modules.users.service import read_settings

            privacy = read_settings(user).default_privacy
        gap = timedelta(minutes=gap_minutes)
        slots = plan_slots(db, account.id, gap, len(clips))
        first = slots[0]
        items: list[PublishingQueueItem] = []
        for clip in clips:
            try:
                with db.begin_nested():
                    items.append(_enqueue(db, clip, account, slots[len(items)], privacy,
                                          f"Auto-scheduled after rendering ({format_gap(gap)} apart)"))
            except (ConflictError, ValidationFailed) as exc:
                log.info("Clip %s not auto-scheduled: %s", clip.id, exc.message)
        if items:
            _keep_timeline_order(db, source.id, account.id)
        db.commit()
        if items:
            log.info("Auto-scheduled %d clips of source %s from %s, %s apart", len(items), source.id,
                     first.isoformat(), format_gap(gap))
            _start_due(db, items)
        return items
    except Exception:  # noqa: BLE001 - never fail the render/analysis job
        db.rollback()
        log.exception("Auto-scheduling the clips of source %s failed", source_id)
        return []


def _keep_timeline_order(db: Session, source_id: uuid.UUID, account_id: uuid.UUID) -> None:
    """Clips of one video that rendered out of order swap their (future) timeline times so they still go
    out in timeline order. The set of times does not change."""
    Q = PublishingQueueItem
    soon = utcnow() + LEAD
    rows = db.execute(select(Q, GeneratedClip).join(GeneratedClip, GeneratedClip.id == Q.clip_id).where(
        GeneratedClip.video_source_id == source_id, GeneratedClip.removed_at.is_(None),
        Q.connected_account_id == account_id, Q.status == QueueStatus.SCHEDULED, Q.origin == ORIGIN,
        Q.scheduled_at > soon)).all()
    if len(rows) < 2:
        return
    times = sorted(ensure_utc(item.scheduled_at) for item, _clip in rows)
    ordered = sorted(rows, key=lambda r: (r[1].start_time, r[1].end_time, str(r[1].id)))
    for (item, _clip), when in zip(ordered, times, strict=True):
        item.scheduled_at = when
    db.flush()


# ------------------------------------------------------------ missed slots
def shift_missed(db: Session, *, now: datetime | None = None) -> int:
    """Timeline posts whose time passed more than ``MISSED_AFTER`` ago (ClipFusion was off, or publishing was
    paused) move forward together, keeping their gaps: the first goes now. Commits. Returns items moved."""
    from app.modules.notifications.service import notify
    from app.modules.scheduling.service import is_user_paused

    now = now or utcnow()
    Q = PublishingQueueItem
    missed = list(db.scalars(select(Q).where(Q.origin == ORIGIN, Q.status == QueueStatus.SCHEDULED,
                                             Q.scheduled_at < now - MISSED_AFTER)))
    if not missed:
        return 0
    moved = 0
    for account_id in {i.connected_account_id for i in missed}:
        group = [i for i in missed if i.connected_account_id == account_id]
        user = db.get(User, group[0].user_id)
        if user is None or is_user_paused(user):
            continue
        earliest = min(ensure_utc(i.scheduled_at) for i in group)
        delay = now - earliest
        later = list(db.scalars(select(Q).where(Q.origin == ORIGIN, Q.status == QueueStatus.SCHEDULED,
                                                Q.connected_account_id == account_id, Q.scheduled_at >= earliest)))
        hours, minutes = divmod(int(delay.total_seconds() // 60), 60)
        shift = f"{hours} h {minutes} min" if hours else f"{minutes} min"
        for item in later:
            item.scheduled_at = ensure_utc(item.scheduled_at) + delay
            history.record(db, item, "retry", QueueStatus.SCHEDULED,
                           f"Upload time was missed (ClipFusion wasn't running); moved {shift} later, gaps kept")
        moved += len(later)
        account = db.get(ConnectedAccount, account_id)
        notify(db, user.id, NotificationType.SYSTEM, "Scheduled uploads moved",
               f"{len(later)} scheduled upload{'s' if len(later) != 1 else ''}"
               f"{' to ' + account.display_name if account else ''} missed their time because ClipFusion wasn't "
               f"running. They were moved {shift} later and keep their spacing.", link="/app/calendar",
               data={"account_id": str(account_id), "moved": len(later)})
    db.commit()
    return moved
