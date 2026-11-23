/**
 * Interactive rect editing in normalised coordinates (drag, 8-handle resize with optional
 * aspect lock, zoom around the centre, nudges, snapping). ``aspect`` arguments are the
 * NORMALISED aspect (w/h in normalised units) = pixel aspect * containerH / containerW.
 */
import { round6 } from "./layout";
import type { Rect } from "./types";

export type Handle = "n" | "s" | "e" | "w" | "ne" | "nw" | "se" | "sw";
export const HANDLES: Handle[] = ["nw", "n", "ne", "e", "se", "s", "sw", "w"];

const clamp = (v: number, lo: number, hi: number) => Math.min(Math.max(v, lo), Math.max(lo, hi));

/** Normalised aspect of a pixel aspect inside a ``w`` x ``h`` px container. */
export function normAspect(pixelAspect: number, containerW: number, containerH: number): number {
  return (pixelAspect * containerH) / containerW;
}

export function roundRect(r: Rect): Rect {
  return { x: round6(r.x), y: round6(r.y), w: round6(r.w), h: round6(r.h) };
}

export function moveRect(r: Rect, dx: number, dy: number): Rect {
  return { ...r, x: clamp(r.x + dx, 0, 1 - r.w), y: clamp(r.y + dy, 0, 1 - r.h) };
}

export function resizeRect(r: Rect, handle: Handle, dx: number, dy: number, aspect: number | null, min = 0.03): Rect {
  const x1 = r.x + r.w;
  const y1 = r.y + r.h;
  if (!aspect) {
    let nx0 = r.x;
    let ny0 = r.y;
    let nx1 = x1;
    let ny1 = y1;
    if (handle.includes("w")) nx0 = clamp(r.x + dx, 0, x1 - min);
    if (handle.includes("e")) nx1 = clamp(x1 + dx, r.x + min, 1);
    if (handle.includes("n")) ny0 = clamp(r.y + dy, 0, y1 - min);
    if (handle.includes("s")) ny1 = clamp(y1 + dy, r.y + min, 1);
    return { x: nx0, y: ny0, w: nx1 - nx0, h: ny1 - ny0 };
  }
  const west = handle.includes("w");
  const east = handle.includes("e");
  const north = handle.includes("n");
  const south = handle.includes("s");
  const ax = west ? x1 : east ? r.x : r.x + r.w / 2;
  const ay = north ? y1 : south ? r.y : r.y + r.h / 2;
  const dw = west ? -dx : dx;
  const dh = north ? -dy : dy;
  let w: number;
  if ((west || east) && (north || south)) w = Math.max(r.w + dw, (r.h + dh) * aspect);
  else if (west || east) w = r.w + dw;
  else w = (r.h + dh) * aspect;
  const maxWx = west ? ax : east ? 1 - ax : 2 * Math.min(ax, 1 - ax);
  const maxH = north ? ay : south ? 1 - ay : 2 * Math.min(ay, 1 - ay);
  const minW = Math.max(min, min * aspect);
  w = clamp(w, minW, Math.min(maxWx, maxH * aspect));
  const h = w / aspect;
  const x = west ? ax - w : east ? ax : ax - w / 2;
  const y = north ? ay - h : south ? ay : ay - h / 2;
  return { x, y, w, h };
}

/** Largest scale factor that still fits the rect inside the frame (zoom = 1 at that size). */
export function zoomOf(r: Rect): number {
  return Math.min(1 / r.w, 1 / r.h);
}

/** Scale ``r`` around its centre so that ``zoomOf`` becomes ``zoom`` (>= 1), kept inside. */
export function zoomRect(r: Rect, zoom: number): Rect {
  const f = zoomOf(r) / Math.max(zoom, 1);
  const w = Math.min(r.w * f, 1);
  const h = Math.min(r.h * f, 1);
  const cx = r.x + r.w / 2;
  const cy = r.y + r.h / 2;
  return { x: clamp(cx - w / 2, 0, 1 - w), y: clamp(cy - h / 2, 0, 1 - h), w, h };
}

/** Re-shape ``r`` to the normalised ``aspect`` keeping its centre (and height when possible). */
export function fitRectToAspect(r: Rect, aspect: number): Rect {
  let h = r.h;
  let w = h * aspect;
  if (w > 1) {
    w = 1;
    h = w / aspect;
  }
  if (h > 1) {
    h = 1;
    w = aspect;
  }
  const cx = r.x + r.w / 2;
  const cy = r.y + r.h / 2;
  return { x: clamp(cx - w / 2, 0, 1 - w), y: clamp(cy - h / 2, 0, 1 - h), w, h };
}

export interface SnapResult {
  rect: Rect;
  guidesX: number[];
  guidesY: number[];
}

/** Snap the rect centre (and edges) to the frame centre and to ``targets`` (e.g. face centres). */
export function snapRect(r: Rect, targets: { x: number; y: number }[], threshold = 0.012): SnapResult {
  const cx = r.x + r.w / 2;
  const cy = r.y + r.h / 2;
  const xs = [0.5, ...targets.map((p) => p.x)];
  const ys = [0.5, ...targets.map((p) => p.y)];
  let bestX: number | null = null;
  let bestY: number | null = null;
  for (const x of xs) if (Math.abs(x - cx) < threshold && (bestX === null || Math.abs(x - cx) < Math.abs(bestX - cx))) bestX = x;
  for (const y of ys) if (Math.abs(y - cy) < threshold && (bestY === null || Math.abs(y - cy) < Math.abs(bestY - cy))) bestY = y;
  const rect = {
    ...r,
    x: bestX === null ? r.x : clamp(bestX - r.w / 2, 0, 1 - r.w),
    y: bestY === null ? r.y : clamp(bestY - r.h / 2, 0, 1 - r.h),
  };
  return { rect, guidesX: bestX === null ? [] : [bestX], guidesY: bestY === null ? [] : [bestY] };
}

export function rectEquals(a: Rect, b: Rect, eps = 1e-6): boolean {
  return Math.abs(a.x - b.x) < eps && Math.abs(a.y - b.y) < eps && Math.abs(a.w - b.w) < eps && Math.abs(a.h - b.h) < eps;
}
