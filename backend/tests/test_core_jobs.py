"""run_job guarantees: one execution at a time, no resurrection of finished/cancelled/deleted jobs."""

from __future__ import annotations

import threading
from datetime import timedelta

import pytest

from app.core.config import get_settings
from app.core.database import SessionLocal, utcnow
from app.models import ProcessingJob
from app.models.enums import JobStatus
from app.workers import jobs
from app.workers.jobs import PermanentJobError, create_job, run_job


@pytest.fixture()
def job(db, user) -> ProcessingJob:
    j, _ = create_job(db, user_id=user.id, job_type="render")
    db.commit()
    return j


def _status(job_id) -> str:
    with SessionLocal() as s:
        row = s.get(ProcessingJob, job_id)
        return row.status if row else "deleted"


def test_a_job_delivered_twice_runs_once(job):
    started, release, runs = threading.Event(), threading.Event(), []

    def handler(ctx, db, j):
        runs.append(1)
        started.set()
        release.wait(10)

    first = threading.Thread(target=run_job, args=(str(job.id), handler), kwargs={"task_name": "x"})
    first.start()
    assert started.wait(10)
    run_job(str(job.id), handler, task_name="x")          # duplicate while running
    release.set()
    first.join(10)
    run_job(str(job.id), handler, task_name="x")          # duplicate after success
    assert runs == [1]
    assert _status(job.id) == JobStatus.SUCCEEDED


def test_a_job_whose_worker_died_is_taken_over(db, job):
    job.status = JobStatus.RUNNING
    job.heartbeat_at = utcnow() - timedelta(seconds=get_settings().job_heartbeat_timeout_seconds + 5)
    db.commit()
    run_job(str(job.id), lambda ctx, d, j: {"ok": True}, task_name="x")
    assert _status(job.id) == JobStatus.SUCCEEDED


def test_cancelling_at_the_last_moment_is_respected(job):
    def handler(ctx, db, j):
        with SessionLocal() as s:
            s.get(ProcessingJob, job.id).status = JobStatus.CANCELLED
            s.commit()

    run_job(str(job.id), handler, task_name="x")
    assert _status(job.id) == JobStatus.CANCELLED


@pytest.mark.parametrize("fails", [False, True])
def test_deleting_the_job_while_it_runs_is_harmless(job, fails):
    def handler(ctx, db, j):
        with SessionLocal() as s:
            s.delete(s.get(ProcessingJob, job.id))
            s.commit()
        if fails:
            raise PermanentJobError("source gone")

    run_job(str(job.id), handler, task_name="x")
    assert _status(job.id) == "deleted"


def test_heartbeat_continues_during_a_silent_step(monkeypatch, job):
    monkeypatch.setattr(jobs, "HEARTBEAT_SECONDS", 0.05)
    beats = []

    def handler(ctx, db, j):
        monkeypatch.setattr(ctx, "heartbeat", lambda: beats.append(1))
        threading.Event().wait(0.5)

    run_job(str(job.id), handler, task_name="x")
    assert len(beats) >= 3
