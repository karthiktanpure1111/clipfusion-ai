"""Output frame timeline: output fps, silence removal and kept ranges on the frame grid.

The main clip is sampled on a CFR grid ``t_i = i / fps`` (clip time). With silence removal,
frames whose clip time falls inside a removed silence are skipped. Kept ranges are snapped
to that grid (``[i0 / fps, i1 / fps)``) so the audio cut points coincide exactly with the
video cut points and A/V sync holds after any number of cuts.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from fractions import Fraction

from app.modules.rendering.video_io import fps_fraction

DEFAULT_FPS = 30.0
_EPS = 1e-6


def output_fps(source_fps: float, max_fps: float) -> Fraction:
    """``min(source fps, max_fps)`` as an exact fraction (30 when the source fps is unknown)."""
    src = source_fps if source_fps and source_fps > 1 else DEFAULT_FPS
    return fps_fraction(min(src, max_fps))


def removed_silences(silences: list[tuple[float, float]], *, clip_start: float, clip_end: float,
                     min_silence: float, padding: float) -> list[tuple[float, float]]:
    """Clip-time ranges to cut: source silences inside the clip that are longer than
    ``min_silence``, shrunk by ``padding`` on both sides, merged and sorted."""
    duration = clip_end - clip_start
    out: list[tuple[float, float]] = []
    for a, b in sorted(silences):
        a, b = max(a - clip_start, 0.0), min(b - clip_start, duration)
        if b - a <= min_silence:
            continue
        a, b = a + padding, b - padding
        if b - a <= _EPS:
            continue
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


@dataclass
class FramePlan:
    """Which frames of the CFR main-clip grid are kept."""

    fps: Fraction
    total_frames: int                                   # frames covering the whole clip
    ranges: list[tuple[int, int]] = field(default_factory=list)  # kept [i0, i1) frame ranges

    @property
    def output_frames(self) -> int:
        return sum(b - a for a, b in self.ranges)

    def time(self, i: int) -> float:
        return i / float(self.fps)

    def kept_clip_ranges(self) -> list[tuple[float, float]]:
        return [(self.time(a), self.time(b)) for a, b in self.ranges]

    def kept_mask(self) -> list[bool]:
        mask = [False] * self.total_frames
        for a, b in self.ranges:
            for i in range(a, b):
                mask[i] = True
        return mask


def plan_frames(duration: float, fps: Fraction, removed: list[tuple[float, float]]) -> FramePlan:
    """Frame plan for a clip of ``duration`` seconds with ``removed`` clip-time ranges."""
    f = float(fps)
    total = max(1, round(duration * f))
    ranges: list[tuple[int, int]] = []
    cursor = 0
    for a, b in removed:
        i_a = min(total, max(0, math.ceil(a * f - _EPS)))
        i_b = min(total, max(0, math.ceil(b * f - _EPS)))
        if i_a > cursor:
            ranges.append((cursor, i_a))
        cursor = max(cursor, i_b)
    if cursor < total:
        ranges.append((cursor, total))
    if not ranges:  # the whole clip is silence: removing it would leave nothing, keep it all
        ranges = [(0, total)]
    return FramePlan(fps=fps, total_frames=total, ranges=ranges)
