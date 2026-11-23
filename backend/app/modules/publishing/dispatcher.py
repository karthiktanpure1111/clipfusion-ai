"""Queue item lifecycle: claiming due items, render gating, job dispatch, failure handling.

Claiming is race-free across workers: PostgreSQL uses ``SELECT ... FOR UPDATE SKIP LOCKED``;
SQLite uses a conditional ``UPDATE ... WHERE status='scheduled'`` and checks ``rowcount``.
"""

from __future__ import annotations

import logging
import os
import socket
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.database import is_postgres, utcnow
from app.core.errors import ConflictError
from app.models import ConnectedAccount, GeneratedClip, PublishingQueueItem, PublishingSchedule, User
from app.models.enums import AccountStatus, JobType, NotificationType, QueueStatus, RenderStatus
from app.modules.publishing import history
from app.modules.publishing.errors import PERMANENT, QUOTA, TRANSIENT, classify
from app.modules.scheduling.service import refresh_item_schedule

log = logging.getLogger(__name__)

BACKOFF_MINUTES = (2, 10, 30, 120)
NOT_RENDERED_RETRY = timedelta(minutes=10)
WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"[:100]


def _notify_failed(db: Session, item: PublishingQueueItem, title: str, body: str, link: str | None = None) -> None:
    from app.modules.notifications.service import notify

    notify(db, item.user_id, NotificationType.PUBLISH_FAILED, title, body,
           link=link or f"/app/clips/{item.clip_id}",
           data={"queue_item_id": str(item.id), "clip_id": str(item.clip_id), "platform": item.platform})


def fail_item(db: Session, item: PublishingQueueItem, message: str, *, link: str | None = None) -> None:
    """Permanently fail an item (history + PUBLISH_FAILED notification; the clip shows
    ``upload_failed`` and its sequential upload run stops). Never commits."""
    from app.modules.upload_runs.service import on_publish_failed

    item.status = QueueStatus.FAILED
    item.last_error = message[:4000]
    item.locked_at = item.locked_by = None
    item.next_attempt_at = None
    history.record(db, item, "failed", QueueStatus.FAILED, message)
    refresh_item_schedule(db, item)
    if on_publish_failed(db, item, message):
        return  # the run's "Upload stopped" notification explains it
    platform = "YouTube" if item.platform == "youtube" else "Instagram"
    _notify_failed(db, item, f"Publishing to {platform} failed", message, link)


def handle_failure(db: Session, item: PublishingQueueItem, exc: BaseException, *, now: datetime | None = None) -> str:
    """Item-level retry policy. Returns the item's new status. Never commits.

    transient -> attempts += 1 and rescheduled after 2/10/30/120 min until max_attempts;
    quota     -> rescheduled at the quota reset (attempts unchanged);
    permanent -> failed + PUBLISH_FAILED notification.
    Items of a running sequential upload are never rescheduled: the run stops at the first failure
    and the user retries it (resuming the same resumable session)."""
    from app.modules.upload_runs.service import in_active_run, on_publish_rescheduled

    now = now or utcnow()
    err = classify(exc)
    item.locked_at = item.locked_by = None
    item.last_error = err.message[:4000]
    if in_active_run(db, item):
        item.attempts += 1
        message = err.message
        if err.kind == QUOTA:
            when = err.retry_at.isoformat() if err.retry_at else "the daily reset"
            message = f"{err.message} Retry after {when}."
        fail_item(db, item, message)
        return item.status
    if err.kind == QUOTA:
        when = max(err.retry_at or now + timedelta(days=1), now + timedelta(minutes=1))
        item.status = QueueStatus.SCHEDULED
        item.scheduled_at = item.next_attempt_at = when
        history.record(db, item, "retry", QueueStatus.SCHEDULED,
                       f"{err.message} Rescheduled for {when.isoformat()}.")
        refresh_item_schedule(db, item)
        on_publish_rescheduled(db, item, err.message)
        return item.status
    item.attempts += 1
    if err.kind == TRANSIENT and item.attempts < item.max_attempts:
        delay = timedelta(minutes=BACKOFF_MINUTES[min(item.attempts - 1, len(BACKOFF_MINUTES) - 1)])
        item.status = QueueStatus.SCHEDULED
        item.scheduled_at = item.next_attempt_at = now + delay
        history.record(db, item, "retry", QueueStatus.SCHEDULED,
                       f"Attempt {item.attempts}/{item.max_attempts} failed: {err.message}. "
                       f"Retrying in {int(delay.total_seconds() // 60)} min.")
        refresh_item_schedule(db, item)
        on_publish_rescheduled(db, item, err.message)
        return item.status
    message = err.message if err.kind == PERMANENT else (
        f"Gave up after {item.attempts} attempts: {err.message}")
    fail_item(db, item, message)
    return item.status


# ----------------------------------------------------------------- claiming
def due_candidates(db: Session, now: datetime, limit: int = 50) -> list[PublishingQueueItem]:
    """Due scheduled items, skipping paused schedules and paused users."""
    rows = db.execute(
        select(PublishingQueueItem, User.settings)
        .join(User, User.id == PublishingQueueItem.user_id)
        .outerjoin(PublishingSchedule, PublishingSchedule.id == PublishingQueueItem.schedule_id)
        .where(PublishingQueueItem.status == QueueStatus.SCHEDULED, PublishingQueueItem.scheduled_at <= now,
               (PublishingQueueItem.schedule_id.is_(None)) | (PublishingSchedule.is_active.is_(True)))
        .order_by(PublishingQueueItem.scheduled_at).limit(limit * 2)).all()
    return [item for item, settings in rows if not (settings or {}).get("publishing_paused")][:limit]


def claim(db: Session, item_id: uuid.UUID, *, now: datetime | None = None) -> PublishingQueueItem | None:
    """Atomically move one scheduled item to ``publishing``. Returns it when this worker won."""
    now = now or utcnow()
    if is_postgres(db):
        item = db.scalar(select(PublishingQueueItem).where(
            PublishingQueueItem.id == item_id, PublishingQueueItem.status == QueueStatus.SCHEDULED)
            .with_for_update(skip_locked=True))
        if item is None:
            db.rollback()
            return None
        item.status, item.locked_at, item.locked_by = QueueStatus.PUBLISHING, now, WORKER_ID
        db.commit()
        return item
    res = db.execute(update(PublishingQueueItem).where(
        PublishingQueueItem.id == item_id, PublishingQueueItem.status == QueueStatus.SCHEDULED)
        .values(status=QueueStatus.PUBLISHING, locked_at=now, locked_by=WORKER_ID)
        .execution_options(synchronize_session=False))
    db.commit()
    if res.rowcount != 1:
        return None
    item = db.get(PublishingQueueItem, item_id)
    if item is not None:
        db.refresh(item)
    return item


# ------------------------------------------------------------ render gating
def _render_state(db: Session, clip: GeneratedClip) -> str:
    """'current' | 'requested' | raises ConflictError (framing warnings) / other errors."""
    try:
        from app.modules.rendering.service import render_is_current, request_render
    except ImportError:
        if clip.output_key and clip.render_status == RenderStatus.RENDERED:
            return "current"
        return "unavailable"
    if render_is_current(clip):
        return "current"
    request_render(db, clip, acknowledge_warnings=False, reason="publish")
    return "requested"


def prepare_and_dispatch(db: Session, item: PublishingQueueItem) -> str:
    """For a claimed item: verify account, ensure a current render, then dispatch the publish job.

    Returns the resulting item status. Commits."""
    from app.workers.jobs import create_job, dispatch_job

    account = db.get(ConnectedAccount, item.connected_account_id)
    clip = db.get(GeneratedClip, item.clip_id)
    if clip is None or account is None:
        fail_item(db, item, "The clip or the connected account no longer exists.")
        db.commit()
        return item.status
    if account.status != AccountStatus.ACTIVE:
        fail_item(db, item, f"The {account.platform} account '{account.display_name}' needs to be reconnected "
                  f"({account.last_error or account.status}).", link="/app/accounts")
        db.commit()
        return item.status
    try:
        state = _render_state(db, clip)
    except ConflictError as exc:
        db.rollback()
        item = db.get(PublishingQueueItem, item.id)
        fail_item(db, item, f"Cannot publish until the framing warnings are reviewed: {exc.message}",
                  link=f"/app/clips/{item.clip_id}/framing")
        db.commit()
        return item.status
    except Exception as exc:  # noqa: BLE001 - render service unavailable: retry later
        db.rollback()
        item = db.get(PublishingQueueItem, item.id)
        handle_failure(db, item, exc)
        db.commit()
        return item.status
    if state == "requested":
        item = db.get(PublishingQueueItem, item.id)
        item.status = QueueStatus.RENDERING
        item.locked_at = item.locked_by = None
        history.record(db, item, "render", QueueStatus.RENDERING, "Rendering the clip before publishing")
        db.commit()
        return item.status
    if state == "unavailable":
        item.status = QueueStatus.SCHEDULED
        item.scheduled_at = utcnow() + NOT_RENDERED_RETRY
        item.locked_at = item.locked_by = None
        item.last_error = "The clip has not been rendered yet; waiting for the render."
        db.commit()
        return item.status
    job, _created = create_job(db, user_id=item.user_id, job_type=JobType.PUBLISH, clip_id=item.clip_id,
                               queue_item_id=item.id, idempotency_key=f"publish:{item.id}", max_attempts=1,
                               params={"queue_item_id": str(item.id)})
    refresh_item_schedule(db, item)
    dispatch_job(db, job, "publish_queue_item")
    return item.status


def release_rendered(db: Session) -> int:
    """Safety net: items waiting in ``rendering`` whose clip render finished/failed."""
    rows = db.execute(select(PublishingQueueItem, GeneratedClip)
                      .join(GeneratedClip, GeneratedClip.id == PublishingQueueItem.clip_id)
                      .where(PublishingQueueItem.status == QueueStatus.RENDERING,
                             GeneratedClip.render_status.in_((RenderStatus.RENDERED, RenderStatus.FAILED)))).all()
    for item, clip in rows:
        if clip.render_status == RenderStatus.RENDERED:
            item.status = QueueStatus.SCHEDULED
        else:
            fail_item(db, item, f"Render failed: {clip.render_error or 'unknown error'}")
    db.commit()
    return len(rows)


def dispatch_due(db: Session, *, now: datetime | None = None, limit: int = 50) -> int:
    """Claim and dispatch due items. Returns how many were claimed by this worker.

    At most one item per connected account transfers at a time: items of an account that is
    already uploading wait for the next pass."""
    from app.modules.scheduling.timeline import shift_missed

    now = now or utcnow()
    release_rendered(db)
    try:
        shift_missed(db, now=now)
    except Exception:  # noqa: BLE001 - never block publishing
        db.rollback()
        log.exception("Shifting missed timeline uploads failed")
    claimed = 0
    busy = set(db.scalars(select(PublishingQueueItem.connected_account_id).where(
        PublishingQueueItem.status == QueueStatus.PUBLISHING)))
    for cand in due_candidates(db, now, limit):
        if cand.connected_account_id in busy:
            continue
        item = claim(db, cand.id, now=now)
        if item is None:
            continue
        claimed += 1
        account_id = item.connected_account_id
        try:
            if prepare_and_dispatch(db, item) == QueueStatus.PUBLISHING:
                busy.add(account_id)  # transferring now: the account's other items wait
        except Exception:
            db.rollback()
            log.exception("Dispatching queue item %s failed", cand.id)
            fresh = db.get(PublishingQueueItem, cand.id)
            if fresh is not None and fresh.status == QueueStatus.PUBLISHING:
                fresh.status = QueueStatus.SCHEDULED
                fresh.scheduled_at = now + timedelta(minutes=BACKOFF_MINUTES[0])
                fresh.locked_at = fresh.locked_by = None
                db.commit()
    return claimed
