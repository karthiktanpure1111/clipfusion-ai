"""Facecam (webcam overlay) detection.

A streamer's facecam is a small, mostly static face near an edge/corner of the frame. Its
overlay usually has a straight, temporally stable border (a stroke, or the boundary between
the static webcam picture and the moving gameplay). We look for long stable horizontal and
vertical edge runs around the face; when none are found the overlay is estimated by
expanding the face box (~2.4x width, ~2.6x height).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import cv2
import numpy as np

from app.schemas.framing import Rect

log = logging.getLogger(__name__)

MAX_FACECAM_AREA = 0.06
_STABLE_FRACTION = 0.6
_BORDER_INSET = 0.015   # crop just inside the detected border so its stroke is not shown


@dataclass(frozen=True)
class FacecamEvidence:
    is_facecam: bool
    rect: Rect | None
    from_border: bool


def _near_corner(r: Rect) -> bool:
    return (r.cx < 0.3 or r.cx > 0.7) and (r.cy < 0.35 or r.cy > 0.65)


def _near_edge(r: Rect) -> bool:
    return r.cx < 0.35 or r.cx > 0.65 or r.cy < 0.35 or r.cy > 0.65


def is_static(rects: list[Rect]) -> bool:
    """Low positional variance relative to the face size."""
    if len(rects) < 2:
        return True
    cx = np.array([r.cx for r in rects])
    cy = np.array([r.cy for r in rects])
    mw = float(np.mean([r.w for r in rects]))
    mh = float(np.mean([r.h for r in rects]))
    return float(cx.std()) <= max(0.25 * mw, 0.004) and float(cy.std()) <= max(0.25 * mh, 0.004)


def expand_face(face: Rect, *, wx: float = 2.4, hy: float = 2.6) -> Rect:
    """Face box expanded around its centre (shifted down a little to include shoulders)."""
    w = min(1.0, face.w * wx)
    h = min(1.0, face.h * hy)
    cx, cy = face.cx, face.cy + face.h * 0.2
    return Rect(x=cx - w / 2, y=cy - h / 2, w=w, h=h).clamp()


def _close_runs(line: np.ndarray, gap: int = 4) -> np.ndarray:
    """Fill gaps of up to ``gap`` pixels in a 1-D boolean array."""
    kernel = np.ones((1, 2 * gap + 1), np.uint8)
    arr = line.astype(np.uint8)[None, :]
    return cv2.morphologyEx(arr, cv2.MORPH_CLOSE, kernel)[0].astype(bool)


def _run_covering(line: np.ndarray, lo: int, hi: int) -> int:
    """Length of the contiguous True run that covers [lo, hi), or 0."""
    if lo < 0 or hi > line.size or hi <= lo or not line[lo:hi].all():
        return 0
    a = lo
    while a > 0 and line[a - 1]:
        a -= 1
    b = hi
    while b < line.size and line[b]:
        b += 1
    return b - a


def _stable_edges(frames: list[np.ndarray], x0: int, y0: int, x1: int, y1: int) -> np.ndarray:
    acc = np.zeros((y1 - y0, x1 - x0), np.float32)
    for gray in frames:
        win = cv2.GaussianBlur(gray[y0:y1, x0:x1], (3, 3), 0)
        edges = cv2.Canny(win, 40, 120)
        acc += cv2.dilate(edges, np.ones((3, 3), np.uint8)) > 0
    return acc / max(1, len(frames)) >= _STABLE_FRACTION


def _dedupe(cands: list[tuple[int, int]], keep: int = 6) -> list[int]:
    """Collapse adjacent positions (same line, +-2 px) and keep the longest ``keep`` lines."""
    groups: list[tuple[int, int]] = []
    for pos, length in sorted(cands):
        if groups and pos - groups[-1][0] <= 2:
            if length > groups[-1][1]:
                groups[-1] = (pos, length)
            continue
        groups.append((pos, length))
    return [p for p, _ in sorted(groups, key=lambda g: -g[1])[:keep]]


def _candidates(stable: np.ndarray, lx1: int, lx2: int, ly1: int, ly2: int
                ) -> tuple[list[int], list[int], list[int], list[int]]:
    """Positions of long stable lines above/below/left/right of the face (window coords)."""
    fw, fh = lx2 - lx1, ly2 - ly1
    top, bottom, left, right = [], [], [], []
    for r in range(stable.shape[0]):
        if ly1 - 2 <= r <= ly2 + 2:
            continue
        length = _run_covering(_close_runs(stable[r]), lx1, lx2)
        if length >= 1.5 * fw:
            (top if r < ly1 else bottom).append((r, length))
    for c in range(stable.shape[1]):
        if lx1 - 2 <= c <= lx2 + 2:
            continue
        length = _run_covering(_close_runs(stable[:, c]), ly1, ly2)
        if length >= 1.5 * fh:
            (left if c < lx1 else right).append((c, length))
    return _dedupe(top), _dedupe(bottom), _dedupe(left), _dedupe(right)


def _best_rect(stable: np.ndarray, sides: tuple[list[int], list[int], list[int], list[int]]
               ) -> tuple[int, int, int, int] | None:
    """Pick the (top, bottom, left, right) combination whose perimeter is best covered by
    stable edges (a webcam border is a closed rectangle); ties -> the smaller rectangle.
    ``-1`` / ``size`` entries stand for the window edge (overlay flush with the frame)."""
    h, w = stable.shape
    row_cum = np.concatenate([np.zeros((h, 1)), np.cumsum(stable, axis=1)], axis=1)
    col_cum = np.concatenate([np.zeros((1, w)), np.cumsum(stable, axis=0)], axis=0)

    def hcov(r: int, a: int, b: int) -> float:
        if r < 0 or r >= h:
            return float(b - a)  # frame edge: fully supported
        return float(row_cum[r, b] - row_cum[r, a])

    def vcov(c: int, a: int, b: int) -> float:
        if c < 0 or c >= w:
            return float(b - a)
        return float(col_cum[b, c] - col_cum[a, c])

    best: tuple[float, int, tuple[int, int, int, int]] | None = None
    tops, bottoms, lefts, rights = sides
    for t in tops:
        for b in bottoms:
            for left in lefts:
                for right in rights:
                    a_x, b_x = max(0, left), min(w, right + 1)
                    a_y, b_y = max(0, t), min(h, b + 1)
                    perim = 2 * (b_x - a_x) + 2 * (b_y - a_y)
                    cov = hcov(t, a_x, b_x) + hcov(b, a_x, b_x) + vcov(left, a_y, b_y) + vcov(right, a_y, b_y)
                    support = cov / max(1, perim)
                    area = (b_x - a_x) * (b_y - a_y)
                    key = (round(support, 2), -area, (t, b, left, right))
                    if best is None or key[:2] > best[:2]:
                        best = key
    if best is None or best[0] < 0.85:
        return None
    return best[2]


def find_overlay_border(face: Rect, frames: list[np.ndarray]) -> Rect | None:
    """Find the webcam overlay rectangle around ``face`` from temporally stable straight edges
    in grayscale ``frames`` (all the same size). Returns a normalised rect or None."""
    if len(frames) < 3:
        return None
    H, W = frames[0].shape[:2]
    fx1, fy1 = int(face.x * W), int(face.y * H)
    fx2, fy2 = int((face.x + face.w) * W), int((face.y + face.h) * H)
    fw, fh = max(1, fx2 - fx1), max(1, fy2 - fy1)
    x0, x1 = max(0, fx1 - int(3.5 * fw)), min(W, fx2 + int(3.5 * fw))
    y0, y1 = max(0, fy1 - int(3.0 * fh)), min(H, fy2 + int(3.0 * fh))
    stable = _stable_edges(frames, x0, y0, x1, y1)
    top, bottom, left, right = _candidates(stable, fx1 - x0, fx2 - x0, fy1 - y0, fy2 - y0)
    sh, sw = stable.shape
    if y0 == 0:
        top.append(-1)
    if y1 == H:
        bottom.append(sh)
    if x0 == 0:
        left.append(-1)
    if x1 == W:
        right.append(sw)
    combo = _best_rect(stable, (top, bottom, left, right))
    if combo is None:
        return None
    t, b, lft, rgt = combo
    px1, px2 = max(0, lft) + x0, min(sw, rgt + 1) + x0
    py1, py2 = max(0, t) + y0, min(sh, b + 1) + y0
    ix, iy = max(2, round((px2 - px1) * _BORDER_INSET)), max(2, round((py2 - py1) * _BORDER_INSET))
    px1, px2 = px1 + (ix if lft >= 0 else 0), px2 - (ix if rgt < sw else 0)  # frame edges have no stroke
    py1, py2 = py1 + (iy if t >= 0 else 0), py2 - (iy if b < sh else 0)
    rect = Rect(x=px1 / W, y=py1 / H, w=(px2 - px1) / W, h=(py2 - py1) / H)
    return rect if _plausible(rect, face) else None


def _plausible(rect: Rect, face: Rect) -> bool:
    contains = (rect.x <= face.x + 1e-3 and rect.y <= face.y + 1e-3
                and rect.x + rect.w >= face.x + face.w - 1e-3 and rect.y + rect.h >= face.y + face.h - 1e-3)
    if not contains:
        return False
    if not (1.3 * face.w <= rect.w <= 7 * face.w and 1.3 * face.h <= rect.h <= 6 * face.h):
        return False
    return rect.area <= 0.35


def analyze_facecam(rects: list[Rect], mean_rect: Rect, frames: list[np.ndarray]) -> FacecamEvidence:
    """Decide whether a track is a facecam and estimate its overlay rect."""
    if mean_rect.area >= MAX_FACECAM_AREA or not is_static(rects):
        return FacecamEvidence(False, None, False)
    border = find_overlay_border(mean_rect, frames) if _near_edge(mean_rect) else None
    if border is not None:
        return FacecamEvidence(True, border, True)
    if _near_corner(mean_rect):
        return FacecamEvidence(True, expand_face(mean_rect), False)
    return FacecamEvidence(False, None, False)
