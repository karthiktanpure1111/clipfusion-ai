"""Publishing tasks: publish one queue item, dispatch due items, poll remote processing,
advance sequential upload runs, refresh OAuth tokens and fetch real platform analytics.

After a YouTube upload returns a video id the clip is marked uploaded, its upload is verified
with ``videos.list`` and only then its media is deleted (``app.modules.storage.cleanup``); the
clip's upload run then starts the next clip."""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import SessionLocal, utcnow
from app.models import ConnectedAccount, GeneratedClip, ProcessingJob, PublishingQueueItem
from app.models.enums import AccountStatus, JobType, QueueStatus
from app.modules.publishing import dispatcher, publishers
from app.modules.publishing.errors import PERMANENT, QUOTA, TRANSIENT, PublishError, classify
from app.modules.upload_runs import service as upload_runs
from app.workers.jobs import JobContext, PermanentJobError, run_job
from app.workers.registry import periodic, task

log = logging.getLogger(__name__)

POLL_BATCH = 100
TOKEN_REFRESH_MARGIN = timedelta(minutes=35)


# --------------------------------------------------------------- publish job
@task("publish_queue_item", queue="publish", job_type=JobType.PUBLISH)
def publish_queue_item(job_id: str) -> None:
    """Upload/publish one queue item (job max_attempts=1; retries happen at item level)."""
    run_job(job_id, publish_handler, task_name="publish_queue_item")


def publish_handler(ctx: JobContext, db: Session, job: ProcessingJob) -> dict[str, Any] | None:
    item = db.get(PublishingQueueItem, job.queue_item_id) if job.queue_item_id else None
    if item is None:
        raise PermanentJobError("Queue item no longer exists")
    if item.status != QueueStatus.PUBLISHING:
        log.info("Queue item %s is %s; nothing to publish", item.id, item.status)
        return {"skipped": item.status}
    account = db.get(ConnectedAccount, item.connected_account_id)
    clip = db.get(GeneratedClip, item.clip_id)
    ctx.progress("publishing", 0.05, f"Publishing to {item.platform}", force=True)
    try:
        if account is None or clip is None:
            raise PublishError(PERMANENT, "The clip or the connected account no longer exists.")
        if clip.removed_at is not None:
            raise PublishError(PERMANENT, "The clip was removed.")
        upload_runs.on_publish_started(db, item)
        db.commit()
        publishers.publish(db, item, account, clip, ctx.progress_fn("uploading", 0.1, 0.9))
        db.commit()
    except Exception as exc:  # noqa: BLE001 - item-level bookkeeping, then fail the job permanently
        db.rollback()
        item = db.get(PublishingQueueItem, job.queue_item_id)
        if item is not None:
            new_status = dispatcher.handle_failure(db, item, exc)
            db.commit()
            log.warning("Publishing item %s failed -> %s: %s", item.id, new_status, item.last_error)
        raise PermanentJobError(str(classify(exc))) from exc
    result = {"status": item.status, "external_id": item.external_id, "external_url": item.external_url}
    if item.platform == "youtube" and item.external_id and item.status in (QueueStatus.PROCESSING_REMOTE,
                                                                          QueueStatus.PUBLISHED):
        upload_runs.on_upload_succeeded(db, item)  # verify + clean up, then the run's next clip
    return result


# --------------------------------------------------------------- periodic
@task("dispatch_due_publications", queue="publish")
def dispatch_due_publications() -> int:
    """Claim due queue items and create their publish jobs (every 60 s)."""
    with SessionLocal() as db:
        return dispatcher.dispatch_due(db)


@task("poll_remote_publications", queue="publish")
def poll_remote_publications() -> int:
    """Advance items the platform is still processing (every 120 s)."""
    with SessionLocal() as db:
        ids = list(db.scalars(select(PublishingQueueItem.id).where(
            PublishingQueueItem.status == QueueStatus.PROCESSING_REMOTE)
            .order_by(PublishingQueueItem.updated_at).limit(POLL_BATCH)))
        for item_id in ids:
            poll_item(db, item_id)
        return len(ids)


def poll_item(db: Session, item_id: uuid.UUID) -> str | None:
    """Poll one processing_remote item; transient errors leave it for the next poll."""
    item = db.get(PublishingQueueItem, item_id)
    if item is None or item.status != QueueStatus.PROCESSING_REMOTE:
        return None
    account = db.get(ConnectedAccount, item.connected_account_id)
    try:
        if account is None:
            raise PublishError(PERMANENT, "The connected account was removed.")
        publishers.poll(db, item, account)
        db.commit()
        if item.status == QueueStatus.PUBLISHED and item.platform == "youtube":
            upload_runs.on_upload_succeeded(db, item, advance_run=False)  # processed: verify + clean up
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        item = db.get(PublishingQueueItem, item_id)
        err = classify(exc)
        if err.kind in (TRANSIENT, QUOTA):
            item.last_error = err.message[:4000]
            log.info("Polling item %s deferred: %s", item_id, err.message)
        else:
            dispatcher.fail_item(db, item, err.message)
        db.commit()
    return item.status


@task("advance_upload_runs", queue="publish")
def advance_upload_runs() -> int:
    """Safety net for sequential uploads (every 60 s): finish bookkeeping lost to a crash, resume
    interrupted transfers and start items that waited for a busy account."""
    with SessionLocal() as db:
        return upload_runs.reconcile_runs(db)


@task("refresh_account_tokens", queue="default")
def refresh_account_tokens() -> int:
    """Keep OAuth tokens fresh and detect revoked grants early (every 1800 s)."""
    from app.modules.accounts.tokens import ensure_token

    refreshed = 0
    with SessionLocal() as db:
        ids = list(db.scalars(select(ConnectedAccount.id).where(ConnectedAccount.status.in_(
            (AccountStatus.ACTIVE, AccountStatus.EXPIRED)))))
        for acc_id in ids:
            acc = db.get(ConnectedAccount, acc_id)
            try:
                ensure_token(db, acc, margin=TOKEN_REFRESH_MARGIN)
                acc.last_checked_at = utcnow()
                db.commit()
                refreshed += 1
            except Exception as exc:  # noqa: BLE001 - recorded on the account by the token layer
                db.rollback()
                log.info("Token refresh for account %s failed: %s", acc_id, exc)
    return refreshed


@task("refresh_platform_analytics", queue="default", job_type=JobType.ANALYTICS)
def refresh_platform_analytics(job_id: str | None = None) -> int | None:
    """Fetch real statistics for published clips. Periodic (all users, every 6 h) or as a
    user-requested analytics job (``job_id``)."""
    from app.modules.analytics.collector import collect_platform_stats

    if job_id:
        def handler(ctx: JobContext, db: Session, job: ProcessingJob) -> dict[str, Any]:
            ctx.progress("fetching", 0.1, "Fetching platform statistics", force=True)
            return collect_platform_stats(db, user_id=job.user_id)

        run_job(job_id, handler, task_name="refresh_platform_analytics")
        return None
    with SessionLocal() as db:
        return int(collect_platform_stats(db).get("snapshots", 0))


periodic("dispatch_due_publications", every_seconds=60)
periodic("poll_remote_publications", every_seconds=120)
periodic("advance_upload_runs", every_seconds=60)
periodic("refresh_account_tokens", every_seconds=1800)
periodic("refresh_platform_analytics", every_seconds=21600)
