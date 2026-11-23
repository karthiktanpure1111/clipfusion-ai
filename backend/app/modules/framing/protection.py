"""Crop protection: analyse what the final 9:16 frame shows before rendering and report
faces that are cut or hidden, hidden streamers, off-frame activity and captions covering
faces — each with one-click framing fixes (``FramingSuggestion.config_patch`` is a partial
FramingConfig to deep-merge; ``keyframes: []`` asks the server to recompute smart keyframes).

Which faces must be visible ("required"):
* smart + auto_switch — the person of the current shot; others are informational;
* smart (single person) — the followed person, plus every significant person when the user
  has not explicitly chosen whom to follow;
* every other mode — every significant person (primary, facecam, or well-covered faces).
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import numpy as np

from app.modules.framing.layout import compile_layout, make_rect
from app.modules.framing.presets import (
    facecam_gameplay_config,
    facecam_region,
    head_union,
    manual_crop_including,
    rect_patch,
)
from app.modules.framing.protection_extras import subtitle_warnings, upscale_warnings
from app.modules.framing.smart_crop import compute_smart_keyframes, shot_plan
from app.modules.framing.tracks import activity_array, primary_track, significant_tracks
from app.modules.framing.visibility import LayerGeom, activity_outside, layer_geometry, visible_fraction
from app.schemas.framing import (
    ClipVisionAnalysis,
    CompiledLayout,
    FaceTrack,
    FramingConfig,
    FramingSuggestion,
    FramingWarning,
)
from app.schemas.subtitles import Caption, SubtitleStyle

log = logging.getLogger(__name__)

OUTSIDE = 0.5          # visible fraction below which a face counts as outside the frame
CUT = 0.95             # visible fraction below which a face counts as cut
MIN_AFFECTED = 0.10    # fraction of the clip a problem must last to be reported
CONTENT_LIMIT = 0.5    # max share of activity energy allowed outside the visible regions

FULL_FRAME_FIX = FramingSuggestion(action="switch_mode", label="Show the full frame (no crop)",
                                   config_patch={"mode": "full_frame"})


class _Ctx:
    def __init__(self, config: FramingConfig, layout: CompiledLayout, vision: ClipVisionAnalysis | None,
                 source_w: int, source_h: int, duration: float) -> None:
        self.config, self.layout, self.vision = config, layout, vision
        self.sw, self.sh, self.duration = source_w, source_h, duration
        self._cache: dict[float, list[LayerGeom]] = {}

    def geoms(self, t: float) -> list[LayerGeom]:
        key = round(t, 4)
        if key not in self._cache:
            self._cache[key] = layer_geometry(self.layout, key, self.sw, self.sh)
        return self._cache[key]


def prepare_config(config: FramingConfig, vision: ClipVisionAnalysis | None, source_w: int, source_h: int,
                   duration: float) -> FramingConfig:
    """Fill in smart-crop keyframes when the config needs them and has none."""
    if config.mode != "smart" or config.smart.keyframes:
        return config
    keys = compute_smart_keyframes(vision, config.smart, source_w=source_w, source_h=source_h, duration=duration,
                                   output_aspect=config.output_width / config.output_height)
    return config.model_copy(update={"smart": config.smart.model_copy(update={"keyframes": keys})})


def time_ranges(times: list[float], dt: float, duration: float) -> list[tuple[float, float]]:
    """Merge sample times (each covering ``dt``) into ``[(start, end)]`` clip ranges."""
    out: list[list[float]] = []
    for t in sorted(times):
        a, b = max(0.0, t - dt / 2), min(duration, t + dt / 2)
        if out and a <= out[-1][1] + 1e-6:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(round(a, 2), round(b, 2)) for a, b in out if b > a]


def _required_fn(ctx: _Ctx) -> Callable[[FaceTrack, float], bool]:
    vision = ctx.vision
    sig = {t.id for t in significant_tracks(vision)}
    if ctx.config.mode != "smart" or vision is None:
        return lambda track, _t: track.id in sig
    smart = ctx.config.smart
    plan = shot_plan(vision, smart, ctx.duration)
    if smart.auto_switch and len({p[2] for p in plan}) > 1:
        def in_shot(track: FaceTrack, t: float) -> bool:
            for i, (s, e, tid) in enumerate(plan):
                if s <= t < e or (i == len(plan) - 1 and t >= e):
                    return tid == track.id
            return False
        return in_shot
    followed = plan[0][2] if plan else None
    explicit = smart.primary_track_id is not None
    return lambda track, _t: track.id == followed or (not explicit and track.id in sig)


def _face_fixes(ctx: _Ctx, track: FaceTrack) -> list[FramingSuggestion]:
    cfg, vision = ctx.config, ctx.vision
    assert vision is not None
    fixes: list[FramingSuggestion] = []
    name = track.label or f"Person {track.id}"
    sig = significant_tracks(vision)
    facecam = track if track.is_facecam else next((t for t in sig if t.is_facecam), None)
    if cfg.mode == "manual":
        res = manual_crop_including(cfg.manual.crop, head_union(track))
        if res is not None:
            action, rect = res
            verb = "Move" if action == "move_crop" else "Enlarge"
            fixes.append(FramingSuggestion(action=action, label=f"{verb} the crop to include {name}",
                                           config_patch={"manual": {"crop": rect_patch(rect), "keyframes": []}}))
    if cfg.mode == "pip" and track.is_facecam:
        fixes.append(FramingSuggestion(action="set_config", label="Use the detected facecam as the overlay",
                                       config_patch={"pip": {"overlay_source": rect_patch(facecam_region(track))}}))
    if cfg.mode == "facecam_gameplay" and track.is_facecam:
        region = facecam_region(track)
        fixes.append(FramingSuggestion(action="set_config", label="Fit the facecam panel to the detected facecam",
                                       config_patch={"facecam_gameplay": {"facecam": rect_patch(make_rect(
                                           region.x, region.y, region.w, region.h))}}))
    if facecam is not None and cfg.mode != "facecam_gameplay":
        fg = facecam_gameplay_config(cfg, vision, facecam, ctx.sw, ctx.sh).facecam_gameplay
        fixes.append(FramingSuggestion(action="switch_mode", label="Facecam + gameplay layout",
                                       config_patch={"mode": "facecam_gameplay",
                                                     "facecam_gameplay": fg.model_dump(mode="json")}))
    if not track.is_facecam and cfg.mode != "smart":
        fixes.append(FramingSuggestion(action="switch_mode", label=f"AI Smart Crop following {name}",
                                       config_patch={"mode": "smart", "smart": {
                                           "primary_track_id": track.id, "auto_switch": False, "keyframes": []}}))
    if cfg.mode == "smart":
        people = [t for t in sig if not t.is_facecam]
        if len(people) >= 2 and not cfg.smart.auto_switch:
            fixes.append(FramingSuggestion(action="enable_auto_switch", label="Follow whoever is speaking",
                                           config_patch={"smart": {"auto_switch": True, "keyframes": []}}))
        fixes.append(FramingSuggestion(action="set_config", label=f"Follow {name}",
                                       config_patch={"smart": {"primary_track_id": track.id,
                                                               "auto_switch": False, "keyframes": []}}))
    fixes.append(FULL_FRAME_FIX)
    return fixes


def _face_warnings(ctx: _Ctx) -> list[FramingWarning]:
    vision = ctx.vision
    assert vision is not None
    dt = 1.0 / vision.sample_fps
    required = _required_fn(ctx)
    primary = primary_track(vision)
    warnings: list[FramingWarning] = []
    for track in vision.tracks:
        samples = [(b.t, visible_fraction(b.rect, ctx.geoms(b.t), ctx.sw, ctx.sh), required(track, b.t))
                   for b in track.boxes if b.t <= ctx.duration + 1e-6]  # the clip may have been trimmed
        req = [(t, f) for t, f, r in samples if r]
        name = track.label or f"Person {track.id}"
        outside = [t for t, f in req if f < OUTSIDE]
        cut = [t for t, f in req if OUTSIDE <= f < CUT]
        mean_vis = round(float(np.mean([f for _, f in req])), 4) if req else None

        def share(times: list[float]) -> float:
            return min(1.0, len(times) * dt / max(ctx.duration, 1e-6))

        common = {"track_id": track.id, "visible_fraction": mean_vis}
        if share(outside) > MIN_AFFECTED:
            fixes = _face_fixes(ctx, track)
            ranges = time_ranges(outside, dt, ctx.duration)
            warnings.append(FramingWarning(code="face_outside", severity="warning", affected_fraction=round(
                share(outside), 4), message=f"{name}'s face is outside the frame for part of the clip.",
                time_ranges=ranges, suggestions=fixes, **common))
            if track.is_facecam or (primary is not None and track.id == primary.id):
                who = "The streamer's facecam" if track.is_facecam else f"The main person ({name})"
                warnings.append(FramingWarning(code="streamer_hidden", severity="critical",
                                               affected_fraction=round(share(outside), 4),
                                               message=f"{who} is hidden by this framing.",
                                               time_ranges=ranges, suggestions=fixes, **common))
        if share(cut) > MIN_AFFECTED:
            warnings.append(FramingWarning(code="head_cut", severity="warning", affected_fraction=round(share(cut), 4),
                                           message=f"{name}'s head is partly cut off by the frame edge.",
                                           time_ranges=time_ranges(cut, dt, ctx.duration),
                                           suggestions=_face_fixes(ctx, track), **common))
        info = [t for t, f, r in samples if not r and f < OUTSIDE]
        if share(info) > MIN_AFFECTED:
            warnings.append(FramingWarning(
                code="face_outside", severity="info", track_id=track.id, affected_fraction=round(share(info), 4),
                message=f"{name} is not in frame while the camera shows someone else.",
                time_ranges=time_ranges(info, dt, ctx.duration), suggestions=_face_fixes(ctx, track)))
    return warnings


def _content_warning(ctx: _Ctx) -> FramingWarning | None:
    vision = ctx.vision
    arr = activity_array(vision.activity) if vision is not None else None
    if arr is None:
        return None
    n = 12 if any(layer.keyframes for layer in ctx.layout.layers) else 1
    times = [ctx.duration * (i + 0.5) / n for i in range(n)]
    values = [activity_outside(arr, ctx.geoms(t), ctx.sw, ctx.sh) for t in times]
    mean_out = float(np.mean(values))
    if mean_out <= CONTENT_LIMIT:
        return None
    blocking = vision.layout_hint in ("facecam_gameplay", "screen_content", "no_faces")
    fixes: list[FramingSuggestion] = []
    if ctx.config.mode == "facecam_gameplay":
        fixes.append(FramingSuggestion(action="set_config", label="Show the whole gameplay in its panel",
                                       config_patch={"facecam_gameplay": {
                                           "gameplay": {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0},
                                           "gameplay_fit": "contain"}}))
    fixes.append(FULL_FRAME_FIX)
    bad = [t for t, v in zip(times, values, strict=True) if v > CONTENT_LIMIT]
    return FramingWarning(code="content_outside", severity="warning" if blocking else "info",
                          message=f"About {round(mean_out * 100)}% of the on-screen action is outside the frame.",
                          affected_fraction=round(len(bad) / n, 4),
                          time_ranges=time_ranges(bad, ctx.duration / n, ctx.duration), suggestions=fixes)


def analyze_protection(config: FramingConfig, vision: ClipVisionAnalysis | None, *, source_w: int,
                       source_h: int, duration: float, subtitle_style: SubtitleStyle | None = None,
                       captions: list[Caption] | None = None) -> list[FramingWarning]:
    """Check the final framing of a clip and return warnings (blocking ones have severity
    ``warning``/``critical``; see :func:`app.schemas.framing.blocking_warnings`)."""
    duration = max(duration, 1e-3)
    config = prepare_config(config, vision, source_w, source_h, duration)
    layout = compile_layout(config, source_w, source_h)
    ctx = _Ctx(config, layout, vision, source_w, source_h, duration)
    whole = [(0.0, round(duration, 2))]
    warnings: list[FramingWarning] = []
    if vision is None:
        warnings.append(FramingWarning(code="vision_missing", severity="info", time_ranges=whole,
                                       message="Face analysis has not run for this clip yet, so faces cannot be "
                                               "protected automatically.", suggestions=[FULL_FRAME_FIX]))
    elif not vision.tracks:
        warnings.append(FramingWarning(code="no_faces_detected", severity="info", time_ranges=whole,
                                       message="No faces were detected in this clip.", suggestions=[FULL_FRAME_FIX]))
    else:
        warnings.extend(_face_warnings(ctx))
    content = _content_warning(ctx)
    if content is not None:
        warnings.append(content)
    if vision is not None and vision.tracks and captions and subtitle_style is not None:
        warnings.extend(subtitle_warnings(ctx.layout, vision, captions, subtitle_style, source_w, source_h,
                                          duration, config))
    warnings.extend(upscale_warnings(config, layout, source_w, source_h, duration))
    return warnings
