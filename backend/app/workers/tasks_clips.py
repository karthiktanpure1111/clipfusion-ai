"""Clip-level background tasks: ``clip_vision``, ``transcribe_clip``, ``generate_metadata`` and
``render_clip`` (thin wrappers around :func:`app.workers.jobs.run_job`).

API v2 pipeline: ``clip_vision`` (proxy, thumbnail, face tracking, framing) is followed by an
automatic render unless blocking framing warnings need a review (the clip then stays
"processing -> needs framing review" and the user is notified). Clips are rendered clean (no
subtitles). Removed clips are never processed."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import utcnow
from app.core.errors import AppError, ConflictError, DependencyMissing, ValidationFailed
from app.core.storage import clip_key, get_storage, materialize, temp_workdir
from app.models import GeneratedClip, ProcessingJob, Transcript, User, VideoSource
from app.models.enums import JobType, NotificationType, RenderStatus
from app.modules.clips.media import (
    load_audio,
    load_transcript,
    range_text,
    range_words,
    source_media,
    speech_ranges,
    stored_vision,
)
from app.schemas.media import MediaInfo, TranscriptData, TranscriptSegment
from app.workers.jobs import JobCancelled, JobContext, PermanentJobError, run_job
from app.workers.registry import task

log = logging.getLogger(__name__)

MAX_VISION_PASSES = 3
USER_FACING_ERRORS = (PermanentJobError, ValidationFailed, DependencyMissing)


def _load(db: Session, job: ProcessingJob) -> tuple[GeneratedClip, VideoSource]:
    clip = db.get(GeneratedClip, job.clip_id) if job.clip_id else None
    if clip is None or clip.removed_at is not None:
        raise PermanentJobError("The clip no longer exists (it was removed).")
    source = db.get(VideoSource, clip.video_source_id)
    if source is not None and not source.storage_key and source.original_deleted_at is not None:
        raise PermanentJobError("The original video was deleted to free space; attach it again to process "
                                "this clip.")
    if source is None or not source.storage_key or not get_storage().exists(source.storage_key):
        raise PermanentJobError("The source video file is not available.")
    return clip, source


def _reload(db: Session, clip: GeneratedClip) -> GeneratedClip:
    """Re-read the clip and everything attached to it after a long step (sessions use
    ``expire_on_commit=False``), or stop and remove its files when it was deleted meanwhile.

    Discards unflushed changes: commit before calling."""
    user_id, clip_id = clip.user_id, clip.id
    db.expire_all()
    fresh = db.get(GeneratedClip, clip_id)
    if fresh is None or fresh.removed_at is not None:
        get_storage().delete_prefix(f"users/{user_id}/clips/{clip_id}/")
        raise PermanentJobError("The clip was removed while it was being processed.")
    return fresh


def _media(source: VideoSource, path: Path) -> MediaInfo:
    from app.modules.analysis.probe import probe_media

    return source_media(source) or probe_media(path)


# ---------------------------------------------------------------- clip_vision
def _analyse_range(ctx: JobContext, db: Session, clip: GeneratedClip, source: VideoSource, *,
                   span: tuple[float, float] = (0.0, 1.0)) -> dict[str, Any]:
    """Analyse the clip's current range and store the result (``span`` = share of job progress)."""
    from app.modules.analysis.modes import get_profile
    from app.modules.analysis.probe import extract_frame
    from app.modules.clipping.proxy import make_clip_proxy
    from app.modules.faces.models import ensure_face_models
    from app.modules.framing import service as framing
    from app.modules.framing.suggest import suggest_framing
    from app.modules.storage.service import refresh_media_bytes
    from app.modules.tracking.tracker import analyze_clip_vision

    def at(fraction: float) -> float:
        return span[0] + (span[1] - span[0]) * fraction

    storage = get_storage()
    start, end = clip.start_time, clip.end_time
    with temp_workdir("cf-vision-") as wd, materialize(source.storage_key) as src:
        media = _media(source, src)
        ctx.progress("proxy", at(0.02), "Encoding the editor preview", force=True)
        profile = get_profile(source.processing_mode)
        proxy_start = make_clip_proxy(src, start, end, wd / "proxy.mp4", preset=profile.proxy_preset)
        proxy_key = clip_key(clip.user_id, clip.id, "proxy.mp4")
        storage.put_file(wd / "proxy.mp4", proxy_key, "video/mp4")
        ctx.progress("thumbnail", at(0.2), "Extracting the thumbnail")
        thumb = extract_frame(src, (start + end) / 2, wd / "thumb.jpg")
        thumb_key = clip_key(clip.user_id, clip.id, "thumb.jpg")
        storage.put_file(thumb, thumb_key, "image/jpeg")
        clip.proxy_key, clip.proxy_start = proxy_key, proxy_start
        if clip.thumbnail_key is None or not clip.output_key:
            clip.thumbnail_key = thumb_key
        refresh_media_bytes(db, clip)
        db.commit()  # the editors stay usable even if the face analysis fails
        ctx.progress("faces", at(0.25), "Detecting and tracking faces", force=True)
        try:
            ensure_face_models()
        except Exception as exc:  # noqa: BLE001 - the detector falls back to what is installed
            log.warning("face models unavailable (%s); using the available detector", exc)
        speech = speech_ranges(load_audio(db, source.id), start, end, has_audio=media.has_audio)
        vision = analyze_clip_vision(src, start, end, media, speech=speech, sample_fps=profile.face_sample_fps,
                                     progress=ctx.progress_fn("faces", at(0.25), at(0.88)))
    ctx.progress("framing", at(0.9), "Choosing the framing", force=True)
    clip = _reload(db, clip)  # the user may have edited or trimmed the clip meanwhile
    current = clip.framing
    user = db.get(User, clip.user_id)
    kwargs: dict[str, Any] = {"media": media, "vision": vision}
    if current is None or current.auto_generated:
        preset = framing.default_preset(db, user) if user is not None else None
        template = framing.video_template(db, clip)
        if template is not None:  # the framing chosen for this video
            config = framing.refit_config(template, media.width, media.height)
            kwargs["preset_id"] = None
        elif preset is not None:
            config = framing.refit_config(framing.preset_config(preset), media.width, media.height)
            kwargs["preset_id"] = preset.id
        else:
            config = suggest_framing(vision, media, framing.clip_duration(clip))
            kwargs["preset_id"] = None
        kwargs["auto_generated"] = True
    else:  # the user edited the framing: keep it, recompute keyframes for the (new) analysis
        config = framing.with_fresh_keyframes(framing.load_config(current, media))
        kwargs["auto_generated"] = False
    saved, _ = framing.save_framing(db, clip, config, **kwargs)
    db.commit()
    return {"framing_mode": saved.mode, "faces": len(vision.tracks), "detector": vision.detector,
            "warnings": len(saved.warnings), "proxy_start": proxy_start}


def _clip_vision(ctx: JobContext, db: Session, job: ProcessingJob) -> dict[str, Any]:
    clip, source = _load(db, job)
    result: dict[str, Any] = {}
    for _ in range(MAX_VISION_PASSES):
        analysed = (clip.start_time, clip.end_time)
        result = _analyse_range(ctx, db, clip, source)
        clip = _reload(db, clip)
        if (clip.start_time, clip.end_time) == analysed:
            break
        log.info("clip %s was trimmed during its vision analysis; analysing the new range", clip.id)
    if job.params.get("auto_render", True):
        result["auto_render"] = _auto_render(db, clip)
        if result["auto_render"] != "requested":  # no render follows: this may be the video's last job
            _auto_schedule(db, clip.video_source_id, exclude_job=job.id)
    return result


def _auto_render(db: Session, clip: GeneratedClip) -> str:
    """Render automatically after the framing was chosen, unless blocking framing warnings need a
    review first (then notify). Returns ``requested`` | ``current`` | ``needs_review`` | ``skipped``."""
    from app.modules.notifications.service import notify
    from app.modules.rendering.service import render_is_current, request_render

    if clip.removed_at is not None or clip.upload_status in ("uploading", "uploaded"):
        return "skipped"
    if render_is_current(clip):
        return "current"
    try:
        request_render(db, clip, acknowledge_warnings=False, reason="auto")
    except ConflictError as exc:
        db.rollback()
        if exc.code != "framing_warnings_unacknowledged":
            log.info("automatic render of clip %s not started: %s", clip.id, exc.message)
            return "skipped"
        clip = _reload(db, clip)
        notify(db, clip.user_id, NotificationType.FRAMING_REVIEW, "Framing review needed",
               f"“{clip.title or 'A clip'}” was not rendered automatically: the framing would hide or cut a face "
               "or important content. Review the framing, then render it.",
               link=f"/app/clips/{clip.id}/framing", data={"clip_id": str(clip.id)})
        db.commit()
        return "needs_review"
    except AppError as exc:  # e.g. insufficient disk space: reported on the clip
        db.rollback()
        clip = _reload(db, clip)
        clip.render_error = exc.message[:2000]
        db.commit()
        log.warning("automatic render of clip %s not started: %s", clip.id, exc.message)
        return "skipped"
    return "requested"


@task("clip_vision", queue="analysis", job_type=JobType.CLIP_VISION)
def clip_vision_task(job_id: str) -> None:
    """Proxy + thumbnail + face tracking + framing suggestion for a clip, then the automatic render."""
    run_job(job_id, _clip_vision, task_name="clip_vision")


# ------------------------------------------------------------ transcribe_clip
def splice_transcript(db: Session, source: VideoSource, data: TranscriptData, start: float, end: float) -> None:
    """Replace the source transcript inside ``[start, end]`` with ``data`` (source times)."""
    row = db.scalar(select(Transcript).where(Transcript.video_source_id == source.id))
    if row is None:
        row = Transcript(video_source_id=source.id, language=data.language, model=data.model, segments=[], text="",
                         word_count=0, duration=source.duration)
        db.add(row)
        existing: list[TranscriptSegment] = []
    else:
        existing = TranscriptData(model=row.model or data.model, segments=row.segments or []).segments
    kept: list[TranscriptSegment] = []
    for seg in existing:
        if seg.end <= start or seg.start >= end:
            kept.append(seg)
            continue
        for part in ([w for w in seg.words if w.end <= start + 1e-6], [w for w in seg.words if w.start >= end - 1e-6]):
            if part:
                kept.append(seg.model_copy(update={"start": part[0].start, "end": part[-1].end, "words": part,
                                                   "text": " ".join(w.word.strip() for w in part)}))
    merged = sorted([*kept, *data.segments], key=lambda s: (s.start, s.end))
    merged = [s.model_copy(update={"id": i}) for i, s in enumerate(merged)]
    row.segments = [s.model_dump(mode="json") for s in merged]
    row.text = " ".join(s.text.strip() for s in merged).strip()
    row.word_count = sum(len(s.words) for s in merged)
    row.language = row.language or data.language
    db.flush()


def _transcribe_clip(ctx: JobContext, db: Session, job: ProcessingJob) -> dict[str, Any]:
    from app.modules.analysis.audio import extract_audio_wav
    from app.modules.framing.service import refresh_warnings
    from app.modules.transcription.service import transcribe

    clip, source = _load(db, job)
    language = job.params.get("language") or None
    start, end = clip.start_time, clip.end_time
    with temp_workdir("cf-transcribe-") as wd, materialize(source.storage_key) as src:
        ctx.progress("audio", 0.05, "Extracting audio", force=True)
        wav = extract_audio_wav(src, wd / "clip.wav", start=start, end=end)
        ctx.progress("transcribing", 0.15, "Transcribing", force=True)
        try:
            data = transcribe(wav, language=language, offset=start,
                              progress=ctx.progress_fn("transcribing", 0.15, 0.9))
        except DependencyMissing as exc:
            raise PermanentJobError(exc.message) from exc
    clip = _reload(db, clip)
    source = db.get(VideoSource, clip.video_source_id)
    splice_transcript(db, source, data, start, end)
    words = range_words(load_transcript(db, source.id), clip.start_time, clip.end_time)
    clip.transcript_text = range_text(words)
    refresh_warnings(db, clip)
    db.commit()
    return {"language": data.language, "words": len(words)}


@task("transcribe_clip", queue="analysis", job_type=JobType.TRANSCRIBE_CLIP)
def transcribe_clip_task(job_id: str) -> None:
    """Re-transcribe a clip range (updates the clip's transcript text; no captions in API v2)."""
    run_job(job_id, _transcribe_clip, task_name="transcribe_clip")


# ---------------------------------------------------------- generate_metadata
def _generate_metadata(ctx: JobContext, db: Session, job: ProcessingJob) -> dict[str, Any]:
    from app.modules.sources.clip_factory import generate_metadata, sibling_titles

    clip = db.get(GeneratedClip, job.clip_id) if job.clip_id else None
    if clip is None or clip.removed_at is not None:
        raise PermanentJobError("The clip no longer exists.")
    source = db.get(VideoSource, clip.video_source_id)
    ctx.progress("metadata", 0.1, "Writing titles, captions and hashtags", force=True)
    meta = generate_metadata(transcript_text=clip.transcript_text, source=source, category=clip.category,
                             hook=clip.hook_text, use_llm=bool(job.params.get("use_llm", True)),
                             avoid_titles=sibling_titles(db, clip.video_source_id, exclude=clip.id), strict=True)
    clip.social_metadata = meta.model_dump(mode="json")
    if not clip.title and meta.youtube.title:
        clip.title = meta.youtube.title[:300]
    db.commit()
    return {"source": meta.source}


@task("generate_metadata", queue="default", job_type=JobType.METADATA)
def generate_metadata_task(job_id: str) -> None:
    """(Re)generate a clip's YouTube/Instagram metadata."""
    run_job(job_id, _generate_metadata, task_name="generate_metadata")


# ---------------------------------------------------------------- render_clip
def _sibling_jpg(key: str) -> str:
    return key[: -len(Path(key).suffix)] + ".jpg" if Path(key).suffix else key + ".jpg"


def _check_framing(ctx: JobContext, db: Session, clip: GeneratedClip, source: VideoSource) -> GeneratedClip:
    """Last gate before encoding: a cropping layout is never rendered without a face analysis of
    the current range, nor while blocking warnings are unacknowledged (the framing or the trim
    may have changed after the render was requested)."""
    from app.modules.framing.service import refresh_warnings
    from app.modules.rendering.service import assert_framing_allowed

    def unchecked() -> bool:
        return clip.framing is not None and clip.framing.mode != "full_frame" and stored_vision(clip) is None

    for _ in range(MAX_VISION_PASSES):
        if not unchecked():
            break
        try:
            _analyse_range(ctx, db, clip, source, span=(0.02, 0.3))
        except DependencyMissing as exc:
            if not clip.framing.warnings_acknowledged:
                raise PermanentJobError(f"The crop could not be checked for hidden faces: {exc.message}") from exc
            log.warning("rendering clip %s without a face analysis (acknowledged by the user)", clip.id)
            break
        clip = _reload(db, clip)
    else:
        if unchecked():
            raise RuntimeError("the clip kept changing while its framing was being analysed")
    refresh_warnings(db, clip)
    db.commit()
    try:
        assert_framing_allowed(clip)
    except ConflictError as exc:
        raise PermanentJobError(exc.message) from exc
    return clip


def _render(ctx: JobContext, db: Session, job: ProcessingJob) -> dict[str, Any]:
    from app.modules.notifications.service import notify
    from app.modules.rendering.renderer import render_clip
    from app.modules.rendering.service import build_render_spec, compute_render_hash
    from app.modules.scheduling.service import on_clip_render_finished
    from app.modules.storage.service import (
        InsufficientDiskSpace,
        ensure_disk_space,
        estimated_render_bytes,
        refresh_media_bytes,
    )

    clip, source = _load(db, job)
    clip.render_status, clip.render_error = RenderStatus.RENDERING, None
    db.commit()
    storage = get_storage()
    try:
        ctx.progress("preparing", 0.02, "Preparing the render", force=True)
        try:
            ensure_disk_space(estimated_render_bytes(clip.duration or 60.0), "render this clip")
        except InsufficientDiskSpace as exc:
            raise PermanentJobError(exc.message) from exc
        clip = _check_framing(ctx, db, clip, source)
        with temp_workdir("cf-render-") as wd, materialize(source.storage_key) as src:
            media = _media(source, src)
            render_hash = compute_render_hash(clip)
            try:
                spec = build_render_spec(db, clip, source_path=src, media=media, workdir=wd,
                                         output_path=wd / "render.mp4")
                result = render_clip(spec, progress=ctx.progress_fn("rendering", 0.3, 0.95))
            except ValidationFailed as exc:
                raise PermanentJobError(exc.message) from exc
            ctx.progress("saving", 0.96, "Saving the video", force=True)
            out_key = clip_key(clip.user_id, clip.id, f"render-{render_hash[:12]}.mp4")
            storage.put_file(result.output_path, out_key, "video/mp4")
            thumb_key = None
            if result.thumbnail_path is not None and Path(result.thumbnail_path).is_file():
                thumb_key = _sibling_jpg(out_key)
                storage.put_file(Path(result.thumbnail_path), thumb_key, "image/jpeg")
    except JobCancelled:
        db.rollback()
        clip = _reload(db, clip)
        clip.render_status = RenderStatus.STALE if clip.output_key else RenderStatus.NOT_RENDERED
        db.commit()
        raise
    clip = _reload(db, clip)
    old_out = clip.output_key
    clip.output_key, clip.output_size, clip.output_duration = out_key, result.size_bytes, result.duration
    clip.render_hash, clip.rendered_at, clip.render_error = render_hash, utcnow(), None
    if thumb_key is not None:
        clip.thumbnail_key = thumb_key
    clip.render_status = RenderStatus.RENDERED if compute_render_hash(clip) == render_hash else RenderStatus.STALE
    if job.params.get("reason") not in AUTO_RENDER_REASONS:  # automatic renders stay quiet
        outcome = "is ready to upload." if clip.render_status == RenderStatus.RENDERED else \
            "was rendered, but it was edited meanwhile. Render it again to include your changes."
        notify(db, clip.user_id, NotificationType.RENDER_COMPLETE, "Render complete",
               f"“{clip.title or 'Your clip'}” {outcome}", link=f"/app/clips/{clip.id}",
               data={"clip_id": str(clip.id)})
    on_clip_render_finished(db, clip, True)
    db.commit()
    if old_out and old_out != out_key:
        for key in (old_out, _sibling_jpg(old_out)):
            if key == clip.thumbnail_key:
                continue
            try:
                storage.delete(key)
            except Exception:  # noqa: BLE001 - orphaned files are removed by maintenance
                log.warning("could not delete the previous render object %s", key)
    refresh_media_bytes(db, clip)
    db.commit()
    if clip.render_status == RenderStatus.STALE and job.params.get("reason") in AUTO_RENDER_REASONS:
        _rerender(db, clip, job.params.get("reason"))
    _notify_source_ready(db, clip.video_source_id, exclude_job=job.id)
    _auto_schedule(db, clip.video_source_id, exclude_job=job.id)
    log.info("clip %s rendered (%s, %.1fs)", clip.id, out_key, result.duration)
    return {"output_key": out_key, "duration": result.duration, "size_bytes": result.size_bytes,
            "width": result.width, "height": result.height, "render_hash": render_hash}


AUTO_RENDER_REASONS = ("auto", "video_framing", "publish")


def _rerender(db: Session, clip: GeneratedClip, reason: str) -> None:
    """The clip changed while it was rendering (e.g. the video framing was applied): render it again."""
    from app.modules.rendering.service import assert_framing_allowed, compute_render_hash
    from app.workers.jobs import create_job, dispatch_job

    try:
        assert_framing_allowed(clip)
        job, created = create_job(db, user_id=clip.user_id, job_type=JobType.RENDER,
                                  video_source_id=clip.video_source_id, clip_id=clip.id, params={"reason": reason},
                                  idempotency_key=f"render:{clip.id}:{compute_render_hash(clip)[:16]}")
        clip.render_status = RenderStatus.QUEUED
        if created:
            dispatch_job(db, job, "render_clip")
        else:
            db.commit()
    except Exception as exc:  # noqa: BLE001 - e.g. blocking framing warnings: the user reviews it
        db.rollback()
        log.info("clip %s not re-rendered automatically: %s", clip.id, exc)


def _auto_schedule(db: Session, source_id, *, exclude_job) -> None:  # noqa: ANN001
    """Auto-schedule: once no analysis/framing/render job of the video is left, its ready clips are scheduled
    in timeline order (the first right away, then one per gap)."""
    from app.modules.scheduling.timeline import auto_schedule_source
    from app.workers.jobs import ACTIVE_STATUSES

    active = db.scalar(select(ProcessingJob.id).where(
        ProcessingJob.video_source_id == source_id, ProcessingJob.id != exclude_job,
        ProcessingJob.job_type.in_((JobType.ANALYSIS, JobType.CLIP_VISION, JobType.RENDER)),
        ProcessingJob.status.in_(ACTIVE_STATUSES)).limit(1))
    if active is None:
        auto_schedule_source(db, source_id)


def _notify_source_ready(db: Session, source_id, *, exclude_job) -> None:  # noqa: ANN001
    """One "clips ready to upload" notification once every automatic clip job of the source finished."""
    from app.modules.notifications.service import notify
    from app.workers.jobs import ACTIVE_STATUSES

    active = db.scalar(select(ProcessingJob.id).where(
        ProcessingJob.video_source_id == source_id, ProcessingJob.id != exclude_job,
        ProcessingJob.job_type.in_((JobType.ANALYSIS, JobType.CLIP_VISION, JobType.RENDER)),
        ProcessingJob.status.in_(ACTIVE_STATUSES)).limit(1))
    if active is not None:
        return
    source = db.get(VideoSource, source_id)
    if source is None:
        return
    ready = [c for c in db.scalars(select(GeneratedClip).where(
        GeneratedClip.video_source_id == source_id, GeneratedClip.removed_at.is_(None),
        GeneratedClip.upload_status.in_(("none", "failed")), GeneratedClip.render_status == RenderStatus.RENDERED))]
    if not ready:
        return
    notify(db, source.user_id, NotificationType.CLIPS_READY, f"{len(ready)} clips ready to upload"[:200],
           f"The clips of “{source.title or 'your video'}” are generated. Review them and upload in order.",
           link=f"/app/videos/{source_id}", data={"video_source_id": str(source_id), "clips": len(ready)})
    db.commit()


def _render_failed(db: Session, job: ProcessingJob, exc: BaseException) -> None:
    from app.modules.notifications.service import notify
    from app.modules.scheduling.service import on_clip_render_finished

    clip = db.get(GeneratedClip, job.clip_id) if job.clip_id else None
    if clip is None:
        return
    # Only messages written for users are shown: raw exceptions may contain storage keys or paths.
    message = str(exc) if isinstance(exc, USER_FACING_ERRORS) and str(exc) else \
        "An unexpected error occurred while rendering. Please try again."
    clip.render_status, clip.render_error = RenderStatus.FAILED, message[:2000]
    notify(db, clip.user_id, NotificationType.RENDER_FAILED, "Render failed",
           f"“{clip.title or 'Your clip'}” could not be rendered: {message[:300]}",
           link=f"/app/clips/{clip.id}", data={"clip_id": str(clip.id), "job_id": str(job.id)})
    on_clip_render_finished(db, clip, False)


@task("render_clip", queue="render", job_type=JobType.RENDER)
def render_clip_task(job_id: str) -> None:
    """Final 1080x1920 export of a clip (framing + audio + bumpers; no subtitles)."""
    run_job(job_id, _render, task_name="render_clip", on_final_failure=_render_failed)
