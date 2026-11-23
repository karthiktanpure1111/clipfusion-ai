"""Maintenance tasks: stale-job reaper, temp cleanup, expired uploads, local-mode recovery."""

from __future__ import annotations

import os
import time
import uuid
from datetime import timedelta

from sqlalchemy import update

from app.core.config import get_settings
from app.core.database import utcnow
from app.models import ProcessingJob, UploadSession, VideoSource
from app.models.enums import JobStatus, JobType, SourceStatus
from app.modules.uploads.service import upload_dir
from app.workers import registry, tasks_maintenance
from tests.test_api_core_helpers import dispatched, make_source  # noqa: F401 - fixture


def _job(db, source: VideoSource, status: str, *, attempts: int = 0, age_minutes: float = 0,
         heartbeat_minutes: float | None = None, next_attempt_minutes: float | None = None) -> ProcessingJob:
    job = ProcessingJob(user_id=source.user_id, job_type=JobType.ANALYSIS, video_source_id=source.id, status=status,
                        stage="x", progress=0.1, attempts=attempts, max_attempts=3)
    db.add(job)
    db.commit()
    now = utcnow()
    values = {"created_at": now - timedelta(minutes=age_minutes), "updated_at": now - timedelta(minutes=age_minutes)}
    if heartbeat_minutes is not None:
        values["heartbeat_at"] = now - timedelta(minutes=heartbeat_minutes)
    if next_attempt_minutes is not None:
        values["next_attempt_at"] = now + timedelta(minutes=next_attempt_minutes)
    db.execute(update(ProcessingJob).where(ProcessingJob.id == job.id).values(**values))
    db.commit()
    db.refresh(job)
    return job


def test_periodic_tasks_registered() -> None:
    periodic = registry.all_periodic()
    assert {k: periodic[k].every_seconds for k in ("reap_stale_jobs", "cleanup_temp", "cleanup_expired_uploads")} \
        == {"reap_stale_jobs": 300, "cleanup_temp": 3600, "cleanup_expired_uploads": 3600}
    assert registry.task_for_job_type(JobType.ANALYSIS) == "analyze_source"
    assert registry.task_for_job_type(JobType.IMPORT) == "import_source"
    assert registry.get_task("analyze_source").queue == "analysis"


def test_reaper_requeues_fails_and_redispatches(db, user, dispatched) -> None:  # noqa: F811
    timeout_min = get_settings().job_heartbeat_timeout_seconds / 60
    src = make_source(db, user, status=SourceStatus.PROCESSING)
    stale = _job(db, src, JobStatus.RUNNING, attempts=1, age_minutes=timeout_min + 30,
                 heartbeat_minutes=timeout_min + 5)
    src2 = make_source(db, user, status=SourceStatus.PROCESSING)
    exhausted = _job(db, src2, JobStatus.RUNNING, attempts=3, age_minutes=timeout_min + 30,
                     heartbeat_minutes=timeout_min + 5)
    stuck = _job(db, make_source(db, user, status=SourceStatus.QUEUED), JobStatus.QUEUED, age_minutes=20)
    fresh = _job(db, make_source(db, user, status=SourceStatus.QUEUED), JobStatus.QUEUED, age_minutes=1)
    alive = _job(db, make_source(db, user), JobStatus.RUNNING, attempts=1, age_minutes=60, heartbeat_minutes=1)
    tasks_maintenance.reap_stale_jobs()
    db.expire_all()
    names = {k["job_id"]: n for n, k in dispatched}
    assert db.get(ProcessingJob, stale.id).status == JobStatus.QUEUED and names[str(stale.id)] == "analyze_source"
    assert db.get(ProcessingJob, exhausted.id).status == JobStatus.FAILED and str(exhausted.id) not in names
    assert db.get(VideoSource, src2.id).status == SourceStatus.FAILED
    assert db.get(ProcessingJob, stuck.id).status == JobStatus.QUEUED and str(stuck.id) in names
    assert str(fresh.id) not in names and str(alive.id) not in names
    assert db.get(ProcessingJob, alive.id).status == JobStatus.RUNNING


def test_recover_jobs_redispatches_interrupted_jobs(db, user, dispatched) -> None:  # noqa: F811
    running = _job(db, make_source(db, user), JobStatus.RUNNING, attempts=1, heartbeat_minutes=1)
    retrying = _job(db, make_source(db, user), JobStatus.RETRYING, attempts=1, next_attempt_minutes=5)
    queued = _job(db, make_source(db, user), JobStatus.QUEUED)
    stats = tasks_maintenance.recover_jobs()
    db.expire_all()
    ids = {k["job_id"] for _, k in dispatched}
    assert {str(running.id), str(retrying.id), str(queued.id)} <= ids and stats["redispatched"] >= 3
    assert db.get(ProcessingJob, running.id).status == JobStatus.QUEUED
    assert db.get(ProcessingJob, retrying.id).status == JobStatus.RETRYING


def test_cleanup_temp_keeps_active_uploads(db, user) -> None:
    root = get_settings().temp_dir
    old = time.time() - 3 * 86400
    stale_dir = root / f"cf-job-{uuid.uuid4().hex}"
    stale_dir.mkdir(parents=True)
    (stale_dir / "a.wav").write_bytes(b"x")
    fresh_dir = root / f"cf-job-{uuid.uuid4().hex}"
    fresh_dir.mkdir()
    active = UploadSession(user_id=user.id, filename="a.mp4", total_size=10, chunk_size=10, total_chunks=1,
                           received_chunks=[], status="uploading", expires_at=utcnow() + timedelta(hours=1))
    db.add(active)
    db.commit()
    active_dir = upload_dir(active.id)
    active_dir.mkdir(parents=True, exist_ok=True)
    orphan_dir = upload_dir(uuid.uuid4())
    orphan_dir.mkdir(parents=True)
    for p in (stale_dir / "a.wav", stale_dir, active_dir, orphan_dir):
        os.utime(p, (old, old))
    removed = tasks_maintenance.cleanup_temp()
    assert removed >= 2 and not stale_dir.exists() and not orphan_dir.exists()
    assert fresh_dir.exists() and active_dir.exists()


def test_cleanup_expired_uploads(db, user) -> None:
    expired = UploadSession(user_id=user.id, filename="a.mp4", total_size=10, chunk_size=10, total_chunks=1,
                            received_chunks=[], status="uploading", expires_at=utcnow() - timedelta(minutes=1))
    live = UploadSession(user_id=user.id, filename="b.mp4", total_size=10, chunk_size=10, total_chunks=1,
                         received_chunks=[], status="uploading", expires_at=utcnow() + timedelta(hours=1))
    db.add_all([expired, live])
    db.commit()
    upload_dir(expired.id).mkdir(parents=True, exist_ok=True)
    (upload_dir(expired.id) / "0.part").write_bytes(b"0123456789")
    assert tasks_maintenance.cleanup_expired_uploads() >= 1
    db.expire_all()
    assert db.get(UploadSession, expired.id).status == "aborted" and not upload_dir(expired.id).exists()
    assert db.get(UploadSession, live.id).status == "uploading"
