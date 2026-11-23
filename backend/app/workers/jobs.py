"""Persistent job lifecycle: create -> enqueue -> run (progress/heartbeat) -> retry/fail.

Task functions should be thin wrappers around :func:`run_job`::

    @task("render_clip", queue="render", job_type=JobType.RENDER)
    def render_clip_task(job_id: str) -> None:
        run_job(job_id, _render_handler, task_name="render_clip", on_final_failure=_render_failed)

    def _render_handler(ctx: JobContext, db: Session, job: ProcessingJob) -> dict | None:
        ctx.progress("rendering", 0.1, "Compositing frames")
        ...
        return {"output_key": key}   # stored in job.result

Handlers own their domain-state updates (clip.render_status, ...) and must commit them
via ``db``. :func:`run_job` commits job bookkeeping. Raise :class:`PermanentJobError`
for failures that retrying cannot fix (invalid input, missing permissions).
"""

from __future__ import annotations

import logging
import threading
import time
import traceback
import uuid
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import SessionLocal, utcnow
from app.models import ProcessingJob
from app.models.enums import JobStatus
from app.workers.registry import enqueue

log = logging.getLogger(__name__)

ACTIVE_STATUSES = (JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.RETRYING)
RETRY_BACKOFF_SECONDS = (30, 120, 600, 1800)
HEARTBEAT_SECONDS = 30.0


class PermanentJobError(Exception):
    """Fail the job immediately without retrying."""


class JobCancelled(Exception):
    pass


def create_job(db: Session, *, user_id: uuid.UUID, job_type: str, video_source_id: uuid.UUID | None = None,
               clip_id: uuid.UUID | None = None, queue_item_id: uuid.UUID | None = None,
               params: dict | None = None, idempotency_key: str | None = None,
               max_attempts: int | None = None, stage: str = "queued") -> tuple[ProcessingJob, bool]:
    """Create (or return the already-active) job. Returns ``(job, created)``.

    ``idempotency_key`` deduplicates *active* jobs (e.g. ``analysis:{source_id}``): if an
    active job holds the key it is returned instead of creating a duplicate."""
    if idempotency_key:
        existing = db.scalar(select(ProcessingJob).where(ProcessingJob.idempotency_key == idempotency_key))
        if existing is not None:
            if existing.status in ACTIVE_STATUSES:
                return existing, False
            existing.idempotency_key = None  # finished job releases the key
            db.flush()
    job = ProcessingJob(
        user_id=user_id, job_type=str(job_type), video_source_id=video_source_id, clip_id=clip_id,
        queue_item_id=queue_item_id, params=params or {}, idempotency_key=idempotency_key,
        status=JobStatus.QUEUED, stage=stage, progress=0.0,
        max_attempts=max_attempts or get_settings().job_max_attempts,
    )
    db.add(job)
    db.flush()
    return job, True


def dispatch_job(db: Session, job: ProcessingJob, task_name: str, countdown: float | None = None) -> None:
    """Commit the job row, then enqueue its task (commit first so the worker can see it)."""
    db.commit()
    try:
        job.task_id = enqueue(task_name, countdown=countdown, job_id=str(job.id))
        db.commit()
    except Exception as exc:  # broker down: keep the job queued; the reaper will retry dispatch
        log.exception("Failed to enqueue %s for job %s", task_name, job.id)
        job.message = f"Waiting for worker: {exc.__class__.__name__}"
        db.commit()


class JobContext:
    """Progress/heartbeat/cancellation helper passed to handlers. Writes use a separate
    short-lived session so progress is visible immediately without committing handler state."""

    def __init__(self, job_id: uuid.UUID, params: dict[str, Any]) -> None:
        self.job_id = job_id
        self.params = params
        self._last_write = 0.0
        self._last_stage: str | None = None
        self._lock = threading.Lock()

    def progress(self, stage: str, fraction: float, message: str | None = None, *, force: bool = False) -> None:
        now = time.monotonic()
        with self._lock:
            if not force and stage == self._last_stage and now - self._last_write < 1.0:
                return
            self._last_write, self._last_stage = now, stage
        with SessionLocal() as s:
            job = s.get(ProcessingJob, self.job_id)
            if job is None:
                return
            if job.status == JobStatus.CANCELLED:
                raise JobCancelled()
            job.stage = stage[:60]
            job.progress = float(min(max(fraction, 0.0), 1.0))
            if message is not None:
                job.message = message[:500]
            job.heartbeat_at = utcnow()
            s.commit()

    def heartbeat(self) -> None:
        with SessionLocal() as s:
            job = s.get(ProcessingJob, self.job_id)
            if job is not None:
                job.heartbeat_at = utcnow()
                s.commit()

    def check_cancelled(self) -> None:
        with SessionLocal() as s:
            job = s.get(ProcessingJob, self.job_id)
            if job is None or job.status == JobStatus.CANCELLED:
                raise JobCancelled()

    def progress_fn(self, stage: str, start: float, end: float) -> Callable[[float, str | None], None]:
        """Map a sub-step's 0..1 progress onto ``[start, end]`` of the overall job."""

        def _cb(frac: float, message: str | None = None) -> None:
            self.progress(stage, start + (end - start) * min(max(frac, 0.0), 1.0), message)

        return _cb


Handler = Callable[[JobContext, Session, ProcessingJob], dict | None]
FailureHook = Callable[[Session, ProcessingJob, BaseException], None]


def _claim(db: Session, jid: uuid.UUID) -> ProcessingJob | None:
    """Atomically take the job (waiting, or running in a worker that died); None when it is
    finished or another worker is on it. Brokers may deliver a message twice, and running a
    publish job twice would upload the clip twice."""
    now = utcnow()
    dead = now - timedelta(seconds=get_settings().job_heartbeat_timeout_seconds)
    claimed = db.execute(
        update(ProcessingJob)
        .where(ProcessingJob.id == jid, or_(
            ProcessingJob.status.in_([JobStatus.QUEUED, JobStatus.RETRYING]),
            and_(ProcessingJob.status == JobStatus.RUNNING,
                 or_(ProcessingJob.heartbeat_at.is_(None), ProcessingJob.heartbeat_at < dead))))
        .values(status=JobStatus.RUNNING, attempts=ProcessingJob.attempts + 1, heartbeat_at=now, error=None,
                next_attempt_at=None, started_at=func.coalesce(ProcessingJob.started_at, now))
        .execution_options(synchronize_session=False))
    db.commit()
    return db.get(ProcessingJob, jid, populate_existing=True) if claimed.rowcount == 1 else None


class _Heartbeat:
    """Keeps ``heartbeat_at`` fresh while the handler runs, so a long silent step (model download,
    big upload) is not mistaken for a dead worker and started a second time."""

    def __init__(self, ctx: JobContext) -> None:
        self._ctx = ctx
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="cf-heartbeat", daemon=True)

    def _loop(self) -> None:
        while not self._stop.wait(HEARTBEAT_SECONDS):
            try:
                self._ctx.heartbeat()
            except Exception:  # database hiccup: try again next time
                log.debug("Heartbeat failed for job %s", self._ctx.job_id, exc_info=True)

    def __enter__(self) -> None:
        self._thread.start()

    def __exit__(self, *_exc: object) -> None:
        self._stop.set()


def run_job(job_id: str, handler: Handler, *, task_name: str,
            on_final_failure: FailureHook | None = None) -> None:
    """Run ``handler`` for the job, at most once at a time, with retries and bookkeeping."""
    jid = uuid.UUID(job_id)
    db = SessionLocal()
    try:
        job = _claim(db, jid)
        if job is None:
            log.info("Job %s is finished, gone or already running elsewhere; nothing to do", job_id)
            return

        ctx = JobContext(jid, dict(job.params or {}))
        try:
            with _Heartbeat(ctx):
                result = handler(ctx, db, job)
        except JobCancelled:
            db.rollback()
            job = db.get(ProcessingJob, jid, populate_existing=True)
            if job is None:
                return
            job.status = JobStatus.CANCELLED
            job.finished_at = utcnow()
            job.idempotency_key = None
            db.commit()
            return
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            job = db.get(ProcessingJob, jid, populate_existing=True)
            if job is None:  # deleted (with its video or clip) while it was running
                log.info("Job %s was deleted while running: %s", job_id, exc)
                return
            permanent = isinstance(exc, PermanentJobError)
            job.error = f"{exc.__class__.__name__}: {exc}"[:4000]
            log.error("Job %s (%s) attempt %s failed: %s\n%s", job_id, job.job_type, job.attempts, exc,
                      traceback.format_exc(limit=8))
            if not permanent and job.attempts < job.max_attempts:
                delay = RETRY_BACKOFF_SECONDS[min(job.attempts - 1, len(RETRY_BACKOFF_SECONDS) - 1)]
                job.status = JobStatus.RETRYING
                job.next_attempt_at = utcnow() + timedelta(seconds=delay)
                job.message = f"Retrying in {delay}s (attempt {job.attempts}/{job.max_attempts})"
                db.commit()
                try:
                    enqueue(task_name, countdown=delay, job_id=job_id)
                except Exception:
                    log.exception("Could not schedule retry for job %s; reaper will pick it up", job_id)
                return
            job.status = JobStatus.FAILED
            job.finished_at = utcnow()
            job.idempotency_key = None
            job.message = "Failed"
            db.commit()
            if on_final_failure is not None:
                try:
                    on_final_failure(db, job, exc)
                    db.commit()
                except Exception:
                    db.rollback()
                    log.exception("on_final_failure hook failed for job %s", job_id)
            return

        job = db.get(ProcessingJob, jid, populate_existing=True)
        if job is None:
            return
        if job.status == JobStatus.CANCELLED:  # cancelled at the last moment: keep the user's choice
            job.finished_at = job.finished_at or utcnow()
            job.idempotency_key = None
            db.commit()
            return
        job.status = JobStatus.SUCCEEDED
        job.progress = 1.0
        job.stage = "done"
        job.finished_at = utcnow()
        job.idempotency_key = None
        if result:
            job.result = result
        db.commit()
    finally:
        db.close()
