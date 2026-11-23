"""Clips service: listing/serialisation (ClipOut/ClipDetail with sequence + derived status),
trimming and editing, Remove Clip (soft delete), bulk actions and metadata regeneration.

API v2: approve/reject and subtitles are gone. A clip's ``sequence`` is its 1-based position by
source start time among the source's non-removed clips (always chronological, independent of
the order in which clips finished processing)."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.core.database import utcnow
from app.core.deps import get_owned
from app.core.errors import AppError, ConflictError, ValidationFailed
from app.models import (
    GeneratedClip,
    ProcessingJob,
    PublishingQueueItem,
    UploadRunItem,
    User,
    UserAsset,
    VideoSource,
)
from app.models.enums import (
    AssetKind,
    CleanupStatus,
    ClipStatus,
    JobStatus,
    JobType,
    QueueStatus,
    RenderStatus,
    UploadRunItemStatus,
    UploadStatus,
)
from app.modules.clips.media import (
    MAX_CLIP_SECONDS,
    MIN_CLIP_SECONDS,
    clip_source,
    job_out,
    load_transcript,
    mark_render_stale,
    range_text,
    range_words,
    signed_url,
    source_media,
    stored_warnings,
)
from app.modules.clips.schemas import ClipDetail, ClipOut, ClipPatch
from app.modules.common.pagination import Page, PageParams, paginate
from app.modules.framing import service as framing_service
from app.modules.rendering.service import render_is_current, render_options, request_render
from app.schemas.framing import blocking_warnings
from app.schemas.media import ClipMetadata
from app.schemas.render import RenderOptions

log = logging.getLogger(__name__)

SORTS = {
    "score": (GeneratedClip.highlight_score.desc(), GeneratedClip.created_at.desc()),
    "created": (GeneratedClip.created_at.desc(), GeneratedClip.start_time.asc()),
    "start": (GeneratedClip.start_time.asc(), GeneratedClip.end_time.asc()),
    "sequence": (GeneratedClip.start_time.asc(), GeneratedClip.end_time.asc(), GeneratedClip.id.asc()),
}
CLIP_WORK_JOBS = (JobType.CLIP_VISION, JobType.RENDER, JobType.TRANSCRIBE_CLIP)
CANCEL_ON_REMOVE = (QueueStatus.PENDING_APPROVAL, QueueStatus.SCHEDULED, QueueStatus.RENDERING)
UPLOADING_QUEUE = (QueueStatus.PUBLISHING,)


# ------------------------------------------------------------ status/sequence
def needs_framing_review(clip: GeneratedClip, current: bool | None = None) -> bool:
    """Blocking framing warnings that were not acknowledged stop the automatic render."""
    if clip.removed_at is not None or clip.framing is None or clip.framing.warnings_acknowledged:
        return False
    if current if current is not None else render_is_current(clip):
        return False
    return bool(blocking_warnings(stored_warnings(clip)))


def clip_status(clip: GeneratedClip, *, busy: bool, current: bool | None = None) -> str:
    """Derived lifecycle status (``ClipOut.status``)."""
    if clip.removed_at is not None:
        return ClipStatus.REMOVED
    if clip.cleanup_status == CleanupStatus.CLEANED:
        return ClipStatus.CLEANED
    if clip.upload_status == UploadStatus.UPLOADING:
        return ClipStatus.UPLOADING
    if clip.upload_status == UploadStatus.UPLOADED:
        return ClipStatus.UPLOADED
    if clip.upload_status == UploadStatus.FAILED:
        return ClipStatus.UPLOAD_FAILED
    current = render_is_current(clip) if current is None else current
    if current:
        return ClipStatus.GENERATED
    if busy or clip.render_status in (RenderStatus.QUEUED, RenderStatus.RENDERING):
        return ClipStatus.PROCESSING
    if needs_framing_review(clip, current):
        return ClipStatus.PROCESSING
    return ClipStatus.PENDING


def busy_clip_ids(db: Session, clip_ids: list[uuid.UUID]) -> set[uuid.UUID]:
    """Clips with an active face-tracking/render job."""
    from app.workers.jobs import ACTIVE_STATUSES

    if not clip_ids:
        return set()
    return set(db.scalars(select(ProcessingJob.clip_id).where(
        ProcessingJob.clip_id.in_(clip_ids), ProcessingJob.job_type.in_(CLIP_WORK_JOBS),
        ProcessingJob.status.in_(ACTIVE_STATUSES))))


def derive_statuses(db: Session, clips: list[GeneratedClip]) -> dict[uuid.UUID, str]:
    busy = busy_clip_ids(db, [c.id for c in clips if c.removed_at is None])
    return {c.id: str(clip_status(c, busy=c.id in busy)) for c in clips}


def sequences(db: Session, source_ids: set[uuid.UUID]) -> dict[uuid.UUID, int]:
    """``clip_id -> sequence`` for every non-removed clip of the given sources."""
    if not source_ids:
        return {}
    out: dict[uuid.UUID, int] = {}
    counters: dict[uuid.UUID, int] = {}
    rows = db.execute(select(GeneratedClip.id, GeneratedClip.video_source_id).where(
        GeneratedClip.video_source_id.in_(source_ids), GeneratedClip.removed_at.is_(None))
        .order_by(GeneratedClip.video_source_id, *SORTS["sequence"])).all()
    for cid, sid in rows:
        counters[sid] = counters.get(sid, 0) + 1
        out[cid] = counters[sid]
    return out


# ------------------------------------------------------------ serialisation
def _breakdown(raw: Any) -> dict[str, float]:
    out = {}
    for k, v in (raw or {}).items():
        try:
            out[str(k)] = float(v)
        except (TypeError, ValueError):
            continue
    return out


def _thumbnail(clip: GeneratedClip) -> str | None:
    from app.modules.storage.cleanup import youtube_thumbnail

    url = signed_url(clip.thumbnail_key)
    if url is None and (clip.cleanup_status == CleanupStatus.CLEANED or clip.removed_at is not None):
        return youtube_thumbnail(clip.youtube_video_id)
    return url


def _metadata(clip: GeneratedClip) -> ClipMetadata:
    try:
        return ClipMetadata.model_validate(clip.social_metadata or {})
    except ValueError:
        return ClipMetadata()


def _clip_out(clip: GeneratedClip, source_title: str, sequence: int | None, busy: bool,
              scheduled: Any = None) -> ClipOut:
    warnings = stored_warnings(clip)
    framing = clip.framing
    current = render_is_current(clip)
    media = bool(clip.output_key or clip.proxy_key or clip.thumbnail_key) and clip.removed_at is None \
        and clip.cleanup_status != CleanupStatus.CLEANED
    return ClipOut(
        id=clip.id, video_source_id=clip.video_source_id, source_title=source_title, sequence=sequence,
        status=clip_status(clip, busy=busy, current=current), start_time=clip.start_time, end_time=clip.end_time,
        duration=clip.duration, target_duration=clip.target_duration, highlight_score=clip.highlight_score,
        score_breakdown=_breakdown(clip.score_breakdown), category=clip.category,
        reasons=[str(r) for r in (clip.reasons or [])], transcript_text=clip.transcript_text,
        hook_text=clip.hook_text, detection_method=clip.detection_method, retention_score=clip.retention_score,
        title=clip.title, social_metadata=_metadata(clip), render_status=clip.render_status,
        render_is_current=current,
        needs_framing_review=needs_framing_review(clip, current), thumbnail_url=_thumbnail(clip),
        proxy_url=signed_url(clip.proxy_key), proxy_start=clip.proxy_start, output_url=signed_url(clip.output_key),
        output_duration=clip.output_duration, rendered_at=clip.rendered_at,
        framing_mode=framing.mode if framing is not None else None, warning_count=len(warnings),
        blocking_warning_count=len(blocking_warnings(warnings)),
        warnings_acknowledged=bool(framing.warnings_acknowledged) if framing is not None else False,
        upload_status=clip.upload_status or "none", youtube_video_id=clip.youtube_video_id,
        scheduled_at=scheduled.scheduled_at if scheduled is not None else None,
        scheduled_item_id=scheduled.id if scheduled is not None else None,
        youtube_url=clip.youtube_url, uploaded_at=clip.uploaded_at, upload_error=clip.upload_error,
        cleanup_status=clip.cleanup_status or "none", media_available=media,
        media_bytes=int(clip.media_bytes or 0) if media else 0, freed_bytes=int(clip.freed_bytes or 0),
        removed_at=clip.removed_at, created_at=clip.created_at, updated_at=clip.updated_at)


def clip_outs(db: Session, clips: list[GeneratedClip]) -> list[ClipOut]:
    """Serialise clips (batched source titles, sequences and active jobs)."""
    ids = {c.video_source_id for c in clips}
    titles = dict(db.execute(select(VideoSource.id, VideoSource.title).where(VideoSource.id.in_(ids))).all()) \
        if ids else {}
    seq = sequences(db, ids)
    busy = busy_clip_ids(db, [c.id for c in clips if c.removed_at is None])
    from app.modules.scheduling.timeline import scheduled_clip_ids

    scheduled = scheduled_clip_ids(db, [c.id for c in clips if c.removed_at is None])
    return [_clip_out(c, titles.get(c.video_source_id, ""), seq.get(c.id), c.id in busy, scheduled.get(c.id))
            for c in clips]


def clip_out(db: Session, clip: GeneratedClip) -> ClipOut:
    return clip_outs(db, [clip])[0]


def clip_detail(db: Session, clip: GeneratedClip) -> ClipDetail:
    """ClipDetail = ClipOut + metadata, render options, active jobs, publishing history and a source summary."""
    from app.workers.jobs import ACTIVE_STATUSES

    source = clip_source(db, clip)
    base = clip_out(db, clip)
    jobs = db.scalars(select(ProcessingJob).where(ProcessingJob.clip_id == clip.id,
                                                  ProcessingJob.status.in_(ACTIVE_STATUSES))
                      .order_by(ProcessingJob.created_at))
    items = db.scalars(select(PublishingQueueItem).where(PublishingQueueItem.clip_id == clip.id)
                       .order_by(PublishingQueueItem.scheduled_at, PublishingQueueItem.created_at))
    media = source_media(source)
    return ClipDetail(
        **base.model_dump(), render_options=render_options(clip),
        render_error=clip.render_error, active_jobs=[job_out(db, j) for j in jobs],
        queue_items=[{"id": i.id, "platform": i.platform, "status": i.status, "scheduled_at": i.scheduled_at,
                      "published_at": i.published_at, "external_url": i.external_url, "last_error": i.last_error}
                     for i in items],
        source={"id": source.id, "title": source.title,
                "duration": source.duration or (media.duration if media else None),
                "width": source.width or (media.width if media else None),
                "height": source.height or (media.height if media else None),
                "fps": source.fps or (media.fps if media else None), "attribution": source.attribution,
                "original_available": bool(source.storage_key)})


# -------------------------------------------------------------------- list
def list_clips(db: Session, user: User, params: PageParams, *, video_source_id: uuid.UUID | None = None,
               status: str | None = None, render_status: str | None = None, min_score: float | None = None,
               q: str | None = None, sort: str | None = None, include_removed: bool = False) -> Page[ClipOut]:
    """``status`` (comma-separated derived statuses) is filtered after derivation; ``sort`` defaults to
    ``sequence`` (source timestamp order) when ``video_source_id`` is given, else ``created``."""
    wanted = {s.strip() for s in (status or "").split(",") if s.strip()}
    sort = sort or ("sequence" if video_source_id is not None else "created")
    stmt = select(GeneratedClip).where(GeneratedClip.user_id == user.id)
    if video_source_id is not None:
        stmt = stmt.where(GeneratedClip.video_source_id == video_source_id)
    if not include_removed and ClipStatus.REMOVED not in wanted:
        stmt = stmt.where(GeneratedClip.removed_at.is_(None))
    if render_status:
        stmt = stmt.where(GeneratedClip.render_status.in_(render_status.split(",")))
    if min_score is not None:
        stmt = stmt.where(GeneratedClip.highlight_score >= min_score)
    if q and q.strip():
        needle = q.strip().lower()
        stmt = stmt.where(or_(func.lower(GeneratedClip.title).contains(needle, autoescape=True),
                              func.lower(GeneratedClip.transcript_text).contains(needle, autoescape=True)))
    if sort == "sequence" and video_source_id is None:
        stmt = stmt.join(VideoSource, VideoSource.id == GeneratedClip.video_source_id).order_by(
            VideoSource.created_at.desc(), GeneratedClip.video_source_id, *SORTS["sequence"])
    else:
        stmt = stmt.order_by(*SORTS.get(sort, SORTS["created"]))
    stmt = stmt.options(selectinload(GeneratedClip.framing))
    if wanted:
        rows = list(db.scalars(stmt))
        statuses = derive_statuses(db, rows)
        rows = [c for c in rows if statuses[c.id] in wanted]
        total = len(rows)
        rows = rows[params.offset: params.offset + params.limit]
    else:
        rows, total = paginate(db, stmt, params)
    return Page[ClipOut](items=clip_outs(db, rows), total=total, limit=params.limit, offset=params.offset)


# ------------------------------------------------------------------- edits
def _validate_assets(db: Session, user: User, options: RenderOptions) -> None:
    refs = [(options.intro_asset_id, AssetKind.INTRO), (options.outro_asset_id, AssetKind.OUTRO),
            (options.watermark.asset_id, AssetKind.WATERMARK)]
    for asset_id, kind in refs:
        if asset_id is None:
            continue
        asset = db.get(UserAsset, asset_id)
        if asset is None or asset.user_id != user.id or asset.kind != kind:
            raise ValidationFailed(f"The selected {kind} asset does not exist.", code="asset_not_found")


def _validate_range(db: Session, clip: GeneratedClip, source: VideoSource, start: float, end: float) -> None:
    media = source_media(source)
    limit = source.duration or (media.duration if media else None)
    if start < 0 or (limit and end > limit + 1e-3):
        raise ValidationFailed(f"The clip must lie inside the source video (0 - {limit or 0:.2f} s).",
                               code="clip_out_of_range")
    length = end - start
    if not MIN_CLIP_SECONDS <= length <= MAX_CLIP_SECONDS:
        raise ValidationFailed(f"Clips must be {MIN_CLIP_SECONDS:.0f}-{MAX_CLIP_SECONDS:.0f} seconds long.",
                               code="invalid_clip_length")
    dup = db.scalar(select(GeneratedClip.id).where(GeneratedClip.video_source_id == clip.video_source_id,
                                                   GeneratedClip.start_time == start, GeneratedClip.end_time == end,
                                                   GeneratedClip.id != clip.id))
    if dup is not None:
        raise ConflictError("Another clip already covers exactly this range.", code="clip_exists")


def require_not_removed(clip: GeneratedClip) -> None:
    if clip.removed_at is not None:
        raise ConflictError("This clip was removed.", code="clip_removed")


def update_clip(db: Session, user: User, clip: GeneratedClip, body: ClipPatch) -> ClipDetail:
    """PATCH: trim (framing re-analysed), title, metadata, render options."""
    require_not_removed(clip)
    fields = body.model_fields_set
    source = clip_source(db, clip)
    old_start = clip.start_time
    range_changed = False
    if ("start_time" in fields and body.start_time is not None) or ("end_time" in fields and body.end_time is not None):
        start = round(body.start_time if body.start_time is not None else clip.start_time, 3)
        end = round(body.end_time if body.end_time is not None else clip.end_time, 3)
        if (start, end) != (round(clip.start_time, 3), round(clip.end_time, 3)):
            if clip.upload_status in (UploadStatus.UPLOADING, UploadStatus.UPLOADED):
                raise ConflictError("This clip was already uploaded; its range can no longer change.",
                                    code="clip_uploaded")
            _validate_range(db, clip, source, start, end)
            clip.start_time, clip.end_time, clip.duration = start, end, round(end - start, 3)
            range_changed = True
    if "title" in fields and body.title is not None:
        clip.title = body.title.strip()
    if "social_metadata" in fields and body.social_metadata is not None:
        meta = body.social_metadata.model_copy(update={"source": "user", "generated_at": utcnow().isoformat()})
        clip.social_metadata = meta.model_dump(mode="json")
    if "render_options" in fields and body.render_options is not None:
        _validate_assets(db, user, body.render_options)
        dump = body.render_options.model_dump(mode="json")
        if dump != render_options(clip).model_dump(mode="json"):
            mark_render_stale(clip)
        clip.render_options = dump
    if range_changed:
        transcript = load_transcript(db, clip.video_source_id)
        if transcript is not None:
            clip.transcript_text = range_text(range_words(transcript, clip.start_time, clip.end_time))
        framing_service.adapt_to_range(db, clip, old_start)
        mark_render_stale(clip)
        db.commit()
        if source.storage_key is not None:
            framing_service.request_analysis(db, clip)  # proxy, thumbnail, vision, keyframes, warnings for the new range
    else:
        db.commit()
    log.info("clip %s updated (%s)", clip.id, ", ".join(sorted(fields)))
    return clip_detail(db, clip)


# ------------------------------------------------------------- Remove Clip
def remove_clip(db: Session, user: User, clip: GeneratedClip) -> dict[str, Any]:
    """Remove Clip: delete only this clip's media (MP4, proxy, thumbnail, temp), cancel its pending
    publications and jobs, keep a small record (title, timestamps, upload history). Refused (409
    ``clip_uploading``) while it is uploading. Never touches the original or other clips. Commits."""
    from app.modules.publishing import history
    from app.modules.storage.service import delete_clip_media, maybe_auto_delete_original
    from app.workers.jobs import ACTIVE_STATUSES

    if clip.removed_at is not None:
        return {"id": clip.id, "status": "removed", "freed_bytes": int(clip.freed_bytes or 0)}
    transferring = db.scalar(select(PublishingQueueItem.id).where(
        PublishingQueueItem.clip_id == clip.id, PublishingQueueItem.status.in_(UPLOADING_QUEUE)).limit(1))
    if clip.upload_status == UploadStatus.UPLOADING or transferring is not None:
        raise ConflictError("This clip is being uploaded right now; remove it after the upload has finished.",
                            code="clip_uploading")
    now = utcnow()
    for item in db.scalars(select(PublishingQueueItem).where(PublishingQueueItem.clip_id == clip.id,
                                                             PublishingQueueItem.status.in_(CANCEL_ON_REMOVE))):
        item.status, item.locked_at, item.locked_by = QueueStatus.CANCELLED, None, None
        history.record(db, item, "cancel", QueueStatus.CANCELLED, "Cancelled: the clip was removed")
    for job in db.scalars(select(ProcessingJob).where(ProcessingJob.clip_id == clip.id,
                                                      ProcessingJob.status.in_(ACTIVE_STATUSES))):
        job.status, job.finished_at, job.idempotency_key = JobStatus.CANCELLED, now, None
        job.message = "Cancelled: the clip was removed"
    for run_item in db.scalars(select(UploadRunItem).where(UploadRunItem.clip_id == clip.id,
                                                           UploadRunItem.status == UploadRunItemStatus.PENDING)):
        run_item.status, run_item.error = UploadRunItemStatus.CANCELLED, "The clip was removed."
    if clip.render_status in (RenderStatus.QUEUED, RenderStatus.RENDERING):
        clip.render_status = RenderStatus.NOT_RENDERED
    clip.removed_at = now
    db.flush()
    try:
        freed = delete_clip_media(db, clip)
    except Exception as exc:  # noqa: BLE001 - the record is removed; maintenance retries the deletion
        log.warning("Removing the files of clip %s failed: %s", clip.id, exc)
        freed = 0
        clip.cleanup_status = CleanupStatus.FAILED
        clip.cleanup_error = f"Deleting the clip's files failed: {exc}"[:2000]
    clip.freed_bytes = int(clip.freed_bytes or 0) + int(freed)
    db.commit()
    log.info("clip %s removed (%d bytes freed)", clip.id, freed)
    maybe_auto_delete_original(db, db.get(VideoSource, clip.video_source_id))
    return {"id": clip.id, "status": "removed", "freed_bytes": int(freed)}


def bulk(db: Session, user: User, ids: list[uuid.UUID], action: str, *,
         acknowledge_warnings: bool = False) -> list[dict[str, Any]]:
    """Apply ``action`` (render | remove) to each clip; per-clip results (errors never abort the batch)."""
    results = []
    for cid in dict.fromkeys(ids):
        try:
            clip = get_owned(db, GeneratedClip, cid, user, name="Clip")
            if action == "render":
                request_render(db, clip, acknowledge_warnings=acknowledge_warnings)
            elif action == "remove":
                remove_clip(db, user, clip)
            else:
                raise ValidationFailed(f"Unknown action '{action}'")
            results.append({"id": cid, "ok": True, "error": None})
        except AppError as exc:
            db.rollback()
            results.append({"id": cid, "ok": False, "error": {"code": exc.code, "message": exc.message}})
        except Exception:  # noqa: BLE001 - report and continue with the next clip
            db.rollback()
            log.exception("bulk %s failed for clip %s", action, cid)
            results.append({"id": cid, "ok": False,
                            "error": {"code": "internal_error", "message": "An unexpected error occurred"}})
    return results


def regenerate_metadata(db: Session, user: User, clip: GeneratedClip, use_llm: bool | None) -> ProcessingJob:
    """Start (or return the active) ``generate_metadata`` job; commits."""
    from app.workers.jobs import create_job, dispatch_job

    require_not_removed(clip)
    if use_llm is None:
        use_llm = bool((user.settings or {}).get("default_use_llm", True))
    job, created = create_job(db, user_id=user.id, job_type=JobType.METADATA, video_source_id=clip.video_source_id,
                              clip_id=clip.id, params={"use_llm": use_llm}, idempotency_key=f"metadata:{clip.id}")
    if created:
        dispatch_job(db, job, "generate_metadata")
    else:
        db.commit()
    return job
