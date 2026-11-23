"""Helpers over :class:`ClipVisionAnalysis` used by smart crop, protection and suggestions."""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Sequence

import numpy as np

from app.schemas.framing import ActivityGrid, ClipVisionAnalysis, FaceTrack, Rect

MAX_GAP = 1.5  # seconds a face may be missing before it counts as absent


def track_by_id(vision: ClipVisionAnalysis, track_id: int | None) -> FaceTrack | None:
    if track_id is None:
        return None
    return next((t for t in vision.tracks if t.id == track_id), None)


def primary_track(vision: ClipVisionAnalysis | None) -> FaceTrack | None:
    """The flagged primary track, else the most prominent one."""
    if vision is None or not vision.tracks:
        return None
    flagged = next((t for t in vision.tracks if t.is_primary), None)
    return flagged or max(vision.tracks, key=lambda t: (t.prominence, -t.id))


def significant_tracks(vision: ClipVisionAnalysis | None) -> list[FaceTrack]:
    """People that matter for framing: primary/facecam, or well covered and reasonably
    prominent (>= 30% of the top prominence). Minor/background faces are excluded."""
    if vision is None or not vision.tracks:
        return []
    top = max(t.prominence for t in vision.tracks) or 1e-9
    return [t for t in vision.tracks
            if t.is_primary or t.is_facecam or (t.coverage >= 0.25 and t.prominence >= 0.3 * top)]


def box_at(track: FaceTrack, t: float, max_gap: float = MAX_GAP) -> Rect | None:
    """Face box at clip time ``t``: linear interpolation between detections at most
    ``max_gap`` apart; the nearest box within ``max_gap / 2`` outside them; else None."""
    boxes = track.boxes
    if not boxes:
        return None
    times = [b.t for b in boxes]
    i = bisect_left(times, t)
    if i < len(boxes) and abs(times[i] - t) < 1e-6:
        return boxes[i].rect
    if 0 < i < len(boxes):
        a, b = boxes[i - 1], boxes[i]
        if b.t - a.t <= max_gap:
            u = (t - a.t) / (b.t - a.t)
            ra, rb = a.rect, b.rect
            return Rect(x=ra.x + (rb.x - ra.x) * u, y=ra.y + (rb.y - ra.y) * u,
                        w=ra.w + (rb.w - ra.w) * u, h=ra.h + (rb.h - ra.h) * u)
    near = min(boxes, key=lambda b: abs(b.t - t))
    return near.rect if abs(near.t - t) <= max_gap / 2 else None


def union_rect(rects: Sequence[Rect]) -> Rect:
    """Bounding rect of ``rects``."""
    x1 = min(r.x for r in rects)
    y1 = min(r.y for r in rects)
    x2 = max(r.x + r.w for r in rects)
    y2 = max(r.y + r.h for r in rects)
    return Rect(x=max(0.0, x1), y=max(0.0, y1), w=min(1.0, x2) - max(0.0, x1), h=min(1.0, y2) - max(0.0, y1))


def head_box(face: Rect) -> Rect:
    """Face detector box grown to the whole head (hair above, chin below, ears at the sides)."""
    x1 = face.x - 0.12 * face.w
    x2 = face.x + face.w * 1.12
    y1 = face.y - 0.25 * face.h
    y2 = face.y + face.h * 1.08
    x1, y1 = max(0.0, x1), max(0.0, y1)
    return Rect(x=x1, y=y1, w=min(1.0, x2) - x1, h=min(1.0, y2) - y1)


def activity_array(activity: ActivityGrid | None) -> np.ndarray | None:
    """Activity values as a ``(rows, cols)`` array summing to 1 (None when unavailable)."""
    if activity is None or not activity.values or activity.rows * activity.cols != len(activity.values):
        return None
    arr = np.asarray(activity.values, dtype=np.float64).reshape(activity.rows, activity.cols)
    total = arr.sum()
    return arr / total if total > 0 else None


def activity_centroid(activity: ActivityGrid | None) -> tuple[float, float] | None:
    """Energy-weighted centre of the activity grid in normalised source coordinates."""
    arr = activity_array(activity)
    if arr is None:
        return None
    rows, cols = arr.shape
    xs = (np.arange(cols) + 0.5) / cols
    ys = (np.arange(rows) + 0.5) / rows
    return float((arr.sum(axis=0) * xs).sum()), float((arr.sum(axis=1) * ys).sum())
