"""Secondary crop-protection checks: captions covering faces and heavily upscaled layers."""

from __future__ import annotations

import logging

from app.modules.framing.geometry import fit_rects, rect_at, source_to_canvas, to_px
from app.modules.framing.tracks import box_at, significant_tracks
from app.schemas.framing import (
    ClipVisionAnalysis,
    CompiledLayout,
    FramingConfig,
    FramingSuggestion,
    FramingWarning,
    Rect,
)
from app.schemas.subtitles import Caption, SubtitleStyle

log = logging.getLogger(__name__)

UPSCALE_LIMIT = 2.0
_OVERLAP = 0.10  # share of a face's canvas area a caption block may cover


def _faces_on_canvas(layout: CompiledLayout, vision: ClipVisionAnalysis, t: float, source_w: int,
                     source_h: int) -> list[Rect]:
    out: list[Rect] = []
    for track in significant_tracks(vision):
        face = box_at(track, t)
        if face is None:
            continue
        for layer in layout.layers:
            r = source_to_canvas(face, layer, t, source_w, source_h, layout.width, layout.height)
            if r is not None:
                out.append(r)
    return out


def _merge(ranges: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out: list[list[float]] = []
    for a, b in sorted(ranges):
        if out and a <= out[-1][1] + 0.05:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(round(a, 2), round(b, 2)) for a, b in out]


def subtitle_warnings(layout: CompiledLayout, vision: ClipVisionAnalysis, captions: list[Caption],
                      style: SubtitleStyle, source_w: int, source_h: int, duration: float,
                      config: FramingConfig) -> list[FramingWarning]:
    """``subtitle_overlap_face`` when a laid-out caption block covers a visible face.
    Skipped (with a log line) when the subtitle layout engine is unavailable."""
    try:
        from app.modules.subtitles.text_layout import layout_caption
    except ImportError:
        log.info("Subtitle layout engine unavailable; caption/face overlap not checked")
        return []
    hits: list[tuple[float, float]] = []
    block_cy: list[float] = []
    for cap in captions:
        times = (cap.start, (cap.start + cap.end) / 2, max(cap.start, cap.end - 0.05))
        faces = [f for t in times for f in _faces_on_canvas(layout, vision, t, source_w, source_h)]
        if not faces:
            continue
        try:
            lay = layout_caption(cap, style, layout.width, layout.height,
                                 avoid_rects=faces if style.avoid_faces else None)
        except Exception as exc:  # noqa: BLE001 - layout problems must not break protection
            log.warning("Caption layout failed (%s); overlap check skipped", exc)
            return []
        if any(lay.block.intersection_area(f) > _OVERLAP * f.area for f in faces):
            hits.append((max(0.0, cap.start), min(duration, cap.end)))
            block_cy.append(lay.block.cy)
    if not hits:
        return []
    ranges = _merge(hits)
    lower = sum(block_cy) / len(block_cy) > 0.5
    fixes = []
    if config.mode == "facecam_gameplay":
        flip = "bottom" if config.facecam_gameplay.facecam_position == "top" else "top"
        fixes.append(FramingSuggestion(action="set_config", label=f"Move the facecam panel to the {flip}",
                                       config_patch={"facecam_gameplay": {"facecam_position": flip}}))
    fixes.append(FramingSuggestion(
        action="switch_mode", label=f"Full frame placed at the {'top' if lower else 'bottom'} (captions stay clear)",
        config_patch={"mode": "full_frame", "full_frame": {"vertical_position": 0.0 if lower else 1.0}}))
    covered = sum(b - a for a, b in ranges)
    return [FramingWarning(code="subtitle_overlap_face", severity="warning",
                           message="Captions cover a face. Move the captions (Subtitle Studio > Position) "
                                   "or change the framing.",
                           affected_fraction=round(min(1.0, covered / max(duration, 1e-6)), 4),
                           time_ranges=ranges, suggestions=fixes)]


def _max_upscale(layout: CompiledLayout, layer_index: int, source_w: int, source_h: int, duration: float) -> float:
    layer = layout.layers[layer_index]
    times = [0.0] + [k.t for k in layer.keyframes][:: max(1, len(layer.keyframes) // 20)]
    worst = 0.0
    for t in times:
        src = to_px(rect_at(layer, min(t, duration)), source_w, source_h)
        crop, draw = fit_rects(src, to_px(layer.dest, layout.width, layout.height), layer.fit)
        if crop[2] > 0:
            worst = max(worst, draw[2] / crop[2])
    return worst


def _upscale_fixes(config: FramingConfig, layer_id: str) -> list[FramingSuggestion]:
    fixes: list[FramingSuggestion] = []
    if layer_id == "facecam" and config.mode == "facecam_gameplay":
        split = round(max(0.15, config.facecam_gameplay.split * 0.75), 4)
        fixes.append(FramingSuggestion(action="set_config", label="Make the facecam panel smaller",
                                       config_patch={"facecam_gameplay": {"split": split}}))
    if config.mode == "smart" and config.smart.zoom > 1.0:
        fixes.append(FramingSuggestion(action="set_config", label="Zoom out to 1x",
                                       config_patch={"smart": {"zoom": 1.0, "keyframes": []}}))
    fixes.append(FramingSuggestion(action="switch_mode", label="Show the full frame (no crop)",
                                   config_patch={"mode": "full_frame"}))
    return fixes


def upscale_warnings(config: FramingConfig, layout: CompiledLayout, source_w: int, source_h: int,
                     duration: float) -> list[FramingWarning]:
    """``upscale_quality`` (info) for layers whose source pixels are enlarged > 2x."""
    out = []
    for i, layer in enumerate(layout.layers):
        scale = _max_upscale(layout, i, source_w, source_h, duration)
        if scale > UPSCALE_LIMIT:
            out.append(FramingWarning(
                code="upscale_quality", severity="info",
                message=f"The {layer.id} area is enlarged {scale:.1f}x, so it may look soft.",
                affected_fraction=1.0, time_ranges=[(0.0, round(duration, 2))],
                suggestions=_upscale_fixes(config, layer.id)))
    return out
