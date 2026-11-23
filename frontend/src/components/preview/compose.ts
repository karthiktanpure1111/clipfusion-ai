/**
 * Canvas compositor mirroring backend/app/modules/rendering/compositor.py.
 * All geometry is in OUTPUT pixels (layout.width x layout.height); ``ds`` = device px per
 * output px. Background blur: background_source drawn ``cover`` onto a small canvas
 * (downscale factor max(1, sigma / 2.5)), blurred with the proportionally scaled sigma
 * (CSS blur() radius = standard deviation), dimmed (rgb *= 1 - dim) and upscaled.
 */
import { fitRects, rectAt, toPx } from "@/lib/framing/geometry";
import type { CompiledLayer, CompiledLayout, PxRect, Rect } from "@/lib/framing/types";

export const REFERENCE_WIDTH = 1080;
const SMALL_BLUR_SIGMA = 2.5;
const LAYER_SHADOW = { sigma: 18.0, offsetY: 10.0, opacity: 0.45 };

type Canvas = HTMLCanvasElement | OffscreenCanvas;
type Ctx = CanvasRenderingContext2D | OffscreenCanvasRenderingContext2D;

function canvas(w: number, h: number): Canvas {
  if (typeof OffscreenCanvas !== "undefined") return new OffscreenCanvas(w, h);
  const c = document.createElement("canvas");
  c.width = w;
  c.height = h;
  return c;
}

/** Scratch buffers for the blurred background (re-used across frames). */
export class Scratch {
  private a: Canvas | null = null;
  private b: Canvas | null = null;
  private c: Canvas | null = null;
  get(w: number, h: number, pad: number) {
    const size = (cv: Canvas | null, cw: number, ch: number) => {
      if (!cv) return canvas(cw, ch);
      if (cv.width !== cw || cv.height !== ch) {
        cv.width = cw;
        cv.height = ch;
      }
      return cv;
    };
    this.a = size(this.a, w, h);
    this.b = size(this.b, w + 2 * pad, h + 2 * pad);
    this.c = size(this.c, w, h);
    return [this.a, this.b, this.c] as const;
  }
}

const ctx2d = (c: Canvas) => c.getContext("2d") as Ctx;

export type Drawable = HTMLVideoElement | HTMLImageElement;

function mediaSize(m: Drawable): [number, number] {
  return m instanceof HTMLVideoElement ? [m.videoWidth, m.videoHeight] : [m.naturalWidth, m.naturalHeight];
}

function drawBlurBackground(ctx: Ctx, video: HTMLVideoElement, layout: CompiledLayout, scratch: Scratch) {
  const W = layout.width;
  const H = layout.height;
  const [fw, fh] = mediaSize(video);
  const bg = layout.background;
  const [crop] = fitRects(toPx(layout.background_source, fw, fh), [0, 0, W, H], "cover");
  const x0 = Math.max(Math.floor(crop[0]), 0);
  const y0 = Math.max(Math.floor(crop[1]), 0);
  const x1 = Math.min(Math.max(Math.ceil(crop[0] + crop[2]), x0 + 1), fw);
  const y1 = Math.min(Math.max(Math.ceil(crop[1] + crop[3]), y0 + 1), fh);
  const sigma = bg.blur * (W / REFERENCE_WIDTH);
  const down = Math.max(1, sigma / SMALL_BLUR_SIGMA);
  const sw = Math.max(8, Math.round(W / down));
  const sh = Math.max(8, Math.round(H / down));
  const smallSigma = (sigma * sw) / W;
  const pad = Math.ceil(3 * smallSigma) + 1;
  const [a, b, c] = scratch.get(sw, sh, pad);
  const actx = ctx2d(a);
  actx.drawImage(video, x0, y0, x1 - x0, y1 - y0, 0, 0, sw, sh);
  let result: Canvas = a;
  if (sigma > 0.3) {
    // edge-replicate padding so the blur does not darken the borders (OpenCV border mode)
    const bctx = ctx2d(b);
    bctx.drawImage(a, pad, pad);
    bctx.drawImage(a, 0, 0, 1, sh, 0, pad, pad, sh);
    bctx.drawImage(a, sw - 1, 0, 1, sh, sw + pad, pad, pad, sh);
    bctx.drawImage(b, 0, pad, sw + 2 * pad, 1, 0, 0, sw + 2 * pad, pad);
    bctx.drawImage(b, 0, sh + pad - 1, sw + 2 * pad, 1, 0, sh + pad, sw + 2 * pad, pad);
    const cctx = ctx2d(c);
    cctx.clearRect(0, 0, sw, sh);
    if (typeof cctx.filter === "string") {
      cctx.filter = `blur(${smallSigma}px)`;
      cctx.drawImage(b, -pad, -pad);
      cctx.filter = "none";
    } else cctx.drawImage(b, pad, pad, sw, sh, 0, 0, sw, sh);
    result = c;
  }
  if (bg.dim > 0) {
    const rctx = ctx2d(result);
    rctx.fillStyle = `rgba(0,0,0,${bg.dim})`;
    rctx.fillRect(0, 0, sw, sh);
  }
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = "medium";
  ctx.drawImage(result, 0, 0, sw, sh, 0, 0, W, H);
}

function drawCover(ctx: Ctx, img: HTMLImageElement, W: number, H: number) {
  const [iw, ih] = mediaSize(img);
  if (!iw || !ih) return;
  const [crop] = fitRects([0, 0, iw, ih], [0, 0, W, H], "cover");
  ctx.drawImage(img, crop[0], crop[1], crop[2], crop[3], 0, 0, W, H);
}

export function roundRectPath(x: number, y: number, w: number, h: number, r: number): Path2D {
  const p = new Path2D();
  p.roundRect(x, y, w, h, Math.max(0, Math.min(r, w / 2, h / 2)));
  return p;
}

function drawLayer(ctx: Ctx, video: HTMLVideoElement, layer: CompiledLayer, layout: CompiledLayout, t: number, ds: number) {
  const [fw, fh] = mediaSize(video);
  const k = layout.width / REFERENCE_WIDTH;
  const [crop, draw] = fitRects(toPx(rectAt(layer, t), fw, fh), toPx(layer.dest, layout.width, layout.height), layer.fit);
  if (crop[2] <= 0 || crop[3] <= 0 || draw[2] < 0.5 || draw[3] < 0.5) return;
  const x = Math.round(draw[0]);
  const y = Math.round(draw[1]);
  const w = Math.round(draw[0] + draw[2]) - x;
  const h = Math.round(draw[1] + draw[3]) - y;
  const radius = layer.radius * k;
  const shape = radius > 0.5 ? roundRectPath(x, y, w, h, radius) : null;
  if (layer.shadow) {
    ctx.save();
    ctx.shadowColor = `rgba(0,0,0,${LAYER_SHADOW.opacity})`;
    ctx.shadowBlur = 2 * LAYER_SHADOW.sigma * k * ds;
    ctx.shadowOffsetY = LAYER_SHADOW.offsetY * k * ds;
    ctx.fillStyle = "#000";
    if (shape) ctx.fill(shape);
    else ctx.fillRect(x, y, w, h);
    ctx.restore();
  }
  ctx.save();
  if (shape) ctx.clip(shape);
  // map the (sub-pixel) source crop onto the exact draw rect, then clip to the rounded rect
  const sx = draw[2] / crop[2];
  const sy = draw[3] / crop[3];
  ctx.beginPath();
  ctx.rect(x, y, w, h);
  ctx.clip();
  ctx.drawImage(video, crop[0], crop[1], crop[2], crop[3], draw[0], draw[1], crop[2] * sx, crop[3] * sy);
  ctx.restore();
  const bw = layer.border_width * k;
  if (bw > 0.25) {
    const ring = new Path2D();
    ring.addPath(roundRectPath(x, y, w, h, radius));
    ring.addPath(roundRectPath(x + bw, y + bw, w - 2 * bw, h - 2 * bw, Math.max(radius - bw, 0)));
    ctx.fillStyle = layer.border_color;
    ctx.fill(ring, "evenodd");
  }
}

export interface ComposeOptions {
  video: HTMLVideoElement | null;
  layout: CompiledLayout;
  t: number;
  ds: number;
  bgImage?: HTMLImageElement | null;
  scratch: Scratch;
}

/** Background + layers (ascending z) for clip time ``t``. Leaves ctx in output-px space. */
export function composeFrame(ctx: Ctx, o: ComposeOptions): void {
  const { layout, ds } = o;
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.clearRect(0, 0, ctx.canvas.width, ctx.canvas.height);
  ctx.setTransform(ds, 0, 0, ds, 0, 0);
  const W = layout.width;
  const H = layout.height;
  const bg = layout.background;
  const ready = !!o.video && o.video.readyState >= 2 && o.video.videoWidth > 0;
  ctx.fillStyle = bg.type === "color" ? bg.color : "#0F0F12";
  ctx.fillRect(0, 0, W, H);
  if (bg.type === "image" && o.bgImage?.complete) drawCover(ctx, o.bgImage, W, H);
  if (!ready || !o.video) return;
  if (bg.type === "blur") drawBlurBackground(ctx, o.video, layout, o.scratch);
  for (const layer of [...layout.layers].sort((a, b) => a.z - b.z)) drawLayer(ctx, o.video, layer, layout, o.t, ds);
}

/** Dashed safe-area guide (normalised margins). */
export function drawSafeArea(ctx: Ctx, W: number, H: number, sa: { top: number; bottom: number; left: number; right: number }, ds: number) {
  ctx.save();
  ctx.setTransform(ds, 0, 0, ds, 0, 0);
  ctx.strokeStyle = "rgba(81,91,212,0.9)";
  ctx.lineWidth = 2 / ds;
  ctx.setLineDash([10 / ds, 8 / ds]);
  ctx.strokeRect(sa.left * W, sa.top * H, W * (1 - sa.left - sa.right), H * (1 - sa.top - sa.bottom));
  ctx.fillStyle = "rgba(81,91,212,0.12)";
  ctx.fillRect(0, H * (1 - sa.bottom), W, H * sa.bottom);
  ctx.restore();
}

/** Visible-face boxes on the output (canvas-normalised rects). */
export function drawFaceBoxes(ctx: Ctx, W: number, H: number, faces: Rect[], ds: number) {
  ctx.save();
  ctx.setTransform(ds, 0, 0, ds, 0, 0);
  ctx.strokeStyle = "#DD2A7B";
  ctx.lineWidth = 3 / ds;
  for (const f of faces) {
    const p: PxRect = toPx(f, W, H);
    ctx.strokeRect(p[0], p[1], p[2], p[3]);
  }
  ctx.restore();
}
