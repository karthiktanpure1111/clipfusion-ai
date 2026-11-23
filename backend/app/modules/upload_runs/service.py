"""Sequential YouTube uploads (``/upload-runs``).

A run uploads a source's clips **one at a time, in sequence order** (source timestamp order)
through the existing publishing pipeline: each clip gets (or reuses) its ``clip:account``
publishing queue item, which is claimed and dispatched as a ``publish_queue_item`` job (YouTube
resumable upload, session URI persisted). The run advances when that job reports the upload
transfer finished (YouTube returned a video id).

Guarantees
* one running run per connected account (partial unique index) and, through the dispatcher, one
  transfer at a time per account;
* the run **stops at the first failure** -- the clip is never skipped or marked done -- and Retry
  resumes from that clip; Cancel stops after the current transfer (never interrupts it);
* restarts never upload twice: the queue item's idempotency key, the persisted resumable
  session URI and the reconciliation pass (``reconcile_runs``) resume interrupted transfers.

After a verified upload the clip's media is deleted (``app.modules.storage.cleanup``)."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import is_postgres, utcnow
from app.core.deps import get_owned
from app.core.errors import ConflictError, NotFoundError, ValidationFailed
from app.integrations.google_oauth import SCOPE_YOUTUBE_UPLOAD
from app.models import (
    ConnectedAccount,
    GeneratedClip,
    ProcessingJob,
    PublishingQueueItem,
    UploadRun,
    UploadRunItem,
    User,
    VideoSource,
)
from app.models.enums import (
    AccountStatus,
    CleanupStatus,
    JobType,
    NotificationType,
    Platform,
    QueueStatus,
    UploadRunItemStatus,
    UploadRunMode,
    UploadRunStatus,
    UploadStatus,
)
from app.modules.upload_runs.schemas import UploadRunCreate

log = logging.getLogger(__name__)

RIGHTS_MESSAGE = ("Confirm that you have the creator's permission, a licence, or other rights to publish this "
                  "content.")
DONE_REMOTE = (QueueStatus.PUBLISHED, QueueStatus.PROCESSING_REMOTE)
NOT_TRANSFERRING = (QueueStatus.SCHEDULED, QueueStatus.RENDERING, QueueStatus.PENDING_APPROVAL)
UPLOAD_PROGRESS_SPAN = (0.1, 0.9)  # publish job progress range of the byte transfer
MAX_RESUMES = 4  # publish jobs per clip transfer before an interrupted upload counts as failed


# ------------------------------------------------------------- eligibility
def _ordered_clips(db: Session, source_id: uuid.UUID) -> list[GeneratedClip]:
    return list(db.scalars(select(GeneratedClip).where(GeneratedClip.video_source_id == source_id,
                                                       GeneratedClip.removed_at.is_(None))
                           .order_by(GeneratedClip.start_time, GeneratedClip.end_time, GeneratedClip.id)))


def uploadable(clip: GeneratedClip) -> bool:
    """Generated (current render) and not uploaded yet (a failed upload can be retried)."""
    from app.modules.rendering.service import render_is_current

    return (clip.removed_at is None and clip.cleanup_status != CleanupStatus.CLEANED
            and clip.upload_status in (UploadStatus.NONE, UploadStatus.FAILED) and render_is_current(clip))


def _account(db: Session, user: User, account_id: uuid.UUID) -> ConnectedAccount:
    account = get_owned(db, ConnectedAccount, account_id, user, name="Account")
    if account.platform != Platform.YOUTUBE:
        raise ValidationFailed("Sequential uploads go to a YouTube account.", code="not_youtube")
    if account.status != AccountStatus.ACTIVE:
        raise ConflictError(f"The YouTube account '{account.display_name}' must be reconnected before uploading "
                            f"({account.last_error or account.status}).", code="youtube_auth_expired")
    if SCOPE_YOUTUBE_UPLOAD not in (account.scopes or []):
        raise ConflictError("The YouTube connection lacks the upload permission; reconnect it and allow uploads.",
                            code="permission_missing")
    return account


def _running_run(db: Session, account_id: uuid.UUID, *, exclude: uuid.UUID | None = None) -> UploadRun | None:
    stmt = select(UploadRun).where(UploadRun.connected_account_id == account_id,
                                   UploadRun.status == UploadRunStatus.RUNNING)
    if exclude is not None:
        stmt = stmt.where(UploadRun.id != exclude)
    return db.scalar(stmt.limit(1))


def _in_progress(run: UploadRun) -> ConflictError:
    return ConflictError("Another upload is already running on this YouTube account; wait for it to finish or "
                         "cancel it.", code="upload_in_progress", details={"upload_run_id": str(run.id)})


def _select_clips(db: Session, source: VideoSource, data: UploadRunCreate) -> list[GeneratedClip]:
    from app.modules.scheduling.timeline import ACTIVE, scheduled_clip_ids

    clips = _ordered_clips(db, source.id)
    if data.mode == UploadRunMode.SINGLE:
        clip = db.get(GeneratedClip, data.clip_id)
        if clip is None or clip.video_source_id != source.id or clip.user_id != source.user_id:
            raise NotFoundError("Clip not found in this video.")
        if clip.removed_at is not None:
            raise ConflictError("This clip was removed.", code="clip_removed")
        if clip.upload_status == UploadStatus.UPLOADED:
            raise ConflictError("This clip was already uploaded.", code="already_uploaded",
                                details={"youtube_url": clip.youtube_url})
        if clip.upload_status == UploadStatus.UPLOADING:
            raise ConflictError("This clip is being uploaded right now.", code="clip_uploading")
        if not uploadable(clip):
            raise ConflictError("This clip is not generated yet (it is still processing, needs a framing review, "
                                "or its render is out of date).", code="clip_not_ready")
        return [clip]
    # scheduled clips keep their time (upload one of them now with mode "single")
    scheduled = scheduled_clip_ids(db, [c.id for c in clips], statuses=ACTIVE)
    ready = [c for c in clips if uploadable(c) and c.id not in scheduled]
    if data.mode == UploadRunMode.NEXT:
        ready = ready[:1]
    if not ready:
        raise ConflictError("There is no generated clip left to upload for this video (scheduled clips upload at "
                            "their time).", code="nothing_to_upload")
    return ready


# ------------------------------------------------------------------ create
def create_run(db: Session, user: User, data: UploadRunCreate) -> UploadRun:
    """POST /upload-runs. Commits and starts the first upload."""
    from app.modules.users.service import read_settings

    if not data.rights_confirmed:
        raise ValidationFailed(RIGHTS_MESSAGE, code="rights_not_confirmed")
    source = get_owned(db, VideoSource, data.video_source_id, user, name="Video")
    account = _account(db, user, data.connected_account_id)
    running = _running_run(db, account.id)
    if running is not None:
        raise _in_progress(running)
    clips = _select_clips(db, source, data)
    run = UploadRun(id=uuid.uuid4(), user_id=user.id, video_source_id=source.id, connected_account_id=account.id,
                    mode=data.mode, privacy=data.privacy or read_settings(user).default_privacy,
                    status=UploadRunStatus.RUNNING, rights_confirmed_at=utcnow())
    db.add(run)
    db.flush()
    for pos, clip in enumerate(clips, start=1):
        db.add(UploadRunItem(run_id=run.id, clip_id=clip.id, position=pos, status=UploadRunItemStatus.PENDING))
    try:
        db.commit()
    except IntegrityError as exc:  # another request started a run on this account at the same moment
        db.rollback()
        running = _running_run(db, account.id)
        if running is not None:
            raise _in_progress(running) from exc
        raise
    log.info("Upload run %s started: %d clips of source %s -> account %s", run.id, len(clips), source.id, account.id)
    advance(db, run.id)
    return db.get(UploadRun, run.id)


# --------------------------------------------------------------- queue item
def _payload(db: Session, clip: GeneratedClip, run: UploadRun) -> dict[str, Any]:
    from app.modules.publishing.payloads import build_payload

    payload = build_payload(clip, Platform.YOUTUBE, overrides={"privacy": run.privacy})
    source = db.get(VideoSource, clip.video_source_id)
    credit = (source.attribution or "").strip() if source is not None else ""
    if credit and credit.lower() not in (payload.get("description") or "").lower():
        payload["description"] = ((payload.get("description") or "").rstrip() + "\n\n" + credit).strip()[:5000]
    return payload


def _queue_item(db: Session, run: UploadRun, clip: GeneratedClip) -> PublishingQueueItem:
    """The clip's queue item for the run's account, (re)opened to publish now. Never commits.
    Already uploaded or in-flight items are returned unchanged (never uploaded twice)."""
    from app.modules.publishing import history

    key = f"{clip.id}:{run.connected_account_id}"
    item = db.scalar(select(PublishingQueueItem).where(PublishingQueueItem.idempotency_key == key))
    now = utcnow()
    if item is not None and (item.status in DONE_REMOTE and item.external_id):
        return item
    if item is not None and item.status in (QueueStatus.PUBLISHING, QueueStatus.RENDERING):
        return item
    payload = _payload(db, clip, run)
    if item is None:
        item = PublishingQueueItem(id=uuid.uuid4(), user_id=clip.user_id, clip_id=clip.id,
                                   connected_account_id=run.connected_account_id, platform=Platform.YOUTUBE,
                                   idempotency_key=key, status=QueueStatus.SCHEDULED, attempts=0)
        db.add(item)
    if item.external_id:  # a previous upload was rejected/failed on YouTube: upload again from scratch
        item.external_id = item.external_url = item.remote_status = None
        item.upload_session_url = None
    # A failed transfer keeps its resumable session URI so the retry resumes instead of re-uploading.
    item.status = QueueStatus.SCHEDULED
    item.scheduled_at = now
    item.payload = payload
    item.schedule_id = None
    item.rights_confirmed = True
    item.attempts = 0
    item.max_attempts = 1
    item.next_attempt_at = None
    item.last_error = None
    item.locked_at = item.locked_by = None
    item.published_at = None
    item.origin = None
    history.record(db, item, "scheduled", QueueStatus.SCHEDULED,
                   f"Sequential upload (run {str(run.id)[:8]}): uploading now")
    db.flush()
    return item


def account_busy(db: Session, account_id: uuid.UUID, *, exclude: uuid.UUID | None = None) -> bool:
    """True while another item of the account is transferring (one upload at a time per account)."""
    stmt = select(PublishingQueueItem.id).where(PublishingQueueItem.connected_account_id == account_id,
                                                PublishingQueueItem.status == QueueStatus.PUBLISHING)
    if exclude is not None:
        stmt = stmt.where(PublishingQueueItem.id != exclude)
    return db.scalar(stmt.limit(1)) is not None


def _dispatch(db: Session, item: PublishingQueueItem) -> None:
    """Claim and dispatch the item now unless the account is busy (the periodic dispatcher then
    picks it up as soon as the account is free). Commits."""
    from app.modules.publishing import dispatcher

    if item.status != QueueStatus.SCHEDULED or account_busy(db, item.connected_account_id, exclude=item.id):
        return
    claimed = dispatcher.claim(db, item.id)
    if claimed is not None:
        dispatcher.prepare_and_dispatch(db, claimed)


def _start_item(db: Session, run: UploadRun, run_item: UploadRunItem, clip: GeneratedClip) -> bool:
    """Start uploading ``clip``. Returns True when its queue item shows it was uploaded already."""
    run_item.status, run_item.error = UploadRunItemStatus.UPLOADING, None
    run_item.started_at, run_item.finished_at, run_item.progress = utcnow(), None, 0.0
    run_item.attempts += 1
    run.current_clip_id, run.last_error = clip.id, None
    clip.upload_status, clip.upload_error = UploadStatus.UPLOADING, None
    item = _queue_item(db, run, clip)
    run_item.queue_item_id = item.id
    db.commit()
    if item.status in DONE_REMOTE and item.external_id:
        _mark_item_uploaded(db, run, run_item, item, clip)
        db.commit()
        return True
    _dispatch(db, item)
    return False


# ------------------------------------------------------------- transitions
def _run_items(db: Session, run_id: uuid.UUID) -> list[UploadRunItem]:
    db.flush()  # populate_existing must not discard pending changes (sessions do not autoflush)
    return list(db.scalars(select(UploadRunItem).where(UploadRunItem.run_id == run_id)
                           .order_by(UploadRunItem.position).execution_options(populate_existing=True)))


def _finish(db: Session, run: UploadRun, status: str, *, notify_user: bool = True) -> None:
    from app.modules.notifications.service import notify

    run.status, run.finished_at, run.current_clip_id = status, utcnow(), None
    if not notify_user:
        return
    items = _run_items(db, run.id)
    uploaded = sum(1 for i in items if i.status == UploadRunItemStatus.UPLOADED)
    if status == UploadRunStatus.COMPLETED:
        title, body = "Uploads finished", f"{uploaded} clip{'s' if uploaded != 1 else ''} uploaded to YouTube in order."
    elif status == UploadRunStatus.FAILED:
        title = "Upload stopped"
        body = f"The upload stopped at a failed clip: {run.last_error or 'unknown error'} Retry to resume from it."
    else:
        title, body = "Uploads cancelled", f"{uploaded} clip{'s' if uploaded != 1 else ''} were uploaded before you " \
                                           "cancelled."
    notify(db, run.user_id, NotificationType.UPLOAD_RUN, title, body[:2000],
           link=f"/app/videos/{run.video_source_id}", data={"upload_run_id": str(run.id), "status": status})


def _lock_run(db: Session, run_id: uuid.UUID) -> UploadRun | None:
    db.flush()
    stmt = select(UploadRun).where(UploadRun.id == run_id)
    if is_postgres(db):
        stmt = stmt.with_for_update()
    return db.scalars(stmt.execution_options(populate_existing=True)).first()


def advance(db: Session, run_id: uuid.UUID) -> UploadRun | None:
    """Reconcile the current upload and start the next clip, or finish the run. Idempotent; commits."""
    run = _lock_run(db, run_id)
    if run is None or run.status != UploadRunStatus.RUNNING:
        db.commit()
        return run
    items = _run_items(db, run.id)
    current = next((i for i in items if i.status == UploadRunItemStatus.UPLOADING), None)
    if current is not None:
        _reconcile(db, run, current)
        if current.status == UploadRunItemStatus.UPLOADING or run.status != UploadRunStatus.RUNNING:
            db.commit()
            return run
    failed = next((i for i in items if i.status == UploadRunItemStatus.FAILED), None)
    if failed is not None:
        run.failed_clip_id = failed.clip_id
        run.last_error = run.last_error or failed.error
        _finish(db, run, UploadRunStatus.FAILED)
        db.commit()
        return run
    if run.cancel_requested:
        for i in items:
            if i.status == UploadRunItemStatus.PENDING:
                i.status = UploadRunItemStatus.CANCELLED
        _finish(db, run, UploadRunStatus.CANCELLED)
        db.commit()
        return run
    for nxt in (i for i in items if i.status == UploadRunItemStatus.PENDING):
        clip = db.get(GeneratedClip, nxt.clip_id)
        if clip is None or clip.removed_at is not None:
            nxt.status, nxt.error = UploadRunItemStatus.CANCELLED, "The clip was removed."
            continue
        if clip.upload_status == UploadStatus.UPLOADED and clip.youtube_video_id:
            nxt.status, nxt.finished_at = UploadRunItemStatus.UPLOADED, utcnow()
            nxt.youtube_video_id, nxt.youtube_url, nxt.progress = clip.youtube_video_id, clip.youtube_url, 1.0
            continue
        if _start_item(db, run, nxt, clip):
            continue  # it turned out to be uploaded already (never uploaded twice)
        return db.get(UploadRun, run.id)
    _finish(db, run, UploadRunStatus.COMPLETED)
    db.commit()
    return run


def _publish_job(db: Session, item_id: uuid.UUID) -> ProcessingJob | None:
    return db.scalar(select(ProcessingJob).where(ProcessingJob.queue_item_id == item_id,
                                                 ProcessingJob.job_type == JobType.PUBLISH)
                     .order_by(ProcessingJob.created_at.desc()).limit(1)
                     .execution_options(populate_existing=True))


def transfer_progress(job: ProcessingJob | None) -> float | None:
    if job is None:
        return None
    lo, hi = UPLOAD_PROGRESS_SPAN
    if job.status == "succeeded":
        return 1.0
    return round(min(1.0, max(0.0, (float(job.progress or 0.0) - lo) / (hi - lo))), 4)


def _reconcile(db: Session, run: UploadRun, run_item: UploadRunItem) -> None:
    """Bring an ``uploading`` run item in line with its queue item (crash/restart safe). No commit."""
    from app.workers.jobs import ACTIVE_STATUSES, create_job, dispatch_job

    db.flush()
    item = None
    if run_item.queue_item_id is not None:
        item = db.get(PublishingQueueItem, run_item.queue_item_id, populate_existing=True)
    clip = db.get(GeneratedClip, run_item.clip_id, populate_existing=True)
    if item is None:  # interrupted before the queue item existed: start this clip again
        run_item.status = UploadRunItemStatus.PENDING
        if clip is not None and clip.upload_status == UploadStatus.UPLOADING:
            clip.upload_status = UploadStatus.NONE
        return
    if item.status in DONE_REMOTE and item.external_id:
        _mark_item_uploaded(db, run, run_item, item, clip)
        return
    if item.status in (QueueStatus.FAILED, QueueStatus.CANCELLED):
        message = item.last_error or ("The upload was cancelled." if item.status == QueueStatus.CANCELLED
                                      else "The upload failed.")
        _mark_item_failed(db, run, run_item, clip, message)
        return
    job = _publish_job(db, item.id)
    if item.status == QueueStatus.PUBLISHING and (job is None or job.status not in ACTIVE_STATUSES):
        # The publish job died (e.g. restart) while the item was transferring: resume it. The
        # resumable session URI on the item makes YouTube continue instead of starting over.
        tries = db.scalar(select(func.count(ProcessingJob.id)).where(
            ProcessingJob.queue_item_id == item.id, ProcessingJob.job_type == JobType.PUBLISH,
            ProcessingJob.created_at >= (run_item.started_at or item.created_at))) or 0
        if tries >= MAX_RESUMES:
            from app.modules.publishing.dispatcher import fail_item

            fail_item(db, item, "The upload was interrupted repeatedly; retry it when the connection is stable.")
            return
        new_job, created = create_job(db, user_id=item.user_id, job_type=JobType.PUBLISH, clip_id=item.clip_id,
                                      queue_item_id=item.id, idempotency_key=f"publish:{item.id}", max_attempts=1,
                                      params={"queue_item_id": str(item.id), "resumed": True})
        if created:
            log.info("Resuming interrupted upload of queue item %s (run %s)", item.id, run.id)
            dispatch_job(db, new_job, "publish_queue_item")
        return
    if item.status == QueueStatus.SCHEDULED:
        db.commit()
        _dispatch(db, item)
        return
    run_item.progress = transfer_progress(job) if item.status == QueueStatus.PUBLISHING else run_item.progress


def _mark_item_uploaded(db: Session, run: UploadRun, run_item: UploadRunItem, item: PublishingQueueItem,
                        clip: GeneratedClip | None) -> None:
    run_item.status, run_item.progress, run_item.finished_at = UploadRunItemStatus.UPLOADED, 1.0, utcnow()
    run_item.youtube_video_id, run_item.youtube_url, run_item.error = item.external_id, item.external_url, None
    if run.current_clip_id == run_item.clip_id:
        run.current_clip_id = None
    if clip is not None:
        _apply_clip_uploaded(clip, item)


def _mark_item_failed(db: Session, run: UploadRun, run_item: UploadRunItem, clip: GeneratedClip | None,
                      message: str) -> None:
    run_item.status, run_item.finished_at, run_item.error = UploadRunItemStatus.FAILED, utcnow(), message[:2000]
    run.failed_clip_id, run.last_error = run_item.clip_id, message[:2000]
    if clip is not None and clip.upload_status != UploadStatus.UPLOADED:
        clip.upload_status, clip.upload_error = UploadStatus.FAILED, message[:2000]


def _apply_clip_uploaded(clip: GeneratedClip, item: PublishingQueueItem) -> None:
    clip.upload_status = UploadStatus.UPLOADED
    clip.youtube_video_id = (item.external_id or "")[:32] or clip.youtube_video_id
    clip.youtube_url = item.external_url or (f"https://www.youtube.com/watch?v={item.external_id}"
                                             if item.external_id else clip.youtube_url)
    clip.uploaded_at = clip.uploaded_at or utcnow()
    clip.upload_error = None


def _active_run_item(db: Session, item: PublishingQueueItem) -> tuple[UploadRun, UploadRunItem] | None:
    row = db.execute(select(UploadRun, UploadRunItem).join(UploadRunItem, UploadRunItem.run_id == UploadRun.id)
                     .where(UploadRunItem.queue_item_id == item.id,
                            UploadRunItem.status == UploadRunItemStatus.UPLOADING,
                            UploadRun.status == UploadRunStatus.RUNNING).limit(1)).first()
    return (row[0], row[1]) if row is not None else None


# --------------------------------------------------------- publishing hooks
def on_publish_started(db: Session, item: PublishingQueueItem) -> None:
    """A YouTube transfer starts: the clip shows ``uploading``. No commit."""
    if item.platform != Platform.YOUTUBE:
        return
    clip = db.get(GeneratedClip, item.clip_id)
    if clip is not None and clip.upload_status != UploadStatus.UPLOADED:
        clip.upload_status, clip.upload_error = UploadStatus.UPLOADING, None


def on_upload_succeeded(db: Session, item: PublishingQueueItem, *, advance_run: bool = True) -> None:
    """YouTube returned a video id: record it on the clip and the run, verify + clean up the clip's
    media, then start the next clip of the run. Commits; never raises."""
    from app.modules.storage.cleanup import cleanup_after_upload

    if item.platform != Platform.YOUTUBE or not item.external_id:
        return
    try:
        clip = db.get(GeneratedClip, item.clip_id)
        if clip is not None:
            _apply_clip_uploaded(clip, item)
        found = _active_run_item(db, item)
        if found is not None:
            _mark_item_uploaded(db, found[0], found[1], item, clip)
        db.commit()
        outcome = cleanup_after_upload(db, clip) if clip is not None else None
        if found is not None and outcome == "upload_failed":  # YouTube rejected/failed it: stop here
            run = db.get(UploadRun, found[0].id)
            run_item = db.get(UploadRunItem, found[1].id)
            if run is not None and run.status == UploadRunStatus.RUNNING and run_item is not None:
                _mark_item_failed(db, run, run_item, clip, clip.upload_error or "The YouTube upload failed.")
                _finish(db, run, UploadRunStatus.FAILED)
                db.commit()
            return
        if found is not None and advance_run:
            advance(db, found[0].id)
    except Exception:  # noqa: BLE001 - the reconciliation pass finishes the bookkeeping
        db.rollback()
        log.exception("Post-upload bookkeeping of queue item %s failed", item.id)


def on_publish_failed(db: Session, item: PublishingQueueItem, message: str) -> bool:
    """The queue item failed (or a later YouTube check rejected the video). Records the failure on
    the clip and stops the item's run (Retry resumes from this clip). Returns True when the item
    belonged to a running upload run. Never commits."""
    clip = db.get(GeneratedClip, item.clip_id)
    if item.platform == Platform.YOUTUBE and clip is not None:
        if clip.upload_status != UploadStatus.UPLOADED or (item.external_id and item.external_id == clip.youtube_video_id):
            clip.upload_status, clip.upload_error = UploadStatus.FAILED, message[:2000]
    found = _active_run_item(db, item)
    if found is None:
        # A clip of a running run that was uploaded earlier and rejected afterwards also stops the run.
        row = db.execute(select(UploadRun, UploadRunItem).join(UploadRunItem, UploadRunItem.run_id == UploadRun.id)
                         .where(UploadRunItem.queue_item_id == item.id,
                                UploadRunItem.status == UploadRunItemStatus.UPLOADED,
                                UploadRun.status == UploadRunStatus.RUNNING).limit(1)).first()
        if row is None:
            return False
        run, run_item = row[0], row[1]
        _mark_item_failed(db, run, run_item, clip, message)
        if not any(i.status == UploadRunItemStatus.UPLOADING for i in _run_items(db, run.id)):
            _finish(db, run, UploadRunStatus.FAILED)
        return True
    run, run_item = found
    _mark_item_failed(db, run, run_item, clip, message)
    _finish(db, run, UploadRunStatus.FAILED)
    return True


def on_publish_rescheduled(db: Session, item: PublishingQueueItem, message: str) -> None:
    """A (non-run) publication will be retried later: the clip is no longer ``uploading``. No commit."""
    clip = db.get(GeneratedClip, item.clip_id)
    if clip is not None and clip.upload_status == UploadStatus.UPLOADING:
        clip.upload_status, clip.upload_error = UploadStatus.NONE, f"Retrying later: {message}"[:2000]


def in_active_run(db: Session, item: PublishingQueueItem) -> bool:
    return _active_run_item(db, item) is not None


# ------------------------------------------------------------ retry/cancel
def retry_run(db: Session, run: UploadRun) -> UploadRun:
    """Resume a failed (or cancelled) run from its first unfinished clip. Commits."""
    if run.status == UploadRunStatus.RUNNING:
        return run
    if run.status == UploadRunStatus.COMPLETED:
        raise ConflictError("This upload run already finished.", code="run_finished")
    account = db.get(ConnectedAccount, run.connected_account_id)
    if account is None:
        raise ConflictError("The YouTube account of this run was removed.", code="account_missing")
    user = db.get(User, run.user_id)
    _account(db, user, account.id)
    running = _running_run(db, account.id, exclude=run.id)
    if running is not None:
        raise _in_progress(running)
    for item in _run_items(db, run.id):
        if item.status in (UploadRunItemStatus.FAILED, UploadRunItemStatus.CANCELLED):
            clip = db.get(GeneratedClip, item.clip_id)
            if clip is not None and clip.removed_at is None:
                item.status, item.error, item.progress = UploadRunItemStatus.PENDING, None, None
    run.status, run.cancel_requested, run.finished_at = UploadRunStatus.RUNNING, False, None
    run.failed_clip_id = run.last_error = None
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        running = _running_run(db, run.connected_account_id, exclude=run.id)
        if running is not None:
            raise _in_progress(running) from exc
        raise
    log.info("Upload run %s retried", run.id)
    advance(db, run.id)
    return db.get(UploadRun, run.id)


def cancel_run(db: Session, run: UploadRun) -> UploadRun:
    """Stop the run after the current transfer (a transfer is never interrupted midway). Commits."""
    from app.modules.publishing import history

    if run.status != UploadRunStatus.RUNNING:
        return run
    current = next((i for i in _run_items(db, run.id) if i.status == UploadRunItemStatus.UPLOADING), None)
    if current is not None and current.queue_item_id is not None:
        stopped = db.execute(update(PublishingQueueItem).where(
            PublishingQueueItem.id == current.queue_item_id, PublishingQueueItem.status.in_(NOT_TRANSFERRING))
            .values(status=QueueStatus.CANCELLED, locked_at=None, locked_by=None)
            .execution_options(synchronize_session=False)).rowcount
        if stopped:  # it had not started transferring yet
            item = db.get(PublishingQueueItem, current.queue_item_id, populate_existing=True)
            history.record(db, item, "cancel", QueueStatus.CANCELLED, "Sequential upload cancelled")
            current.status, current.error = UploadRunItemStatus.CANCELLED, "Cancelled before the upload started."
            clip = db.get(GeneratedClip, current.clip_id)
            if clip is not None and clip.upload_status == UploadStatus.UPLOADING:
                clip.upload_status = UploadStatus.NONE
            current = None
    run.cancel_requested = True
    db.commit()
    if current is None:
        advance(db, run.id)
    return db.get(UploadRun, run.id)


# ------------------------------------------------------------- maintenance
def reconcile_runs(db: Session) -> int:
    """Periodic safety net: advance every running run (finishes bookkeeping lost to a crash,
    resumes interrupted transfers, dispatches items that waited for a busy account)."""
    ids = list(db.scalars(select(UploadRun.id).where(UploadRun.status == UploadRunStatus.RUNNING)))
    for run_id in ids:
        try:
            advance(db, run_id)
        except Exception:  # noqa: BLE001 - next pass retries
            db.rollback()
            log.exception("Reconciling upload run %s failed", run_id)
    return len(ids)


# ----------------------------------------------------------- serialisation
def run_out(db: Session, run: UploadRun) -> dict[str, Any]:
    from app.modules.clips.service import sequences

    items = _run_items(db, run.id)
    clips = {c.id: c for c in db.scalars(select(GeneratedClip).where(
        GeneratedClip.id.in_([i.clip_id for i in items])))} if items else {}
    seq = sequences(db, {run.video_source_id})
    out_items = []
    for i in items:
        progress = i.progress
        if i.status == UploadRunItemStatus.UPLOADING and i.queue_item_id is not None:
            progress = transfer_progress(_publish_job(db, i.queue_item_id))
        clip = clips.get(i.clip_id)
        out_items.append({"clip_id": i.clip_id, "sequence": seq.get(i.clip_id), "title": clip.title if clip else "",
                          "status": i.status, "progress": progress,
                          "youtube_url": i.youtube_url or (clip.youtube_url if clip and i.status == "uploaded" else None),
                          "error": i.error})
    uploaded = sum(1 for i in items if i.status == UploadRunItemStatus.UPLOADED)
    remaining = sum(1 for i in items if i.status in (UploadRunItemStatus.PENDING, UploadRunItemStatus.UPLOADING,
                                                     UploadRunItemStatus.FAILED))
    return {"id": run.id, "video_source_id": run.video_source_id, "connected_account_id": run.connected_account_id,
            "mode": run.mode, "privacy": run.privacy, "status": run.status, "cancel_requested": run.cancel_requested,
            "total": len(items), "uploaded": uploaded, "remaining": remaining, "current_clip_id": run.current_clip_id,
            "failed_clip_id": run.failed_clip_id, "last_error": run.last_error, "created_at": run.created_at,
            "updated_at": run.updated_at, "items": out_items}


def list_runs(db: Session, user: User, *, video_source_id: uuid.UUID | None = None,
              active: bool | None = None, limit: int = 50) -> list[UploadRun]:
    stmt = select(UploadRun).where(UploadRun.user_id == user.id)
    if video_source_id is not None:
        stmt = stmt.where(UploadRun.video_source_id == video_source_id)
    if active is True:
        stmt = stmt.where(UploadRun.status == UploadRunStatus.RUNNING)
    elif active is False:
        stmt = stmt.where(UploadRun.status != UploadRunStatus.RUNNING)
    return list(db.scalars(stmt.order_by(UploadRun.created_at.desc()).limit(limit)))
