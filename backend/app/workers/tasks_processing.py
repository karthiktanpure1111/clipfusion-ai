"""Source-level background tasks: ``import_source`` (authorised cloud download / direct link) and
``analyze_source`` (full analysis pipeline -> clips).

API v2: processing is fully automatic. An import that finishes starts the analysis (unless
``auto_analyze`` is false); the analysis creates clips, whose ``clip_vision`` jobs render them
automatically. Per-stage progress and timings are persisted on the analysis job (``stages``) for
``GET /sources/{id}/pipeline``. Stored transcript/audio/scene/visual results are reused on
re-analysis."""

from __future__ import annotations

import inspect
import logging
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import SessionLocal, utcnow
from app.core.errors import AppError, IntegrationNotConfigured, PermissionDenied, ValidationFailed
from app.core.storage import materialize, temp_workdir
from app.models import ConnectedAccount, ProcessingJob, Transcript, User, VideoAnalysis, VideoSource
from app.models.enums import JobType, NotificationType, SourceStatus, SourceType
from app.modules.notifications.service import notify
from app.schemas.media import (
    AnalysisOptions,
    AudioAnalysis,
    RetentionPoint,
    SceneCut,
    TranscriptData,
    VisualActivity,
)
from app.workers.jobs import JobCancelled, JobContext, PermanentJobError, run_job
from app.workers.registry import task

log = logging.getLogger(__name__)
ANALYSIS_SHARE = 0.9  # pipeline progress maps onto 0..0.9; clip creation (metadata) uses the rest
_PERMANENT = (ValidationFailed, PermissionDenied, IntegrationNotConfigured)
PIPELINE_KEYS = ("audio", "transcription", "video_analysis", "highlights", "metadata")
# Engine stage names (old and new) -> pipeline stage keys.
ENGINE_STAGE = {"audio": "audio", "transcription": "transcription",
                "transcribe": "transcription", "transcript": "transcription", "scenes": "video_analysis",
                "visual": "video_analysis", "video_analysis": "video_analysis", "vision": "video_analysis",
                "highlights": "highlights", "metadata": "metadata"}
STAGE_WRITE_INTERVAL = 1.5
# Stages that are certainly finished when the key starts (audio -> transcription in one thread; the
# highlights need both the speech and the video analysis).
SEQUENTIAL_BEFORE = {"transcription": ("audio",), "highlights": ("audio", "transcription", "video_analysis"),
                     "metadata": ("audio", "transcription", "video_analysis", "highlights")}


def _source_or_fail(db: Session, job: ProcessingJob) -> VideoSource:
    source = db.get(VideoSource, job.video_source_id) if job.video_source_id else None
    if source is None:
        raise PermanentJobError("The video no longer exists.")
    return source


def _message(exc: BaseException) -> str:
    return exc.message if isinstance(exc, AppError) else str(exc) or exc.__class__.__name__


# ------------------------------------------------------------------- import
def _download(ctx: JobContext, db: Session, source: VideoSource, params: dict, work: Path) -> tuple[Path, str]:
    max_bytes = get_settings().max_upload_bytes
    progress = ctx.progress_fn("downloading", 0.02, 0.8)
    if params.get("provider") == "google_drive":
        from app.integrations.google_drive import download_drive_file
        from app.modules.accounts.tokens import get_access_token

        account = db.get(ConnectedAccount, uuid.UUID(str(params.get("account_id"))))
        if account is None or account.user_id != source.user_id:
            raise PermanentJobError("The Google account used for this import is no longer connected.")
        token = get_access_token(db, account)
        path, filename, _mime = download_drive_file(token, str(params.get("file_id")), work, max_bytes=max_bytes,
                                                    progress=progress)
    elif params.get("provider") == "youtube":
        from app.integrations import ytdlp

        try:
            from app.modules.storage.service import ensure_disk_space

            path, filename, _mime = ytdlp.download(
                str(params.get("video_id")), work, quality=str(params.get("quality") or "best"),
                max_bytes=max_bytes, progress=progress,
                # the download, the stored copy and the analysis files all need room
                ensure_space=lambda size: ensure_disk_space(int(size * 2.2), "download this video"))
        except ytdlp.YouTubeDownloadRefused as exc:
            raise PermanentJobError(exc.message) from exc
    else:
        from app.integrations.cloud_import import download_url

        path, filename, _mime = download_url(str(params.get("url")), work, max_bytes=max_bytes, progress=progress)
    return path, filename


def _import_handler(ctx: JobContext, db: Session, job: ProcessingJob) -> dict[str, Any]:
    from app.modules.storage.service import InsufficientDiskSpace, ensure_disk_space
    from app.modules.uploads.service import ingest_video_file

    source = _source_or_fail(db, job)
    params = dict(job.params or {})
    if source.storage_key:
        return {"already_imported": True}
    ctx.progress("downloading", 0.01, "Starting download", force=True)
    try:
        ensure_disk_space(0, "import this video")
    except InsufficientDiskSpace as exc:
        raise PermanentJobError(exc.message) from exc
    with temp_workdir("cf-import-") as work:
        try:
            path, filename = _download(ctx, db, source, params, work)
            ctx.progress("validating", 0.85, "Validating the video", force=True)
            result = ingest_video_file(db, path, source=source, original_filename=filename)
        except _PERMANENT as exc:
            db.rollback()
            raise PermanentJobError(_message(exc)) from exc
    if result.duplicate:
        db.rollback()
        dup = result.source
        raise PermanentJobError(f"This file was already added as “{dup.title or 'Untitled video'}” "
                                f"(video {dup.id}).")
    if not source.title:
        source.title = Path(filename).stem[:500] or "Imported video"
    source.original_deleted_at = None
    auto = bool(params.get("auto_analyze", False))
    notify(db, source.user_id, NotificationType.SYSTEM, f"Import finished: {source.title}"[:200],
           "The video was imported and validated." + (" Processing starts automatically." if auto else
                                                      " Start the processing to generate clips."),
           link=f"/app/videos/{source.id}", data={"video_source_id": str(source.id)})
    db.commit()
    log.info("Imported source %s (%s bytes)", source.id, source.file_size)
    analysis_job = None
    if auto:
        from app.modules.sources.service import auto_start_analysis

        started = auto_start_analysis(db, db.get(User, source.user_id), source, params.get("analysis"))
        analysis_job = str(started.id) if started is not None else None
    return {"file_size": source.file_size, "duration": source.duration, "analysis_job_id": analysis_job}


def on_import_failed(db: Session, job: ProcessingJob, exc: BaseException) -> None:
    source = db.get(VideoSource, job.video_source_id) if job.video_source_id else None
    if source is None:
        return
    # A YouTube-link video keeps its metadata and can get another file attached.
    source.status = SourceStatus.METADATA_ONLY if source.source_type == SourceType.YOUTUBE and \
        not source.storage_key else SourceStatus.FAILED
    source.error = f"Import failed: {_message(exc)}"[:2000]
    notify(db, source.user_id, NotificationType.PROCESSING_FAILED,
           f"Import failed: {source.title or 'video'}"[:200], source.error,
           link=f"/app/videos/{source.id}", data={"video_source_id": str(source.id), "job_id": str(job.id)})


@task("import_source", queue="default", job_type=JobType.IMPORT)
def import_source(job_id: str) -> None:
    """Download an authorised file (HTTPS link or Google Drive) and store it as the source original."""
    run_job(job_id, _import_handler, task_name="import_source", on_final_failure=on_import_failed)


# ------------------------------------------------------------- stage tracker
def _engine_run_state() -> Any:
    """The engine's live run state (per-stage ``fractions`` and ``timings``) when the progress
    callback is invoked from it. The engine calls ``progress`` synchronously on the caller's
    thread from its run object; reading that object is the only way to get each stage's own
    measured fraction while audio/transcription and video analysis run concurrently."""
    frame = sys._getframe(2)  # engine_progress <- _Run.handle
    for _ in range(3):
        if frame is None:
            return None
        candidate = frame.f_locals.get("self")
        if isinstance(getattr(candidate, "fractions", None), dict) and \
                isinstance(getattr(candidate, "timings", None), dict):
            return candidate
        frame = frame.f_back
    return None


class StageTracker:
    """Records per-stage status/progress/timings of the analysis job in ``ProcessingJob.stages``
    (separate short sessions, throttled) while forwarding progress to the job context.

    Stages may run concurrently (the engine overlaps transcription with the video analysis), so
    each stage is tracked independently. ``progress`` is only set when it was measured."""

    def __init__(self, ctx: JobContext | None, job_id: uuid.UUID) -> None:
        self.ctx, self.job_id = ctx, job_id
        self.stages: dict[str, Any] = {k: {"status": "pending"} for k in PIPELINE_KEYS}
        self._last_write = 0.0
        self._lock = threading.Lock()
        self.write(force=True)

    def write(self, *, force: bool = False) -> None:
        now = time.monotonic()
        with self._lock:
            if not force and now - self._last_write < STAGE_WRITE_INTERVAL:
                return
            self._last_write = now
            snapshot = {k: dict(v) if isinstance(v, dict) else v for k, v in self.stages.items()}
        try:
            with SessionLocal() as s:
                job = s.get(ProcessingJob, self.job_id)
                if job is not None:
                    job.stages = snapshot
                    s.commit()
        except Exception:  # noqa: BLE001 - progress bookkeeping must not fail the analysis
            log.debug("Could not persist stages of job %s", self.job_id, exc_info=True)

    @property
    def running(self) -> list[str]:
        return [k for k in PIPELINE_KEYS if self.stages[k].get("status") == "running"]

    def start(self, key: str, message: str | None = None, *, measurable: bool = False) -> None:
        st = self.stages.setdefault(key, {})
        if st.get("status") != "running":
            st.update(status="running", started_at=utcnow().isoformat(), finished_at=None, seconds=None,
                      progress=0.0 if measurable else None, error=None)
        if message:
            st["message"] = message[:300]

    def finish(self, key: str, *, message: str | None = None) -> None:
        st = self.stages.setdefault(key, {})
        if st.get("status") == "running":
            end = utcnow()
            st["status"], st["finished_at"], st["progress"] = "done", end.isoformat(), 1.0
            try:
                st["seconds"] = round(max(0.0, (end - datetime.fromisoformat(st["started_at"])).total_seconds()), 3)
            except (KeyError, TypeError, ValueError):
                st["seconds"] = None
        if message:
            st["message"] = message[:300]

    def finish_all(self) -> None:
        for key in self.running:
            self.finish(key)

    def fail(self, error: str) -> None:
        for key in self.running or [k for k in PIPELINE_KEYS if self.stages[k].get("status") == "pending"][:1]:
            self.stages[key].update(status="failed", finished_at=utcnow().isoformat(), error=error[:1000])
        self.write(force=True)

    def _apply_record(self, name: str, record: Any, fraction: Any = None) -> None:
        key = ENGINE_STAGE.get(str(name))
        if key is None or not isinstance(record, dict):
            return
        st = self.stages.setdefault(key, {})
        if st.get("status") == "failed":
            return
        started, finished = record.get("started_at"), record.get("finished_at")
        if record.get("skipped"):
            st.update(status="skipped", started_at=started, finished_at=finished, seconds=0.0, progress=None)
            st.setdefault("message", "Reused stored results")
            return
        if started:
            st["started_at"] = str(started)
        if finished:
            st.update(status="done", finished_at=str(finished), progress=1.0)
        elif started:
            st["status"] = "running"
            if isinstance(fraction, (int, float)):
                st["progress"] = round(min(1.0, max(0.0, float(fraction))), 4)
        if record.get("seconds") is not None:
            try:
                st["seconds"] = round(float(record["seconds"]), 3)
            except (TypeError, ValueError):
                pass

    def engine_progress(self, stage: str, overall: float, message: str | None) -> None:
        """``analyze_source`` progress callback."""
        if self.ctx is not None:
            self.ctx.progress(stage, overall * ANALYSIS_SHARE, message)  # raises JobCancelled
        key = ENGINE_STAGE.get(stage)
        run = None
        try:
            run = _engine_run_state()
        except Exception:  # noqa: BLE001 - introspection is best effort
            run = None
        if run is not None:
            fractions = dict(run.fractions)
            for name, record in dict(run.timings).items():
                self._apply_record(name, record, fractions.get(name))
        elif key is not None:  # fallback: order-based bookkeeping, progress not measurable
            for earlier in SEQUENTIAL_BEFORE.get(key, ()):
                self.finish(earlier)
            self.start(key, message)
        if key is not None and message:
            self.stages[key]["message"] = message[:300]
        self.write()

    def metadata_progress(self, fraction: float, message: str | None) -> None:
        self.start("metadata", message, measurable=True)
        st = self.stages["metadata"]
        st["progress"] = round(min(1.0, max(0.0, fraction)), 4)
        if self.ctx is not None:
            self.ctx.progress("metadata", ANALYSIS_SHARE + (1 - ANALYSIS_SHARE) * fraction, message)
        self.write()

    def apply_timings(self, timings: Any) -> None:
        """The engine's measured per-stage records (``result.stage_timings``) are authoritative."""
        if not isinstance(timings, dict):
            return
        for name, value in timings.items():
            if isinstance(value, dict):
                self._apply_record(name, value)
            else:
                key = ENGINE_STAGE.get(str(name))
                try:
                    seconds = float(value)
                except (TypeError, ValueError):
                    continue
                if key is not None and self.stages.get(key, {}).get("status") in ("done", "running"):
                    self.stages[key]["seconds"] = round(seconds, 3)

    def set_device(self, device: str | None, model: str | None) -> None:
        self.stages["_device"] = {"transcription": device if device in ("cuda", "cpu") else None, "model": model}


# ------------------------------------------------------------------ analysis
def _options(job: ProcessingJob, source: VideoSource) -> AnalysisOptions:
    raw = dict((job.params or {}).get("options") or source.analysis_options or {})
    raw.pop("retention", None)
    options = AnalysisOptions.model_validate({k: v for k, v in raw.items() if k in AnalysisOptions.model_fields})
    points = (source.retention_data or {}).get("points") or []
    if points:
        options.retention = [RetentionPoint.model_validate(p) for p in points]
    return options


def _reusable_transcript(db: Session, source: VideoSource, options: AnalysisOptions) -> TranscriptData | None:
    """The stored transcript when it was made with the model this request would use (the engine's
    ``expected_transcription_model``: mode profile / explicit model / GPU) and the same language."""
    row = db.scalar(select(Transcript).where(Transcript.video_source_id == source.id))
    if row is None or not row.segments:
        return None
    expected = options.whisper_model
    try:
        from app.modules.analysis.modes import expected_transcription_model

        expected = expected_transcription_model(options)
    except Exception:  # noqa: BLE001 - older engine: only an explicit model must match
        pass
    if expected and row.model != expected:
        return None
    if options.language and row.language and row.language != options.language:
        return None
    try:
        return TranscriptData(language=row.language, model=row.model, duration=row.duration, segments=row.segments)
    except ValidationError:
        return None


def _reusable_analysis(db: Session, source: VideoSource) -> dict[str, Any]:
    """Stored audio/scene/visual results as ``existing_*`` candidates for ``analyze_source``."""
    from app.modules.clips.media import source_media

    out: dict[str, Any] = {}
    media = source_media(source)
    if media is not None and media.duration:
        out["existing_media"] = media
    row = db.scalar(select(VideoAnalysis).where(VideoAnalysis.video_source_id == source.id))
    if row is None:
        return out
    try:
        if row.audio:
            out["existing_audio"] = AudioAnalysis.model_validate(row.audio)
        if row.scenes:
            out["existing_scenes"] = [SceneCut(time=float(t)) for t in row.scenes]
        if row.visual:
            out["existing_visual"] = VisualActivity.model_validate(row.visual)
    except (ValidationError, TypeError, ValueError):
        log.info("Stored analysis of source %s is not reusable", source.id)
    return out


def _supported(fn: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return {}
    if any(p.kind == p.VAR_KEYWORD for p in params.values()):
        return kwargs
    return {k: v for k, v in kwargs.items() if k in params}


class _StagePersister:
    """Persist each pipeline stage's result immediately (survives later failures/retries)."""

    def __init__(self, db: Session, source: VideoSource, reused: TranscriptData | None,
                 reused_objects: tuple = ()) -> None:
        self.db, self.source, self.reused = db, source, reused
        self.reused_ids = {id(o) for o in reused_objects}

    def analysis_row(self) -> VideoAnalysis:
        row = self.db.scalar(select(VideoAnalysis).where(VideoAnalysis.video_source_id == self.source.id))
        if row is None:
            row = VideoAnalysis(video_source_id=self.source.id, audio={}, scenes=[], visual={}, highlight_debug={})
            self.db.add(row)
            self.db.flush()
        return row

    def __call__(self, name: str, value: Any) -> None:
        if id(value) in self.reused_ids:
            return
        if name == "transcript" and value is not self.reused and isinstance(value, TranscriptData):
            self._save_transcript(value)
        elif name == "audio" and isinstance(value, AudioAnalysis):
            self.analysis_row().audio = value.model_dump(mode="json")
        elif name == "scenes" and isinstance(value, list):
            self.analysis_row().scenes = [round(float(c.time), 3) for c in value if isinstance(c, SceneCut)]
        elif name == "visual" and isinstance(value, VisualActivity):
            self.analysis_row().visual = value.model_dump(mode="json")
        else:
            return
        self.db.commit()

    def _save_transcript(self, t: TranscriptData) -> None:
        row = self.db.scalar(select(Transcript).where(Transcript.video_source_id == self.source.id))
        if row is None:
            row = Transcript(video_source_id=self.source.id, model=t.model)
            self.db.add(row)
            self.db.flush()
        row.language, row.model, row.duration = t.language, t.model, t.duration
        row.segments = [s.model_dump(mode="json") for s in t.segments]
        row.text = t.text
        row.word_count = len(t.words()) or len(t.text.split())


def _device_warning(device: str | None) -> str | None:
    wanted = get_settings().whisper_device
    if device == "cpu" and wanted in ("cuda", "auto"):
        try:  # faster-whisper runs on CTranslate2
            import ctranslate2

            has_cuda = ctranslate2.get_cuda_device_count() > 0
        except Exception:  # noqa: BLE001
            has_cuda = False
        if wanted == "cuda" or has_cuda:
            return "GPU unavailable for transcription; it ran on the CPU instead (slower, same results)."
    return None


def _analyze_handler(ctx: JobContext, db: Session, job: ProcessingJob) -> dict[str, Any]:
    from app.modules.analysis.pipeline import analyze_source
    from app.modules.scheduling.service import on_clips_generated
    from app.modules.sources.clip_factory import create_clips

    source = _source_or_fail(db, job)
    if not source.storage_key:
        if source.original_deleted_at is not None:
            raise PermanentJobError("The original video was deleted to free space; attach it again to process it.")
        raise PermanentJobError("The video has no stored original file.")
    user = db.get(User, source.user_id)
    options = _options(job, source)
    reused = _reusable_transcript(db, source, options)
    existing = {"existing_transcript": reused, **_reusable_analysis(db, source)}
    existing = {k: v for k, v in existing.items() if v is not None}
    accepted = _supported(analyze_source, existing)
    source.status, source.error = SourceStatus.PROCESSING, None
    source.processing_mode = getattr(options, "mode", None) or source.processing_mode
    db.commit()
    tracker = StageTracker(ctx, job.id)
    ctx.progress("probe", 0.0, "Starting analysis", force=True)
    persister = _StagePersister(db, source, reused, tuple(accepted.values()))
    try:
        with temp_workdir("cf-analysis-") as work, materialize(source.storage_key) as path:
            result = analyze_source(path, work, options, source_title=source.title or None,
                                    on_stage_result=persister, progress=tracker.engine_progress, **accepted)
    except JobCancelled:
        tracker.fail("Cancelled")
        raise
    except Exception as exc:
        tracker.fail(_message(exc))
        raise
    tracker.apply_timings(getattr(result, "stage_timings", None))
    tracker.finish_all()
    for key in ("audio", "transcription", "video_analysis", "highlights"):
        if tracker.stages[key].get("status") == "pending":
            tracker.stages[key].update(status="skipped", message="Reused stored results")
    if reused is not None and "existing_transcript" in accepted:
        tracker.stages["transcription"]["message"] = "Reused the stored transcript"
    device = getattr(result, "device", None)
    model = getattr(result, "transcription_model", None) or getattr(getattr(result, "transcript", None), "model", None)
    tracker.set_device(device if isinstance(device, str) else None, model if isinstance(model, str) else None)
    tracker.write(force=True)
    warnings = list(getattr(result, "warnings", []) or [])
    gpu_note = _device_warning(device if isinstance(device, str) else None)
    if gpu_note and gpu_note not in warnings:
        warnings.append(gpu_note)
    analysis = persister.analysis_row()
    analysis.highlight_debug = {"warnings": warnings, "highlights": len(result.highlights),
                                "options": options.model_dump(mode="json", exclude={"retention"}),
                                "used_retention": bool(options.retention), "finished_at": utcnow().isoformat(),
                                "device": device if isinstance(device, str) else None, "transcription_model": model,
                                "reused": sorted(accepted)}
    db.commit()

    tracker.start("metadata", f"Creating {len(result.highlights)} clips", measurable=True)
    tracker.write(force=True)
    clips = create_clips(db, source, result.highlights, use_llm=options.use_llm, progress=tracker.metadata_progress)
    tracker.finish("metadata", message=f"{len(clips)} clips created")
    tracker.write(force=True)
    source.status, source.processed_at, source.error = SourceStatus.READY, utcnow(), None
    title = source.title or "video"
    body = (f"{len(clips)} new clips were found. They are being framed and rendered automatically." if clips
            else "No new highlight clips were found (existing and removed clips were kept).")
    notify(db, source.user_id, NotificationType.PROCESSING_COMPLETE, f"Analysis finished: {title}"[:200], body,
           link=f"/app/videos/{source.id}", data={"video_source_id": str(source.id), "clips": len(clips)})
    db.commit()
    if user is not None and clips:
        on_clips_generated(db, user, clips)
        db.commit()
    log.info("Analysis of source %s finished: %d highlights, %d clips", source.id, len(result.highlights), len(clips))
    return {"highlights": len(result.highlights), "clips_created": len(clips), "warnings": warnings,
            "device": device if isinstance(device, str) else None, "transcription_model": model}


def on_analysis_failed(db: Session, job: ProcessingJob, exc: BaseException) -> None:
    source = db.get(VideoSource, job.video_source_id) if job.video_source_id else None
    if source is None:
        return
    source.status = SourceStatus.FAILED
    source.error = _message(exc)[:2000]
    stages = dict(job.stages or {})
    for key, st in stages.items():
        if isinstance(st, dict) and st.get("status") == "running":
            stages[key] = {**st, "status": "failed", "error": source.error[:1000], "finished_at": utcnow().isoformat()}
    job.stages = stages
    notify(db, source.user_id, NotificationType.PROCESSING_FAILED,
           f"Processing failed: {source.title or 'video'}"[:200], source.error,
           link=f"/app/videos/{source.id}", data={"video_source_id": str(source.id), "job_id": str(job.id)})


@task("analyze_source", queue="analysis", job_type=JobType.ANALYSIS)
def analyze_source_task(job_id: str) -> None:
    """Run the whole-source analysis and create clips (+ clip_vision jobs -> automatic renders)."""
    run_job(job_id, _analyze_handler, task_name="analyze_source", on_final_failure=on_analysis_failed)
