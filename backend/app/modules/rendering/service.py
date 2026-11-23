"""Render requests, render freshness (input hash), downloads, server-side preview frames and
the :class:`RenderSpec` builder shared with the ``render_clip`` worker.

A render is refused (HTTP 409 ``framing_warnings_unacknowledged``) while blocking framing
warnings are unacknowledged — faces are never silently cropped (ARCHITECTURE.md section 6).
API v2: clips are exported clean -- subtitles are never burned in and do not affect the hash."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import unicodedata
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, object_session

from app.core.errors import ConflictError, ValidationFailed
from app.core.storage import get_storage, materialize, temp_workdir
from app.models import GeneratedClip, ProcessingJob, UserAsset, VideoSource
from app.models.enums import AssetKind, JobType, RenderStatus
from app.modules.clips.media import (
    clip_source,
    job_out,
    load_audio,
    require_media,
    signed_url,
    silences_in,
    source_media,
    stored_vision,
    stored_warnings,
)
from app.modules.framing.layout import compile_layout, default_framing_config
from app.modules.framing.protection import prepare_config
from app.modules.framing.service import clip_duration, load_config, refresh_warnings
from app.schemas.framing import FramingConfig, blocking_warnings
from app.schemas.media import MediaInfo
from app.schemas.render import RenderOptions, RenderSpec

log = logging.getLogger(__name__)

HASH_VERSION = 2  # v2: subtitles/fonts removed from the render inputs


def render_options(clip: GeneratedClip) -> RenderOptions:
    try:
        return RenderOptions.model_validate(clip.render_options or {})
    except ValueError:
        log.warning("clip %s has invalid render options; using defaults", clip.id)
        return RenderOptions()


def _asset_ids(options: RenderOptions, config: dict | None) -> list[str]:
    ids = [options.intro_asset_id, options.outro_asset_id,
           options.watermark.asset_id if options.watermark.enabled else None]
    if config and (config.get("background") or {}).get("type") == "image":
        ids.append(config["background"].get("image_asset_id"))
    return sorted(str(i) for i in ids if i)


def compute_render_hash(clip: GeneratedClip) -> str:
    """sha256 over every render input: clip range, framing config (incl. keyframes), render options
    and asset ids (clips are exported without subtitles)."""
    db = object_session(clip)
    config: dict | None = clip.framing.config if clip.framing is not None else None
    if config is None and db is not None:
        source = db.get(VideoSource, clip.video_source_id)
        media = source_media(source) if source is not None else None
        if media is not None:
            config = default_framing_config(media.width, media.height).model_dump(mode="json")
    options = render_options(clip)
    payload = {
        "v": HASH_VERSION, "start": round(float(clip.start_time), 3), "end": round(float(clip.end_time), 3),
        "framing": config, "options": options.model_dump(mode="json"), "assets": _asset_ids(options, config),
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def render_is_current(clip: GeneratedClip) -> bool:
    """True when a rendered file exists and was produced from the clip's current inputs."""
    if not clip.output_key or not clip.render_hash:
        return False
    return clip.render_hash == compute_render_hash(clip)


def active_render_job(db: Session, clip: GeneratedClip) -> ProcessingJob | None:
    from app.workers.jobs import ACTIVE_STATUSES

    return db.scalar(select(ProcessingJob).where(ProcessingJob.idempotency_key == f"render:{clip.id}",
                                                 ProcessingJob.status.in_(ACTIVE_STATUSES)))


def assert_framing_allowed(clip: GeneratedClip) -> None:
    """Refuse (409) while blocking framing warnings are unacknowledged. Checked when a render is
    requested AND again by the worker, because the framing may change while the job is queued."""
    blocking = blocking_warnings(stored_warnings(clip))
    if blocking and not clip.framing.warnings_acknowledged:
        raise ConflictError(
            "The framing hides or cuts faces or content. Fix the framing or acknowledge the warnings "
            "to render anyway.", code="framing_warnings_unacknowledged",
            details=[w.model_dump(mode="json") for w in blocking])


def request_render(db: Session, clip: GeneratedClip, *, acknowledge_warnings: bool = False,
                   reason: str = "user") -> ProcessingJob:
    """Queue a render of ``clip`` (or return the active one). Commits.

    Raises ConflictError ``framing_warnings_unacknowledged`` (details = blocking warnings)
    unless the warnings were acknowledged before or ``acknowledge_warnings`` is True."""
    from app.modules.storage.service import ensure_disk_space, estimated_render_bytes
    from app.workers.jobs import create_job, dispatch_job

    if clip.removed_at is not None:
        raise ConflictError("This clip was removed.", code="clip_removed")
    active = active_render_job(db, clip)
    if active is not None:
        return active
    source = clip_source(db, clip)
    if source.storage_key is None:
        if source.original_deleted_at is not None:
            raise ConflictError("The original video was deleted to free space; attach it again to render this "
                                "clip.", code="original_deleted")
        raise ValidationFailed("The source video file is not available.", code="source_missing")
    ensure_disk_space(estimated_render_bytes(clip.duration or 60.0), "render this clip")
    if clip.framing is not None:
        refresh_warnings(db, clip)
        if acknowledge_warnings:
            clip.framing.warnings_acknowledged = True
    assert_framing_allowed(clip)
    job, created = create_job(db, user_id=clip.user_id, job_type=JobType.RENDER, video_source_id=clip.video_source_id,
                              clip_id=clip.id, params={"reason": reason}, idempotency_key=f"render:{clip.id}")
    clip.render_status = RenderStatus.QUEUED
    clip.render_error = None
    log.info("render of clip %s requested (%s)", clip.id, reason)
    if created:
        dispatch_job(db, job, "render_clip")
    else:
        db.commit()
    return job


def latest_render_job(db: Session, clip: GeneratedClip) -> ProcessingJob | None:
    return db.scalar(select(ProcessingJob).where(ProcessingJob.clip_id == clip.id,
                                                 ProcessingJob.job_type == JobType.RENDER)
                     .order_by(ProcessingJob.created_at.desc()).limit(1))


def render_state(db: Session, clip: GeneratedClip) -> dict[str, Any]:
    job = latest_render_job(db, clip)
    return {"render_status": clip.render_status, "render_is_current": render_is_current(clip),
            "job": job_out(db, job) if job is not None else None, "output_url": signed_url(clip.output_key),
            "output_size": clip.output_size, "output_duration": clip.output_duration,
            "rendered_at": clip.rendered_at, "render_error": clip.render_error}


def slugify(text: str, max_len: int = 60) -> str:
    ascii_text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")[:max_len].strip("-")
    return slug or "clip"


def download_filename(clip: GeneratedClip) -> str:
    return f"{slugify(clip.title)}-{str(clip.id)[:8]}.mp4"


def download_info(clip: GeneratedClip) -> dict[str, str]:
    if not clip.output_key and clip.cleanup_status == "cleaned" and clip.youtube_url:
        raise ConflictError(f"This clip's file was deleted after its verified YouTube upload: {clip.youtube_url}",
                            code="media_deleted", details={"youtube_url": clip.youtube_url})
    if not clip.output_key and clip.removed_at is not None:
        raise ConflictError("This clip was removed.", code="clip_removed")
    if not clip.output_key:
        raise ConflictError("This clip has not been rendered yet.", code="not_rendered")
    filename = download_filename(clip)
    url = signed_url(clip.output_key, download_name=filename)
    if url is None:
        raise ConflictError("The rendered file is not available.", code="output_missing")
    return {"url": url, "filename": filename}


# ------------------------------------------------------------ spec builder
def local_copy(key: str, workdir: Path, name: str) -> Path:
    """Local path of a storage object (in place for local storage, else downloaded)."""
    storage = get_storage()
    local = storage.local_path(key)
    if local is not None and Path(local).is_file():
        return Path(local)
    dest = workdir / name
    storage.download(key, dest)
    return dest


def _asset_path(db: Session, user_id: uuid.UUID, asset_id: uuid.UUID | None, kind: str, workdir: Path) -> Path | None:
    if asset_id is None:
        return None
    asset = db.get(UserAsset, asset_id)
    if asset is None or asset.user_id != user_id or asset.kind != kind:
        raise ValidationFailed(f"The {kind} asset selected for this clip no longer exists.", code="asset_missing")
    return local_copy(asset.storage_key, workdir, f"{kind}-{asset.id}{Path(asset.storage_key).suffix}")


def resolve_assets(db: Session, user_id: uuid.UUID, options: RenderOptions, config: FramingConfig, workdir: Path,
                   *, include_bumpers: bool = True) -> dict[str, Path | None]:
    """Materialised intro/outro/watermark/background files referenced by the clip."""
    bg = config.background
    return {
        "intro_path": _asset_path(db, user_id, options.intro_asset_id, AssetKind.INTRO, workdir)
        if include_bumpers else None,
        "outro_path": _asset_path(db, user_id, options.outro_asset_id, AssetKind.OUTRO, workdir)
        if include_bumpers else None,
        "watermark_path": _asset_path(db, user_id, options.watermark.asset_id, AssetKind.WATERMARK, workdir)
        if options.watermark.enabled else None,
        "background_image_path": _asset_path(db, user_id, bg.image_asset_id, AssetKind.BACKGROUND, workdir)
        if bg.type == "image" else None,
    }


def build_render_spec(db: Session, clip: GeneratedClip, *, source_path: Path, media: MediaInfo, workdir: Path,
                      output_path: Path, framing: FramingConfig | None = None,
                      include_bumpers: bool = True) -> RenderSpec:
    """RenderSpec of the clip's stored (or the given) framing. Subtitles are always disabled
    (clips are exported clean)."""
    vision = stored_vision(clip)
    duration = clip_duration(clip)
    config = prepare_config(framing or load_config(clip.framing, media), vision, media.width, media.height, duration)
    options = render_options(clip)
    return RenderSpec(
        source_path=source_path, start=clip.start_time, end=clip.end_time, media=media,
        layout=compile_layout(config, media.width, media.height), subtitles_enabled=False, captions=[],
        face_tracks=vision.tracks if vision else [], options=options,
        silences=silences_in(load_audio(db, clip.video_source_id), clip.start_time, clip.end_time),
        **resolve_assets(db, clip.user_id, options, config, workdir, include_bumpers=include_bumpers),
        output_path=output_path, thumbnail_path=output_path.with_suffix(".jpg"))


def preview_frame(db: Session, clip: GeneratedClip, t: float, *, framing: FramingConfig | None = None) -> bytes:
    """JPEG of clip time ``t`` composed by the real export renderer (exact WYSIWYG check, no subtitles)."""
    from app.modules.rendering.renderer import render_preview_frame

    source = clip_source(db, clip)
    media = require_media(source)
    if source.storage_key is None:
        raise ConflictError("The source video file is not available.", code="source_missing")
    with temp_workdir("cf-preview-") as wd, materialize(source.storage_key) as src:
        spec = build_render_spec(db, clip, source_path=src, media=media, workdir=wd, output_path=wd / "unused.mp4",
                                 framing=framing, include_bumpers=False)
        data = render_preview_frame(spec, t, wd / "frame.jpg").read_bytes()
    return data
