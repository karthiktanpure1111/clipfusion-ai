"""Burned-in captions for the renderer: per-caption layout (with face avoidance), animation
state at the frame's clip time and cached Pillow tiles blended onto the canvas."""

from __future__ import annotations

import logging
from bisect import bisect_left, bisect_right

import numpy as np

from app.modules.framing.geometry import lerp, source_to_canvas
from app.modules.subtitles.animation import caption_frame_state
from app.modules.subtitles.text_layout import CaptionLayout, layout_caption
from app.modules.subtitles.text_renderer import CaptionRenderer
from app.schemas.framing import CompiledLayout, FaceTrack, Rect
from app.schemas.render import FontFile
from app.schemas.subtitles import Caption, SubtitleStyle

log = logging.getLogger(__name__)

FACE_HOLD_S = 0.75   # a face sample is trusted this long when no neighbouring sample exists


def face_rect_at(track: FaceTrack, t: float, max_gap: float = 2.0) -> Rect | None:
    """Source rect of ``track`` at clip time ``t``: linear interpolation between samples
    less than ``max_gap`` apart, otherwise the nearest sample within ``FACE_HOLD_S``."""
    boxes = track.boxes
    if not boxes:
        return None
    times = [b.t for b in boxes]
    i = bisect_right(times, t)
    before = boxes[i - 1] if i > 0 else None
    after = boxes[i] if i < len(boxes) else None
    if before and after and after.t - before.t <= max_gap and after.t > before.t:
        u = (t - before.t) / (after.t - before.t)
        a, b = before.rect, after.rect
        return Rect(x=lerp(a.x, b.x, u), y=lerp(a.y, b.y, u), w=lerp(a.w, b.w, u), h=lerp(a.h, b.h, u))
    near = min((b for b in (before, after) if b is not None), key=lambda b: abs(b.t - t))
    return near.rect if abs(near.t - t) <= FACE_HOLD_S else None


def visible_faces(tracks: list[FaceTrack], layout: CompiledLayout, t: float, source_w: int,
                  source_h: int) -> list[Rect]:
    """Canvas rects of every face visible through any layer at clip time ``t``."""
    out: list[Rect] = []
    for track in tracks:
        face = face_rect_at(track, t)
        if face is None:
            continue
        for layer in layout.layers:
            r = source_to_canvas(face, layer, t, source_w, source_h, layout.width, layout.height)
            if r is not None and r.w > 1e-4 and r.h > 1e-4:
                out.append(r)
    return out


class CaptionTrack:
    """Draws the captions active at a clip time onto a canvas."""

    def __init__(self, captions: list[Caption], style: SubtitleStyle, layout: CompiledLayout, *,
                 source_w: int, source_h: int, face_tracks: list[FaceTrack] | None = None,
                 custom_fonts: list[FontFile] | None = None) -> None:
        self.captions = sorted((c for c in captions if c.words), key=lambda c: c.start)
        self._starts = [c.start for c in self.captions]
        self.style = style
        self.layout = layout
        self.source_w, self.source_h = source_w, source_h
        self.face_tracks = face_tracks or []
        self.custom_fonts = custom_fonts or []
        self.renderer = CaptionRenderer()
        self._layouts: dict[tuple, CaptionLayout] = {}

    def _avoid_rects(self, c: Caption) -> list[Rect]:
        if not (self.style.avoid_faces and self.face_tracks):
            return []
        rects: list[Rect] = []
        for t in (c.start, (c.start + c.end) / 2, max(c.start, c.end - 1e-3)):
            rects += visible_faces(self.face_tracks, self.layout, t, self.source_w, self.source_h)
        return rects

    def layout_for(self, c: Caption) -> CaptionLayout:
        """Cached layout of a caption (computed for the whole caption so lines never jump)."""
        key = (c.id, c.start, c.end, c.text)
        cached = self._layouts.get(key)
        if cached is None:
            cached = layout_caption(c, self.style, self.layout.width, self.layout.height,
                                    avoid_rects=self._avoid_rects(c), custom_fonts=self.custom_fonts)
            self._layouts[key] = cached
        return cached

    def active(self, t: float) -> list[Caption]:
        hi = bisect_right(self._starts, t)
        lo = bisect_left(self._starts, t - 3600.0)
        return [c for c in self.captions[lo:hi] if c.start <= t < c.end]

    def draw(self, canvas: np.ndarray, t: float) -> int:
        """Blend captions active at ``t``; returns how many were drawn."""
        drawn = 0
        for c in self.active(t):
            state = caption_frame_state(c, self.style, t)
            if not state["visible"] or state["opacity"] <= 0:
                continue
            tile = self.renderer.render(c, self.layout_for(c), state, self.style)
            if tile is not None:
                tile.composite_onto(canvas, state["opacity"])
                drawn += 1
        return drawn
