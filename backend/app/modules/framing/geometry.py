"""Normative framing geometry shared by the smart-crop engine, crop protection, the renderer
and (mirrored in TypeScript) the frontend preview. See app/schemas/framing.py for semantics.

Pixel rects are ``(x, y, w, h)`` float tuples.
"""

from __future__ import annotations

from bisect import bisect_right

from app.schemas.framing import CompiledLayer, Keyframe, Rect

PxRect = tuple[float, float, float, float]


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def interpolate_keyframes(keyframes: list[Keyframe], t: float) -> Rect:
    """Rect at clip time ``t``: linear interpolation of x, y, w, h between surrounding
    keyframes; hold the first/last keyframe outside the range; a keyframe with ``cut=True``
    is reached instantly (no interpolation from the previous keyframe)."""
    if not keyframes:
        raise ValueError("no keyframes")
    if t <= keyframes[0].t:
        return keyframes[0].rect
    if t >= keyframes[-1].t:
        return keyframes[-1].rect
    times = [k.t for k in keyframes]
    i = bisect_right(times, t)  # keyframes[i-1].t <= t < keyframes[i].t
    a, b = keyframes[i - 1], keyframes[i]
    if b.cut or b.t <= a.t:
        return a.rect
    u = (t - a.t) / (b.t - a.t)
    return Rect(
        x=lerp(a.rect.x, b.rect.x, u),
        y=lerp(a.rect.y, b.rect.y, u),
        w=lerp(a.rect.w, b.rect.w, u),
        h=lerp(a.rect.h, b.rect.h, u),
    )


def rect_at(layer: CompiledLayer, t: float) -> Rect:
    """Source rect of a layer at clip time ``t`` (keyframes override the static source)."""
    return interpolate_keyframes(layer.keyframes, t) if layer.keyframes else layer.source


def to_px(rect: Rect, width: float, height: float) -> PxRect:
    return (rect.x * width, rect.y * height, rect.w * width, rect.h * height)


def to_norm(px: PxRect, width: float, height: float) -> Rect:
    x, y, w, h = px
    return Rect(x=x / width, y=y / height, w=w / width, h=h / height)


def fit_rects(src: PxRect, dst: PxRect, fit: str) -> tuple[PxRect, PxRect]:
    """Return ``(src_crop, dst_draw)`` in pixels for drawing ``src`` into ``dst``.

    cover   — crop ``src`` around its centre to ``dst``'s aspect, draw into all of ``dst``.
    contain — draw all of ``src`` scaled uniformly and centred inside ``dst``.
    """
    sx, sy, sw, sh = src
    dx, dy, dw, dh = dst
    if sw <= 0 or sh <= 0 or dw <= 0 or dh <= 0:
        return (sx, sy, max(sw, 0.0), max(sh, 0.0)), (dx, dy, 0.0, 0.0)
    if fit == "cover":
        target = dw / dh
        if sw / sh > target:  # source too wide -> trim left/right
            nw = sh * target
            return (sx + (sw - nw) / 2, sy, nw, sh), dst
        nh = sw / target       # source too tall -> trim top/bottom
        return (sx, sy + (sh - nh) / 2, sw, nh), dst
    scale = min(dw / sw, dh / sh)
    w, h = sw * scale, sh * scale
    return src, (dx + (dw - w) / 2, dy + (dh - h) / 2, w, h)


def visible_source_rect_px(layer: CompiledLayer, t: float, source_w: float, source_h: float,
                           canvas_w: float, canvas_h: float) -> Rect:
    """Normalised source rect that is visible through ``layer`` at clip time ``t``."""
    src = to_px(rect_at(layer, t), source_w, source_h)
    dst = to_px(layer.dest, canvas_w, canvas_h)
    crop, _ = fit_rects(src, dst, layer.fit)
    return to_norm(crop, source_w, source_h).clamp()


def intersection(a: Rect, b: Rect) -> Rect | None:
    x1, y1 = max(a.x, b.x), max(a.y, b.y)
    x2, y2 = min(a.x + a.w, b.x + b.w), min(a.y + a.h, b.y + b.h)
    if x2 <= x1 or y2 <= y1:
        return None
    return Rect(x=x1, y=y1, w=x2 - x1, h=y2 - y1)


def source_to_canvas(face: Rect, layer: CompiledLayer, t: float, source_w: float, source_h: float,
                     canvas_w: float, canvas_h: float) -> Rect | None:
    """Map a normalised source rect (e.g. a face) to normalised canvas coordinates through
    ``layer`` at time ``t``; returns only the visible part, or None when not visible."""
    src = to_px(rect_at(layer, t), source_w, source_h)
    dst = to_px(layer.dest, canvas_w, canvas_h)
    crop, draw = fit_rects(src, dst, layer.fit)
    cx, cy, cw, ch = crop
    if cw <= 0 or ch <= 0:
        return None
    fx, fy, fw, fh = to_px(face, source_w, source_h)
    ix1, iy1 = max(fx, cx), max(fy, cy)
    ix2, iy2 = min(fx + fw, cx + cw), min(fy + fh, cy + ch)
    if ix2 <= ix1 or iy2 <= iy1:
        return None
    sx, sy = draw[2] / cw, draw[3] / ch
    x = draw[0] + (ix1 - cx) * sx
    y = draw[1] + (iy1 - cy) * sy
    return Rect(x=x / canvas_w, y=y / canvas_h, w=(ix2 - ix1) * sx / canvas_w, h=(iy2 - iy1) * sy / canvas_h)
