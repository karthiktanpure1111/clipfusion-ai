"""Clip-level read helpers shared by the clips, framing and rendering services and the clip
workers (source media info, transcript words, audio silences, stored vision, job
serialisation). No FastAPI, no commits."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError
from app.models import GeneratedClip, ProcessingJob, Transcript, VideoAnalysis, VideoSource
from app.models.enums import RenderStatus
from app.schemas.framing import ClipVisionAnalysis, FramingWarning, blocking_warnings
from app.schemas.media import AudioAnalysis, MediaInfo, TranscriptData, TranscriptWord

log = logging.getLogger(__name__)

MIN_CLIP_SECONDS = 3.0
MAX_CLIP_SECONDS = 180.0
VISION_RANGE_TOLERANCE = 0.05  # seconds


# ------------------------------------------------------------------ source
def clip_source(db: Session, clip: GeneratedClip) -> VideoSource:
    """The clip's source video (always owned by the clip's user)."""
    source = db.get(VideoSource, clip.video_source_id)
    if source is None:
        raise NotFoundError("Source video not found")
    return source


def source_media(source: VideoSource) -> MediaInfo | None:
    """Probed media info of a source (``media_info`` JSON, else the scalar columns)."""
    info = dict(source.media_info or {})
    if info:
        try:
            media = MediaInfo.model_validate(info)
            if media.width > 0 and media.height > 0:
                return media
        except ValidationError:
            log.debug("source %s has invalid media_info", source.id)
    if source.width and source.height:
        return MediaInfo(duration=float(source.duration or 0.0), width=int(source.width), height=int(source.height),
                         fps=float(source.fps or 30.0), has_audio=bool(source.has_audio))
    return None


def require_media(source: VideoSource) -> MediaInfo:
    """Media info or a 409 ``source_not_ready`` (the source has not been probed yet)."""
    media = source_media(source)
    if media is None:
        raise ConflictError("The source video has not been analysed yet.", code="source_not_ready")
    return media


# --------------------------------------------------------------- transcript
def load_transcript(db: Session, source_id: uuid.UUID) -> TranscriptData | None:
    row = db.scalar(select(Transcript).where(Transcript.video_source_id == source_id))
    if row is None:
        return None
    try:
        return TranscriptData(language=row.language, model=row.model or "unknown", duration=row.duration,
                              segments=row.segments or [])
    except ValidationError:
        log.warning("transcript of source %s is not a valid TranscriptData", source_id)
        return None


def range_words(transcript: TranscriptData | None, start: float, end: float) -> list[TranscriptWord]:
    """Source-time transcript words overlapping ``[start, end)``."""
    if transcript is None:
        return []
    return [w for w in transcript.words() if w.end > start and w.start < end and w.word.strip()]


def range_text(words: list[TranscriptWord]) -> str:
    return " ".join(w.word.strip() for w in words).strip()


def clip_relative_words(words: list[TranscriptWord], start: float, end: float) -> list[dict[str, Any]]:
    """``[{text, start, end, probability}]`` clip-relative, clamped into the clip."""
    duration = max(0.0, end - start)
    out = []
    for w in sorted(words, key=lambda w: w.start):
        s = min(max(w.start - start, 0.0), duration)
        e = min(max(w.end - start, s), duration)
        out.append({"text": w.word.strip(), "start": round(s, 3), "end": round(e, 3),
                    "probability": round(float(w.probability), 4)})
    return out


# -------------------------------------------------------------------- audio
def load_audio(db: Session, source_id: uuid.UUID) -> AudioAnalysis | None:
    row = db.scalar(select(VideoAnalysis).where(VideoAnalysis.video_source_id == source_id))
    if row is None or not row.audio:
        return None
    try:
        return AudioAnalysis.model_validate(row.audio)
    except ValidationError:
        log.warning("audio analysis of source %s is invalid", source_id)
        return None


def silences_in(audio: AudioAnalysis | None, start: float, end: float) -> list[tuple[float, float]]:
    """Source-second silences overlapping ``[start, end]``, clipped to it."""
    if audio is None:
        return []
    out = []
    for a, b in sorted(audio.silences):
        a, b = max(a, start), min(b, end)
        if b > a:
            out.append((round(a, 3), round(b, 3)))
    return out


def speech_ranges(audio: AudioAnalysis | None, start: float, end: float,
                  has_audio: bool = True) -> list[tuple[float, float]] | None:
    """Source-second non-silent ranges inside ``[start, end]`` (complement of the silences).
    None when no audio analysis exists; ``[]`` for sources without audio."""
    if not has_audio:
        return []
    if audio is None:
        return None
    out, cur = [], start
    for a, b in silences_in(audio, start, end):
        if a > cur:
            out.append((round(cur, 3), round(a, 3)))
        cur = max(cur, b)
    if end > cur:
        out.append((round(cur, 3), round(end, 3)))
    return out


# --------------------------------------------------------- stored clip state
def vision_matches(clip: GeneratedClip, vision: ClipVisionAnalysis) -> bool:
    """True when ``vision`` was computed for the clip's current range (its times are relative
    to the analysed start, so an analysis of another range must never be used)."""
    return (abs(vision.clip_start - clip.start_time) <= VISION_RANGE_TOLERANCE
            and abs(vision.clip_end - clip.end_time) <= VISION_RANGE_TOLERANCE)


def stored_vision(clip: GeneratedClip) -> ClipVisionAnalysis | None:
    """The stored face analysis, or None when absent or computed before the clip was trimmed."""
    framing = clip.framing
    if framing is None or not framing.vision:
        return None
    try:
        vision = ClipVisionAnalysis.model_validate(framing.vision)
    except ValidationError:
        log.warning("clip %s has an invalid stored vision analysis", clip.id)
        return None
    return vision if vision_matches(clip, vision) else None


def stored_warnings(clip: GeneratedClip) -> list[FramingWarning]:
    framing = clip.framing
    if framing is None:
        return []
    out = []
    for w in framing.warnings or []:
        try:
            out.append(FramingWarning.model_validate(w))
        except ValidationError:
            continue
    return out


def blocking_keys(warnings: list[FramingWarning]) -> set[tuple[str, int | None]]:
    """Identity of the blocking warnings (used to decide when an acknowledgement resets)."""
    return {(w.code, w.track_id) for w in blocking_warnings(warnings)}


def mark_render_stale(clip: GeneratedClip) -> None:
    """An input of the render changed: a finished render no longer matches the clip."""
    if clip.render_status == RenderStatus.RENDERED:
        clip.render_status = RenderStatus.STALE


# --------------------------------------------------------------------- jobs
def job_out(db: Session, job: ProcessingJob) -> Any:
    """Serialise a job with the jobs module's ``JobOut`` (imported lazily)."""
    from app.modules.jobs.service import job_out as _job_out

    out = _job_out(db, job)
    return out.model_dump(mode="json") if hasattr(out, "model_dump") else out


def signed_url(key: str | None, *, download_name: str | None = None) -> str | None:
    """Short-lived signed URL for a storage key (None when absent or unavailable)."""
    if not key:
        return None
    from app.core.storage import get_storage

    try:
        return get_storage().url(key, download_name=download_name)
    except Exception:  # noqa: BLE001 - a broken object must not break listings
        log.warning("could not sign storage key %s", key)
        return None


def job_out_model() -> Any:
    """Response model for JobOut endpoints (the jobs module's model when available)."""
    try:
        from app.modules.jobs.schemas import JobOut
    except ImportError:
        return dict[str, Any]
    return JobOut
