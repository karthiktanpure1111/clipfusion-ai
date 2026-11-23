"""Publishing queue operations for the /publishing API (user-scoped; commits)."""

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
from app.core.storage import get_storage
from app.models import (
    ConnectedAccount,
    GeneratedClip,
    PublishingHistory,
    PublishingQueueItem,
    PublishingSchedule,
    User,
)
from app.models.enums import AccountStatus, Platform, QueueStatus
from app.modules.publishing import dispatcher, history
from app.modules.publishing.payloads import validate_payload
from app.modules.publishing.schemas import QueueCreate
from app.modules.scheduling import service as scheduling
from app.modules.scheduling.slots import ensure_utc

log = logging.getLogger(__name__)

EDITABLE = (QueueStatus.PENDING_APPROVAL, QueueStatus.SCHEDULED, QueueStatus.RENDERING, QueueStatus.FAILED)
CANCELLABLE = (QueueStatus.PENDING_APPROVAL, QueueStatus.SCHEDULED, QueueStatus.RENDERING, QueueStatus.FAILED)
RETRYABLE = (QueueStatus.FAILED, QueueStatus.CANCELLED)
PAST_TOLERANCE = timedelta(minutes=1)


# ------------------------------------------------------------ serialization
def _thumb(clip: GeneratedClip | None) -> str | None:
    if clip is None or not clip.thumbnail_key:
        return None
    try:
        return get_storage().url(clip.thumbnail_key)
    except Exception:  # noqa: BLE001 - a missing thumbnail must not break listings
        return None


def _lookups(db: Session, clip_ids: set, account_ids: set) -> tuple[dict, dict]:
    clips = {c.id: c for c in db.scalars(select(GeneratedClip).where(GeneratedClip.id.in_(clip_ids)))} \
        if clip_ids else {}
    accounts = {a.id: a for a in db.scalars(select(ConnectedAccount).where(
        ConnectedAccount.id.in_(account_ids)))} if account_ids else {}
    return clips, accounts


def serialize_items(db: Session, items: list[PublishingQueueItem]) -> list[dict[str, Any]]:
    clips, accounts = _lookups(db, {i.clip_id for i in items}, {i.connected_account_id for i in items})
    out = []
    for i in items:
        clip, acc = clips.get(i.clip_id), accounts.get(i.connected_account_id)
        out.append({c: getattr(i, c) for c in (
            "id", "clip_id", "connected_account_id", "schedule_id", "platform", "status", "scheduled_at", "payload",
            "attempts", "max_attempts", "last_error", "external_id", "external_url", "remote_status",
            "published_at", "created_at", "updated_at")}
            | {"clip_title": clip.title if clip else "", "thumbnail_url": _thumb(clip),
               "account_name": acc.display_name if acc else ""})
    return out


# -------------------------------------------------------------------- queue
def list_queue(db: Session, user: User, *, status: str | None = None, platform: str | None = None,
               account_id: uuid.UUID | None = None, start: datetime | None = None, end: datetime | None = None,
               limit: int = 200) -> list[PublishingQueueItem]:
    Q = PublishingQueueItem
    stmt = select(Q).where(Q.user_id == user.id)
    if status:
        stmt = stmt.where(Q.status.in_([s.strip() for s in status.split(",") if s.strip()]))
    if platform:
        stmt = stmt.where(Q.platform == platform)
    if account_id:
        stmt = stmt.where(Q.connected_account_id == account_id)
    if start:
        stmt = stmt.where(Q.scheduled_at >= start)
    if end:
        stmt = stmt.where(Q.scheduled_at < end)
    return list(db.scalars(stmt.order_by(Q.scheduled_at, Q.created_at).limit(limit)))


def _first_active_schedule(db: Session, account: ConnectedAccount) -> PublishingSchedule | None:
    return db.scalar(select(PublishingSchedule).where(
        PublishingSchedule.connected_account_id == account.id, PublishingSchedule.is_active.is_(True))
        .order_by(PublishingSchedule.created_at).limit(1))


def create_queue_items(db: Session, user: User, data: QueueCreate) -> list[PublishingQueueItem]:
    """Queue one clip for several accounts (all-or-nothing). Commits."""
    if not data.rights_confirmed:
        raise ValidationFailed("Confirm that you own the content or are authorised to publish it.",
                               code="rights_not_confirmed")
    clip = get_owned(db, GeneratedClip, data.clip_id, user, name="Clip")
    if data.scheduled_at is not None and ensure_utc(data.scheduled_at) < utcnow() - PAST_TOLERANCE:
        raise ValidationFailed("scheduled_at must be in the future")
    items = []
    for acc_id in dict.fromkeys(data.account_ids):
        account = get_owned(db, ConnectedAccount, acc_id, user, name="Account")
        patch = data.youtube if account.platform == Platform.YOUTUBE else data.instagram
        overrides = patch.model_dump(exclude_none=True) if patch else None
        schedule = _first_active_schedule(db, account) if data.scheduled_at is None else None
        items.append(scheduling.enqueue_clip(db, clip, account, schedule=schedule, scheduled_at=data.scheduled_at,
                                             payload_overrides=overrides, rights_confirmed=True))
    db.commit()
    return items


def update_item(db: Session, user: User, item_id: uuid.UUID, *, scheduled_at: datetime | None,
                payload: dict[str, Any] | None) -> PublishingQueueItem:
    item = get_owned(db, PublishingQueueItem, item_id, user, name="Queue item")
    if item.status not in EDITABLE:
        raise ConflictError(f"A publication that is {item.status} can no longer be edited.", code="not_editable")
    changes = []
    if scheduled_at is not None:
        scheduled_at = ensure_utc(scheduled_at)
        if scheduled_at < utcnow() - PAST_TOLERANCE:
            raise ValidationFailed("scheduled_at must be in the future")
        item.scheduled_at = scheduled_at
        changes.append(f"time -> {scheduled_at.isoformat()}")
    if payload is not None:
        item.payload = validate_payload(item.platform, {**(item.payload or {}), **payload})
        changes.append("details edited")
    if changes:
        history.record(db, item, "edit", item.status, "; ".join(changes))
    _refresh_schedule(db, item)
    db.commit()
    return item


def _refresh_schedule(db: Session, item: PublishingQueueItem) -> None:
    if item.schedule_id:
        schedule = db.get(PublishingSchedule, item.schedule_id)
        if schedule is not None:
            db.flush()
            scheduling.refresh_next_run(db, schedule)


def cancel_item(db: Session, user: User, item_id: uuid.UUID) -> PublishingQueueItem:
    item = get_owned(db, PublishingQueueItem, item_id, user, name="Queue item")
    if item.status not in CANCELLABLE:
        raise ConflictError(f"A publication that is {item.status} cannot be cancelled.", code="not_cancellable")
    item.status = QueueStatus.CANCELLED
    item.locked_at = item.locked_by = None
    history.record(db, item, "cancel", QueueStatus.CANCELLED, "Cancelled by user")
    _refresh_schedule(db, item)
    db.commit()
    return item


def _reopen(db: Session, item: PublishingQueueItem, when: datetime) -> None:
    account = db.get(ConnectedAccount, item.connected_account_id)
    if account is None or account.status != AccountStatus.ACTIVE:
        raise ConflictError("Reconnect the account before retrying.", code="account_inactive")
    if scheduling.already_published(db, item.clip_id, item.connected_account_id):
        raise ConflictError("This clip was already published to this account.", code="already_published")
    item.status = QueueStatus.SCHEDULED
    item.scheduled_at = when
    item.attempts = 0
    item.last_error = None
    item.next_attempt_at = None


def retry_item(db: Session, user: User, item_id: uuid.UUID) -> PublishingQueueItem:
    """Re-queue a failed/cancelled item now (resumes a persisted upload session if any)."""
    item = get_owned(db, PublishingQueueItem, item_id, user, name="Queue item")
    if item.status not in RETRYABLE:
        raise ConflictError(f"Only failed or cancelled publications can be retried (status: {item.status}).",
                            code="not_retryable")
    _reopen(db, item, utcnow())
    history.record(db, item, "retry", QueueStatus.SCHEDULED, "Retry requested by user")
    _refresh_schedule(db, item)
    db.commit()
    return item


def publish_now(db: Session, user: User, item_id: uuid.UUID) -> PublishingQueueItem:
    """Publish immediately (bypasses the slot and any pause). Commits and dispatches."""
    item = get_owned(db, PublishingQueueItem, item_id, user, name="Queue item")
    if item.status in RETRYABLE:
        _reopen(db, item, utcnow())
    elif item.status in (QueueStatus.SCHEDULED, QueueStatus.PENDING_APPROVAL):
        item.status = QueueStatus.SCHEDULED
        item.scheduled_at = utcnow()
    else:
        raise ConflictError(f"A publication that is {item.status} cannot be published now.", code="not_publishable")
    history.record(db, item, "publish_now", QueueStatus.SCHEDULED, "Publish now requested by user")
    db.commit()
    claimed = dispatcher.claim(db, item.id)
    if claimed is not None:
        dispatcher.prepare_and_dispatch(db, claimed)
    db.refresh(item)
    return item


# ---------------------------------------------------------- history/calendar
def list_history(db: Session, user: User, *, clip_id: uuid.UUID | None = None, platform: str | None = None,
                 limit: int = 100) -> list[PublishingHistory]:
    H = PublishingHistory
    stmt = select(H).where(H.user_id == user.id)
    if clip_id:
        stmt = stmt.where(H.clip_id == clip_id)
    if platform:
        stmt = stmt.where(H.platform == platform)
    return list(db.scalars(stmt.order_by(H.created_at.desc()).limit(limit)))


def calendar(db: Session, user: User, start: datetime, end: datetime) -> list[dict[str, Any]]:
    """Queue entries (by publish/scheduled time) merged with history-only publications."""
    start, end = ensure_utc(start), ensure_utc(end)
    if end <= start or end - start > timedelta(days=370):
        raise ValidationFailed("end must be after start and the range at most ~1 year")
    Q, H = PublishingQueueItem, PublishingHistory
    items = [i for i in db.scalars(select(Q).where(Q.user_id == user.id, Q.status != QueueStatus.CANCELLED))
             if (t := i.published_at or i.scheduled_at) is not None and start <= t < end]
    hist = list(db.scalars(select(H).where(H.user_id == user.id, H.queue_item_id.is_(None),
                                           H.action.in_(("publish", "failed")), H.created_at >= start,
                                           H.created_at < end)))
    clips, accounts = _lookups(db, {i.clip_id for i in items} | {h.clip_id for h in hist if h.clip_id},
                               {i.connected_account_id for i in items}
                               | {h.connected_account_id for h in hist if h.connected_account_id})
    out = []
    for i in items:
        clip, acc = clips.get(i.clip_id), accounts.get(i.connected_account_id)
        out.append({"kind": "queue", "id": i.id, "at": i.published_at or i.scheduled_at, "platform": i.platform,
                    "status": i.status, "clip_id": i.clip_id, "clip_title": clip.title if clip else "",
                    "thumbnail_url": _thumb(clip), "account_id": i.connected_account_id,
                    "account_name": acc.display_name if acc else "", "external_url": i.external_url})
    for h in hist:
        clip, acc = clips.get(h.clip_id), accounts.get(h.connected_account_id)
        out.append({"kind": "history", "id": h.id, "at": h.created_at, "platform": h.platform, "status": h.status,
                    "clip_id": h.clip_id, "clip_title": clip.title if clip else "", "thumbnail_url": _thumb(clip),
                    "account_id": h.connected_account_id, "account_name": acc.display_name if acc else "",
                    "external_url": h.external_url})
    return sorted(out, key=lambda e: e["at"])


def next_times(db: Session, user: User) -> dict[str, Any]:
    paused = scheduling.is_user_paused(user)
    per_account = scheduling.next_publish_times(db, user)
    accounts = list(db.scalars(select(ConnectedAccount).where(ConnectedAccount.user_id == user.id)))
    rows = []
    for acc in accounts:
        schedule = _first_active_schedule(db, acc)
        free = scheduling.assign_next_slot(db, schedule) if schedule and not paused else None
        rows.append({"account_id": acc.id, "platform": acc.platform, "display_name": acc.display_name,
                     "next_publish_at": per_account.get(acc.id), "next_free_slot": free})
    return {"paused": paused, "next_publish_at": min(per_account.values()) if per_account else None,
            "accounts": rows}


def set_paused(db: Session, user: User, paused: bool) -> dict[str, bool]:
    scheduling.set_user_paused(db, user, paused)
    db.commit()
    return {"paused": paused}
