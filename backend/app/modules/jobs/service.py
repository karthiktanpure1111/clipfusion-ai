"""Job queries, serialisation, cancellation and manual retry (user-scoped)."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import utcnow
from app.core.errors import AppError, ConflictError, ValidationFailed
from app.models import GeneratedClip, ProcessingJob, User, VideoSource
from app.models.enums import JobStatus, JobType, RenderStatus, SourceStatus
from app.modules.common.pagination import PageParams, paginate
from app.modules.jobs.schemas import JobOut
from app.workers.jobs import ACTIVE_STATUSES, dispatch_job

log = logging.getLogger(__name__)

# Job types whose retry belongs to another workflow (publishing queue items have their own retry).
NOT_RETRYABLE_HERE = {JobType.PUBLISH: "Retry the publication from the publishing queue instead.",
                      JobType.ANALYTICS: "Analytics refreshes run periodically; use POST /analytics/refresh."}


# --------------------------------------------------------------- serialisation
def jobs_out(db: Session, jobs: Sequence[ProcessingJob]) -> list[JobOut]:
    """Serialise jobs with ``source_title``/``clip_title`` using two batched lookups."""
    source_ids = {j.video_source_id for j in jobs if j.video_source_id}
    clip_ids = {j.clip_id for j in jobs if j.clip_id}
    titles: dict[uuid.UUID, str] = {}
    if source_ids:
        titles.update(db.execute(select(VideoSource.id, VideoSource.title)
                                 .where(VideoSource.id.in_(source_ids))).tuples().all())
    clip_titles: dict[uuid.UUID, str] = {}
    if clip_ids:
        clip_titles.update(db.execute(select(GeneratedClip.id, GeneratedClip.title)
                                      .where(GeneratedClip.id.in_(clip_ids))).tuples().all())
    out = []
    for j in jobs:
        item = JobOut.model_validate(j)
        item.source_title = titles.get(j.video_source_id) if j.video_source_id else None
        item.clip_title = clip_titles.get(j.clip_id) if j.clip_id else None
        out.append(item)
    return out


def job_out(db: Session, job: ProcessingJob) -> JobOut:
    """Contract: serialise one job (adds ``source_title`` and ``clip_title``)."""
    return jobs_out(db, [job])[0]


# ---------------------------------------------------------------------- queries
def list_jobs(db: Session, user: User, params: PageParams, *, status: str | None = None,
              job_type: str | None = None, video_source_id: uuid.UUID | None = None,
              clip_id: uuid.UUID | None = None, active: bool | None = None) -> tuple[list[ProcessingJob], int]:
    """The user's jobs, newest first, with optional filters."""
    stmt = select(ProcessingJob).where(ProcessingJob.user_id == user.id)
    if status:
        stmt = stmt.where(ProcessingJob.status == status)
    if job_type:
        stmt = stmt.where(ProcessingJob.job_type == job_type)
    if video_source_id:
        stmt = stmt.where(ProcessingJob.video_source_id == video_source_id)
    if clip_id:
        stmt = stmt.where(ProcessingJob.clip_id == clip_id)
    if active is True:
        stmt = stmt.where(ProcessingJob.status.in_(ACTIVE_STATUSES))
    elif active is False:
        stmt = stmt.where(ProcessingJob.status.not_in(ACTIVE_STATUSES))
    stmt = stmt.order_by(ProcessingJob.created_at.desc(), ProcessingJob.id)
    return paginate(db, stmt, params)


def idempotency_key_for(job: ProcessingJob) -> str | None:
    """The deduplication key a job of this type/target holds while active (ARCHITECTURE §4)."""
    if job.job_type == JobType.ANALYSIS and job.video_source_id:
        return f"analysis:{job.video_source_id}"
    if job.job_type == JobType.IMPORT and job.video_source_id:
        return f"import:{job.video_source_id}"
    if job.job_type == JobType.CLIP_VISION and job.clip_id:
        return f"clip_vision:{job.clip_id}"
    if job.job_type == JobType.RENDER and job.clip_id:
        return f"render:{job.clip_id}"
    if job.job_type == JobType.TRANSCRIBE_CLIP and job.clip_id:
        return f"transcribe_clip:{job.clip_id}"
    if job.job_type == JobType.METADATA and job.clip_id:
        return f"metadata:{job.clip_id}"
    return None


# ------------------------------------------------------------- domain state
def _on_cancel(db: Session, job: ProcessingJob) -> None:
    """Put the job's target back into a consistent state after a user cancellation."""
    if job.job_type in (JobType.ANALYSIS, JobType.IMPORT) and job.video_source_id:
        source = db.get(VideoSource, job.video_source_id)
        if source is None:
            return
        if job.job_type == JobType.ANALYSIS and source.status in (SourceStatus.QUEUED, SourceStatus.PROCESSING):
            source.status = SourceStatus.READY if source.processed_at else SourceStatus.UPLOADED
        elif job.job_type == JobType.IMPORT and source.status == SourceStatus.IMPORTING:
            source.status = SourceStatus.FAILED
            source.error = "Import cancelled."
    elif job.job_type == JobType.RENDER and job.clip_id:
        clip = db.get(GeneratedClip, job.clip_id)
        if clip is not None and clip.render_status in (RenderStatus.QUEUED, RenderStatus.RENDERING):
            clip.render_status = RenderStatus.STALE if clip.output_key else RenderStatus.NOT_RENDERED
            clip.render_error = "Render cancelled."
            from app.modules.scheduling.service import on_clip_render_finished

            on_clip_render_finished(db, clip, False)


def _on_retry(db: Session, job: ProcessingJob) -> None:
    if job.job_type == JobType.ANALYSIS and job.video_source_id:
        source = db.get(VideoSource, job.video_source_id)
        if source is not None:
            if not source.storage_key:
                raise ConflictError("The video has no stored file to analyse.", code="no_file")
            source.status, source.error = SourceStatus.QUEUED, None
    elif job.job_type == JobType.IMPORT and job.video_source_id:
        source = db.get(VideoSource, job.video_source_id)
        if source is not None:
            if source.storage_key:
                raise ConflictError("The video file was already imported.")
            source.status, source.error = SourceStatus.IMPORTING, None
    elif job.job_type == JobType.RENDER and job.clip_id:
        clip = db.get(GeneratedClip, job.clip_id)
        if clip is not None:
            clip.render_status, clip.render_error = RenderStatus.QUEUED, None


# ------------------------------------------------------------------ actions
def cancel_job(db: Session, job: ProcessingJob) -> ProcessingJob:
    """Cancel an active job. Running workers notice at their next progress update."""
    if job.status not in ACTIVE_STATUSES:
        raise ConflictError(f"Job is already {job.status}.", code="job_not_active")
    job.status = JobStatus.CANCELLED
    job.finished_at = utcnow()
    job.idempotency_key = None
    job.message = "Cancelled by user"
    _on_cancel(db, job)
    db.commit()
    log.info("Job %s (%s) cancelled by user %s", job.id, job.job_type, job.user_id)
    return job


def retry_job(db: Session, job: ProcessingJob) -> ProcessingJob:
    """Re-run a failed or cancelled job (same row, attempts reset)."""
    from app.workers.registry import task_for_job_type

    if job.status not in (JobStatus.FAILED, JobStatus.CANCELLED):
        raise ConflictError("Only failed or cancelled jobs can be retried.", code="job_not_retryable")
    if job.job_type in NOT_RETRYABLE_HERE:
        raise ValidationFailed(NOT_RETRYABLE_HERE[JobType(job.job_type)], code="job_not_retryable")
    duplicate = db.scalar(select(ProcessingJob.id).where(
        ProcessingJob.id != job.id, ProcessingJob.job_type == job.job_type,
        ProcessingJob.status.in_(ACTIVE_STATUSES),
        ProcessingJob.video_source_id == job.video_source_id if job.video_source_id
        else ProcessingJob.video_source_id.is_(None),
        ProcessingJob.clip_id == job.clip_id if job.clip_id else ProcessingJob.clip_id.is_(None)))
    if duplicate is not None:
        raise ConflictError("An equivalent job is already running.", code="job_active",
                            details={"job_id": str(duplicate)})
    task_name = task_for_job_type(job.job_type)
    if task_name is None:
        raise AppError(f"No worker task is registered for '{job.job_type}' jobs.", code="dependency_missing",
                       status_code=503)
    _on_retry(db, job)
    job.status = JobStatus.QUEUED
    job.attempts = 0
    job.error = None
    job.progress = 0.0
    job.stage = "queued"
    job.message = "Retry requested"
    job.started_at = job.finished_at = job.heartbeat_at = job.next_attempt_at = None
    job.result = {}
    job.idempotency_key = idempotency_key_for(job)
    dispatch_job(db, job, task_name)
    return job
