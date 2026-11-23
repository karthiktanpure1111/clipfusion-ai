"""Framing config builders shared by :mod:`suggest` (automatic framing) and
:mod:`protection` (one-click fixes). All builders are deterministic."""

from __future__ import annotations

from app.modules.framing.layout import aspect_rect, make_rect, panel_aspects
from app.modules.framing.tracks import activity_centroid, head_box, union_rect
from app.modules.tracking.facecam import expand_face
from app.schemas.framing import (
    FULL_FRAME,
    ClipVisionAnalysis,
    FacecamGameplayConfig,
    FaceTrack,
    FramingConfig,
    Rect,
)


def mean_face(track: FaceTrack) -> Rect:
    n = len(track.boxes)
    return Rect(x=sum(b.rect.x for b in track.boxes) / n, y=sum(b.rect.y for b in track.boxes) / n,
                w=sum(b.rect.w for b in track.boxes) / n, h=sum(b.rect.h for b in track.boxes) / n)


def head_union(track: FaceTrack) -> Rect:
    """Bounding box of the head over the whole clip."""
    return union_rect([head_box(b.rect) for b in track.boxes])


def facecam_region(track: FaceTrack) -> Rect:
    """The webcam overlay region of a facecam track (estimated when no border was found),
    grown if needed so it contains the head at every sample."""
    region = track.facecam_rect or expand_face(mean_face(track))
    return union_rect([region, head_union(track)])


def _split_for(rect: Rect, config: FramingConfig, source_w: int, source_h: int) -> float:
    """Facecam panel height so the panel aspect matches the facecam aspect (cover ~ no trim)."""
    aspect = (rect.w * source_w) / (rect.h * source_h)
    split = config.output_width / (aspect * config.output_height)
    return round(min(0.45, max(0.25, split)), 4)


def gameplay_region(vision: ClipVisionAnalysis, avoid: Rect | None, aspect: float, source_w: int,
                    source_h: int) -> Rect:
    """Full-height region at the gameplay panel ``aspect`` centred on the activity centroid,
    shifted horizontally off the facecam when that is possible."""
    cx = (activity_centroid(vision.activity) or (0.5, 0.5))[0]
    rect = aspect_rect(source_w, source_h, aspect, cx=cx)
    if avoid is None or rect.intersection_area(avoid) <= 0:
        return rect
    options = []
    if avoid.x - rect.w >= 0:
        options.append(avoid.x - rect.w)
    if avoid.x + avoid.w + rect.w <= 1:
        options.append(avoid.x + avoid.w)
    if not options:
        return rect
    x = min(options, key=lambda v: abs(v + rect.w / 2 - cx))
    return make_rect(x, rect.y, rect.w, rect.h)


def facecam_gameplay_config(base: FramingConfig, vision: ClipVisionAnalysis, track: FaceTrack, source_w: int,
                            source_h: int, *, contain_gameplay: bool = False) -> FramingConfig:
    """Stacked facecam (top) + gameplay layout for a streamer. ``contain_gameplay`` shows the
    whole frame in the gameplay panel instead of a cropped region."""
    cam = facecam_region(track)
    cam = make_rect(cam.x, cam.y, cam.w, cam.h)
    fg = FacecamGameplayConfig(facecam=cam, facecam_position="top", split=_split_for(cam, base, source_w, source_h),
                               gap=0.0, facecam_fit="cover", gameplay_fit="contain" if contain_gameplay else "cover")
    if contain_gameplay:
        fg = fg.model_copy(update={"gameplay": FULL_FRAME})
    else:
        aspect = panel_aspects(base, fg)[1]
        fg = fg.model_copy(update={"gameplay": gameplay_region(vision, cam, aspect, source_w, source_h)})
    return base.model_copy(update={"mode": "facecam_gameplay", "facecam_gameplay": fg})


def person_region(track: FaceTrack, aspect: float, source_w: int, source_h: int) -> Rect | None:
    """Region at ``aspect`` around a person (head ~35-40% from the top) containing the head at
    every sample, or None when impossible."""
    head = head_union(track)
    face = mean_face(track)
    h = min(1.0, max(3.4 * face.h, head.h * 1.4))
    w = h * source_h * aspect / source_w
    if w < head.w * 1.1:
        w = min(1.0, head.w * 1.1)
        h = min(1.0, w * source_w / aspect / source_h)
    rect = make_rect(head.cx - w / 2, head.y - 0.22 * h, w, h)
    contains = (rect.x <= head.x + 1e-6 and rect.x + rect.w >= head.x + head.w - 1e-6
                and rect.y <= head.y + 1e-6 and rect.y + rect.h >= head.y + head.h - 1e-6)
    return rect if contains else None


def two_person_config(base: FramingConfig, a: FaceTrack, b: FaceTrack, source_w: int,
                      source_h: int) -> FramingConfig | None:
    """Stacked two-person layout (left person on top, right person below), split 50/50."""
    left, right = sorted((a, b), key=lambda t: t.mean_center[0])
    fg = FacecamGameplayConfig(facecam_position="top", split=0.5, gap=0.0, facecam_fit="cover",
                               gameplay_fit="cover")
    top_aspect, bottom_aspect = panel_aspects(base, fg)
    top = person_region(left, top_aspect, source_w, source_h)
    bottom = person_region(right, bottom_aspect, source_w, source_h)
    if top is None or bottom is None:
        return None
    fg = fg.model_copy(update={"facecam": top, "gameplay": bottom})
    return base.model_copy(update={"mode": "facecam_gameplay", "facecam_gameplay": fg})


def manual_crop_including(crop: Rect, target: Rect) -> tuple[str, Rect] | None:
    """Move (same size) or expand (same aspect) ``crop`` so it contains ``target``.
    Returns ``("move_crop" | "expand_crop", rect)`` or None when it cannot fit."""
    if target.w <= crop.w and target.h <= crop.h:
        x = min(max(crop.x, target.x + target.w - crop.w), target.x)
        y = min(max(crop.y, target.y + target.h - crop.h), target.y)
        return "move_crop", make_rect(x, y, crop.w, crop.h)
    scale = max(target.w / crop.w, target.h / crop.h) * 1.04
    w, h = crop.w * scale, crop.h * scale
    if w > 1.0 + 1e-6 or h > 1.0 + 1e-6:
        return None
    x = min(max(target.cx - w / 2, target.x + target.w - w), target.x)
    y = min(max(target.cy - h / 2, target.y + target.h - h), target.y)
    return "expand_crop", make_rect(x, y, w, h)


def rect_patch(rect: Rect) -> dict:
    return {"x": rect.x, "y": rect.y, "w": rect.w, "h": rect.h}
