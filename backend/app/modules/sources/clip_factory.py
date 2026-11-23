"""Turn highlight candidates into GeneratedClip rows (+ social metadata + clip_vision jobs).

Shared by the ``analyze_source`` task and the manual-clip endpoint. Clips of a source that
were removed (Remove Clip) keep their row, so re-analysis never re-creates them: a candidate
overlapping any existing clip -- removed or not -- is skipped.
"""

from __future__ import annotations

import inspect
import logging
import uuid
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import GeneratedClip, Transcript, VideoSource
from app.models.enums import JobType, RenderStatus, ReviewStatus
from app.modules.clipping.boundaries import temporal_iou
from app.schemas.media import ClipMetadata, HighlightCandidate, TranscriptData
from app.schemas.render import RenderOptions
from app.workers.jobs import create_job, dispatch_job

log = logging.getLogger(__name__)

MAX_EXISTING_IOU = 0.6
CLIP_VISION_TASK = "clip_vision"
Progress = Callable[[float, str | None], None]


def range_text(transcript: TranscriptData | None, start: float, end: float) -> str:
    """Transcript words whose midpoint lies inside ``[start, end]`` (source seconds)."""
    if transcript is None:
        return ""
    words = [w.word for w in transcript.words() if start <= (w.start + w.end) / 2 <= end]
    if words:
        return "".join(w if w.startswith(" ") else f" {w}" for w in words).strip()
    return " ".join(s.text.strip() for s in transcript.segments if s.end > start and s.start < end).strip()


def load_transcript(db: Session, source: VideoSource) -> TranscriptData | None:
    row = db.scalar(select(Transcript).where(Transcript.video_source_id == source.id))
    if row is None:
        return None
    return TranscriptData(language=row.language, model=row.model, duration=row.duration, segments=row.segments)


def sibling_titles(db: Session, source_id: uuid.UUID, *, exclude: uuid.UUID | None = None) -> list[str]:
    """Titles of the source's other clips (metadata avoids repeating them)."""
    stmt = select(GeneratedClip.title).where(GeneratedClip.video_source_id == source_id)
    if exclude is not None:
        stmt = stmt.where(GeneratedClip.id != exclude)
    return [t for t in db.scalars(stmt) if t]


def _supported_kwargs(fn: Callable, candidates: dict) -> dict:
    """Only the keyword arguments ``fn`` accepts (the metadata engine gains parameters over time)."""
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return {}
    if any(p.kind == p.VAR_KEYWORD for p in params.values()):
        return candidates
    return {k: v for k, v in candidates.items() if k in params}


def generate_metadata(*, transcript_text: str, source: VideoSource | None, category: str | None, hook: str | None,
                      use_llm: bool, avoid_titles: list[str] | None = None, strict: bool = False) -> ClipMetadata:
    """Titles/captions/descriptions/hashtags for one clip. The source's credit line
    (``attribution``) and the sibling clips' titles are passed when the generator supports them.
    ``strict`` re-raises generator errors (the metadata job reports them); otherwise empty metadata
    is returned so clip creation never fails because of metadata."""
    from app.modules.metadata.generator import generate_clip_metadata

    optional = _supported_kwargs(generate_clip_metadata, {
        "attribution": (source.attribution if source is not None else None) or None,
        "avoid_titles": list(avoid_titles or [])})
    try:
        return generate_clip_metadata(transcript_text=transcript_text,
                                      source_title=(source.title if source is not None else None) or None,
                                      category=category, hook=hook, use_llm=use_llm, **optional)
    except Exception as exc:  # noqa: BLE001 - metadata must not block clip creation
        if strict:
            raise
        log.warning("Metadata generation failed for a clip of source %s: %s", getattr(source, "id", None), exc)
        return ClipMetadata()


def _title(meta: ClipMetadata, cand: HighlightCandidate, index: int) -> str:
    title = (meta.youtube.title or cand.hook or "").strip()
    return (title or f"Clip {index + 1}")[:300]


def _mode_render_options(source: VideoSource) -> dict:
    """Render settings of the video's processing mode (fast/balanced/best); users can still change them."""
    from app.modules.analysis.modes import get_profile

    profile = get_profile(source.processing_mode)
    return RenderOptions(video_preset=profile.render_preset, video_crf=profile.render_crf).model_dump(mode="json")


def create_clips(db: Session, source: VideoSource, candidates: list[HighlightCandidate], *,
                 use_llm: bool, progress: Progress | None = None) -> list[GeneratedClip]:
    """Create clips for ``candidates`` and dispatch one ``clip_vision`` job per new clip (each
    followed by an automatic render).

    Candidates overlapping an existing (removed or not) or just created clip of the source with
    temporal IoU > 0.6 are skipped. ``progress(fraction, message)`` reports metadata generation.
    Commits (job dispatch commits)."""
    rows = db.execute(select(GeneratedClip.start_time, GeneratedClip.end_time, GeneratedClip.title)
                      .where(GeneratedClip.video_source_id == source.id)).tuples().all()
    existing = [(s, e) for s, e, _t in rows]
    titles = [t for _s, _e, t in rows if t]
    transcript: TranscriptData | None = None
    transcript_loaded = False
    created: list[GeneratedClip] = []
    total = max(len(candidates), 1)
    for i, cand in enumerate(candidates):
        if progress is not None:
            progress(i / total, f"Writing titles and descriptions ({i + 1}/{len(candidates)})")
        start, end = round(float(cand.start), 3), round(float(cand.end), 3)
        if end <= start:
            continue
        if any(temporal_iou(start, end, s, e) > MAX_EXISTING_IOU for s, e in existing):
            log.info("Skipping candidate %.2f-%.2f of source %s: overlaps an existing clip", start, end, source.id)
            continue
        text = cand.transcript.strip()
        if not text:
            if not transcript_loaded:
                transcript, transcript_loaded = load_transcript(db, source), True
            text = range_text(transcript, start, end)
        meta = generate_metadata(transcript_text=text, source=source, category=cand.category, hook=cand.hook,
                                 use_llm=use_llm, avoid_titles=titles)
        clip = GeneratedClip(
            user_id=source.user_id, video_source_id=source.id, start_time=start, end_time=end,
            duration=round(end - start, 3), target_duration=cand.target_duration,
            highlight_score=float(cand.score), score_breakdown=dict(cand.breakdown), category=cand.category,
            reasons=list(cand.reasons), transcript_text=text, hook_text=cand.hook or meta.youtube.hook or None,
            detection_method=cand.method, retention_score=cand.retention_score,
            title=_title(meta, cand, len(existing)), social_metadata=meta.model_dump(mode="json"),
            render_options=_mode_render_options(source), review_status=ReviewStatus.PENDING,
            render_status=RenderStatus.NOT_RENDERED, upload_status="none", cleanup_status="none", freed_bytes=0)
        db.add(clip)
        db.flush()
        existing.append((start, end))
        titles.append(clip.title)
        created.append(clip)
    if progress is not None:
        progress(1.0, f"{len(created)} clips created")
    for clip in created:
        job, is_new = create_job(db, user_id=source.user_id, job_type=JobType.CLIP_VISION,
                                 video_source_id=source.id, clip_id=clip.id, params={"auto_render": True},
                                 idempotency_key=f"clip_vision:{clip.id}", stage="queued")
        if is_new:
            dispatch_job(db, job, CLIP_VISION_TASK)
    db.commit()
    log.info("Created %d clips for source %s (%d candidates)", len(created), source.id, len(candidates))
    return created
