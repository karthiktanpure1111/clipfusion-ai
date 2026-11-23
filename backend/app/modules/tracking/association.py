"""Associate per-frame face detections into tracks (greedy IoU + centre distance) and merge
fragments of the same person."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from app.schemas.framing import Rect


@dataclass
class Observation:
    """One detection inside a track (clip-relative time)."""

    t: float
    rect: Rect
    score: float
    patch: np.ndarray | None = field(default=None, repr=False)  # face-aligned 32x32 gray crop
    mouth: float = 0.0   # mouth-region difference energy vs the previous observation (gray levels)
    upper: float = 0.0   # eye-region difference energy (head-motion / jitter baseline)


@dataclass
class RawTrack:
    obs: list[Observation] = field(default_factory=list)

    @property
    def start(self) -> float:
        return self.obs[0].t

    @property
    def end(self) -> float:
        return self.obs[-1].t

    @property
    def last(self) -> Observation:
        return self.obs[-1]

    def mean_rect(self) -> Rect:
        n = len(self.obs)
        return Rect(x=sum(o.rect.x for o in self.obs) / n, y=sum(o.rect.y for o in self.obs) / n,
                    w=sum(o.rect.w for o in self.obs) / n, h=sum(o.rect.h for o in self.obs) / n)


def iou(a: Rect, b: Rect) -> float:
    inter = a.intersection_area(b)
    union = a.area + b.area - inter
    return inter / union if union > 0 else 0.0


def face_distance(a: Rect, b: Rect, aspect: float) -> float:
    """Centre distance in face-height units (x scaled by the frame aspect so units are square)."""
    dx = (a.cx - b.cx) * aspect
    dy = a.cy - b.cy
    return math.hypot(dx, dy) / max(1e-6, max(a.h, b.h))


def _size_ok(a: Rect, b: Rect, lo: float = 0.4, hi: float = 2.5) -> bool:
    ratio = b.area / max(a.area, 1e-9)
    return lo <= ratio <= hi


def _match_score(track: RawTrack, det: Observation, aspect: float, max_gap: float) -> float | None:
    gap = det.t - track.end
    if gap <= 0 or gap > max_gap + 1e-6:
        return None
    last = track.last.rect
    if not _size_ok(last, det.rect):
        return None
    overlap = iou(last, det.rect)
    dist = face_distance(last, det.rect, aspect)
    limit = 1.2 + 1.0 * gap
    if overlap < 0.1 and dist > limit:
        return None
    return overlap + max(0.0, 1.0 - dist / limit)


def associate(frames: list[tuple[float, list[Observation]]], *, aspect: float,
              max_gap: float = 1.5) -> list[RawTrack]:
    """Greedy frame-by-frame association. ``frames`` is ``[(t, detections)]`` in time order."""
    tracks: list[RawTrack] = []
    for _t, dets in frames:
        active = [tr for tr in tracks if 0 < _t - tr.end <= max_gap + 1e-6]
        pairs: list[tuple[float, int, int]] = []
        for ti, tr in enumerate(active):
            for di, det in enumerate(dets):
                score = _match_score(tr, det, aspect, max_gap)
                if score is not None:
                    pairs.append((score, ti, di))
        used_t: set[int] = set()
        used_d: set[int] = set()
        for _score, ti, di in sorted(pairs, key=lambda p: (-p[0], p[1], p[2])):
            if ti in used_t or di in used_d:
                continue
            active[ti].obs.append(dets[di])
            used_t.add(ti)
            used_d.add(di)
        for di, det in enumerate(dets):
            if di not in used_d:
                tracks.append(RawTrack(obs=[det]))
    return tracks


def _overlap_in_time(a: RawTrack, b: RawTrack) -> int:
    times_a = {round(o.t, 4) for o in a.obs}
    return sum(1 for o in b.obs if round(o.t, 4) in times_a)


def _can_merge(a: RawTrack, b: RawTrack, aspect: float, max_gap: float) -> bool:
    first, second = (a, b) if a.start <= b.start else (b, a)
    if _overlap_in_time(first, second) > 1:
        return False
    gap = second.start - first.end
    if 0 <= gap <= max_gap and _size_ok(first.last.rect, second.obs[0].rect) \
            and face_distance(first.last.rect, second.obs[0].rect, aspect) <= 1.5 + gap:
        return True
    # same place, same size, disjoint in time: the same (re-detected) person, e.g. a facecam
    ma, mb = first.mean_rect(), second.mean_rect()
    return _size_ok(ma, mb, 0.6, 1.67) and face_distance(ma, mb, aspect) <= 0.75


def merge_fragments(tracks: list[RawTrack], *, aspect: float, max_gap: float = 1.5) -> list[RawTrack]:
    """Repeatedly merge time-disjoint fragments that continue each other spatially."""
    tracks = sorted(tracks, key=lambda tr: (tr.start, tr.obs[0].rect.x))
    merged = True
    while merged:
        merged = False
        for i in range(len(tracks)):
            for j in range(i + 1, len(tracks)):
                if _can_merge(tracks[i], tracks[j], aspect, max_gap):
                    combined = sorted(tracks[i].obs + tracks[j].obs, key=lambda o: o.t)
                    dedup: list[Observation] = []
                    for o in combined:  # keep one observation per timestamp (higher score)
                        if dedup and abs(dedup[-1].t - o.t) < 1e-6:
                            if o.score > dedup[-1].score:
                                dedup[-1] = o
                            continue
                        dedup.append(o)
                    tracks[i] = RawTrack(obs=dedup)
                    del tracks[j]
                    merged = True
                    break
            if merged:
                break
    return tracks
