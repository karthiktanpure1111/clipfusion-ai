"""Speaker activity: per-face mouth-motion scoring and a hysteresis speaker timeline.

Articulation of a face at a sample = mouth-region (lower 40% of the face box) frame-difference
energy minus 1.25x the eye-region energy (rigid head motion, detector jitter and compression noise).
``speaking_score`` contrasts articulation during detected speech with articulation outside it.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from app.modules.tracking.association import Observation
from app.schemas.framing import SpeakerSegment

_SCORE_SCALE = 3.0        # articulation (gray levels) giving a speaking score of 0.5
_MIN_ACTIVE = 1.0         # minimum smoothed articulation to count as "speaking"


PATCH = 32
_MAX_PAIR_GAP = 0.6       # seconds between observations whose patches are compared


def compute_face_motion(obs: list[Observation]) -> None:
    """Fill ``mouth``/``upper`` energies from consecutive face-aligned patches of one track
    (alignment by detection removes head translation)."""
    prev: Observation | None = None
    for o in obs:
        if prev is not None and o.patch is not None and prev.patch is not None                 and o.t - prev.t <= _MAX_PAIR_GAP:
            diff = np.abs(o.patch.astype(np.int16) - prev.patch.astype(np.int16))
            o.mouth = float(diff[int(PATCH * 0.6):, int(PATCH * 0.15):int(PATCH * 0.85)].mean())
            o.upper = float(diff[int(PATCH * 0.15):int(PATCH * 0.45), int(PATCH * 0.15):int(PATCH * 0.85)].mean())
        prev = o


def articulation(o: Observation) -> float:
    return max(0.0, o.mouth - 1.25 * o.upper)


def in_ranges(t: float, ranges: Sequence[tuple[float, float]]) -> bool:
    return any(a <= t <= b for a, b in ranges)


def speaking_score(obs: list[Observation], speech: Sequence[tuple[float, float]] | None) -> float:
    """0..1 mouth activity during speech relative to outside speech (or overall when the
    speech ranges are unknown)."""
    if not obs:
        return 0.0
    if speech:
        inside = [articulation(o) for o in obs if in_ranges(o.t, speech)]
        outside = [articulation(o) for o in obs if not in_ranges(o.t, speech)]
        if not inside:
            return 0.0
        s = float(np.mean(inside)) - (float(np.mean(outside)) if outside else 0.0)
    else:
        s = float(np.mean([articulation(o) for o in obs]))
    s = max(0.0, s)
    return round(s / (s + _SCORE_SCALE), 4)


def _activity_series(obs: list[Observation], times: np.ndarray, window: float) -> np.ndarray:
    """Smoothed articulation on ``times`` (NaN where the face is absent)."""
    out = np.full(times.shape, np.nan)
    if not obs:
        return out
    ot = np.array([o.t for o in obs])
    oa = np.array([articulation(o) for o in obs])
    for i, t in enumerate(times):
        mask = np.abs(ot - t) <= window / 2
        if mask.any():
            out[i] = float(oa[mask].mean())
    return out


def _winners(series: dict[int, np.ndarray], times: np.ndarray,
             speech: Sequence[tuple[float, float]] | None) -> list[int | None]:
    ids = sorted(series)
    winners: list[int | None] = []
    for i, t in enumerate(times):
        if speech and not in_ranges(float(t), speech):
            winners.append(None)
            continue
        vals = [(series[k][i], k) for k in ids if not np.isnan(series[k][i])]
        if not vals:
            winners.append(None)
            continue
        vals.sort(reverse=True)
        best, k = vals[0]
        second = vals[1][0] if len(vals) > 1 else 0.0
        winners.append(k if best >= _MIN_ACTIVE and best >= 1.2 * second else None)
    return winners


def speaker_timeline(tracks: dict[int, list[Observation]], *, duration: float, step: float,
                     speech: Sequence[tuple[float, float]] | None, default_track: int | None,
                     hysteresis: float = 2.5) -> list[SpeakerSegment]:
    """Active-speaker segments covering ``[0, duration]``. A new speaker takes over only after
    dominating for ``hysteresis`` seconds (the switch is placed where they started)."""
    if not tracks or duration <= 0:
        return []
    if len(tracks) == 1 or default_track is None:
        only = default_track if default_track is not None else next(iter(tracks))
        return [SpeakerSegment(start=0.0, end=round(duration, 3), track_id=only)]
    times = np.arange(0.0, duration + 1e-9, step)
    series = {k: _activity_series(v, times, window=1.5) for k, v in tracks.items()}
    winners = _winners(series, times, speech)

    current = next((w for w in winners if w is not None), default_track)
    switches: list[tuple[float, int]] = [(0.0, current)]
    cand: int | None = None
    cand_since = 0.0
    for t, w in zip(times, winners, strict=True):
        if w is None or w == current:
            cand = None if w == current else cand
            continue
        if w != cand:
            cand, cand_since = w, float(t)
        if float(t) - cand_since >= hysteresis - 1e-9:
            current = w
            switches.append((cand_since, w))
            cand = None

    segments: list[SpeakerSegment] = []
    for i, (start, tid) in enumerate(switches):
        end = switches[i + 1][0] if i + 1 < len(switches) else duration
        if end - start <= 1e-6:
            continue
        if segments and segments[-1].track_id == tid:
            segments[-1] = SpeakerSegment(start=segments[-1].start, end=round(end, 3), track_id=tid)
        else:
            segments.append(SpeakerSegment(start=round(start, 3), end=round(end, 3), track_id=tid))
    return segments
