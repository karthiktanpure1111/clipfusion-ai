/**
 * Exact TypeScript port of backend/app/modules/framing/geometry.py (normative framing
 * geometry shared with the renderer) plus face sampling from rendering/caption_track.py.
 * Parity is pinned by shared/fixtures/framing_cases.json (see geometry.test.ts).
 */
import type { CompiledLayer, CompiledLayout, FaceTrack, Keyframe, PxRect, Rect } from "./types";

export function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

/** Python ``bisect_right`` over ascending numbers. */
export function bisectRight(arr: number[], x: number): number {
  let lo = 0;
  let hi = arr.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (x < arr[mid]) hi = mid;
    else lo = mid + 1;
  }
  return lo;
}

/** ``Rect.clamp`` of the schema. */
export function clampRect(r: Rect): Rect {
  const w = Math.min(Math.max(r.w, 1e-4), 1.0);
  const h = Math.min(Math.max(r.h, 1e-4), 1.0);
  const x = Math.min(Math.max(r.x, 0.0), 1.0 - w);
  const y = Math.min(Math.max(r.y, 0.0), 1.0 - h);
  return { x, y, w, h };
}

/**
 * Rect at clip time ``t``: linear interpolation between surrounding keyframes; the first /
 * last keyframe is held outside the range; a keyframe with ``cut`` is reached instantly.
 */
export function interpolateKeyframes(keyframes: Keyframe[], t: number): Rect {
  if (keyframes.length === 0) throw new Error("no keyframes");
  if (t <= keyframes[0].t) return keyframes[0].rect;
  const last = keyframes[keyframes.length - 1];
  if (t >= last.t) return last.rect;
  const i = bisectRight(
    keyframes.map((k) => k.t),
    t,
  );
  const a = keyframes[i - 1];
  const b = keyframes[i];
  if (b.cut || b.t <= a.t) return a.rect;
  const u = (t - a.t) / (b.t - a.t);
  return {
    x: lerp(a.rect.x, b.rect.x, u),
    y: lerp(a.rect.y, b.rect.y, u),
    w: lerp(a.rect.w, b.rect.w, u),
    h: lerp(a.rect.h, b.rect.h, u),
  };
}

/** Source rect of a layer at clip time ``t`` (keyframes override the static source). */
export function rectAt(layer: Pick<CompiledLayer, "keyframes" | "source">, t: number): Rect {
  return layer.keyframes.length ? interpolateKeyframes(layer.keyframes, t) : layer.source;
}

export function toPx(rect: Rect, width: number, height: number): PxRect {
  return [rect.x * width, rect.y * height, rect.w * width, rect.h * height];
}

export function toNorm(px: PxRect, width: number, height: number): Rect {
  const [x, y, w, h] = px;
  return { x: x / width, y: y / height, w: w / width, h: h / height };
}

/**
 * ``[srcCrop, dstDraw]`` in pixels for drawing ``src`` into ``dst``.
 * cover — crop src around its centre to dst's aspect and fill dst; contain — draw all of src
 * scaled uniformly and centred inside dst.
 */
export function fitRects(src: PxRect, dst: PxRect, fit: string): [PxRect, PxRect] {
  const [sx, sy, sw, sh] = src;
  const [dx, dy, dw, dh] = dst;
  if (sw <= 0 || sh <= 0 || dw <= 0 || dh <= 0) {
    return [
      [sx, sy, Math.max(sw, 0), Math.max(sh, 0)],
      [dx, dy, 0, 0],
    ];
  }
  if (fit === "cover") {
    const target = dw / dh;
    if (sw / sh > target) {
      const nw = sh * target;
      return [[sx + (sw - nw) / 2, sy, nw, sh], dst];
    }
    const nh = sw / target;
    return [[sx, sy + (sh - nh) / 2, sw, nh], dst];
  }
  const scale = Math.min(dw / sw, dh / sh);
  const w = sw * scale;
  const h = sh * scale;
  return [src, [dx + (dw - w) / 2, dy + (dh - h) / 2, w, h]];
}

/** Normalised source rect visible through ``layer`` at clip time ``t``. */
export function visibleSourceRect(
  layer: CompiledLayer,
  t: number,
  sourceW: number,
  sourceH: number,
  canvasW: number,
  canvasH: number,
): Rect {
  const src = toPx(rectAt(layer, t), sourceW, sourceH);
  const dst = toPx(layer.dest, canvasW, canvasH);
  const [crop] = fitRects(src, dst, layer.fit);
  return clampRect(toNorm(crop, sourceW, sourceH));
}

export function intersection(a: Rect, b: Rect): Rect | null {
  const x1 = Math.max(a.x, b.x);
  const y1 = Math.max(a.y, b.y);
  const x2 = Math.min(a.x + a.w, b.x + b.w);
  const y2 = Math.min(a.y + a.h, b.y + b.h);
  if (x2 <= x1 || y2 <= y1) return null;
  return { x: x1, y: y1, w: x2 - x1, h: y2 - y1 };
}

export function intersectionArea(a: Rect, b: Rect): number {
  const ix = Math.max(0, Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x));
  const iy = Math.max(0, Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y));
  return ix * iy;
}

/**
 * Map a normalised source rect (e.g. a face) to normalised canvas coordinates through
 * ``layer`` at time ``t``; only the visible part is returned, or null when not visible.
 */
export function sourceToCanvas(
  face: Rect,
  layer: CompiledLayer,
  t: number,
  sourceW: number,
  sourceH: number,
  canvasW: number,
  canvasH: number,
): Rect | null {
  const src = toPx(rectAt(layer, t), sourceW, sourceH);
  const dst = toPx(layer.dest, canvasW, canvasH);
  const [crop, draw] = fitRects(src, dst, layer.fit);
  const [cx, cy, cw, ch] = crop;
  if (cw <= 0 || ch <= 0) return null;
  const [fx, fy, fw, fh] = toPx(face, sourceW, sourceH);
  const ix1 = Math.max(fx, cx);
  const iy1 = Math.max(fy, cy);
  const ix2 = Math.min(fx + fw, cx + cw);
  const iy2 = Math.min(fy + fh, cy + ch);
  if (ix2 <= ix1 || iy2 <= iy1) return null;
  const sx = draw[2] / cw;
  const sy = draw[3] / ch;
  const x = draw[0] + (ix1 - cx) * sx;
  const y = draw[1] + (iy1 - cy) * sy;
  return { x: x / canvasW, y: y / canvasH, w: ((ix2 - ix1) * sx) / canvasW, h: ((iy2 - iy1) * sy) / canvasH };
}

export const FACE_HOLD_S = 0.75;

/**
 * Source rect of ``track`` at clip time ``t`` (port of rendering/caption_track.face_rect_at):
 * interpolation between samples less than ``maxGap`` apart, otherwise the nearest sample
 * within FACE_HOLD_S.
 */
export function faceRectAt(track: FaceTrack, t: number, maxGap = 2.0): Rect | null {
  const boxes = track.boxes;
  if (!boxes.length) return null;
  const i = bisectRight(
    boxes.map((b) => b.t),
    t,
  );
  const before = i > 0 ? boxes[i - 1] : null;
  const after = i < boxes.length ? boxes[i] : null;
  if (before && after && after.t - before.t <= maxGap && after.t > before.t) {
    const u = (t - before.t) / (after.t - before.t);
    const a = before.rect;
    const b = after.rect;
    return { x: lerp(a.x, b.x, u), y: lerp(a.y, b.y, u), w: lerp(a.w, b.w, u), h: lerp(a.h, b.h, u) };
  }
  const cands = [before, after].filter((b): b is NonNullable<typeof b> => b !== null);
  let near = cands[0];
  for (const c of cands) if (Math.abs(c.t - t) < Math.abs(near.t - t)) near = c;
  return Math.abs(near.t - t) <= FACE_HOLD_S ? near.rect : null;
}

/** Canvas rects of every face visible through any layer at clip time ``t``. */
export function visibleFaces(
  tracks: FaceTrack[],
  layout: CompiledLayout,
  t: number,
  sourceW: number,
  sourceH: number,
): Rect[] {
  const out: Rect[] = [];
  for (const track of tracks) {
    const face = faceRectAt(track, t);
    if (!face) continue;
    for (const layer of layout.layers) {
      const r = sourceToCanvas(face, layer, t, sourceW, sourceH, layout.width, layout.height);
      if (r && r.w > 1e-4 && r.h > 1e-4) out.push(r);
    }
  }
  return out;
}
