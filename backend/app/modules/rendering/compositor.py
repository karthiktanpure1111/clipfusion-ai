"""Per-frame canvas compositor (OpenCV/numpy) following ``app/schemas/framing.py``.

* Background ``blur``: ``background_source`` drawn ``cover`` into the canvas, blurred with
  sigma ``blur * W / 1080`` - computed on a downscaled image with a proportionally scaled
  sigma, then darkened ``rgb *= 1 - dim`` and upscaled. ``color`` fills; ``image`` draws the
  asset ``cover`` (pre-computed once).
* Layers in ascending ``z``: ``geometry.fit_rects`` decides the source crop and the drawn
  rect; sampling uses a sub-pixel affine warp (smooth keyframe pans; area pre-shrink for
  strong downscales). ``radius`` rounds corners (anti-aliased), ``border_width`` strokes
  inside the drawn rect, ``shadow`` adds a soft drop shadow (sizes in px @1080 width).
* Watermark (image or text) is pre-rendered once and blended last.

When opaque ``cover`` layers without rounded corners tile the whole canvas (e.g. the
facecam + gameplay split screen) the background can never be seen, so it is not computed at
all (bit-identical output, ~4 ms less per 1080x1920 frame).
"""

from __future__ import annotations

import logging
import math
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw

from app.core.errors import ValidationFailed
from app.modules.framing.geometry import PxRect, fit_rects, rect_at, to_px
from app.modules.subtitles.text_renderer import Overlay, hex_to_rgb
from app.schemas.framing import CompiledLayer, CompiledLayout
from app.schemas.render import FontFile, WatermarkOptions

log = logging.getLogger(__name__)

REFERENCE_WIDTH = 1080.0
SMALL_BLUR_SIGMA = 2.5        # sigma (px) used on the downscaled background
DOWNSCALE_PRESHRINK = 0.75    # below this scale, area-resample before the affine warp
LAYER_SHADOW = {"sigma": 18.0, "offset_y": 10.0, "opacity": 0.45}


def hex_to_bgr(color: str) -> tuple[int, int, int]:
    r, g, b = hex_to_rgb(color if color.startswith("#") and len(color) == 7 else "#000000")
    return b, g, r


@lru_cache(maxsize=64)
def rounded_mask(w: int, h: int, radius: float, ss: int = 4) -> np.ndarray:
    """Anti-aliased rounded-rect coverage (float32 0..1) of size ``h x w``."""
    if radius <= 0.5:
        return np.ones((h, w), np.float32)
    big = Image.new("L", (w * ss, h * ss), 0)
    r = min(radius * ss, w * ss / 2, h * ss / 2)
    ImageDraw.Draw(big).rounded_rectangle((0, 0, w * ss - 1, h * ss - 1), radius=r, fill=255)
    return np.asarray(big.resize((w, h), Image.Resampling.BOX), np.float32) / 255.0


@lru_cache(maxsize=32)
def border_mask(w: int, h: int, radius: float, width: float) -> np.ndarray:
    """Coverage of a stroke of ``width`` px drawn inside a rounded rect."""
    outer = rounded_mask(w, h, radius)
    b = int(round(width))
    if b <= 0 or 2 * b >= min(w, h):
        return outer if b > 0 else np.zeros((h, w), np.float32)
    inner = np.zeros((h, w), np.float32)
    inner[b:h - b, b:w - b] = rounded_mask(w - 2 * b, h - 2 * b, max(radius - b, 0.0))
    return np.clip(outer - inner, 0.0, 1.0)


@lru_cache(maxsize=16)
def shadow_tile(w: int, h: int, radius: float, sigma: float, opacity: float) -> tuple[np.ndarray, int]:
    """Blurred shadow alpha for a ``w x h`` rounded rect; returns (alpha, margin)."""
    margin = int(math.ceil(3 * sigma))
    alpha = np.zeros((h + 2 * margin, w + 2 * margin), np.float32)
    alpha[margin:margin + h, margin:margin + w] = rounded_mask(w, h, radius)
    ds = max(1.0, sigma / 4.0)
    small = cv2.resize(alpha, (max(1, round(alpha.shape[1] / ds)), max(1, round(alpha.shape[0] / ds))),
                       interpolation=cv2.INTER_AREA)
    small = cv2.GaussianBlur(small, (0, 0), sigma / ds)
    return cv2.resize(small, (alpha.shape[1], alpha.shape[0]), interpolation=cv2.INTER_LINEAR) * opacity, margin


def _blend_mask(region: np.ndarray, patch: np.ndarray, mask: np.ndarray) -> None:
    """region = patch * mask + region * (1 - mask), in place (uint8 BGR)."""
    region[:] = cv2.blendLinear(patch, region, mask, 1.0 - mask)


def warp_region(frame: np.ndarray, crop: PxRect, draw: PxRect, canvas_w: int, canvas_h: int
                ) -> tuple[np.ndarray, int, int] | None:
    """Sample source ``crop`` (px) into canvas ``draw`` (px). Returns ``(patch, x, y)`` with
    the patch clipped to the canvas, or None when nothing is visible."""
    cx, cy, cw, ch = crop
    dx, dy, dw, dh = draw
    if cw <= 0 or ch <= 0 or dw < 0.5 or dh < 0.5:
        return None
    x0, y0 = max(int(round(dx)), 0), max(int(round(dy)), 0)
    x1, y1 = min(int(round(dx + dw)), canvas_w), min(int(round(dy + dh)), canvas_h)
    if x1 <= x0 or y1 <= y0:
        return None
    sx, sy = dw / cw, dh / ch
    src = frame
    if min(sx, sy) < DOWNSCALE_PRESHRINK:
        fh, fw = frame.shape[:2]
        ix0, iy0 = max(int(math.floor(cx)), 0), max(int(math.floor(cy)), 0)
        ix1, iy1 = min(int(math.ceil(cx + cw)), fw), min(int(math.ceil(cy + ch)), fh)
        if ix1 <= ix0 or iy1 <= iy0:
            return None
        tw, th = max(1, round((ix1 - ix0) * sx)), max(1, round((iy1 - iy0) * sy))
        src = cv2.resize(frame[iy0:iy1, ix0:ix1], (tw, th), interpolation=cv2.INTER_AREA)
        fx, fy = tw / (ix1 - ix0), th / (iy1 - iy0)
        cx, cy, cw, ch = (cx - ix0) * fx, (cy - iy0) * fy, cw * fx, ch * fy
        sx, sy = dw / cw, dh / ch
    m = np.float32([[1.0 / sx, 0.0, cx + (x0 + 0.5 - dx) / sx - 0.5],
                    [0.0, 1.0 / sy, cy + (y0 + 0.5 - dy) / sy - 0.5]])
    patch = cv2.warpAffine(src, m, (x1 - x0, y1 - y0), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                           borderMode=cv2.BORDER_REPLICATE)
    return patch, x0, y0


class Compositor:
    """Draws background + layers of a :class:`CompiledLayout` for decoded source frames of
    ``frame_w x frame_h`` (any decode size: rects are normalised)."""

    def __init__(self, layout: CompiledLayout, frame_w: int, frame_h: int, *,
                 background_image: Path | None = None) -> None:
        self.layout = layout
        self.W, self.H = int(layout.width), int(layout.height)
        self.fw, self.fh = int(frame_w), int(frame_h)
        self.k = self.W / REFERENCE_WIDTH
        self.layers: list[CompiledLayer] = sorted(layout.layers, key=lambda layer: layer.z)
        self.watermark: Overlay | None = None
        self._static_bg: np.ndarray | None = None
        self.background_hidden = self._layers_cover_canvas()
        bg = layout.background
        if bg.type == "color":
            self._static_bg = np.empty((self.H, self.W, 3), np.uint8)
            self._static_bg[:] = hex_to_bgr(bg.color)
        elif bg.type == "image":
            if background_image is None or not Path(background_image).is_file():
                raise ValidationFailed("The background image asset is missing.", code="background_image_missing")
            self._static_bg = self._load_background_image(Path(background_image))

    # ------------------------------------------------------------ background
    def _layers_cover_canvas(self) -> bool:
        """True when opaque cover layers draw every canvas pixel at every time (exact test on
        the integer pixel rects :func:`warp_region` fills)."""
        mask = np.zeros((self.H, self.W), dtype=bool)
        for layer in self.layers:
            if layer.fit != "cover" or layer.radius * self.k > 0.5:
                continue
            rects = [rect_at(layer, k.t) for k in layer.keyframes] or [rect_at(layer, 0.0)]
            if any(r.w * self.fw <= 0 or r.h * self.fh <= 0 for r in rects):
                continue
            dx, dy, dw, dh = to_px(layer.dest, self.W, self.H)
            if dw < 0.5 or dh < 0.5:
                continue
            x0, y0 = max(int(round(dx)), 0), max(int(round(dy)), 0)
            x1, y1 = min(int(round(dx + dw)), self.W), min(int(round(dy + dh)), self.H)
            mask[y0:y1, x0:x1] = True
        return bool(mask.all())

    def _load_background_image(self, path: Path) -> np.ndarray:
        img = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if img is None:
            raise ValidationFailed("The background image could not be decoded.", code="background_image_invalid")
        h, w = img.shape[:2]
        crop, _ = fit_rects((0.0, 0.0, float(w), float(h)), (0.0, 0.0, float(self.W), float(self.H)), "cover")
        x, y, cw, ch = (int(round(v)) for v in crop)
        return cv2.resize(img[y:y + ch, x:x + cw], (self.W, self.H), interpolation=cv2.INTER_AREA)

    def _blur_background(self, frame: np.ndarray) -> np.ndarray:
        bg = self.layout.background
        src = to_px(self.layout.background_source, self.fw, self.fh)
        crop, _ = fit_rects(src, (0.0, 0.0, float(self.W), float(self.H)), "cover")
        x0, y0 = max(int(math.floor(crop[0])), 0), max(int(math.floor(crop[1])), 0)
        x1 = min(max(int(math.ceil(crop[0] + crop[2])), x0 + 1), self.fw)
        y1 = min(max(int(math.ceil(crop[1] + crop[3])), y0 + 1), self.fh)
        sigma = bg.blur * self.k
        ds = max(1.0, sigma / SMALL_BLUR_SIGMA)
        sw, sh = max(8, round(self.W / ds)), max(8, round(self.H / ds))
        small = cv2.resize(frame[y0:y1, x0:x1], (sw, sh), interpolation=cv2.INTER_AREA)
        if sigma > 0.3:
            small = cv2.GaussianBlur(small, (0, 0), sigma * sw / self.W)
        if bg.dim > 0:
            small = cv2.convertScaleAbs(small, alpha=1.0 - bg.dim)
        return cv2.resize(small, (self.W, self.H), interpolation=cv2.INTER_LINEAR)

    def background(self, frame: np.ndarray) -> np.ndarray:
        if self.background_hidden:
            return np.empty((self.H, self.W, 3), np.uint8)    # every pixel is overwritten by a layer
        if self._static_bg is not None:
            return self._static_bg.copy()
        return self._blur_background(frame)

    # ---------------------------------------------------------------- layers
    def _draw_shadow(self, canvas: np.ndarray, x: int, y: int, w: int, h: int, radius: float) -> None:
        alpha, margin = shadow_tile(w, h, radius, LAYER_SHADOW["sigma"] * self.k, LAYER_SHADOW["opacity"])
        oy = int(round(LAYER_SHADOW["offset_y"] * self.k))
        ax0, ay0 = x - margin, y - margin + oy
        cx0, cy0 = max(ax0, 0), max(ay0, 0)
        cx1, cy1 = min(ax0 + alpha.shape[1], self.W), min(ay0 + alpha.shape[0], self.H)
        if cx1 <= cx0 or cy1 <= cy0:
            return
        a = alpha[cy0 - ay0:cy1 - ay0, cx0 - ax0:cx1 - ax0]
        region = canvas[cy0:cy1, cx0:cx1]
        region[:] = (region.astype(np.float32) * (1.0 - a)[..., None] + 0.5).astype(np.uint8)

    def draw_layer(self, canvas: np.ndarray, frame: np.ndarray, layer: CompiledLayer, t: float) -> None:
        src = to_px(rect_at(layer, t), self.fw, self.fh)
        dst = to_px(layer.dest, self.W, self.H)
        crop, draw = fit_rects(src, dst, layer.fit)
        res = warp_region(frame, crop, draw, self.W, self.H)
        if res is None:
            return
        patch, x, y = res
        h, w = patch.shape[:2]
        radius = layer.radius * self.k
        if layer.shadow:
            self._draw_shadow(canvas, x, y, w, h, radius)
        region = canvas[y:y + h, x:x + w]
        if radius > 0.5:
            _blend_mask(region, patch, rounded_mask(w, h, radius))
        else:
            region[:] = patch
        bw = layer.border_width * self.k
        if bw > 0.25:
            ring = border_mask(w, h, radius, bw)
            color = np.empty_like(patch)
            color[:] = hex_to_bgr(layer.border_color)
            _blend_mask(region, color, ring)

    def compose(self, frame: np.ndarray, t: float) -> np.ndarray:
        """New ``H x W`` BGR canvas with background and layers for clip time ``t``."""
        canvas = self.background(frame)
        for layer in self.layers:
            self.draw_layer(canvas, frame, layer, t)
        return canvas

    # ------------------------------------------------------------- watermark
    def set_watermark(self, options: WatermarkOptions, image_path: Path | None,
                      custom_fonts: list[FontFile] | None = None) -> None:
        """Pre-render the watermark (image asset preferred, else text)."""
        if not options.enabled:
            return
        if image_path is not None:
            rgba = _load_rgba(Path(image_path))
            target_w = max(1, round(options.scale * self.W))
            target_h = max(1, round(rgba.shape[0] * target_w / rgba.shape[1]))
            interp = cv2.INTER_AREA if target_w < rgba.shape[1] else cv2.INTER_CUBIC
            rgba = cv2.resize(rgba, (target_w, target_h), interpolation=interp)
        elif options.text and options.text.strip():
            rgba = _text_watermark(options.text.strip(), options.scale * self.W, self.W, custom_fonts or [])
        else:
            if options.asset_id is not None:
                raise ValidationFailed("The watermark image asset is missing.", code="watermark_missing")
            log.warning("Watermark enabled without an image or text; skipping it")
            return
        alpha = rgba[..., 3].astype(np.float32) * options.opacity
        premul = rgba[..., :3].astype(np.float32) * (alpha[..., None] / 255.0)
        h, w = alpha.shape
        m = options.margin * self.W
        x = {"top_left": m, "bottom_left": m, "center": (self.W - w) / 2}.get(options.position, self.W - w - m)
        y = {"top_left": m, "top_right": m, "center": (self.H - h) / 2}.get(options.position, self.H - h - m)
        self.watermark = Overlay(x=int(round(x)), y=int(round(y)),
                                 premul_bgr=np.clip(premul + 0.5, 0, 255).astype(np.uint8),
                                 alpha=np.clip(alpha + 0.5, 0, 255).astype(np.uint8))

    def draw_watermark(self, canvas: np.ndarray) -> None:
        if self.watermark is not None:
            self.watermark.composite_onto(canvas)


def _load_rgba(path: Path) -> np.ndarray:
    """BGRA uint8 image (alpha = 255 when the file has none)."""
    if not path.is_file():
        raise ValidationFailed("The watermark image asset is missing.", code="watermark_missing")
    img = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if img is None:
        try:
            img = cv2.cvtColor(np.asarray(Image.open(path).convert("RGBA")), cv2.COLOR_RGBA2BGRA)
        except Exception as exc:  # noqa: BLE001
            raise ValidationFailed("The watermark image could not be decoded.", code="watermark_invalid") from exc
    if img.dtype != np.uint8:
        img = cv2.convertScaleAbs(img, alpha=255.0 / max(1, int(img.max())))
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGRA)
    elif img.shape[2] == 3:
        img = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
    return img


def _text_watermark(text: str, target_w: float, canvas_w: int, custom_fonts: list[FontFile]) -> np.ndarray:
    """White text with a dark outline and soft shadow, sized so its width ~= ``target_w``."""
    from app.modules.subtitles.fonts import resolve_font
    from app.modules.subtitles.text_layout import load_font

    path = str(resolve_font("Inter", 700, False, custom_fonts))
    probe = load_font(path, 100.0)
    size = 100.0 * target_w / max(probe.getlength(text), 1.0)
    size = min(max(size, 0.02 * canvas_w), 0.07 * canvas_w)
    font = load_font(path, size)
    stroke = max(1.0, size * 0.06)
    left, top, right, bottom = font.getbbox(text, stroke_width=stroke)
    pad = int(math.ceil(size * 0.3))
    w, h = int(right - left) + 2 * pad, int(bottom - top) + 2 * pad
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((pad - left, pad - top), text, font=font, fill=(255, 255, 255, 255),
                             stroke_width=stroke, stroke_fill=(0, 0, 0, 200))
    rgba = np.asarray(img).copy()
    shadow = cv2.GaussianBlur(rgba[..., 3].astype(np.float32), (0, 0), size * 0.08) * 0.5
    a_text = rgba[..., 3].astype(np.float32) / 255.0
    alpha = a_text * 255.0 + shadow * (1.0 - a_text)
    rgb = rgba[..., :3].astype(np.float32) * (a_text * 255.0 / np.maximum(alpha, 1e-3))[..., None]
    out = np.dstack([rgb, alpha]).clip(0, 255).astype(np.uint8)
    return cv2.cvtColor(out, cv2.COLOR_RGBA2BGRA)
