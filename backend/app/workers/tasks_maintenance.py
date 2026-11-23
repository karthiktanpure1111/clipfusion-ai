"""Maintenance tasks: stale-job reaper, temp-file cleanup, expired upload cleanup, verified
post-upload media cleanup (+ automatic original deletion), and local-mode job recovery at startup."""

from __future__ import annotations

import logging
import shutil
import time
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import SessionLocal, utcnow
from app.models import GeneratedClip, ProcessingJob, UploadSession
from app.models.enums import JobStatus, JobType, RenderStatus
from app.workers.jobs import dispatch_job
from app.workers.registry import periodic, task, task_for_job_type

log = logging.getLogger(__name__)

QUEUED_GRACE = timedelta(minutes=10)
TEMP_MAX_AGE_SECONDS = 24 * 3600
ASSEMBLING_GRACE = timedelta(hours=6)
STALE_MESSAGE = "The worker stopped responding"


# ------------------------------------------------------------- failure hooks
def _render_failed(db: Session, job: ProcessingJob, exc: BaseException) -> None:
    clip = db.get(GeneratedClip, job.clip_id) if job.clip_id else None
    if clip is None or clip.render_status not in (RenderStatus.QUEUED, RenderStatus.RENDERING):
        return
    from app.modules.scheduling.service import on_clip_render_finished

    clip.render_status = RenderStatus.FAILED
    clip.render_error = str(exc)
    on_clip_render_finished(db, clip, False)


def _failure_hook(job_type: str) -> Callable[[Session, ProcessingJob, BaseException], None] | None:
    if job_type == JobType.IMPORT:
        from app.workers.tasks_processing import on_import_failed

        return on_import_failed
    if job_type == JobType.ANALYSIS:
        from app.workers.tasks_processing import on_analysis_failed

        return on_analysis_failed
    if job_type == JobType.RENDER:
        return _render_failed
    return None


def _fail_exhausted(db: Session, job: ProcessingJob) -> None:
    job.status = JobStatus.FAILED
    job.finished_at = utcnow()
    job.idempotency_key = None
    job.error = f"{STALE_MESSAGE} after {job.attempts} attempt(s)."
    job.message = "Failed"
    db.commit()
    hook = _failure_hook(job.job_type)
    if hook is not None:
        try:
            hook(db, job, RuntimeError(job.error))
            db.commit()
        except Exception:
            db.rollback()
            log.exception("Failure hook for stale job %s failed", job.id)


def _redispatch(db: Session, job: ProcessingJob, reason: str, countdown: float | None = None) -> bool:
    name = task_for_job_type(job.job_type)
    if name is None:
        log.warning("No task registered for job type %s (job %s); cannot re-dispatch", job.job_type, job.id)
        return False
    if job.status == JobStatus.RUNNING:
        job.status = JobStatus.QUEUED
        job.heartbeat_at = None
    job.message = reason[:500]
    dispatch_job(db, job, name, countdown=countdown)
    return True


# --------------------------------------------------------------------- reaper
def reap(db: Session) -> dict[str, int]:
    """Re-dispatch (or fail) jobs whose worker died and jobs that were never picked up."""
    s = get_settings()
    now = utcnow()
    stale_before = now - timedelta(seconds=s.job_heartbeat_timeout_seconds)
    queued_before = now - QUEUED_GRACE
    stats = {"requeued": 0, "failed": 0, "redispatched": 0}
    running = list(db.scalars(select(ProcessingJob).where(
        ProcessingJob.status == JobStatus.RUNNING,
        or_(ProcessingJob.heartbeat_at < stale_before,
            ProcessingJob.heartbeat_at.is_(None) & (ProcessingJob.updated_at < stale_before)))))
    for job in running:
        if job.attempts >= job.max_attempts:
            _fail_exhausted(db, job)
            stats["failed"] += 1
        elif _redispatch(db, job, f"{STALE_MESSAGE}; re-queued"):
            stats["requeued"] += 1
    waiting = list(db.scalars(select(ProcessingJob).where(
        ProcessingJob.updated_at < queued_before,
        or_((ProcessingJob.status == JobStatus.QUEUED) & (ProcessingJob.created_at < queued_before),
            (ProcessingJob.status == JobStatus.RETRYING) & (ProcessingJob.next_attempt_at < queued_before)))))
    for job in waiting:
        if job.status == JobStatus.RETRYING and job.attempts >= job.max_attempts:
            _fail_exhausted(db, job)
            stats["failed"] += 1
        elif _redispatch(db, job, "Waiting for a worker; re-dispatched"):
            stats["redispatched"] += 1
    if any(stats.values()):
        log.info("Job reaper: %s", stats)
    return stats


@task("reap_stale_jobs", queue="default")
def reap_stale_jobs() -> dict[str, int]:
    """Periodic (300 s): recover jobs lost to crashed workers or a down broker."""
    with SessionLocal() as db:
        return reap(db)


# -------------------------------------------------------------------- cleanup
def _age_seconds(path: Path, now: float) -> float:
    try:
        newest = path.stat().st_mtime
        if path.is_dir():
            for child in path.iterdir():
                newest = max(newest, child.stat().st_mtime)
    except OSError:
        return 0.0
    return now - newest


def _remove(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)


def clean_temp_dir(db: Session, *, max_age: float = TEMP_MAX_AGE_SECONDS) -> int:
    """Delete TEMP_DIR entries older than ``max_age`` seconds, keeping active upload sessions."""
    root = get_settings().temp_dir
    if not root.is_dir():
        return 0
    active = {str(i) for i in db.scalars(select(UploadSession.id).where(
        UploadSession.status.in_(("uploading", "assembling"))))}
    now, removed = time.time(), 0
    for entry in list(root.iterdir()):
        if entry.name == "uploads" and entry.is_dir():
            for child in list(entry.iterdir()):
                if child.name not in active and _age_seconds(child, now) > max_age:
                    _remove(child)
                    removed += 1
        elif _age_seconds(entry, now) > max_age:
            _remove(entry)
            removed += 1
    if removed:
        log.info("Temp cleanup removed %d entries", removed)
    return removed


@task("cleanup_temp", queue="default")
def cleanup_temp() -> int:
    """Periodic (3600 s): remove stale scratch files."""
    with SessionLocal() as db:
        return clean_temp_dir(db)


def expire_uploads(db: Session) -> int:
    """Abort expired upload sessions (and sessions stuck while assembling) and delete their chunks."""
    from app.modules.uploads.service import upload_dir

    now = utcnow()
    rows = list(db.scalars(select(UploadSession).where(or_(
        (UploadSession.status == "uploading") & (UploadSession.expires_at < now),
        (UploadSession.status == "assembling") & (UploadSession.expires_at < now - ASSEMBLING_GRACE)))))
    for session in rows:
        session.status = "aborted"
        shutil.rmtree(upload_dir(session.id), ignore_errors=True)
    db.commit()
    if rows:
        log.info("Aborted %d expired upload sessions", len(rows))
    return len(rows)


@task("cleanup_expired_uploads", queue="default")
def cleanup_expired_uploads() -> int:
    """Periodic (3600 s): abort expired resumable uploads."""
    with SessionLocal() as db:
        return expire_uploads(db)


# ------------------------------------------------------------------ recovery
def recover_jobs() -> dict[str, int]:
    """Local mode startup: re-dispatch every job that was queued, retrying or running when
    the process stopped (running ones whose attempts are exhausted are failed)."""
    stats = {"redispatched": 0, "failed": 0}
    with SessionLocal() as db:
        jobs = list(db.scalars(select(ProcessingJob).where(ProcessingJob.status.in_(
            (JobStatus.QUEUED, JobStatus.RETRYING, JobStatus.RUNNING))).order_by(ProcessingJob.created_at)))
        now = utcnow()
        for job in jobs:
            if job.status in (JobStatus.RUNNING, JobStatus.RETRYING) and job.attempts >= job.max_attempts:
                _fail_exhausted(db, job)
                stats["failed"] += 1
                continue
            countdown = None
            if job.status == JobStatus.RETRYING and job.next_attempt_at and job.next_attempt_at > now:
                countdown = (job.next_attempt_at - now).total_seconds()
            if _redispatch(db, job, "Recovered after restart", countdown=countdown):
                stats["redispatched"] += 1
    if any(stats.values()):
        log.info("Recovered jobs at startup: %s", stats)
    return stats


# ------------------------------------------------------ post-upload cleanup
def verify_uploads(db: Session, *, limit: int = 50) -> dict[str, int]:
    """Re-check uploaded clips whose media is still stored: delete it once YouTube confirms the
    upload (never when unsure), retry failed deletions of removed clips, and delete originals of
    finished sources when the user enabled ``auto_delete_original``."""
    from app.models import VideoSource
    from app.models.enums import CleanupStatus
    from app.modules.storage.cleanup import cleanup_after_upload, pending_cleanups
    from app.modules.storage.service import delete_clip_media, maybe_auto_delete_original

    stats: dict[str, int] = {}
    interval = timedelta(seconds=get_settings().upload_verify_interval_seconds)
    now = utcnow()
    for clip in pending_cleanups(db, limit=limit):
        if clip.cleanup_checked_at is not None and now - clip.cleanup_checked_at < interval:
            continue
        outcome = cleanup_after_upload(db, clip)
        stats[outcome] = stats.get(outcome, 0) + 1
    removed = list(db.scalars(select(GeneratedClip).where(GeneratedClip.removed_at.is_not(None),
                                                          GeneratedClip.cleanup_status == CleanupStatus.FAILED)
                              .limit(limit)))
    for clip in removed:
        try:
            freed = delete_clip_media(db, clip)
            clip.freed_bytes = int(clip.freed_bytes or 0) + freed
            clip.cleanup_status, clip.cleanup_error = CleanupStatus.NONE, None
            db.commit()
            stats["removed_files_deleted"] = stats.get("removed_files_deleted", 0) + 1
        except Exception as exc:  # noqa: BLE001 - try again next pass
            db.rollback()
            log.warning("Deleting the files of removed clip %s failed again: %s", clip.id, exc)
    sources = list(db.scalars(select(VideoSource).where(VideoSource.storage_key.is_not(None),
                                                        VideoSource.original_deleted_at.is_(None))))
    for source in sources:
        if maybe_auto_delete_original(db, source):
            stats["originals_deleted"] = stats.get("originals_deleted", 0) + 1
    if stats:
        log.info("Post-upload cleanup: %s", stats)
    return stats


@task("verify_uploaded_clips", queue="default")
def verify_uploaded_clips() -> dict[str, int]:
    """Periodic: verified post-upload media cleanup (see :func:`verify_uploads`)."""
    with SessionLocal() as db:
        return verify_uploads(db)


periodic("reap_stale_jobs", every_seconds=300)
periodic("verify_uploaded_clips", every_seconds=120)
periodic("cleanup_temp", every_seconds=3600)
periodic("cleanup_expired_uploads", every_seconds=3600)
