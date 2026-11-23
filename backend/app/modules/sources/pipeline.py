"""GET /sources/{id}/pipeline: real per-stage progress of a source's processing.

Ingestion comes from the import job (or the stored upload), the analysis stages and ``metadata``
from the per-stage records the ``analyze_source`` worker persists on its job (``stages``), and
``clip_generation`` (face tracking + framing + render of every clip) from the clip jobs:
progress = finished clips / total. Nothing is estimated: ``progress`` is null when it is not
measurable and ``eta_seconds`` only extrapolates the running stage's measured progress."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import utcnow
from app.models import GeneratedClip, ProcessingJob, Transcript, VideoSource
from app.models.enums import JobStatus, JobType

STAGES: list[tuple[str, str]] = [
    ("ingestion", "Getting the video"), ("audio", "Audio analysis"), ("transcription", "Transcription"),
    ("video_analysis", "Video analysis"), ("highlights", "Finding highlights"),
    ("clip_generation", "Generating clips"), ("metadata", "Titles, descriptions & hashtags"),
    ("completed", "Completed"),
]
ANALYSIS_KEYS = ("audio", "transcription", "video_analysis", "highlights", "metadata")
ACTIVE = (JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.RETRYING)
CLIP_JOBS = (JobType.CLIP_VISION, JobType.RENDER)
MIN_ETA_PROGRESS = 0.02
COUNT_KEY = {"pending": "pending", "processing": "processing", "generated": "generated",
             "uploading": "generated", "uploaded": "uploaded", "upload_failed": "upload_failed",
             "removed": "removed", "cleaned": "cleaned"}


def _dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _seconds(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    return round(max(0.0, (end - start).total_seconds()), 3)


def _stage(key: str, label: str, **kw: Any) -> dict[str, Any]:
    out = {"key": key, "label": label, "status": "pending", "progress": None, "started_at": None,
           "finished_at": None, "seconds": None, "message": None, "error": None}
    out.update({k: v for k, v in kw.items() if v is not None or k in ("progress",)})
    return out


def _latest(db: Session, source_id: uuid.UUID, job_type: str) -> ProcessingJob | None:
    return db.scalar(select(ProcessingJob).where(ProcessingJob.video_source_id == source_id,
                                                 ProcessingJob.job_type == job_type)
                     .order_by(ProcessingJob.created_at.desc()).limit(1))


def _job_status(job: ProcessingJob) -> str:
    if job.status in ACTIVE:
        return "running" if job.status == JobStatus.RUNNING else "pending"
    if job.status == JobStatus.SUCCEEDED:
        return "done"
    return "failed"


def _ingestion(source: VideoSource, job: ProcessingJob | None) -> dict[str, Any]:
    label = dict(STAGES)["ingestion"]
    if job is not None and (job.status in ACTIVE or not source.storage_key):
        st = _job_status(job)
        msg = job.message if st != "failed" else None
        return _stage("ingestion", label, status=st, progress=round(job.progress, 4) if st == "running" else
                      (1.0 if st == "done" else None), started_at=job.started_at, finished_at=job.finished_at,
                      seconds=_seconds(job.started_at, job.finished_at), message=msg,
                      error=(source.error or job.error) if st == "failed" else None)
    if source.storage_key or source.original_deleted_at is not None:
        start = job.started_at if job is not None else None
        end = job.finished_at if job is not None else None
        return _stage("ingestion", label, status="done", progress=1.0, started_at=start, finished_at=end,
                      seconds=_seconds(start, end),
                      message="Original deleted to free space" if source.original_deleted_at else None)
    return _stage("ingestion", label, message="Waiting for the original file: upload it or add a direct link")


def _clip_generation(db: Session, source: VideoSource, analysis: ProcessingJob | None) -> tuple[dict, dict]:
    from app.modules.clips.service import derive_statuses

    clips = list(db.scalars(select(GeneratedClip).where(GeneratedClip.video_source_id == source.id)))
    statuses = derive_statuses(db, clips)
    counts = {"total": len(clips), "pending": 0, "processing": 0, "generated": 0, "upload_failed": 0, "uploaded": 0,
              "removed": 0, "cleaned": 0}
    for st in statuses.values():
        counts[COUNT_KEY.get(st, "processing")] += 1
    label = dict(STAGES)["clip_generation"]
    live = [c for c in clips if c.removed_at is None]
    since = analysis.created_at if analysis is not None else None
    stmt = select(ProcessingJob).where(ProcessingJob.video_source_id == source.id,
                                       ProcessingJob.job_type.in_(CLIP_JOBS))
    if since is not None:
        stmt = stmt.where(ProcessingJob.created_at >= since)
    jobs = list(db.scalars(stmt))
    busy = {j.clip_id for j in jobs if j.status in ACTIVE}
    if not live:
        status = "skipped" if analysis is not None and analysis.status == JobStatus.SUCCEEDED else "pending"
        return _stage("clip_generation", label, status=status,
                      message="No clips" if status == "skipped" else None), counts
    if analysis is not None and analysis.status != JobStatus.SUCCEEDED and not jobs:
        return _stage("clip_generation", label), counts
    done = [c for c in live if c.id not in busy]
    starts = [j.started_at for j in jobs if j.started_at is not None]
    ends = [j.finished_at for j in jobs if j.finished_at is not None]
    running = bool(busy)
    failed_renders = sum(1 for c in live if c.id not in busy and c.render_status == "failed")
    review = sum(1 for c in live if statuses.get(c.id) == "processing" and c.id not in busy)
    notes = []
    if review:
        notes.append(f"{review} clip{'s' if review != 1 else ''} need{'s' if review == 1 else ''} a framing review")
    if failed_renders:
        notes.append(f"{failed_renders} render{'s' if failed_renders != 1 else ''} failed")
    return _stage("clip_generation", label, status="running" if running else "done",
                  progress=round(len(done) / len(live), 4), started_at=min(starts) if starts else None,
                  finished_at=None if running else (max(ends) if ends else None),
                  seconds=None if running else _seconds(min(starts) if starts else None, max(ends) if ends else None),
                  message=f"{len(done)}/{len(live)} clips finished",
                  error="; ".join(notes) or None), counts


def pipeline(db: Session, source: VideoSource) -> dict[str, Any]:
    """PipelineOut of ``source`` (see docs/API_V2.md)."""
    now = utcnow()
    imp = _latest(db, source.id, JobType.IMPORT)
    ana = _latest(db, source.id, JobType.ANALYSIS)
    stages: dict[str, dict[str, Any]] = {"ingestion": _ingestion(source, imp)}
    recorded = dict((ana.stages or {}) if ana is not None else {})
    for key in ANALYSIS_KEYS:
        rec = dict(recorded.get(key) or {})
        st = rec.get("status") or "pending"
        if ana is not None and ana.status in (JobStatus.FAILED, JobStatus.CANCELLED) and st == "running":
            st = "failed"
            rec["error"] = rec.get("error") or source.error or "Processing failed"
        if ana is not None and ana.status == JobStatus.SUCCEEDED and st in ("pending", "running"):
            st = "done" if st == "running" else "skipped"
        started, finished = _dt(rec.get("started_at")), _dt(rec.get("finished_at"))
        seconds = rec.get("seconds")
        if seconds is None:
            seconds = _seconds(started, finished)
        progress = rec.get("progress")
        if st == "done":
            progress = 1.0
        stages[key] = _stage(key, dict(STAGES)[key], status=st, progress=progress, started_at=started,
                             finished_at=finished, seconds=seconds, message=rec.get("message"),
                             error=rec.get("error") if st == "failed" else None)
    clip_stage, counts = _clip_generation(db, source, ana)
    stages["clip_generation"] = clip_stage

    import_failed = imp is not None and imp.status == JobStatus.FAILED and not source.storage_key
    analysis_failed = ana is not None and ana.status == JobStatus.FAILED
    active = (imp is not None and imp.status in ACTIVE) or (ana is not None and ana.status in ACTIVE) or \
        clip_stage["status"] == "running"
    finished_candidates = [d for d in (ana.finished_at if ana else None, clip_stage["finished_at"]) if d]
    if active:
        status = "running"
    elif import_failed or analysis_failed:
        status = "failed"
    elif ana is not None and ana.status == JobStatus.SUCCEEDED:
        status = "completed"
    else:
        status = "idle"
    done_at = max(finished_candidates) if status == "completed" and finished_candidates else None
    stages["completed"] = _stage("completed", dict(STAGES)["completed"],
                                 status="done" if status == "completed" else ("failed" if status == "failed"
                                                                              else "pending"),
                                 finished_at=done_at, error=source.error if status == "failed" else None)
    starts = [d for d in ((imp.started_at if imp else None), (ana.started_at if ana else None)) if d]
    started_at = min(starts) if starts else None
    finished_at = done_at if status == "completed" else ((ana.finished_at if ana else None) if status == "failed"
                                                         else None)
    elapsed = _seconds(started_at, finished_at or (now if started_at else None))
    eta = None
    running = next((s for s in stages.values() if s["status"] == "running"), None)
    if running is not None and running["progress"] is not None and running["started_at"] is not None:
        p = float(running["progress"])
        if MIN_ETA_PROGRESS <= p < 1.0:
            spent = (now - running["started_at"]).total_seconds()
            eta = round(max(0.0, spent * (1.0 - p) / p), 1)
    device = dict(recorded.get("_device") or {})
    if not device.get("model"):
        device["model"] = db.scalar(select(Transcript.model).where(Transcript.video_source_id == source.id))
    if device.get("transcription") not in ("cuda", "cpu"):
        device["transcription"] = None
    return {
        "source_id": source.id, "status": status,
        "mode": source.processing_mode or ((((ana.params or {}).get("options") or {}).get("mode")) if ana else None),
        "started_at": started_at, "finished_at": finished_at, "elapsed_seconds": elapsed, "eta_seconds": eta,
        "stages": [stages[k] for k, _label in STAGES], "clips": counts,
        "device": {"transcription": device.get("transcription"), "model": device.get("model")},
    }
