"""Pillow caption rasteriser: a caption state -> premultiplied BGRA overlay tile.

Drawing rules (mirrored by the frontend canvas preview; ``k = canvas_w / 1080``):

* Caption-level ``scale`` (pop) scales every coordinate and size around the block centre.
  A word's animation ``scale`` grows it vertically around the line centre; each line is then
  re-flowed with the scaled widths (gaps unchanged) and re-aligned, so the growing word pushes
  its neighbours apart (see :func:`reflow_lines`). Text stays crisp: it is re-rasterised at
  the larger size, never resampled.
* Outline: ``outline_width * k`` px *outside* the glyph (Pillow ``stroke_width``; on a
  canvas use ``lineWidth = 2 * outline`` stroked before the fill). Strokes of all words are
  drawn before any fill so neighbouring outlines never cover letters.
* Shadow: cast by the text (fill + outline) only, gaussian with sigma ``blur * k / 2``
  (canvas ``shadowBlur`` semantics), offset ``offset_x/y * k``, colour * ``opacity``.
* Background ``box``: one rounded rect around the text block inflated by ``padding``;
  ``line``: one rounded rect per line inflated by ``padding`` horizontally and
  ``padding / 4`` vertically. Rects are unioned before ``background_opacity`` is applied.
* Highlight box: rounded rect (radius ``0.22 * word font size``) covering the scaled word box
  (line height tall) inflated horizontally by ``0.12 * word font size``.
* Caption-level ``opacity`` is applied when compositing (not baked into cached tiles); the
  short caption-level ``pop`` scale resamples the cached scale-1 tile (:func:`scale_overlay`).
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image, ImageDraw

from app.modules.subtitles.animation import effective_style, state_key
from app.modules.subtitles.text_layout import CaptionLayout, WordBox, char_offsets, load_font
from app.schemas.subtitles import Caption, SubtitleStyle

HIGHLIGHT_BOX_PAD = 0.12
HIGHLIGHT_BOX_RADIUS = 0.22
LINE_VPAD_RATIO = 0.25


def hex_to_rgb(color: str) -> tuple[int, int, int]:
    c = color.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


@dataclass
class Overlay:
    """Premultiplied BGR + alpha tile placed at canvas pixel ``(x, y)``."""

    x: int
    y: int
    premul_bgr: np.ndarray   # (h, w, 3) uint8, colour already multiplied by alpha
    alpha: np.ndarray        # (h, w) uint8

    def composite_onto(self, frame: np.ndarray, opacity: float = 1.0) -> None:
        """Alpha-blend the tile onto a BGR uint8 ``frame`` in place."""
        if opacity <= 0.0:
            return
        fh, fw = frame.shape[:2]
        h, w = self.alpha.shape
        x0, y0 = max(self.x, 0), max(self.y, 0)
        x1, y1 = min(self.x + w, fw), min(self.y + h, fh)
        if x1 <= x0 or y1 <= y0:
            return
        sx, sy = x0 - self.x, y0 - self.y
        rows, cols = slice(sy, sy + y1 - y0), slice(sx, sx + x1 - x0)
        region = frame[y0:y1, x0:x1]
        if opacity >= 1.0:  # fast integer path: region * (255 - a) / 255 + premultiplied colour
            inv = self._inverse_alpha()[rows, cols]
            region[:] = cv2.add(cv2.multiply(region, inv, scale=1.0 / 255.0), self.premul_bgr[rows, cols])
            return
        a = self.alpha[rows, cols].astype(np.float32)[..., None] * (opacity / 255.0)
        src = self.premul_bgr[rows, cols].astype(np.float32) * opacity
        region[:] = np.clip(region.astype(np.float32) * (1.0 - a) + src + 0.5, 0, 255).astype(np.uint8)

    def _inverse_alpha(self) -> np.ndarray:
        inv = getattr(self, "_inv", None)
        if inv is None:
            inv = cv2.merge([255 - self.alpha] * 3)
            self._inv = inv
        return inv


class _Xf:
    """Caption-level scale around the block centre, then translation into the tile."""

    def __init__(self, cx: float, cy: float, s: float, ox: float, oy: float) -> None:
        self.cx, self.cy, self.s, self.ox, self.oy = cx, cy, s, ox, oy

    def pt(self, x: float, y: float) -> tuple[float, float]:
        return self.cx + (x - self.cx) * self.s - self.ox, self.cy + (y - self.cy) * self.s - self.oy

    def box(self, x0: float, y0: float, x1: float, y1: float) -> tuple[float, float, float, float]:
        a, b = self.pt(x0, y0)
        c, d = self.pt(x1, y1)
        return a, b, c, d


def _tile_bounds(layout: CaptionLayout, style: SubtitleStyle, max_word_scale: float) -> tuple[int, int, int, int]:
    k = layout.scale_k
    x, y, w, h = layout.block_px
    sh = style.shadow
    shadow_extra = (1.5 * sh.blur * k + max(abs(sh.offset_x), abs(sh.offset_y)) * k) if sh.enabled else 0.0
    pad = style.background_padding * k if style.background_mode != "none" else 0.0
    margin = pad + style.outline_width * k + shadow_extra + layout.font_size * (0.3 + 0.6 * (max_word_scale - 1.0))
    margin += 0.05 * max(w, h)  # pop overshoot
    margin += max((wb.w for wb in layout.words), default=0.0) * (max_word_scale - 1.0)  # re-flow growth
    x0, y0 = int(np.floor(x - margin)), int(np.floor(y - margin))
    x1, y1 = int(np.ceil(x + w + margin)), int(np.ceil(y + h + margin))
    return max(x0, 0), max(y0, 0), min(x1, layout.canvas_w), min(y1, layout.canvas_h)


def _word_geometry(wb: WordBox, ws: float, left: float) -> tuple[float, float, float, float]:
    """Left x, baseline y, width and box top of a word scaled by ``ws`` (vertically around
    the line centre; ``left`` comes from :func:`reflow_lines`)."""
    cy = wb.y + wb.h / 2
    return left, cy + (wb.baseline - cy) * ws, wb.w * ws, cy - wb.h * ws / 2


def reflow_lines(layout: CaptionLayout, state: dict, align: str) -> list[float]:
    """Left x of every word after animation scaling: each line is re-flowed with the scaled
    word widths (gaps unchanged) and re-aligned in the block, so a growing word pushes its
    neighbours instead of overlapping them. Line breaks never change."""
    lefts = [wb.x for wb in layout.words]
    bx, _, bw, _ = layout.block_px
    for ln in layout.lines:
        scales = [state["words"][i]["scale"] for i in ln.words]
        if all(s == 1.0 for s in scales):
            continue
        gaps = [layout.words[b].x - (layout.words[a].x + layout.words[a].w) for a, b in zip(ln.words, ln.words[1:], strict=False)]
        widths = [layout.words[i].w * s for i, s in zip(ln.words, scales, strict=True)]
        total = sum(widths) + sum(gaps)
        x = {"left": bx, "right": bx + bw - total}.get(align, ln.x + ln.w / 2 - total / 2)
        for j, i in enumerate(ln.words):
            lefts[i] = x
            x += widths[j] + (gaps[j] if j < len(gaps) else 0.0)
    return lefts


def _draw_word(draw: ImageDraw.ImageDraw, layout: CaptionLayout, wb: WordBox, ws: float, left: float, xf: _Xf,
               fill: tuple[int, ...], stroke: float, stroke_fill: tuple[int, ...] | None) -> None:
    left, base, _, _ = _word_geometry(wb, ws, left)
    x, y = xf.pt(left, base)
    size = wb.font_size * ws * xf.s
    font = load_font(str(layout.font_path), size)
    ls = layout.letter_spacing * ws * xf.s
    kw = {"font": font, "anchor": "ls", "fill": fill}
    if stroke > 0 and stroke_fill is not None:
        kw.update(stroke_width=stroke, stroke_fill=stroke_fill)
    if abs(ls) < 1e-3:
        draw.text((x, y), wb.text, **kw)
        return
    for ch, off in zip(wb.text, char_offsets(font, wb.text, ls), strict=True):
        draw.text((x + off, y), ch, **kw)


def _aa_mask(rects: list[tuple[tuple[float, float, float, float], float]], size: tuple[int, int],
             ss: int = 3) -> np.ndarray:
    """Anti-aliased union of rounded rects ``[(box, radius)]`` as uint8 coverage (supersampled)."""
    w, h = size
    mask = np.zeros((h, w), np.uint8)
    if not rects:
        return mask
    x0 = max(int(np.floor(min(b[0] for b, _ in rects))) - 1, 0)
    y0 = max(int(np.floor(min(b[1] for b, _ in rects))) - 1, 0)
    x1 = min(int(np.ceil(max(b[2] for b, _ in rects))) + 1, w)
    y1 = min(int(np.ceil(max(b[3] for b, _ in rects))) + 1, h)
    if x1 <= x0 or y1 <= y0:
        return mask
    big = Image.new("L", ((x1 - x0) * ss, (y1 - y0) * ss), 0)
    d = ImageDraw.Draw(big)
    for (a, b, c, e), r in rects:
        box = ((a - x0) * ss, (b - y0) * ss, (c - x0) * ss, (e - y0) * ss)
        if box[2] - box[0] < 1 or box[3] - box[1] < 1:
            continue
        rad = max(0.0, min(r * ss, (box[3] - box[1]) / 2, (box[2] - box[0]) / 2))
        d.rounded_rectangle(box, radius=rad, fill=255)
    mask[y0:y1, x0:x1] = np.asarray(big.resize((x1 - x0, y1 - y0), Image.Resampling.BOX))
    return mask


def _background_rects(layout: CaptionLayout, style: SubtitleStyle, xf: _Xf) -> list[tuple[tuple, float]]:
    k = layout.scale_k
    pad = style.background_padding * k
    radius = style.background_radius * k * xf.s
    if style.background_mode == "box":
        x, y, w, h = layout.block_px
        rects = [(x - pad, y - pad, x + w + pad, y + h + pad)]
    else:
        vpad = pad * LINE_VPAD_RATIO
        rects = [(ln.x - pad, ln.y - vpad, ln.x + ln.w + pad, ln.y + ln.h + vpad) for ln in layout.lines]
    return [(xf.box(*r), radius) for r in rects]


# Premultiplied RGBA uint8 helpers (OpenCV: SIMD + saturating arithmetic).
def _colour_layer(mask: np.ndarray, color: str, opacity: float) -> np.ndarray:
    """Premultiplied RGBA of a solid colour through a uint8 coverage ``mask``."""
    a = cv2.convertScaleAbs(mask, alpha=opacity) if opacity < 1.0 else mask
    r, g, b = hex_to_rgb(color)
    return cv2.merge([cv2.convertScaleAbs(a, alpha=c / 255.0) for c in (r, g, b)] + [a])


def _premultiplied(rgba: np.ndarray, opacity: float = 1.0) -> np.ndarray:
    """Straight RGBA uint8 (Pillow) -> premultiplied RGBA uint8, alpha scaled by ``opacity``."""
    r, g, b, a = cv2.split(rgba)
    if opacity < 1.0:
        a = cv2.convertScaleAbs(a, alpha=opacity)
    return cv2.merge([cv2.multiply(c, a, scale=1.0 / 255.0) for c in (r, g, b)] + [a])


def _over(top: np.ndarray, bottom: np.ndarray) -> np.ndarray:
    """Porter-Duff 'over' on premultiplied RGBA uint8."""
    inv = cv2.bitwise_not(cv2.extractChannel(top, 3))
    return cv2.add(top, cv2.multiply(bottom, cv2.merge([inv, inv, inv, inv]), scale=1.0 / 255.0))


def _shadow(text: np.ndarray, style: SubtitleStyle, k: float, s: float) -> np.ndarray | None:
    sh = style.shadow
    if not sh.enabled or sh.opacity <= 0:
        return None
    alpha = cv2.extractChannel(text, 3)
    sigma = sh.blur * k * s / 2.0
    if sigma > 0.3:
        alpha = cv2.GaussianBlur(alpha, (0, 0), sigma)
    dx, dy = sh.offset_x * k * s, sh.offset_y * k * s
    if dx or dy:
        m = np.float32([[1, 0, dx], [0, 1, dy]])
        alpha = cv2.warpAffine(alpha, m, (alpha.shape[1], alpha.shape[0]), flags=cv2.INTER_LINEAR,
                               borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    return _colour_layer(alpha, sh.color, sh.opacity)


def _text_layer(layout: CaptionLayout, style: SubtitleStyle, state: dict, xf: _Xf, size: tuple[int, int],
                lefts: list[float]) -> np.ndarray:
    k = layout.scale_k
    outline = style.outline_width * k * xf.s
    oc = (*hex_to_rgb(style.outline_color), 255)
    full = Image.new("RGBA", size, (0, 0, 0, 0))
    full_draw = ImageDraw.Draw(full)
    partial: list[tuple[WordBox, dict]] = []
    visible = [(wb, ws) for wb, ws in zip(layout.words, state["words"], strict=True)
               if ws["visible"] and ws["opacity"] > 0]
    for wb, ws in visible:
        if ws["opacity"] < 1.0:
            partial.append((wb, ws))
        elif outline > 0:
            _draw_word(full_draw, layout, wb, ws["scale"], lefts[wb.index], xf, oc, outline, oc)
    for wb, ws in visible:
        if ws["opacity"] >= 1.0:
            _draw_word(full_draw, layout, wb, ws["scale"], lefts[wb.index], xf, (*hex_to_rgb(ws["color"]), 255),
                       0, None)
    out = _premultiplied(np.asarray(full))
    for wb, ws in partial:
        layer = Image.new("RGBA", size, (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        if outline > 0:
            _draw_word(d, layout, wb, ws["scale"], lefts[wb.index], xf, oc, outline, oc)
        _draw_word(d, layout, wb, ws["scale"], lefts[wb.index], xf, (*hex_to_rgb(ws["color"]), 255), 0, None)
        out = _over(_premultiplied(np.asarray(layer), ws["opacity"]), out)
    return out


def _box_layer(layout: CaptionLayout, style: SubtitleStyle, state: dict, xf: _Xf, size: tuple[int, int],
               lefts: list[float]) -> np.ndarray | None:
    has_bg = style.background_mode != "none" and style.background_opacity > 0
    boxes = [(wb, ws) for wb, ws in zip(layout.words, state["words"], strict=True)
             if ws["box"] and ws["visible"] and ws["opacity"] > 0]
    if not has_bg and not boxes:
        return None
    out = np.zeros((size[1], size[0], 4), np.uint8)
    if has_bg:
        out = _colour_layer(_aa_mask(_background_rects(layout, style, xf), size), style.background_color,
                            style.background_opacity)
    for wb, ws in boxes:
        left, _, width, top = _word_geometry(wb, ws["scale"], lefts[wb.index])
        fs = wb.font_size * ws["scale"]
        pad = HIGHLIGHT_BOX_PAD * fs
        bx = xf.box(left - pad, top, left + width + pad, top + wb.h * ws["scale"])
        mask = _aa_mask([(bx, HIGHLIGHT_BOX_RADIUS * fs * xf.s)], size)
        out = _over(_colour_layer(mask, ws["box"], ws["opacity"]), out)
    return out


def render_caption_tile(layout: CaptionLayout, state: dict, style: SubtitleStyle) -> Overlay | None:
    """Rasterise one caption state (``style`` must already include the caption override)."""
    if not state["visible"] or not layout.words:
        return None
    max_ws = max([ws["scale"] * ws["font_scale"] for ws in state["words"]] + [1.0])
    x0, y0, x1, y1 = _tile_bounds(layout, style, max_ws)
    if x1 <= x0 or y1 <= y0:
        return None
    size = (x1 - x0, y1 - y0)
    bx, by, bw, bh = layout.block_px
    xf = _Xf(bx + bw / 2, by + bh / 2, float(state["scale"]), x0, y0)
    lefts = reflow_lines(layout, state, style.align)
    out = _text_layer(layout, style, state, xf, size, lefts)
    shadow = _shadow(out, style, layout.scale_k, xf.s)
    if shadow is not None:
        out = _over(out, shadow)
    boxes = _box_layer(layout, style, state, xf, size, lefts)
    if boxes is not None:
        out = _over(out, boxes)
    alpha = cv2.extractChannel(out, 3)
    pts = cv2.findNonZero(alpha)
    if pts is None:
        return None
    tx, ty, tw, th = cv2.boundingRect(pts)
    crop = out[ty:ty + th, tx:tx + tw]
    return Overlay(x=x0 + tx, y=y0 + ty, premul_bgr=cv2.cvtColor(crop, cv2.COLOR_RGBA2BGR),
                   alpha=np.ascontiguousarray(alpha[ty:ty + th, tx:tx + tw]))


def scale_overlay(tile: Overlay, scale: float, cx: float, cy: float) -> Overlay:
    """Resample a tile by ``scale`` around canvas point ``(cx, cy)`` (used for the short
    caption-level ``pop`` scale so it does not re-rasterise every frame)."""
    h, w = tile.alpha.shape
    nw, nh = max(1, round(w * scale)), max(1, round(h * scale))
    interp = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
    return Overlay(x=int(round(cx + (tile.x - cx) * scale)), y=int(round(cy + (tile.y - cy) * scale)),
                   premul_bgr=cv2.resize(tile.premul_bgr, (nw, nh), interpolation=interp),
                   alpha=cv2.resize(tile.alpha, (nw, nh), interpolation=interp))


class CaptionRenderer:
    """Caches rasterised tiles by (caption id/text, state, font size, block position). The
    caption-level ``scale`` is applied by resampling the cached scale-1 tile."""

    def __init__(self, max_entries: int = 48) -> None:
        self._cache: OrderedDict[tuple, Overlay | None] = OrderedDict()
        self._max = max_entries
        self.hits = 0
        self.misses = 0

    def _cached(self, key: tuple, build: Callable[[], Overlay | None]) -> Overlay | None:
        if key in self._cache:
            self.hits += 1
            self._cache.move_to_end(key)
            return self._cache[key]
        self.misses += 1
        tile = build()
        self._cache[key] = tile
        if len(self._cache) > self._max:
            self._cache.popitem(last=False)
        return tile

    def render(self, caption: Caption, layout: CaptionLayout, state: dict, style: SubtitleStyle) -> Overlay | None:
        base = (caption.id, caption.text, round(layout.font_size, 3), tuple(round(v, 2) for v in layout.block_px))
        scale = float(state["scale"])
        flat = {**state, "scale": 1.0}
        if abs(scale - 1.0) < 1e-3:
            return self._cached(base + (state_key(flat),),
                                lambda: render_caption_tile(layout, flat, effective_style(style, caption)))
        unscaled = self.render(caption, layout, flat, style)
        if unscaled is None:
            return None
        bx, by, bw, bh = layout.block_px
        return self._cached(base + (state_key(state),),
                            lambda: scale_overlay(unscaled, scale, bx + bw / 2, by + bh / 2))
