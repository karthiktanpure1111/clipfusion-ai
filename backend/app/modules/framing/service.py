"""Framing editor service: per-clip framing state, dry-run checks, AI suggestions, saving with
crop-protection warnings, bulk apply and saved framing presets.

Honesty rules: warnings are always recomputed from the real vision analysis whenever the
framing or the captions change, and an acknowledgement is reset when the set of blocking
warnings changes (a render is refused while blocking warnings are unacknowledged)."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import get_owned
from app.core.errors import AppError, ConflictError, ValidationFailed
from app.models import ClipFramingSettings, FramingPreset, GeneratedClip, ProcessingJob, User
from app.models.enums import JobType, NotificationType
from app.modules.clips.media import (
    blocking_keys,
    clip_source,
    mark_render_stale,
    require_media,
    signed_url,
    source_media,
    stored_vision,
    stored_warnings,
)
from app.modules.framing.geometry import interpolate_keyframes
from app.modules.framing.layout import (
    aspect_rect,
    compile_layout,
    default_framing_config,
    make_rect,
    panel_aspects,
)
from app.modules.framing.protection import analyze_protection, prepare_config
from app.modules.framing.suggest import suggest_framing
from app.schemas.framing import (
    ClipVisionAnalysis,
    CompiledLayout,
    FramingConfig,
    FramingWarning,
    Keyframe,
    Rect,
)
from app.schemas.media import MediaInfo

log = logging.getLogger(__name__)

_UNSET: Any = object()
SETTING_DEFAULT_PRESET = "default_framing_preset_id"


def clip_duration(clip: GeneratedClip) -> float:
    return max(float(clip.end_time) - float(clip.start_time), 1e-3)


def load_config(framing: ClipFramingSettings | None, media: MediaInfo) -> FramingConfig:
    """Stored config, or the default full_frame config when none is stored yet."""
    if framing is not None and framing.config:
        try:
            return FramingConfig.model_validate(framing.config)
        except ValueError:
            log.warning("clip framing %s has an invalid config; using full_frame", framing.id)
    return default_framing_config(media.width, media.height, "full_frame")


def evaluate(clip: GeneratedClip, config: FramingConfig, vision: ClipVisionAnalysis | None, media: MediaInfo,
             ) -> tuple[FramingConfig, list[FramingWarning], CompiledLayout]:
    """Prepared config (smart keyframes filled), protection warnings and the compiled layout.
    Clips are exported without subtitles (API v2), so no caption overlap is checked."""
    duration = clip_duration(clip)
    config = prepare_config(config, vision, media.width, media.height, duration)
    warnings = analyze_protection(config, vision, source_w=media.width, source_h=media.height, duration=duration,
                                  subtitle_style=None, captions=None)
    return config, warnings, compile_layout(config, media.width, media.height)


def with_fresh_keyframes(config: FramingConfig, previous: FramingConfig | None = None) -> FramingConfig:
    """Smart keyframes are always recomputed server-side; a changed manual crop whose keyframes
    were left untouched drops them (``FramingSuggestion.config_patch`` semantics)."""
    update: dict[str, Any] = {}
    if config.smart.keyframes:
        update["smart"] = config.smart.model_copy(update={"keyframes": []})
    if previous is not None and config.manual.keyframes and config.manual.crop != previous.manual.crop \
            and config.manual.keyframes == previous.manual.keyframes:
        update["manual"] = config.manual.model_copy(update={"keyframes": []})
    return config.model_copy(update=update) if update else config


def strip_keyframes(config: FramingConfig) -> FramingConfig:
    return config.model_copy(update={"smart": config.smart.model_copy(update={"keyframes": []}),
                                     "manual": config.manual.model_copy(update={"keyframes": []})})


def _refit(rect: Rect, aspect: float, source_w: int, source_h: int) -> Rect:
    return aspect_rect(source_w, source_h, aspect, cx=rect.cx, cy=rect.cy, height=rect.h)


def _cover_crop(rect: Rect, aspect: float, source_w: int, source_h: int) -> Rect:
    """The part of ``rect`` a ``cover`` fit at pixel ``aspect`` shows (same centre). Only ever
    shrinks, so the picture is exactly what the user previewed."""
    w_px, h_px = rect.w * source_w, rect.h * source_h
    if w_px / h_px > aspect:
        w_px = h_px * aspect
    else:
        h_px = w_px / aspect
    w, h = w_px / source_w, h_px / source_h
    return make_rect(rect.cx - w / 2, rect.cy - h / 2, w, h)


def refit_config(config: FramingConfig, source_w: int, source_h: int) -> FramingConfig:
    """Re-fit normalised rects to a ``source_w`` x ``source_h`` source: aspect-locked manual
    crops keep the output aspect (same centre and height); ``cover`` panels become the region
    that is really shown at the panel's aspect."""
    out_aspect = config.output_width / config.output_height
    manual = config.manual
    if manual.lock_aspect:
        manual = manual.model_copy(update={"crop": _refit(manual.crop, out_aspect, source_w, source_h)})
    fg = config.facecam_gameplay
    face_aspect, game_aspect = panel_aspects(config)
    fg_update: dict[str, Rect] = {}
    if fg.facecam_fit == "cover":
        fg_update["facecam"] = _cover_crop(fg.facecam, face_aspect, source_w, source_h)
    if fg.gameplay_fit == "cover":
        fg_update["gameplay"] = _cover_crop(fg.gameplay, game_aspect, source_w, source_h)
    pip = config.pip
    if pip.overlay_fit == "cover":
        dest = pip.overlay_dest
        overlay_aspect = (dest.w * config.output_width) / (dest.h * config.output_height)
        pip = pip.model_copy(update={
            "overlay_source": _cover_crop(pip.overlay_source, overlay_aspect, source_w, source_h)})
    return strip_keyframes(config.model_copy(update={
        "manual": manual, "facecam_gameplay": fg.model_copy(update=fg_update), "pip": pip}))


def _dump_warnings(warnings: list[FramingWarning]) -> list[dict]:
    return [w.model_dump(mode="json") for w in warnings]


def save_framing(db: Session, clip: GeneratedClip, config: FramingConfig, *, media: MediaInfo | None = None,
                 vision: ClipVisionAnalysis | None = _UNSET, auto_generated: bool = False,
                 preset_id: uuid.UUID | None = _UNSET) -> tuple[ClipFramingSettings, CompiledLayout]:
    """Persist ``config`` (smart keyframes computed, warnings recomputed). Resets the
    acknowledgement when the blocking warnings change and marks a finished render stale."""
    media = media or require_media(clip_source(db, clip))
    framing = clip.framing
    if framing is None:
        framing = ClipFramingSettings(clip_id=clip.id, mode=config.mode, config={}, vision=None, warnings=[],
                                      warnings_acknowledged=False, auto_generated=auto_generated)
        clip.framing = framing
        db.add(framing)
    if vision is not _UNSET:
        framing.vision = vision.model_dump(mode="json") if vision is not None else None
    vision_obj = stored_vision(clip)
    old_keys = blocking_keys(stored_warnings(clip))
    old_config = framing.config
    prepared, warnings, compiled = evaluate(clip, config, vision_obj, media)
    framing.mode = prepared.mode
    framing.config = prepared.model_dump(mode="json")
    framing.warnings = _dump_warnings(warnings)
    if blocking_keys(warnings) != old_keys:
        framing.warnings_acknowledged = False
    framing.auto_generated = auto_generated
    if preset_id is not _UNSET:
        framing.preset_id = preset_id
    if old_config != framing.config:
        mark_render_stale(clip)
    db.flush()
    return framing, compiled


def refresh_warnings(db: Session, clip: GeneratedClip) -> list[FramingWarning]:
    """Recompute the stored warnings (e.g. after captions changed). No-op without framing."""
    framing = clip.framing
    if framing is None:
        return []
    media = require_media(clip_source(db, clip))
    old_keys = blocking_keys(stored_warnings(clip))
    _, warnings, _ = evaluate(clip, load_config(framing, media), stored_vision(clip), media)
    framing.warnings = _dump_warnings(warnings)
    if blocking_keys(warnings) != old_keys:
        framing.warnings_acknowledged = False
    db.flush()
    return warnings


def shift_keyframes(keys: list[Keyframe], delta: float, duration: float) -> list[Keyframe]:
    """Move keyframes by ``delta`` seconds into ``[0, duration]``; the animation is cut at the new
    clip edges (interpolated rects) so every frame keeps the framing it had before the trim."""
    if not keys:
        return []
    keys = sorted(keys, key=lambda k: k.t)
    moved = [k.model_copy(update={"t": round(k.t + delta, 3)}) for k in keys if 0.0 <= k.t + delta <= duration]
    if keys[0].t + delta < 0.0 and (not moved or moved[0].t > 0.0):
        moved.insert(0, Keyframe(t=0.0, rect=interpolate_keyframes(keys, -delta)))
    if keys[-1].t + delta > duration and (not moved or moved[-1].t < duration):
        moved.append(Keyframe(t=round(duration, 3), rect=interpolate_keyframes(keys, duration - delta)))
    return moved


def adapt_to_range(db: Session, clip: GeneratedClip, old_start: float) -> None:
    """After a trim: shift manual keyframes, recompute smart keyframes and warnings. The old face
    analysis no longer applies, so the clip reports ``vision_missing`` until it is re-analysed."""
    framing = clip.framing
    media = source_media(clip_source(db, clip))
    if framing is None or media is None:
        return
    config = load_config(framing, media)
    keys = shift_keyframes(config.manual.keyframes, old_start - clip.start_time, clip_duration(clip))
    config = config.model_copy(update={"manual": config.manual.model_copy(update={"keyframes": keys})})
    save_framing(db, clip, with_fresh_keyframes(config), media=media, auto_generated=framing.auto_generated)


# ----------------------------------------------------------------- reads
def framing_out(db: Session, clip: GeneratedClip) -> dict[str, Any]:
    """FramingOut. Before the vision job ran: default full_frame config + ``vision_missing``."""
    source = clip_source(db, clip)
    media = require_media(source)
    framing = clip.framing
    vision = stored_vision(clip)
    config = load_config(framing, media)
    if framing is None:
        config, warnings, compiled = evaluate(clip, config, None, media)
        acknowledged, auto = False, True
    else:
        config = prepare_config(config, vision, media.width, media.height, clip_duration(clip))
        compiled = compile_layout(config, media.width, media.height)
        warnings, acknowledged, auto = stored_warnings(clip), framing.warnings_acknowledged, framing.auto_generated
    return {
        "clip_id": clip.id, "mode": config.mode, "config": config, "vision": vision, "warnings": warnings,
        "warnings_acknowledged": acknowledged, "auto_generated": auto, "compiled": compiled,
        "source": {"width": media.width, "height": media.height, "fps": media.fps,
                   "duration": source.duration or media.duration},
        "clip_start": clip.start_time, "clip_end": clip.end_time, "clip_duration": clip_duration(clip),
        "proxy_url": signed_url(clip.proxy_key), "proxy_start": clip.proxy_start,
    }


def _stored_config(clip: GeneratedClip, media: MediaInfo) -> FramingConfig | None:
    return load_config(clip.framing, media) if clip.framing is not None else None


def check(db: Session, clip: GeneratedClip, config: FramingConfig) -> dict[str, Any]:
    """Dry run: what ``config`` would look like and which warnings it raises (not saved)."""
    media = require_media(clip_source(db, clip))
    config = with_fresh_keyframes(config, _stored_config(clip, media))
    prepared, warnings, compiled = evaluate(clip, config, stored_vision(clip), media)
    return {"config": prepared, "warnings": warnings, "compiled": compiled}


def suggest(db: Session, clip: GeneratedClip) -> dict[str, Any]:
    """The AI framing suggestion for the clip (not saved)."""
    media = require_media(clip_source(db, clip))
    vision = stored_vision(clip)
    config = suggest_framing(vision, media, clip_duration(clip))
    prepared, warnings, compiled = evaluate(clip, config, vision, media)
    return {"config": prepared, "warnings": warnings, "compiled": compiled}


def update(db: Session, clip: GeneratedClip, config: FramingConfig, *, apply_to_video: bool = False) -> dict[str, Any]:
    """PUT: save a user-edited config; commits. With ``apply_to_video`` the framing becomes the video's
    framing: it is applied to the video's other clips (and to clips created later)."""
    media = require_media(clip_source(db, clip))
    config = with_fresh_keyframes(config, _stored_config(clip, media))
    save_framing(db, clip, config, media=media, auto_generated=False)
    db.commit()
    out = framing_out(db, clip)
    out["applied_to_clips"] = apply_video_framing(db, clip)["applied"] if apply_to_video else 0
    return out


# ------------------------------------------------------------ video framing
SKIP_UPLOAD = ("uploading", "uploaded")


def video_template(db: Session, clip: GeneratedClip) -> FramingConfig | None:
    """The framing saved for the clip's whole video, if any."""
    raw = clip_source(db, clip).framing_template
    if not raw:
        return None
    try:
        return FramingConfig.model_validate(raw)
    except ValueError:
        return None


def apply_video_framing(db: Session, clip: GeneratedClip) -> dict[str, int]:
    """Make ``clip``'s framing the framing of its video and apply it to the video's other clips that are not
    uploaded yet (each re-fitted, its render refreshed). Clips still being analysed pick it up when their
    analysis finishes. Commits. Returns ``{"applied", "needs_review", "rendering"}``."""
    from app.models.enums import CleanupStatus
    from app.modules.notifications.service import notify
    from app.modules.rendering.service import active_render_job, render_is_current, request_render
    from app.workers.jobs import ACTIVE_STATUSES

    if clip.framing is None:
        return {"applied": 0, "needs_review": 0, "rendering": 0}
    media = require_media(clip_source(db, clip))
    template = strip_keyframes(load_config(clip.framing, media))
    source = clip_source(db, clip)
    source.framing_template = template.model_dump(mode="json")
    db.commit()
    siblings = list(db.scalars(select(GeneratedClip).where(
        GeneratedClip.video_source_id == clip.video_source_id, GeneratedClip.id != clip.id,
        GeneratedClip.removed_at.is_(None))))
    vision_busy = set(db.scalars(select(ProcessingJob.clip_id).where(
        ProcessingJob.clip_id.in_([c.id for c in siblings]), ProcessingJob.job_type == JobType.CLIP_VISION,
        ProcessingJob.status.in_(ACTIVE_STATUSES)))) if siblings else set()
    counts = {"applied": 0, "needs_review": 0, "rendering": 0}
    for sib in siblings:
        if sib.upload_status in SKIP_UPLOAD or sib.cleanup_status == CleanupStatus.CLEANED:
            continue
        if sib.framing is None or sib.id in vision_busy:
            continue  # its analysis applies the video framing when it finishes
        try:
            apply_preset_to_clip(db, sib, template, preset_id=None, auto_generated=True, media=media)
            db.commit()
        except AppError as exc:
            db.rollback()
            log.info("video framing not applied to clip %s: %s", sib.id, exc.message)
            continue
        counts["applied"] += 1
        if render_is_current(sib):
            continue
        if active_render_job(db, sib) is not None:
            counts["rendering"] += 1  # the running render finishes stale and is re-run automatically
            continue
        try:
            request_render(db, sib, acknowledge_warnings=False, reason="video_framing")
        except ConflictError as exc:
            db.rollback()
            if exc.code == "framing_warnings_unacknowledged":
                counts["needs_review"] += 1
        except AppError as exc:
            db.rollback()
            log.info("render of clip %s after the video framing change not started: %s", sib.id, exc.message)
    if counts["needs_review"]:
        n = counts["needs_review"]
        notify(db, clip.user_id, NotificationType.FRAMING_REVIEW, "Framing review needed",
               f"The new framing was applied to every clip of “{source.title or 'the video'}”, but {n} clip"
               f"{'s' if n != 1 else ''} would hide or cut a face with it. Review {'them' if n != 1 else 'it'} in "
               "the framing editor.", link=f"/app/videos/{source.id}", data={"video_source_id": str(source.id)})
        db.commit()
    log.info("video framing of source %s applied to %d clips", source.id, counts["applied"])
    return counts


def acknowledge(db: Session, clip: GeneratedClip) -> dict[str, Any]:
    """Acknowledge the current warnings (persisting the default framing when none exists)."""
    if clip.framing is None:
        media = require_media(clip_source(db, clip))
        save_framing(db, clip, default_framing_config(media.width, media.height), media=media,
                     auto_generated=True)
    clip.framing.warnings_acknowledged = True
    db.commit()
    return framing_out(db, clip)


def request_analysis(db: Session, clip: GeneratedClip) -> ProcessingJob:
    """Create (or return the active) ``clip_vision`` job and dispatch it; commits."""
    from app.workers.jobs import create_job, dispatch_job

    if clip_source(db, clip).storage_key is None:
        raise ConflictError("The source video file is not available.", code="source_missing")
    job, created = create_job(db, user_id=clip.user_id, job_type=JobType.CLIP_VISION,
                              video_source_id=clip.video_source_id, clip_id=clip.id,
                              idempotency_key=f"clip_vision:{clip.id}")
    if created:
        dispatch_job(db, job, "clip_vision")
    else:
        db.commit()
    return job


# ---------------------------------------------------------------- presets
def _set_setting(user: User, key: str, value: Any) -> None:
    user.settings = {**(user.settings or {}), key: value}


def default_preset(db: Session, user: User) -> FramingPreset | None:
    """The user's default framing preset (``settings.default_framing_preset_id``, else the
    preset flagged ``is_default``)."""
    sid = (user.settings or {}).get(SETTING_DEFAULT_PRESET)
    if sid:
        try:
            preset = db.get(FramingPreset, uuid.UUID(str(sid)))
        except ValueError:
            preset = None
        if preset is not None and preset.user_id == user.id:
            return preset
    return db.scalar(select(FramingPreset).where(FramingPreset.user_id == user.id, FramingPreset.is_default.is_(True)))


def preset_config(preset: FramingPreset) -> FramingConfig:
    return FramingConfig.model_validate(preset.config or {})


def preset_out(preset: FramingPreset, default_id: uuid.UUID | None) -> dict[str, Any]:
    return {"id": preset.id, "name": preset.name, "mode": preset.mode, "config": preset.config,
            "is_default": preset.id == default_id, "created_at": preset.created_at, "updated_at": preset.updated_at}


def list_presets(db: Session, user: User) -> list[dict[str, Any]]:
    rows = db.scalars(select(FramingPreset).where(FramingPreset.user_id == user.id).order_by(FramingPreset.name))
    default = default_preset(db, user)
    return [preset_out(p, default.id if default else None) for p in rows]


def _check_name(db: Session, user: User, name: str, exclude: uuid.UUID | None = None) -> None:
    stmt = select(FramingPreset.id).where(FramingPreset.user_id == user.id, FramingPreset.name == name)
    if exclude is not None:
        stmt = stmt.where(FramingPreset.id != exclude)
    if db.scalar(stmt) is not None:
        raise ConflictError(f"A framing preset named '{name}' already exists.", code="preset_name_taken")


def _make_default(db: Session, user: User, preset: FramingPreset, is_default: bool) -> None:
    if is_default:
        for other in db.scalars(select(FramingPreset).where(FramingPreset.user_id == user.id,
                                                            FramingPreset.is_default.is_(True))):
            other.is_default = False
        preset.is_default = True
        _set_setting(user, SETTING_DEFAULT_PRESET, str(preset.id))
    else:
        preset.is_default = False
        if str((user.settings or {}).get(SETTING_DEFAULT_PRESET)) == str(preset.id):
            _set_setting(user, SETTING_DEFAULT_PRESET, None)


def create_preset(db: Session, user: User, name: str, config: FramingConfig, is_default: bool) -> dict[str, Any]:
    name = name.strip()
    _check_name(db, user, name)
    config = strip_keyframes(config)
    preset = FramingPreset(id=uuid.uuid4(), user_id=user.id, name=name, mode=config.mode,
                           config=config.model_dump(mode="json"), is_default=False)
    db.add(preset)
    db.flush()
    _make_default(db, user, preset, is_default)
    db.commit()
    default = default_preset(db, user)
    return preset_out(preset, default.id if default else None)


def update_preset(db: Session, user: User, preset_id: uuid.UUID, *, name: str | None,
                  config: FramingConfig | None, is_default: bool | None) -> dict[str, Any]:
    preset = get_owned(db, FramingPreset, preset_id, user, name="Framing preset")
    if name is not None:
        _check_name(db, user, name.strip(), exclude=preset.id)
        preset.name = name.strip()
    if config is not None:
        config = strip_keyframes(config)
        preset.mode, preset.config = config.mode, config.model_dump(mode="json")
    if is_default is not None:
        _make_default(db, user, preset, is_default)
    db.commit()
    default = default_preset(db, user)
    return preset_out(preset, default.id if default else None)


def delete_preset(db: Session, user: User, preset_id: uuid.UUID) -> None:
    preset = get_owned(db, FramingPreset, preset_id, user, name="Framing preset")
    if str((user.settings or {}).get(SETTING_DEFAULT_PRESET)) == str(preset.id):
        _set_setting(user, SETTING_DEFAULT_PRESET, None)
    db.delete(preset)
    db.commit()


# ------------------------------------------------------------------ apply
def apply_preset_to_clip(db: Session, clip: GeneratedClip, base: FramingConfig, *, preset_id: uuid.UUID | None,
                         auto_generated: bool = False, media: MediaInfo | None = None,
                         ) -> tuple[ClipFramingSettings, CompiledLayout]:
    """Re-fit ``base`` to the clip's source and save it (no commit)."""
    media = media or require_media(clip_source(db, clip))
    config = refit_config(base, media.width, media.height)
    return save_framing(db, clip, config, media=media, auto_generated=auto_generated, preset_id=preset_id)


def apply_to_clips(db: Session, user: User, clip_ids: list[uuid.UUID], *, config: FramingConfig | None,
                   preset_id: uuid.UUID | None) -> list[dict[str, Any]]:
    """Apply a config or a saved preset to several clips; one result per clip (commits per clip)."""
    if preset_id is not None:
        preset = get_owned(db, FramingPreset, preset_id, user, name="Framing preset")
        base = preset_config(preset)
    elif config is not None:
        base = config
    else:
        raise ValidationFailed("Provide either a framing config or a preset_id.", code="framing_source_missing")
    results = []
    for cid in dict.fromkeys(clip_ids):
        try:
            clip = get_owned(db, GeneratedClip, cid, user, name="Clip")
            framing, _ = apply_preset_to_clip(db, clip, base, preset_id=preset_id)
            db.commit()
            results.append({"clip_id": cid, "ok": True, "warnings": framing.warnings, "error": None})
        except AppError as exc:
            db.rollback()
            results.append({"clip_id": cid, "ok": False, "warnings": [],
                            "error": {"code": exc.code, "message": exc.message}})
    return results
